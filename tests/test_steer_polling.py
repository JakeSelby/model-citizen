# SPDX-License-Identifier: MIT
"""The steer-polling PreToolUse hook: foreground waits get a note, a long sleep is denied.

Each shape the hook names is matched, every near miss is not, a background command is never
judged, and every judgment is a decision-log row. The coordinator tests run `lifecycle.dispatch`
in a subprocess, as both adapters do, so the note and the denial are checked as Claude Code
receives them beside the rest of the Bash path.

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

import isolation

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "steer-polling.py"
spec = importlib.util.spec_from_file_location("steer_polling", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

# Foreground waits, each with the answer the hook gives and a word its finding names.
NOTED = {
    "sleep 31": "sleep 31",
    "sleep 2m": "sleep 2m",
    "sleep 60 >/dev/null 2>&1": "sleep 60",
    "cd /repo && sleep 45 && gh pr checks 3": "sleep 45",
    "until gh pr checks 12; do sleep 10; done": "until",
    "while ! test -f done.txt; do sleep 2; done": "while",
    "until curl -sf localhost:8000; do sleep $DELAY; done": "until",
    "for i in $(seq 30); do test -f x && break; sleep 5; done": "for",
    "gh pr checks 12 --watch": "gh pr checks --watch",
    "gh pr checks --watch=true": "gh pr checks --watch",
    "gh run watch 991": "gh run watch",
    "docker wait builder": "docker wait",
    "sleep 60 & wait $!": "wait",
    "gh run watch 991 & wait": "gh run watch",
    "while true; do sleep 5 & wait; done": "while",
    "for x in a; do echo \"$x\"; done; sleep 60": "sleep 60",
    "sleep 600 | cat & sleep 60": "sleep 60",
}
DENIED = ("sleep 301", "sleep 600", "sleep 6m", "sleep 1h", "sleep infinity", "X=1 sleep 400",
          "sleep 200; sleep 200", "while true; do sleep 400; done", "sleep 600 & wait $!",
          "sleep 600 & wait", "(sleep 400; echo) & wait", "for x in a; do echo \"$x\"; done; sleep 600",
          "while x; do for y in z; do echo; done; sleep 1; done; sleep 600")
# Near misses: none of these waits in the foreground long enough, or at all.
SILENT = ("sleep 5", "sleep 30", "sleep 0.5", "sleep $N", "ls -la", "gh pr checks 12",
          "gh run view 991", "docker run --rm img", "timeout 600 gh pr checks 12 --watch",
          "gtimeout 120 docker wait builder", "echo 'sleep 600'", 'git commit -m "sleep 600"',
          "echo hi # sleep 600", "sleep 600 &", "sleep 600 & sleep 5 & wait $!",
          "sleep 600 | cat &", "sleep 600 && echo done &", "{ sleep 600; } &",
          "while true; do sleep 5; done &", "until test -f x; do sleep 600; done | cat &",
          "while read line; do echo $line; done < f", "for f in *.py; do wc -l $f; done",
          "echo 'unbalanced")


def run(tool_input, home):
    env = isolation.without_harness_vars(dict(os.environ, HOME=str(home)))
    out = subprocess.run([sys.executable, str(HOOK)],
                         input=json.dumps({"tool_name": "Bash", "session_id": "s",
                                           "tool_input": tool_input}),
                         env=env, capture_output=True, text=True, timeout=60)
    return out.returncode, (json.loads(out.stdout) if out.stdout.strip() else None)


def rows(home):
    log = Path(home) / ".local" / "state" / "agent-harness" / "decisions.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


class Patterns(unittest.TestCase):
    def test_each_foreground_wait_is_noted_and_names_itself(self):
        for command, named in NOTED.items():
            with self.subTest(command=command):
                answer, findings = hook.judge(command)
                self.assertEqual(answer, "note")
                self.assertIn(named, " ".join(findings))

    def test_a_foreground_sleep_past_five_minutes_is_denied(self):
        for command in DENIED:
            with self.subTest(command=command):
                self.assertEqual(hook.judge(command)[0], "deny")

    def test_near_misses_are_not_judged(self):
        for command in SILENT:
            with self.subTest(command=command):
                self.assertEqual(hook.judge(command), (None, []))

    def test_a_loop_is_reported_once_however_often_it_sleeps(self):
        _, findings = hook.judge("while true; do sleep 5; curl x; sleep 5; done")
        self.assertEqual(findings, ["a `while` loop that sleeps"])

    def test_a_trailing_ampersand_backgrounds_the_whole_job(self):
        parts = hook.segments("sleep 1; sleep 600 | cat && echo & while x; do sleep 5; done & ls")
        self.assertEqual([job for _, job in parts], [None, 1, 1, 1, 2, 2, 2, None])
        parts = hook.segments("while x; do sleep 600 & done")
        self.assertEqual([job for _, job in parts], [None, 1, None])

    def test_a_loop_is_reported_again_once_a_later_loop_sleeps(self):
        _, findings = hook.judge("until a; do sleep 5; done; while b; do sleep 5; done")
        self.assertEqual(findings, ["a `until` loop that sleeps", "a `while` loop that sleeps"])

    def test_the_note_names_both_alternatives_and_decides_nothing(self):
        result, logged = hook.answer_for({"tool_input": {"command": "sleep 60"}})
        self.assertEqual(logged, "note")
        fields = result["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", fields)
        self.assertIn("run_in_background", fields["additionalContext"])
        self.assertIn("Monitor", fields["additionalContext"])

    def test_the_denial_names_both_alternatives(self):
        result, logged = hook.answer_for({"tool_input": {"command": "sleep 900"}})
        self.assertEqual(logged, "deny")
        fields = result["hookSpecificOutput"]
        self.assertEqual(fields["permissionDecision"], "deny")
        self.assertIn("run_in_background", fields["permissionDecisionReason"])
        self.assertIn("Monitor", fields["permissionDecisionReason"])

    def test_a_background_command_is_never_judged(self):
        for command in list(NOTED) + list(DENIED):
            with self.subTest(command=command):
                self.assertEqual(hook.answer_for({"tool_input": {"command": command,
                                                                 "run_in_background": True}}),
                                 (None, "background"))


class Logging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_each_decision_is_one_row_and_a_command_with_no_wait_writes_none(self):
        cases = [({"command": "ls"}, None, None),
                 ({"command": "sleep 60"}, "note", "note"),
                 ({"command": "sleep 900"}, "deny", "deny"),
                 ({"command": "sleep 900", "run_in_background": True}, None, "background")]
        for tool_input, shown, logged in cases:
            with self.subTest(tool_input=tool_input):
                before = len(rows(self.home))
                code, out = run(tool_input, self.home)
                self.assertEqual(code, 0)
                if shown is None:
                    self.assertIsNone(out)
                else:
                    self.assertEqual("deny" if "permissionDecision" in out["hookSpecificOutput"]
                                     else "note", shown)
                written = rows(self.home)[before:]
                if logged is None:
                    self.assertEqual(written, [])
                    continue
                self.assertEqual(len(written), 1)
                self.assertEqual(written[0]["point"], "steer-polling")
                self.assertEqual(written[0]["module"], "hooks/steer-polling")
                self.assertEqual(written[0]["deterministic_answer"], logged)
                self.assertEqual(written[0]["input"], tool_input["command"])

    def test_a_malformed_event_is_silent_and_exits_zero(self):
        for text in ("", "not json", "[]", json.dumps({"tool_name": "Bash", "tool_input": {}})):
            with self.subTest(text=text):
                out = subprocess.run([sys.executable, str(HOOK)], input=text, capture_output=True,
                                     text=True, timeout=60,
                                     env=isolation.without_harness_vars(dict(os.environ, HOME=str(self.home))))
                self.assertEqual((out.returncode, out.stdout), (0, ""))


class Coordinator(unittest.TestCase):
    """The hook as the lifecycle coordinator runs it, beside grading and the read-only allow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def dispatch(self, runtime, tool_input, hooks=None):
        if hooks is not None:
            config = self.home / ".config" / "agent-harness" / "config.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"hooks": hooks}))
        script = ("import json, sys\nsys.path.insert(0, %r)\nfrom harness_core import lifecycle\n"
                  "print(json.dumps(lifecycle.dispatch(sys.argv[1], json.load(sys.stdin))))\n"
                  % str(REPO / "lib"))
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s",
                 "cwd": str(self.home), "tool_input": tool_input}
        env = isolation.without_harness_vars(dict(os.environ, HOME=str(self.home)))
        out = subprocess.run([sys.executable, "-c", script, runtime], input=json.dumps(event),
                             env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout).get("hookSpecificOutput") or {}

    def test_a_long_foreground_sleep_is_denied_over_the_read_only_allow(self):
        fields = self.dispatch("claude-code", {"command": "sleep 600"})
        self.assertEqual(fields["permissionDecision"], "deny")
        self.assertIn("steer-polling", fields["permissionDecisionReason"])

    def test_a_polling_loop_carries_the_note_as_context(self):
        fields = self.dispatch("claude-code", {"command": "until test -f x; do sleep 5; done"})
        self.assertNotEqual(fields.get("permissionDecision"), "deny")
        self.assertIn("steer-polling", fields["additionalContext"])

    def test_a_background_sleep_is_not_denied(self):
        fields = self.dispatch("claude-code", {"command": "sleep 600", "run_in_background": True})
        self.assertNotEqual(fields.get("permissionDecision"), "deny")
        self.assertNotIn("steer-polling", fields.get("additionalContext", ""))

    def test_codex_is_not_steered_toward_tools_it_lacks(self):
        fields = self.dispatch("codex", {"command": "sleep 600"})
        self.assertNotIn("steer-polling", json.dumps(fields))

    def test_the_switch_turns_it_off(self):
        fields = self.dispatch("claude-code", {"command": "sleep 600"}, {"steer-polling": "off"})
        self.assertNotIn("steer-polling", json.dumps(fields))


if __name__ == "__main__":
    unittest.main()
