"""The loaded surface and the pinned effort (#482): every row carries the CLI's own `init` counts,
a set stops when one arm's surface moves, the two arms may differ by the harness alone, and every
launch pins its reasoning effort. Every launch here is a fake replaying recorded CLI output; no
test builds an image or calls a model."""
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, GREEN, TASK, Launch, arm_record, gate_reply, options, result
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args
from test_replay_parity import stream

ARMS = BENCH.arms


def init(slash=75, effort=None, **over):
    event = {"type": "system", "subtype": "init", "session_id": "s", "model": "claude-test",
             "skills": ["a", "b"], "agents": ["worker"], "slash_commands": ["c"] * slash,
             "tools": ["Bash", "Read", "Edit"], "mcp_servers": [], "memory_paths": {"user": "/x", "project": "/y"}}
    if effort is not None:
        event["effort"] = effort
    event.update(over)
    return event


def first_call(inp=5, write=100, read=40):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"model": "claude-test", "usage": {"input_tokens": inp, "cache_creation_input_tokens": write,
                                                          "cache_read_input_tokens": read}}}


def run(slash=75, effort=None):
    return stream(init(slash, effort), first_call(), result())


def rehash(record):
    record["declaration_sha256"] = ARMS.digest(record["declaration"])
    record["manifest_sha256"] = ARMS.digest(record["manifest"])
    return record


class LoadedSurfaceTests(unittest.TestCase):
    def test_a_row_carries_all_six_init_counts(self):
        parsed = BENCH.parse_result(run())
        self.assertEqual({f: parsed[f] for f in BENCH.SURFACE_FIELDS},
                         {"init_skills": 2, "init_agents": 1, "init_slash_commands": 75, "init_tools": 3,
                          "init_mcp_servers": 0, "init_memory_paths": 2})
        self.assertEqual(parsed["init_surface_source"], "cli-init")
        self.assertTrue(all(parsed[field] for field in BENCH.SURFACE_HASH_FIELDS))

    def test_a_stream_without_an_init_event_stores_none_for_each(self):
        parsed = BENCH.parse_result(stream(first_call(), result()))
        self.assertEqual({f: parsed[f] for f in BENCH.SURFACE_FIELDS}, dict.fromkeys(BENCH.SURFACE_FIELDS))
        self.assertIsNone(parsed["observed_effort"])

    def test_a_key_the_init_event_lacks_is_none_not_zero(self):
        event = init()
        del event["agents"]
        parsed = BENCH.parse_result(stream(event, result()))
        self.assertIsNone(parsed["init_agents"])
        self.assertEqual(parsed["init_skills"], 2)

    def test_diagnostics_survive_without_a_priced_final_result(self):
        partial = stream(init(slash=81, effort="high"), first_call())
        parsed = BENCH.parse_diagnostics(partial)
        self.assertEqual((parsed["init_slash_commands"], parsed["observed_effort"]), (81, "high"))
        self.assertEqual(parsed["first_call_context"], 145)
        with self.assertRaises(ValueError):
            BENCH.parse_result(partial)

    def test_partial_and_timed_out_rows_keep_their_observed_surface(self):
        partial = stream(init(slash=81, effort="high"), first_call())
        timed_out = subprocess.TimeoutExpired(
            "claude", 1, output=stream(init(slash=82, effort="high"), first_call()))
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([partial, timed_out]))
        self.assertEqual([row["error"] for row in rows], [True, True])
        self.assertEqual([row["init_slash_commands"] for row in rows], [81, 82])
        self.assertEqual([row["observed_effort"] for row in rows], ["high", "high"])

    def test_a_timed_out_stream_with_surface_drift_stops_the_set(self):
        timed_out = subprocess.TimeoutExpired(
            "claude", 1, output=stream(init(slash=84, effort="high"), first_call()))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results.jsonl"
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], options(tmp), Launch([run(), run(), timed_out]), out=out)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["error_kind"], "timeout")
        self.assertIn("init_slash_commands: 75 -> 84", str(caught.exception))

    def test_first_call_context_is_the_first_calls_whole_input(self):
        """The warmth-invariant prefix: input + cache write + cache read of the first call."""
        parsed = BENCH.parse_result(run())
        self.assertEqual(parsed["first_call_context"], 145)
        self.assertEqual(parsed["first_call_cache_write"], 100)

    def test_backfill_derives_the_new_fields_for_rows_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "demo-bare-1.json").write_text(run(slash=80), encoding="utf-8")
            rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "bare", "rep": 1},
                                                 {"task": "demo", "arm": "harness", "rep": 1}], raw)
        self.assertEqual(missing, ["demo-harness-1.json"])
        self.assertEqual(rows[0]["init_slash_commands"], 80)
        self.assertEqual(rows[0]["init_surface_source"], "cli-init")
        self.assertTrue(rows[0]["init_slash_commands_sha256"])
        self.assertEqual(rows[0]["first_call_context"], 145)
        self.assertEqual({f: rows[1][f] for f in BENCH.SURFACE_FIELDS + ("first_call_context",)},
                         dict.fromkeys(BENCH.SURFACE_FIELDS + ("first_call_context",)))

    def test_missing_raw_preserves_existing_diagnostics(self):
        original = {"task": "demo", "arm": "bare", "rep": 1, "init_skills": 4,
                    "init_skills_sha256": "a" * 64, "init_surface_source": "cli-init",
                    "first_call_context": 123}
        with tempfile.TemporaryDirectory() as tmp:
            rows, missing = BENCH.backfill_rows([original], Path(tmp))
        self.assertEqual(missing, ["demo-bare-1.json"])
        for field in ("init_skills", "init_skills_sha256", "init_surface_source",
                      "first_call_context"):
            self.assertEqual(rows[0][field], original[field])


class PairParityTests(unittest.TestCase):
    def pair(self):
        return copy.deepcopy(arm_record("bare")), copy.deepcopy(arm_record("harness"))

    def refusal(self, bare, harness):
        try:
            ARMS.admit_pair(bare, harness)
        except SystemExit as stop:
            return str(stop)
        return None

    def test_a_pair_differing_by_the_harness_alone_is_admitted(self):
        bare, harness = self.pair()
        shared = {"path": "home:.bashrc", "kind": "file", "sha256": "1", "size": 3, "mode": "0644"}
        bare["manifest"]["entries"] = [shared]
        harness["manifest"]["entries"] += [shared, {"path": "home:.config", "kind": "dir", "mode": "0755"},
                                           {"path": "home:.config/agent-harness", "kind": "dir"},
                                           {"path": "home:.config/agent-harness/trusted.txt", "kind": "file"},
                                           {"path": "harness:bin/harness", "kind": "file"}]
        self.assertEqual(ARMS.pair_differences(bare, harness), [])
        self.assertIsNone(self.refusal(bare, harness))

    def test_a_file_outside_the_harness_only_one_arm_holds_is_refused(self):
        bare, harness = self.pair()
        harness["manifest"]["entries"].append({"path": "home:.npmrc", "kind": "file", "sha256": "2"})
        self.assertIn("only in the harness arm, outside the harness component: home:.npmrc",
                      self.refusal(bare, harness))

    def test_a_shared_file_that_differs_is_refused_with_its_fields(self):
        bare, harness = self.pair()
        bare["manifest"]["entries"] = [{"path": "home:.gitconfig", "kind": "file", "sha256": "1"}]
        harness["manifest"]["entries"].append({"path": "home:.gitconfig", "kind": "file", "sha256": "2"})
        self.assertIn("differs outside the harness component: home:.gitconfig (sha256)", self.refusal(bare, harness))

    def test_a_pair_refusal_prints_every_difference(self):
        bare, harness = self.pair()
        for number in range(45):
            harness["manifest"]["entries"].append(
                {"path": "home:.outside-%02d" % number, "kind": "file", "sha256": "2"}
            )

        refusal = self.refusal(bare, harness)

        for number in range(45):
            self.assertIn("home:.outside-%02d" % number, refusal)

    def test_a_different_base_cli_or_effort_is_refused(self):
        for key, value in (("base_image", "other@sha256:1"), ("claude_code_version", "2.0"), ("effort", "low")):
            bare, harness = self.pair()
            harness["declaration"][key] = value
            self.assertIn("declaration %s" % key, self.refusal(bare, harness), msg=key)

    def test_an_extra_component_beyond_the_harness_is_refused(self):
        bare, harness = self.pair()
        harness["declaration"]["components"].append({"name": "@openai/codex", "version": "1"})
        self.assertIn("declaration components", self.refusal(bare, harness))

    def test_unrelated_content_under_a_runtime_directory_is_compared(self):
        bare, harness = self.pair()
        harness["manifest"]["entries"].append(
            {"path": "home:.claude/unrelated.md", "kind": "file", "sha256": "2"})
        self.assertIn("home:.claude/unrelated.md", self.refusal(bare, harness))

    def test_only_links_into_the_harness_are_treatment(self):
        bare, harness = self.pair()
        harness["manifest"]["entries"] += [
            {"path": "home:.local", "kind": "dir"},
            {"path": "home:.local/bin", "kind": "dir"},
            {"path": "home:.local/bin/citizen", "kind": "link",
             "target": "/opt/model-citizen/bin/harness"},
            {"path": "home:.local/bin/unrelated", "kind": "link", "target": "/tmp/unrelated"},
        ]
        refusal = self.refusal(bare, harness)
        self.assertNotIn("home:.local/bin/citizen", refusal)
        self.assertIn("home:.local/bin/unrelated", refusal)

    def test_a_link_at_a_generated_file_path_is_not_mistaken_for_generated_content(self):
        bare, harness = self.pair()
        harness["manifest"]["entries"].append(
            {"path": "home:.claude/settings.json", "kind": "link", "target": "/tmp/settings"})
        self.assertIn("home:.claude/settings.json", self.refusal(bare, harness))

    def test_the_replay_refuses_a_drifted_pair_before_anything_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            opts["arms"]["harness"]["manifest"]["entries"].append({"path": "home:.profile", "kind": "file"})
            rehash(opts["arms"]["harness"])
            launch = Launch([])
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
        self.assertIn("home:.profile", str(caught.exception))
        self.assertEqual((launch.calls, launch.probes), ([], []))


class SurfaceDriftTests(unittest.TestCase):
    # reps=2 runs bare, harness, then harness, bare: the harness arm's second run is the third.
    OUTPUTS = [run(), run(), run(slash=84), run()]

    def test_a_run_whose_surface_moved_stops_the_set_with_the_difference(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results.jsonl"
            launch = Launch(list(self.OUTPUTS))
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], options(tmp), launch, out=out)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertIn("init_slash_commands: 75 -> 84", str(caught.exception))
        self.assertEqual(len(rows), 3)  # the drifting row is written, the fourth never launches
        self.assertEqual([r["surface_drift"] for r in rows[:2]], [[], []])
        self.assertIn("init_slash_commands: 75 -> 84", rows[2]["surface_drift"])
        self.assertTrue(any(line.startswith("init_slash_commands_sha256:")
                            for line in rows[2]["surface_drift"]))
        self.assertEqual(len(launch.outputs), 1)

    def test_allow_surface_drift_runs_on_and_stamps_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp)
            opts["stamp"] = dict(opts["stamp"], surface_drift_allowed=True)
            rows, stopped = BENCH.replay([TASK], opts, Launch(list(self.OUTPUTS)))
        self.assertFalse(stopped)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["surface_drift_allowed"] for r in rows))
        self.assertIn("init_slash_commands: 75 -> 84", rows[2]["surface_drift"])

    def test_same_count_replacement_is_detected_by_the_reported_content_hash(self):
        changed = init(slash_commands=["x"] * 74 + ["replacement"])
        outputs = [run(), run(), stream(changed, first_call(), result()), run()]
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp)
            opts["stamp"] = dict(opts["stamp"], surface_drift_allowed=True)
            rows, _ = BENCH.replay([TASK], opts, Launch(outputs))
        self.assertEqual(rows[2]["init_slash_commands"], 75)
        self.assertTrue(any(line.startswith("init_slash_commands_sha256:")
                            for line in rows[2]["surface_drift"]))

    def test_a_run_with_no_init_event_is_not_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [run(), stream(first_call(), result()), run(), run()]
            rows, stopped = BENCH.replay([TASK], options(tmp), Launch(outputs))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["surface_drift"], [])
        self.assertIsNone(rows[1]["init_skills"])

    def test_the_arms_are_compared_each_with_its_own_first_run(self):
        """Arms load different surfaces by design; only a move within one arm is drift."""
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [run(slash=10), run(slash=90), run(slash=90), run(slash=10)]
            rows, _ = BENCH.replay([TASK], options(tmp), Launch(outputs))
        self.assertEqual([r["surface_drift"] for r in rows], [[]] * 4)


class EffortTests(unittest.TestCase):
    def test_every_command_pins_the_effort_it_is_given(self):
        command = BENCH.arm_command("claude", "claude-test", "prompt", effort="medium")
        self.assertEqual(command[command.index("--effort") + 1], "medium")
        default = BENCH.arm_command("claude", "claude-test", "prompt")
        self.assertEqual(default[default.index("--effort") + 1], ARMS.DEFAULT_EFFORT)
        with self.assertRaises(SystemExit):
            BENCH.arm_command("claude", "claude-test", "prompt", effort="ultracode")

    def test_every_launch_and_row_carries_the_declared_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            for arm in BENCH.ARMS:
                opts["arms"][arm]["declaration"]["effort"] = "low"
                rehash(opts["arms"][arm])
            launch = Launch([run(), run()])
            rows, _ = BENCH.replay([TASK], opts, launch)
        for command, _ in launch.calls:
            self.assertEqual(command[command.index("--effort") + 1], "low")
        self.assertEqual([r["effort"] for r in rows], ["low", "low"])
        self.assertEqual([r["observed_effort"] for r in rows], [None, None])

    def test_the_preflight_pins_it_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            launch = Launch([run(), run()])
            BENCH.preflight([TASK], opts, launch)
        self.assertTrue(all(c[c.index("--effort") + 1] == "high" for c, _ in launch.calls))

    def test_the_declaration_records_it_and_the_image_does_not_change_with_it(self):
        with mock.patch.object(ARMS, "file_sha", lambda path: "f" * 64):
            inputs = {"base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.2.3"}
            high = ARMS.declaration("bare", inputs, effort="high")
            low = ARMS.declaration("bare", inputs, effort="low")
            self.assertEqual((high["effort"], low["effort"]), ("high", "low"))
            self.assertNotEqual(ARMS.digest(high), ARMS.digest(low))
            self.assertEqual(ARMS.image_name(high), ARMS.image_name(low))
            with self.assertRaises(SystemExit):
                ARMS.declaration("bare", inputs, effort="auto")

    def test_an_arm_with_no_pinned_effort_is_refused(self):
        record = dict(arm_record("bare"), protocol={"evidence": "exploratory"})
        record["declaration"] = dict(record["declaration"], effort=None)
        rehash(record)
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        self.assertIn("no reasoning effort pinned", str(caught.exception))

    def test_an_arm_given_the_overriding_variable_is_refused(self):
        record = dict(arm_record("bare"), protocol={"evidence": "exploratory"})
        with mock.patch.dict(ARMS.ARM_ENV, {ARMS.EFFORT_ENV: "max"}):
            with self.assertRaises(SystemExit) as caught:
                ARMS.admit(record)
        self.assertIn(ARMS.EFFORT_ENV, str(caught.exception))

    def test_an_arm_with_a_baked_effort_override_is_refused(self):
        record = copy.deepcopy(arm_record("bare"))
        record["protocol"] = {"evidence": "exploratory"}
        record["manifest"]["environment"][ARMS.EFFORT_ENV] = "max"
        rehash(record)
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        self.assertIn("image bakes", str(caught.exception))

    def test_preflight_refuses_an_observed_effort_mismatch(self):
        mismatched = json.loads(gate_reply(GREEN))
        mismatched.insert(0, init(effort="max"))
        matched = json.loads(gate_reply(GREEN))
        matched.insert(0, init(effort="high"))
        with tempfile.TemporaryDirectory() as tmp:
            checks, _ = BENCH.preflight([TASK], options(tmp),
                                        Launch([json.dumps(mismatched), json.dumps(matched)]))
        self.assertEqual([check["passed"] for check in checks], [False, True])
        self.assertEqual(checks[0]["observed_effort"], "max")
        self.assertIn("observed effort max, pinned high", checks[0]["reply"])

    def test_a_run_that_reports_another_effort_errors_and_stops_the_set(self):
        """Even with --allow-surface-drift: a different effort is a different arm."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "results.jsonl"
            opts = options(tmp, reps=1)
            opts["stamp"] = dict(opts["stamp"], surface_drift_allowed=True)
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, Launch([run(effort="max"), run()]), out=out)
            row = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("effort max, pinned high", str(caught.exception))
        self.assertTrue(row["error"])
        self.assertEqual(row["error_kind"], "effort: observed max, pinned high")

    def test_a_run_that_reports_the_pinned_effort_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([run(effort="high"), run(effort="high")]))
        self.assertEqual([(r["observed_effort"], r["error"]) for r in rows], [("high", False), ("high", False)])


class ReplayCliTests(unittest.TestCase):
    def run_replay(self, tmp, args):
        """`cmd_replay` with the builds, the egress and the set faked; (exit, stdout, fakes, stamps)."""
        fake, out, stamps = FakeArms(), io.StringIO(), []

        def replay(tasks, opts, launch=None, out=None):
            stamps.append(opts["stamp"])
            return fake_replay(tasks, opts, launch, out)

        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", replay), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = BENCH.cmd_replay(args)
        return code, out.getvalue(), fake, stamps

    def test_effort_defaults_to_high_and_only_a_level_is_accepted(self):
        with mock.patch.object(BENCH, "cmd_replay", lambda args: (args.effort, args.allow_surface_drift)):
            self.assertEqual(BENCH.main(["replay", "--exploratory"]), ("high", False))
            self.assertEqual(BENCH.main(["replay", "--exploratory", "--effort", "max", "--allow-surface-drift"]),
                             ("max", True))
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                BENCH.main(["replay", "--exploratory", "--effort", "ultracode"])

    def test_every_arm_is_declared_at_the_chosen_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, out, fake, _ = self.run_replay(tmp, replay_args(tmp, effort="medium"))
        self.assertEqual(code, 0)
        self.assertEqual([d["effort"] for d in fake.built], ["medium", "medium"])
        self.assertIn("at effort medium", out)

    def test_the_stamp_says_whether_drift_was_allowed(self):
        for allowed in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                harness_repo(Path(tmp) / "repo")
                _, _, _, stamps = self.run_replay(tmp, replay_args(tmp, allow_surface_drift=allowed))
            self.assertEqual([s["surface_drift_allowed"] for s in stamps], [allowed])


if __name__ == "__main__":
    unittest.main()
