# SPDX-License-Identifier: MIT
"""With `grade-bash` off, the read-only allow and the delegation read count never load it.

The Bash grader is a library (`bash-grader.py`) that `grade-bash.py` re-exports. Each test here
makes loading `grade-bash.py` raise, the way any fault in a switched-off hook would, and checks
that the Bash path answers as it does with the hook absent rather than denying every command.

Run: python3 -m unittest discover tests
"""
import io
import json
import types
from contextlib import redirect_stdout
from unittest.mock import patch

from test_hook_switches import FORCE_PUSH, REAL, RUNTIMES, Fixture, decisions, lifecycle, posture, pre


class SwitchedOffGrader(Fixture):
    def configure(self, hooks, **keys):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(keys, hooks=hooks, **{posture.CORE_ACK: True})))

    def faulting_load(self, loaded):
        """A loader whose `grade-bash` raises; the rest load as the switch fixture loads them."""
        real_load = lifecycle.load

        def load(name):
            loaded.append(name)
            if name == "grade-bash":
                raise RuntimeError("a fault in the switched-off grade-bash hook")
            return real_load(name) if name in REAL else types.SimpleNamespace(main=lambda *args: None)
        return load

    def answers(self, runtime, command, **fields):
        """`(loaded module names, decisions)` for one Bash event, through `dispatch`."""
        loaded, captured, real_encode = [], [], lifecycle.encode_pre

        def encode(runtime_, original, normalized, results):
            captured.extend(results)
            return real_encode(runtime_, original, normalized, results)

        event = dict(pre("Bash", command=command), cwd=str(self.cwd), **fields)
        with patch.object(lifecycle, "load", self.faulting_load(loaded)), \
                patch.object(lifecycle, "encode_pre", encode):
            lifecycle.dispatch(runtime, event)
        return loaded, captured

    def test_the_read_only_allow_answers_without_loading_grade_bash(self):
        self.configure({"grade-bash": "off", "allow-readonly-bash": "on"})
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime, command="ls"):
                loaded, results = self.answers(runtime, "ls")
                self.assertEqual([d for d in decisions(results) if d], ["allow"])
                self.assertIn("bash-grader", loaded)
                self.assertNotIn("grade-bash", loaded)
            with self.subTest(runtime=runtime, command=FORCE_PUSH):
                loaded, results = self.answers(runtime, FORCE_PUSH)
                self.assertEqual([d for d in decisions(results) if d], [])
                self.assertNotIn("grade-bash", loaded)

    def test_the_hook_entry_point_does_not_deny_every_command(self):
        self.configure({"grade-bash": "off", "allow-readonly-bash": "on"})
        payload = json.dumps(dict(pre("Bash", command="ls"), cwd=str(self.cwd)))
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                out = io.StringIO()
                with patch.object(lifecycle, "load", self.faulting_load([])), \
                        patch("sys.stdin", io.StringIO(payload)), redirect_stdout(out):
                    lifecycle.main(runtime, [])
                # Codex is answered by printing nothing, Claude Code by an explicit allow.
                answer = json.loads(out.getvalue() or "{}").get("hookSpecificOutput", {})
                self.assertEqual(answer.get("permissionDecision"),
                                 "allow" if runtime == "claude-code" else None)

    def test_the_plan_mode_ask_keeps_its_grade_reason_without_grade_bash(self):
        self.configure({"grade-bash": "off", "allow-readonly-bash": "on"}, permissions="bypass")
        loaded, results = self.answers("claude-code", "git push", permission_mode="plan")
        asked = [r["hookSpecificOutput"] for r in results
                 if r.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"]
        self.assertEqual(len(asked), 1)
        self.assertIn("Plan mode widens investigation", asked[0]["permissionDecisionReason"])
        self.assertIn("grade 2, remote-mutating", asked[0]["permissionDecisionReason"])
        self.assertNotIn("grade-bash", loaded)

    def test_the_delegation_read_count_reads_the_library_not_the_hook(self):
        (self.cwd / "notes.txt").write_text("x\n")
        event = {"tool_name": "Bash", "tool_input": {"command": "cat notes.txt"},
                 "cwd": str(self.cwd), "tool_response": {}}
        loaded = []
        with patch.object(lifecycle, "load", self.faulting_load(loaded)):
            paths = lifecycle.delegation_read_paths(event)
        self.assertEqual(paths, [str(self.cwd / "notes.txt")])
        self.assertNotIn("grade-bash", loaded)
