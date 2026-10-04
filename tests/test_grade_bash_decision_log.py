# SPDX-License-Identifier: MIT
"""Every Bash decision the harness makes is one decision-log row, and grading has a deadline.

A refusal the log does not hold is one no later label can grade, so each answer writes a row:
an ask, a refusal, a refusal that offers an approval code, a command let through on a consumed
approval, a refusal an error in the dispatcher turns into, and a command graded past the
grader's deadline. Past the deadline a destructive verb is refused and anything else stays open.

Each case runs the dispatcher's own entry point in a subprocess with a temporary HOME, as both
adapters run it, with a fault or a slow grader put in through `lifecycle.load`.

Run: python3 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_grade_bash import grader

REPO = Path(__file__).resolve().parent.parent
FORCE = "git push --force origin main"

SCRIPT = r"""
import json, sys, time
sys.path.insert(0, %(lib)r)
from harness_core import lifecycle
fault = sys.argv[1]
real_load, real_invoke = lifecycle.load, lifecycle.invoke

def load(name):
    module = real_load(name)
    if fault == "slow" and name in ("grade-bash", "bash-grader"):
        def slow(cmd, cwd="", depth=0):
            time.sleep(30)
            return 0, None, None, None
        module.grade_text = slow
        (getattr(module, "library", None) or module).GRADE_SECONDS = 0.2
    return module

def invoke(name, event):
    if fault == "filter" and name == "filter-output":
        raise RuntimeError("filter failed")
    return real_invoke(name, event)

def investigating(runtime, event):
    raise RuntimeError("dispatcher failed")

lifecycle.load, lifecycle.invoke = load, invoke
if fault == "dispatch":
    lifecycle.investigating = investigating
lifecycle.main("claude-code", [])
""" % {"lib": str(REPO / "lib")}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def run_hook(self, command, mode="default", fault="none", session="s-1"):
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_CONFIG_DIR" and not k.startswith("HARNESS_STANCE_")}
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        payload = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": str(self.home),
                   "tool_name": "Bash", "tool_input": {"command": command},
                   "permission_mode": mode}
        started = time.monotonic()
        out = subprocess.run([sys.executable, "-c", SCRIPT, fault], input=json.dumps(payload),
                             env=env, capture_output=True, text=True, timeout=60)
        self.elapsed = time.monotonic() - started
        self.assertEqual(out.returncode, 0, out.stderr)
        block = (json.loads(out.stdout) if out.stdout.strip() else {}).get("hookSpecificOutput", {})
        return block.get("permissionDecision"), block.get("permissionDecisionReason", "")

    def rows(self):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if r.get("kind") == "decision" and not r.get("sampled")]


class EveryDecisionIsLogged(Base):
    def test_a_refusal_writes_one_row(self):
        self.assertEqual(self.run_hook(FORCE, mode="bypassPermissions")[0], "deny")
        rows = self.rows()
        self.assertEqual([(r["deterministic_answer"], r["input"]) for r in rows], [("deny", FORCE)])

    def test_a_refusal_that_offers_an_approval_code_says_so(self):
        decision, reason = self.run_hook(FORCE, mode="auto")
        self.assertEqual(decision, "deny")
        self.assertIn("approve ", reason)
        self.assertEqual([(r["deterministic_answer"], r.get("approval")) for r in self.rows()],
                         [("deny", "offered")])

    def test_an_ask_writes_one_row(self):
        self.assertEqual(self.run_hook(FORCE)[0], "ask")
        self.assertEqual([r["deterministic_answer"] for r in self.rows()], ["ask"])

    def test_a_refusal_is_logged_when_the_output_filter_fails_after_it(self):
        decision, reason = self.run_hook(FORCE, mode="bypassPermissions", fault="filter")
        self.assertEqual(decision, "deny")
        self.assertEqual([r["deterministic_answer"] for r in self.rows()], ["deny"])

    def test_the_refusal_an_error_turns_into_is_logged_once(self):
        decision, reason = self.run_hook("make build", fault="dispatch")
        self.assertEqual(decision, "deny")
        self.assertIn("unverified", reason)
        self.assertEqual([(r["deterministic_answer"], r.get("error")) for r in self.rows()],
                         [("deny", "RuntimeError")])

    def test_a_consumed_approval_writes_an_allow_row(self):
        library = grader.library
        code = None
        os_env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        with mock.patch.dict(os.environ, os_env):
            code = library.approvals.code_for("s-1", FORCE)
            library.approvals.record("s-1", "approve " + code)
        decision, _reason = self.run_hook(FORCE, mode="auto")
        self.assertIsNone(decision)
        self.assertEqual([(r["deterministic_answer"], r.get("approval")) for r in self.rows()],
                         [("allow", "consumed")])


class Deadline(Base):
    def test_a_destructive_verb_past_the_deadline_is_refused_and_logged(self):
        decision, reason = self.run_hook(FORCE, mode="bypassPermissions", fault="slow")
        self.assertEqual(decision, "deny")
        self.assertIn(grader.DEADLINE_NOTE, reason)
        self.assertLess(self.elapsed, 20)
        self.assertEqual([(r["deterministic_answer"], r.get("timed_out")) for r in self.rows()],
                         [("deny", True)])

    def test_a_read_only_command_past_the_deadline_stays_open_and_is_logged(self):
        decision, _reason = self.run_hook("ls -la", mode="bypassPermissions", fault="slow")
        self.assertNotIn(decision, ("deny", "ask"))
        self.assertEqual([(r["deterministic_answer"], r.get("timed_out")) for r in self.rows()],
                         [("allow", True)])


class GradeWithin(unittest.TestCase):
    """`grade_within` itself, in this process."""

    def slow(self, cmd, cwd="", depth=0):
        time.sleep(30)
        return 0, None, None, None

    def test_past_the_deadline_a_destructive_verb_fails_closed(self):
        with mock.patch.object(grader.library, "grade_text", self.slow):
            (grade, verb, target, _family), timed = grader.grade_within(FORCE, "", 0.1)
        self.assertTrue(timed)
        self.assertEqual((grade, verb, target), (3, "git push --force", grader.DEADLINE_NOTE))

    def test_past_the_deadline_a_read_only_command_stays_open(self):
        with mock.patch.object(grader.library, "grade_text", self.slow):
            (grade, verb, _target, _family), timed = grader.grade_within("ls -la", "", 0.1)
        self.assertTrue(timed)
        self.assertEqual((grade, verb), (1, grader.DEADLINE_VERB))

    def test_within_the_deadline_the_grade_is_the_graders(self):
        self.assertEqual(grader.grade_within(FORCE, "/work/repo"),
                         (grader.grade_text(FORCE, "/work/repo"), False))


if __name__ == "__main__":
    unittest.main()
