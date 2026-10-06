# SPDX-License-Identifier: MIT
"""The figures the hook decision log carries measure what happened, not what was composed.

The read-only allow is tallied only when it is the answer returned, the filter counts the bytes
it read rather than their decoded text, the session start counts lines rather than entries, and
the staging hook counts only the bytes it copied.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isolation import without_config_dir

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "policy" / "hooks"
sys.path.insert(0, str(REPO / "lib"))

from harness_core import lifecycle  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


filter_output = _load("filter_output_counts", HOOKS / "filter-output.py")

DENY = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}}


class ReturnedAnswerTallyTests(unittest.TestCase):
    """`allow-readonly-bash` counts the answer the dispatcher returns."""

    def dispatch(self, tool, tool_input, answers, plan=False):
        counted = []

        def invoke(name, event):
            answer = answers.get(name, {})
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(lifecycle, "enabled", lambda name: name == "allow-readonly-bash"), \
                patch.object(lifecycle, "selected", lambda name, fallback: fallback), \
                patch.object(lifecycle, "invoke", invoke), \
                patch.object(lifecycle, "investigating", lambda runtime, event: plan), \
                patch.object(lifecycle, "plan_allowed_tool", lambda tool: plan), \
                patch.object(lifecycle, "tally_readonly",
                             lambda event, kind: counted.append(kind)):
            event = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "s-1",
                     "permission_mode": "plan" if plan else "default", "cwd": str(REPO),
                     "tool_input": tool_input}
            try:
                answer = lifecycle._dispatch("claude-code", event)
            except RuntimeError:
                answer = None
        return answer, counted

    def test_a_read_only_allow_returned_is_counted(self):
        answer, counted = self.dispatch("Bash", {"command": "ls"}, {})
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(counted, ["read-only"])

    def test_a_read_only_allow_a_later_deny_overrides_is_not_counted(self):
        answer, counted = self.dispatch("Bash", {"command": "ls"}, {"steer-polling": DENY})
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(counted, [])

    def test_a_read_only_allow_the_output_filter_fails_after_is_not_counted(self):
        answer, counted = self.dispatch("Bash", {"command": "ls"},
                                        {"filter-output": RuntimeError("filter broke")})
        self.assertIsNone(answer)
        self.assertEqual(counted, [])

    def test_a_plan_tool_allow_a_file_tool_refusal_overrides_is_not_counted(self):
        inputs = {"file_path": str(REPO / "x.txt"), "old_string": "a", "new_string": "b"}
        answer, counted = self.dispatch("Edit", inputs, {"intent-overlap": DENY}, plan=True)
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(counted, [])
        answer, counted = self.dispatch("Edit", inputs, {}, plan=True)
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(counted, ["plan-tool"])


class LoggedFigureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def run_hook(self, argv, raw, cwd=None):
        env = without_config_dir()
        for name in list(env):
            if name.startswith("HARNESS_STANCE_"):
                env.pop(name)
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        out = subprocess.run(argv, input=raw, capture_output=True, env=env,
                             cwd=str(cwd or self.home), timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def rows(self, point):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line]
        return [r for r in rows if r.get("point") == point and r.get("kind") == "decision"]

    def test_the_filter_counts_the_raw_bytes_it_read(self):
        raw = b"ok \xff\xfe done\n" * 3
        self.run_hook([sys.executable, str(HOOKS / "filter-lines.py"), "--session", "s-1"], raw)
        (row,) = self.rows("filter-output")
        self.assertEqual(row["bytes_in"], len(raw))
        self.assertEqual(row["lines_in"], 3)

    def test_the_session_start_counts_lines_not_entries(self):
        repo = self.home / "repo"
        (repo / ".agent-harness").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / ".agent-harness" / "progress.md").write_text("one\ntwo\nthree\n")
        payload = {"hook_event_name": "SessionStart", "session_id": "s-2", "cwd": str(repo)}
        self.run_hook([sys.executable, str(HOOKS / "harness-session.py")],
                      json.dumps(payload).encode(), cwd=repo)
        (row,) = self.rows("harness-session")
        # The framing line, the three handoff lines, the closing line; no commits to list.
        self.assertEqual(row["sections"]["handoff"], 5)

    def test_a_reused_copy_stages_no_bytes(self):
        work, outside = self.home / "work", self.home / "elsewhere"
        work.mkdir()
        outside.mkdir()
        (outside / "report.txt").write_text("hello")
        payload = json.dumps({"tool_name": "SendUserFile", "cwd": str(work), "session_id": "s-3",
                              "tool_input": {"files": [str(outside / "report.txt")]}}).encode()
        for _ in range(2):
            self.run_hook([sys.executable, str(HOOKS / "stage-user-files.py")], payload)
        self.assertEqual([(r["staged"], r["bytes_staged"]) for r in self.rows("stage-user-files")],
                         [(1, 5), (1, 0)])


if __name__ == "__main__":
    unittest.main()
