# SPDX-License-Identifier: MIT
"""Every hook logs each decision with the fields its evaluation needs.

Each case runs the hook as the runtime does, in a subprocess with a temporary HOME, and reads the
decision log it wrote: the filter's bytes in and out, every spawn reroute, the read-only allow's
per-session count summary, and one row per decision from the hooks that logged nothing before.
The fields each row carries are named in docs/runtime-controls.md.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import without_config_dir

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "policy" / "hooks"
DISPATCH = r"""
import sys
sys.path.insert(0, %(lib)r)
from harness_core import lifecycle
lifecycle.main("claude-code", [])
""" % {"lib": str(REPO / "lib")}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


filter_output = _load("filter_output_logging", HOOKS / "filter-output.py")
decisions = _load("decisions_logging", HOOKS / "decisions.py")


def assistant(model):
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "model": model}})


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def env(self):
        env = without_config_dir()
        for name in list(env):
            if name.startswith("HARNESS_STANCE_"):
                env.pop(name)
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        return env

    def call(self, argv, payload=None, cwd=None):
        raw = json.dumps(payload) if payload is not None else ""
        out = subprocess.run(argv, input=raw, capture_output=True, text=True, env=self.env(),
                             cwd=str(cwd or self.home), timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def hook(self, name, payload):
        return self.call([sys.executable, str(HOOKS / (name + ".py"))], payload)

    def rows(self, point):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line]
        return [r for r in rows if r.get("point") == point and r.get("kind") == "decision"]

    def only(self, point):
        rows = self.rows(point)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["module"], "hooks/" + point)
        return rows[0]


class FilterOutputTests(Base):
    def test_a_filtered_run_logs_its_bytes_and_lines_in_and_out(self):
        text = "".join("line %d passed\n" % n if n % 50 == 0 else "noise %d\n" % n
                       for n in range(300))
        source = self.home / "run.txt"
        source.write_text(text)
        command = filter_output.rewrite("cat " + str(source), "s-1")
        self.assertIn("--session s-1", command)
        out = self.call(["bash", "-c", command])
        row = self.only("filter-output")
        self.assertEqual(row["session_id"], "s-1")
        self.assertEqual(row["deterministic_answer"], "filtered")
        self.assertEqual(row["bytes_in"], len(text.encode()))
        self.assertEqual(row["bytes_out"], len(out.stdout.encode()))
        self.assertEqual(row["lines_in"], 300)
        self.assertLess(row["lines_out"], row["lines_in"])
        self.assertEqual(row["runner"], "unknown")

    def test_the_rewrite_names_the_runner_it_matched(self):
        command = filter_output.rewrite("cd sub && python3 -m pytest -q", "s-1")
        self.assertTrue(command.endswith("--session s-1 --runner 'python3 -m pytest'"), command)

    def test_the_hook_passes_the_session_and_a_hand_run_filter_logs_nothing(self):
        out = self.hook("filter-output", {"tool_name": "Bash", "session_id": "abc-1",
                                          "tool_input": {"command": "pytest -q"}})
        command = json.loads(out.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("--session abc-1 --runner pytest", command)
        self.call([sys.executable, str(HOOKS / "filter-lines.py")], None)
        self.assertEqual(self.rows("filter-output"), [])


class RerouteTests(Base):
    def spawn(self, transcript, **tool_input):
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"stances": {"delegation": "tiered"}}))
        return {"tool_name": "Agent", "session_id": "s-2", "transcript_path": str(transcript),
                "tool_input": dict(tool_input, prompt="x")}

    def test_every_reroute_is_a_row_with_the_model_before_and_after(self):
        transcript = self.home / "t.jsonl"
        transcript.write_text(assistant("claude-opus-5") + "\n")
        self.hook("tier-agent-spawns", self.spawn(transcript))
        self.hook("tier-agent-spawns", self.spawn(transcript, model="fable",
                                                  subagent_type="reviewer"))
        rows = self.rows("tier-agent-spawns")
        self.assertEqual([r["deterministic_answer"] for r in rows], ["one-rung", "demoted"])
        self.assertEqual([r["reroute"] for r in rows], ["bare", "top-class"])
        self.assertEqual([(r["model_requested"], r["model"]) for r in rows],
                         [(None, "sonnet"), ("fable", "opus")])
        self.assertEqual(rows[1]["subagent_type_requested"], "reviewer")

    def test_a_named_role_left_alone_writes_nothing(self):
        transcript = self.home / "t.jsonl"
        transcript.write_text(assistant("claude-opus-5") + "\n")
        self.hook("tier-agent-spawns", self.spawn(transcript, subagent_type="reviewer"))
        self.assertEqual(self.rows("tier-agent-spawns"), [])


class ReadOnlySummaryTests(Base):
    def dispatch(self, payload):
        return self.call([sys.executable, "-c", DISPATCH], dict(payload, session_id="s-3",
                                                               cwd=str(self.home)))

    def test_read_only_allows_are_one_count_summary_per_session(self):
        for command in ("ls", "git status", "ls -la"):
            self.dispatch({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "permission_mode": "default", "tool_input": {"command": command}})
        self.assertEqual(self.rows("allow-readonly-bash"), [])
        self.dispatch({"hook_event_name": "SessionEnd", "reason": "exit"})
        row = self.only("allow-readonly-bash")
        self.assertEqual(row["deterministic_answer"], "summary")
        self.assertEqual(row["counts"], {"read-only": 3})
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["session_id"], "s-3")
        self.assertEqual(list((self.home / ".local" / "state" / "agent-harness" / "tallies"
                               / "allow-readonly-bash").glob("*")), [])

    def test_an_orphaned_tally_is_flushed_as_stale_a_day_later(self):
        base = self.home / "state"
        self.assertTrue(decisions.tally("allow-readonly-bash", "old-1", "read-only", base=base))
        orphan = base / "tallies" / "allow-readonly-bash" / "old-1.tally"
        os.utime(str(orphan), (1, 1))
        target = self.home / "log.jsonl"
        self.assertIsNone(decisions.flush_tally("allow-readonly-bash", "new-1", base=base,
                                                target=target))
        rows = [json.loads(line) for line in target.read_text().splitlines()]
        self.assertEqual([(r["session_id"], r["counts"], r["stale"]) for r in rows],
                         [("old-1", {"read-only": 1}, True)])
        self.assertFalse(orphan.exists())

    def test_a_session_id_unfit_for_a_file_name_counts_nothing(self):
        base = self.home / "state"
        for session in ("../escape", "", None, ".hidden"):
            self.assertFalse(decisions.tally("allow-readonly-bash", session, "read-only", base=base))


class AdvisoryHookTests(Base):
    def test_allow_plan_webfetch_logs_the_url_without_its_query(self):
        self.hook("allow-plan-webfetch", {"tool_name": "WebFetch", "permission_mode": "plan",
                                          "session_id": "s-4", "tool_input": {
                                              # Assembled, so the lint sees no address.
                                              "url": "https://u:p" + "@" + "docs.example.com/a/b?token=zzz"}})
        row = self.only("allow-plan-webfetch")
        self.assertEqual(row["input"], "https://docs.example.com/a/b")
        self.assertEqual(row["host"], "docs.example.com")
        self.assertEqual(row["deterministic_answer"], "allow")

    def test_allow_plan_webfetch_outside_plan_mode_logs_nothing(self):
        self.hook("allow-plan-webfetch", {"tool_name": "WebFetch", "permission_mode": "default",
                                          "tool_input": {"url": "https://example.com"}})
        self.assertEqual(self.rows("allow-plan-webfetch"), [])

    def test_stage_user_files_logs_counts_and_names(self):
        work = self.home / "work"
        work.mkdir()
        outside = self.home / "elsewhere"
        outside.mkdir()
        (outside / "report.txt").write_text("hello")
        self.hook("stage-user-files", {"tool_name": "SendUserFile", "cwd": str(work),
                                       "session_id": "s-5",
                                       "tool_input": {"files": [str(outside / "report.txt")]}})
        row = self.only("stage-user-files")
        self.assertEqual(row["deterministic_answer"], "staged")
        self.assertEqual((row["staged"], row["kept"], row["files"], row["bytes_staged"]), (1, 0, 1, 5))
        self.assertEqual(row["input"], "report.txt")

    def test_validate_plan_card_logs_pass_and_fail(self):
        plans = self.home / ".agent-harness" / "plans"
        plans.mkdir(parents=True)
        bad, good = plans / "bad.md", plans / "good.md"
        bad.write_text("# Plan\n\n## Context\n")
        good.write_text("# Plan\n\n## At a glance\n\n## Steps\n\n## Decisions for the reviewer\n\n---\n")
        for path in (bad, good):
            self.hook("validate-plan-card", {"tool_name": "Write", "session_id": "s-6",
                                             "tool_input": {"file_path": str(path)}})
        rows = self.rows("validate-plan-card")
        self.assertEqual([(r["input"], r["deterministic_answer"]) for r in rows],
                         [("bad.md", "fail"), ("good.md", "pass")])
        self.assertGreater(rows[0]["problems"], 0)
        self.assertEqual(rows[1]["problems"], 0)
        self.assertEqual(rows[1]["separator"], True)

    def test_validate_plan_card_ignores_other_files(self):
        other = self.home / "notes.md"
        other.write_text("# x\n")
        self.hook("validate-plan-card", {"tool_name": "Write", "tool_input": {"file_path": str(other)}})
        self.assertEqual(self.rows("validate-plan-card"), [])

    def test_harness_session_logs_a_silent_start(self):
        self.hook("harness-session", {"hook_event_name": "SessionStart", "session_id": "s-7",
                                      "cwd": str(self.home)})
        row = self.only("harness-session")
        self.assertEqual(row["deterministic_answer"], "silent")
        self.assertEqual(row["context_chars"], 0)
        self.assertIn("overrides", row["sections"])

    def test_workspace_session_logs_a_silent_start(self):
        self.hook("workspace-session", {"hook_event_name": "SessionStart", "session_id": "s-8",
                                        "cwd": str(self.home)})
        row = self.only("workspace-session")
        self.assertEqual(row["deterministic_answer"], "silent")
        self.assertEqual(row["context_chars"], 0)


class PointTests(unittest.TestCase):
    def test_every_new_point_is_declared_and_owned(self):
        for point in ("filter-output", "allow-readonly-bash", "validate-plan-card",
                      "harness-session", "workspace-session", "allow-plan-webfetch",
                      "stage-user-files", "neutralize-tool-output"):
            self.assertIn(point, decisions.HOOK_POINTS)
            self.assertNotIn(point, decisions.POINTS)
            self.assertEqual(decisions.module_of(point), "hooks/" + point)

    def test_every_hook_point_has_a_writer(self):
        sources = [path.read_text(encoding="utf-8") for path in sorted(HOOKS.glob("*.py"))
                   if path.name != "decisions.py"]
        sources.append((REPO / "lib" / "harness_core" / "lifecycle.py").read_text(encoding="utf-8"))
        for point in decisions.HOOK_POINTS:
            self.assertTrue(any('"' + point + '"' in text for text in sources), point)


if __name__ == "__main__":
    unittest.main()
