"""What the spawn hooks did to each spawn of a replayed run (#1216): whether each brief carried
the bound and budget `brief-guard` appends, whether `Workflow` launches met the spawn hooks, which
bound ended a trial, and the launcher that stops a trial once its first wave is out. Every stream
is a recorded fixture under tests/fixtures/first-wave; no model is called and no container started."""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_spawns", REPO / "scripts" / "replay_spawns.py")
SPAWNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SPAWNS)

FIXTURES = REPO / "tests" / "fixtures" / "first-wave"


def stream(name):
    return SPAWNS.read_stream(FIXTURES / name)


def lines(name):
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines(keepends=True)


class SpawnBriefTests(unittest.TestCase):
    def test_the_brief_a_hook_rewrote_is_judged_not_the_one_the_model_wrote(self):
        rows = SPAWNS.spawn_briefs(stream("fanout-harness.jsonl"))
        first = rows[0]
        self.assertEqual((first["brief_source"], first["brief_bound"], first["brief_budget"]), ("hook", True, True))
        self.assertEqual(first["model"], "claude-haiku-test")

    def test_the_subagents_own_first_message_is_the_brief_it_received(self):
        second = SPAWNS.spawn_briefs(stream("fanout-harness.jsonl"))[1]
        self.assertEqual((second["brief_source"], second["brief_bound"], second["brief_budget"]),
                         ("thread", True, True))
        self.assertEqual((second["requested_model"], second["model"]), ("opus", "claude-sonnet-test"))

    def test_a_hook_that_changed_nothing_leaves_the_written_brief_unbudgeted(self):
        third = SPAWNS.spawn_briefs(stream("fanout-harness.jsonl"))[2]
        self.assertEqual((third["brief_source"], third["brief_bound"], third["brief_budget"]),
                         ("written", False, False))
        self.assertIsNone(third["model"])  # refused: its thread never ran

    def test_an_unreadable_brief_is_null_and_the_bare_arm_reads_its_written_brief(self):
        unknown = SPAWNS.spawn_briefs(stream("fanout-bare.jsonl"))[0]
        self.assertEqual((unknown["brief_source"], unknown["brief_bound"], unknown["brief_budget"]),
                         (None, None, None))
        bare = SPAWNS.spawn_briefs(stream("fanout-bare.jsonl"), hooks=False)[0]
        self.assertEqual((bare["brief_source"], bare["brief_bound"], bare["brief_budget"]), ("written", False, False))

    def test_a_brief_with_no_prompt_text_is_null_and_no_brief_text_reaches_a_row(self):
        messages = [{"type": "assistant", "parent_tool_use_id": None,
                     "message": {"id": "m", "content": [{"type": "tool_use", "id": "t", "name": "Task", "input": {}}]}}]
        row = SPAWNS.spawn_briefs(messages, hooks=False)[0]
        self.assertEqual((row["tool"], row["brief_bound"], row["brief_budget"]), ("Task", None, None))
        text = json.dumps(SPAWNS.spawn_briefs(stream("fanout-harness.jsonl")))
        self.assertNotIn("vendor A", text)

    def test_the_hooks_own_patterns_judge_the_brief(self):
        bound, budget = SPAWNS.load_patterns()
        guard = (REPO / "policy" / "hooks" / "brief-guard.py").read_text(encoding="utf-8")
        bound_text = guard[guard.index("BOUND = (") + 9:guard.index("CAP_NOTE")]
        self.assertTrue(bound.search(bound_text))
        self.assertTrue(budget.search("Expected spend: about 1,000 output tokens."))

    def test_detectors_that_will_not_load_leave_every_reading_unknown(self):
        patterns = SPAWNS.load_patterns(FIXTURES / "missing-detectors.py")
        self.assertEqual(patterns, (None, None))
        row = SPAWNS.spawn_briefs(stream("fanout-harness.jsonl"), patterns=patterns)[0]
        self.assertEqual((row["brief_bound"], row["brief_budget"]), (None, None))

    def test_a_hook_event_naming_its_call_is_matched_to_that_call(self):
        messages = stream("fanout-harness.jsonl")
        hooks = [m for m in messages if m.get("subtype") == "hook_response" and m["hook_event"] == "PreToolUse"]
        hooks[0]["tool_use_id"], hooks[1]["tool_use_id"] = "t2", "t1"
        messages = [m for m in messages if not (m.get("type") == "user" and m.get("parent_tool_use_id") == "t2")]
        rows = SPAWNS.spawn_briefs(messages)
        self.assertEqual((rows[0]["brief_source"], rows[0]["brief_bound"]), ("written", False))
        self.assertEqual((rows[1]["brief_source"], rows[1]["brief_bound"]), ("hook", True))


class WorkflowHookTests(unittest.TestCase):
    def test_each_launch_records_whether_its_agents_met_the_spawn_hooks(self):
        rows = SPAWNS.workflow_launch_hooks(stream("workflow-harness.jsonl"))
        self.assertEqual([r["passed"] for r in rows], [False, True, None])
        self.assertEqual((rows[1]["pre"], rows[1]["post"]), (1, 1))
        self.assertFalse(rows[2]["activity"])  # never finished, never ran anything visible

    def test_an_unfinished_launch_that_met_no_hook_is_unknown(self):
        messages = stream("workflow-harness.jsonl")[:5]  # w1 running, no result yet
        self.assertEqual([r["passed"] for r in SPAWNS.workflow_launch_hooks(messages)], [None])

    def test_a_stream_with_no_hook_events_cannot_say(self):
        messages = [m for m in stream("workflow-harness.jsonl") if m.get("subtype") != "hook_response"]
        self.assertEqual([r["passed"] for r in SPAWNS.workflow_launch_hooks(messages)], [None, None, None])

    def test_the_windows_own_spawn_calls_explain_their_hook_events(self):
        messages = stream("workflow-harness.jsonl")
        at = next(i for i, m in enumerate(messages) if m.get("hook_name") == "PreToolUse:Agent")
        messages.insert(at, {"type": "assistant", "parent_tool_use_id": None,
                             "message": {"id": "x", "content": [{"type": "tool_use", "id": "t9", "name": "Agent",
                                                                 "input": {"prompt": "p"}}]}})
        rows = SPAWNS.workflow_launch_hooks(messages)
        self.assertEqual((rows[1]["pre"], rows[1]["passed"]), (0, None))  # one call, one event each: unattributable

    def test_hook_events_naming_a_spawn_call_are_not_the_launchs(self):
        messages = stream("workflow-harness.jsonl")
        for m in messages:
            if m.get("hook_name") in ("PreToolUse:Agent", "PostToolUse:Agent"):
                m["tool_use_id"] = "toolu_workflow_agent"
        self.assertTrue(SPAWNS.workflow_launch_hooks(messages)[1]["passed"])


class EndedByTests(unittest.TestCase):
    def test_the_bound_that_ended_a_trial_is_named(self):
        self.assertEqual(SPAWNS.ended_by(stream("fanout-harness.jsonl")), "max-turns")
        self.assertEqual(SPAWNS.ended_by(stream("workflow-harness.jsonl")), "run-cap")
        self.assertEqual(SPAWNS.ended_by(stream("fanout-bare.jsonl")), "finished")
        self.assertEqual(SPAWNS.ended_by(stream("fanout-bare.jsonl"), stopped=True), "first-wave")
        self.assertEqual(SPAWNS.ended_by([], timed_out=True), "timeout")
        self.assertIsNone(SPAWNS.ended_by(stream("fanout-harness.jsonl")[:-1]))

    def test_required_skills_are_read_from_the_init_event(self):
        self.assertTrue(SPAWNS.skills_loaded(stream("fanout-harness.jsonl"), ["bmad-deep-recon"]))
        self.assertFalse(SPAWNS.skills_loaded(stream("fanout-harness.jsonl"), ["bmad-missing"]))
        self.assertIsNone(SPAWNS.skills_loaded(stream("fanout-bare.jsonl"), ["bmad-deep-recon"]))
        self.assertIsNone(SPAWNS.skills_loaded(stream("fanout-harness.jsonl"), []))

    def test_row_fields_carry_every_reading(self):
        fields = SPAWNS.row_fields(stream("fanout-harness.jsonl"),
                                   {"first_wave": True, "requires_skills": ["bmad-deep-recon"]})
        self.assertEqual(set(fields), set(SPAWNS.ROW_DEFAULTS))
        self.assertEqual((fields["first_wave_spawns"], fields["ended_by"], fields["required_skills_loaded"]),
                         (2, "max-turns", True))
        self.assertEqual(len(fields["spawn_briefs"]), 3)
        self.assertEqual(SPAWNS.row_fields([], {}), SPAWNS.ROW_DEFAULTS)


class FirstWaveTests(unittest.TestCase):
    def test_the_wave_is_out_once_each_spawn_of_the_first_spawning_turn_shows_its_model(self):
        watcher, at = SPAWNS.FirstWave(), None
        for index, line in enumerate(lines("fanout-harness.jsonl")):
            if watcher.feed_line(line):
                at = index
                break
        self.assertEqual(watcher.calls, ["t1", "t2"])
        messages = stream("fanout-harness.jsonl")
        self.assertEqual(messages[at]["parent_tool_use_id"], "t1")  # t1's first model report ends it
        self.assertNotIn("t3", json.dumps(messages[:at + 1]))

    def test_a_refused_spawn_counts_as_out(self):
        watcher = SPAWNS.FirstWave()
        refused = [{"type": "assistant", "parent_tool_use_id": None,
                    "message": {"id": "m", "content": [{"type": "tool_use", "id": "t", "name": "Agent", "input": {}}]}},
                   {"type": "user", "parent_tool_use_id": None,
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True}]}}]
        self.assertEqual([watcher.feed(m) for m in refused], [False, True])

    def test_a_workflow_launch_is_out_at_its_result_not_at_its_first_activity(self):
        watcher = SPAWNS.FirstWave()
        done = [watcher.feed_line(line) for line in lines("workflow-harness.jsonl")]
        self.assertEqual(done.index(True), 5)  # w1's result; its activity at 3 does not end it
        self.assertEqual(watcher.calls, ["w1"])

    def test_a_workflow_launch_is_out_once_a_spawn_hook_follows_it(self):
        messages = [m for m in stream("workflow-harness.jsonl")]
        second = messages[6:]  # the w2 launch, whose agent meets PreToolUse:Agent before its result
        watcher = SPAWNS.FirstWave()
        done = [watcher.feed(m) for m in second]
        self.assertEqual(second[done.index(True)].get("hook_name"), "PreToolUse:Agent")
        cut = second[:done.index(True) + 1]
        self.assertEqual([r["passed"] for r in SPAWNS.workflow_launch_hooks(cut)], [True])

    def test_a_run_that_never_spawns_is_never_stopped(self):
        watcher = SPAWNS.FirstWave()
        self.assertFalse(any(watcher.feed(m) for m in stream("fanout-harness.jsonl")[:2]))
        self.assertFalse(watcher.feed_line("not json"))


class FakePopen:
    """Stands in for `docker run`: prints a recorded stream, and stops when killed."""
    def __init__(self, source):
        self.source, self.killed = source, False

    def __call__(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        outer = self

        class Out:
            def __iter__(self):
                for line in outer.source:
                    if outer.killed:
                        return
                    yield line

        self.stdout, self.stderr, self.returncode = Out(), io.StringIO("warn\n"), None
        return self

    def kill(self):
        self.killed = True

    def wait(self):
        self.returncode = 137 if self.killed else 0
        return self.returncode


class FirstWaveLaunchTests(unittest.TestCase):
    def launch(self, name="fanout-harness.jsonl"):
        calls = []

        def base(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        popen = FakePopen(lines(name))
        return SPAWNS.first_wave_launch(base, popen), popen, calls

    def test_the_container_is_stopped_by_name_once_the_wave_is_out(self):
        launch, popen, calls = self.launch()
        done = launch(["docker", "run", "--rm", "--name", "trial-1", "image", "claude"], env={"PATH": "/bin"},
                      timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertTrue(done.first_wave_stopped)
        self.assertEqual(done.first_wave_spawns, 2)
        self.assertEqual(calls, [["docker", "kill", "trial-1"]])
        self.assertNotIn('"t3"', done.stdout)
        self.assertNotIn('"type":"result"', done.stdout)
        self.assertEqual((done.returncode, done.stderr), (137, "warn\n"))
        self.assertEqual(SPAWNS.ended_by(SPAWNS.parse_stream(done.stdout), stopped=done.first_wave_stopped),
                         "first-wave")

    def test_a_run_with_no_spawn_finishes_untouched(self):
        launch, popen, calls = self.launch()
        popen.source = [line for line in lines("fanout-harness.jsonl") if '"tool_use"' not in line]
        done = launch(["docker", "run", "--name", "trial-2", "image"], env={}, timeout=None)
        self.assertFalse(done.first_wave_stopped)
        self.assertEqual((calls, done.returncode), ([], 0))
        self.assertIn('"subtype":"error_max_turns"', done.stdout)

    def test_every_other_command_goes_to_the_base_launcher(self):
        launch, popen, calls = self.launch()
        launch(["docker", "rm", "--force", "trial-1"], env={}, stdout=subprocess.PIPE)
        self.assertEqual(calls, [["docker", "rm", "--force", "trial-1"]])
        self.assertFalse(hasattr(popen, "command"))


class CommandLineTests(unittest.TestCase):
    def test_one_row_per_spawn_and_per_launch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = SPAWNS.main([str(FIXTURES / "fanout-harness.jsonl"), str(FIXTURES / "workflow-harness.jsonl")])
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([r["kind"] for r in rows], ["spawn"] * 3 + ["workflow"] * 3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            SPAWNS.main(["--no-spawn-hooks", str(FIXTURES / "fanout-bare.jsonl")])
        self.assertEqual(json.loads(out.getvalue())["brief_source"], "written")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(SPAWNS.main([]), 2)


if __name__ == "__main__":
    unittest.main()
