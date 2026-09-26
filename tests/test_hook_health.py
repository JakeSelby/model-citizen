# SPDX-License-Identifier: MIT
"""Unit tests for the hook health check: a hook that cannot start fails silently, so doctor says so."""
import argparse
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)


@contextlib.contextmanager
def loud():
    prior = os.environ.pop("HARNESS_QUIET", None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        if prior is not None:
            os.environ["HARNESS_QUIET"] = prior


def settings(command):
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": command}]}]}}


class HookCommandTests(unittest.TestCase):
    def test_every_registered_hook_command_is_found(self):
        # One coordinator command per lifecycle event, plus SessionStart's workspace entry, so the count
        # follows the registered entries.
        template = harness.runtime_template()
        entries = sum(len(group) for group in template["hooks"].values())
        self.assertEqual(entries, len(template["hooks"]) + 1)
        self.assertEqual(len(harness.hook_commands(template)), entries)

    def test_no_hooks_block_is_no_commands(self):
        self.assertEqual(harness.hook_commands({}), [])

    def test_a_malformed_block_is_skipped_rather_than_raising(self):
        self.assertEqual(harness.hook_commands({"hooks": {"PreToolUse": "nonsense"}}), [])
        self.assertEqual(harness.hook_commands({"hooks": {"PreToolUse": [{"hooks": [None]}]}}), [])


class HookHealthTests(unittest.TestCase):
    def test_a_missing_interpreter_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "hook.py"
            script.write_text("")
            with unittest.mock.patch.object(harness, "_tool", return_value=None):
                count, problems = harness.hook_health(settings(f"python3 {script}"))
        self.assertEqual(count, 1)
        self.assertIn("interpreter not found: python3", problems)

    def test_a_missing_script_is_named(self):
        with unittest.mock.patch.object(harness, "_tool", return_value="/usr/bin/python3"):
            count, problems = harness.hook_health(settings("python3 /nowhere/hook.py"))
        self.assertEqual(count, 1)
        self.assertIn("script not found: /nowhere/hook.py", problems)

    def test_a_home_relative_script_path_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior = os.environ.get("HOME")
            os.environ["HOME"] = tmp
            try:
                script = Path(tmp) / ".claude" / "hooks" / "harness" / "hook.py"
                script.parent.mkdir(parents=True)
                script.write_text("")
                with unittest.mock.patch.object(harness, "_tool", return_value="/usr/bin/python3"):
                    _, problems = harness.hook_health(
                        settings("python3 ~/.claude/hooks/harness/hook.py # harness:x"))
            finally:
                if prior is not None:
                    os.environ["HOME"] = prior
        self.assertEqual(problems, [])

    def test_a_trailing_comment_marker_is_not_mistaken_for_a_script(self):
        with unittest.mock.patch.object(harness, "_tool", return_value="/usr/bin/python3"):
            _, problems = harness.hook_health(settings("python3 /nowhere/hook.py # harness:grade-bash"))
        self.assertEqual(problems, ["script not found: /nowhere/hook.py"])

    def test_the_same_problem_is_reported_once(self):
        cfg = {"hooks": {"PreToolUse": [
            {"hooks": [{"command": "python3 /nowhere/hook.py"}]},
            {"hooks": [{"command": "python3 /nowhere/hook.py"}]}]}}
        with unittest.mock.patch.object(harness, "_tool", return_value="/usr/bin/python3"):
            count, problems = harness.hook_health(cfg)
        self.assertEqual(count, 2)
        self.assertEqual(problems, ["script not found: /nowhere/hook.py"])

    def test_an_unparseable_command_is_skipped_rather_than_raising(self):
        count, problems = harness.hook_health(settings('python3 "unclosed'))
        self.assertEqual((count, problems), (1, []))


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ["HARNESS_QUIET"] = "1"

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def _doctor(self):
        with unittest.mock.patch.object(harness, "_version_of", return_value="stub"), \
             unittest.mock.patch.object(harness.shutil, "which", return_value=None), \
             unittest.mock.patch.object(harness, "_diff_lines", return_value=[]):
            with loud() as out:
                harness.cmd_doctor(argparse.Namespace())
        return out.getvalue()

    def test_doctor_says_when_nothing_is_registered(self):
        self.assertIn("hooks: none registered", self._doctor())

    def test_doctor_points_constrained_roles_at_citizen_role_run(self):
        self.assertIn("constrained roles: use `citizen role run`; `citizen role status` reports workers",
                      self._doctor())

    def test_doctor_names_the_guards_that_are_off(self):
        path = Path(self.tmp.name) / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings("python3 /nowhere/grade-bash.py")))
        text = self._doctor()
        self.assertIn("cannot run", text)
        self.assertIn("script not found: /nowhere/grade-bash.py", text)
        self.assertIn("fail silently", text)
        self.assertIn("stop gate", text)


    def test_doctor_checks_codex_hooks(self):
        path = Path(self.tmp.name) / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings("python3 /nowhere/codex-hook.py")))
        text = self._doctor()
        self.assertIn("codex hooks: 1 registered", text)
        self.assertIn("script not found: /nowhere/codex-hook.py", text)

    def test_doctor_does_not_request_installation_for_unmanaged_runtime(self):
        with unittest.mock.patch.object(harness, "load_config", return_value={"claude": {"manage": False}}):
            text = self._doctor()
        self.assertIn("claude hooks: unmanaged", text)
        self.assertNotIn("claude hooks: none registered", text)
        self.assertIn("codex hooks: none registered", text)

    def test_doctor_reports_malformed_hook_json_and_checks_next_runtime(self):
        path = Path(self.tmp.name) / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        text = self._doctor()
        self.assertIn("claude hooks: unreadable configuration", text)
        self.assertIn("codex hooks: none registered", text)


class MessageTests(unittest.TestCase):
    def test_the_deny_tail_keeps_the_marker_and_drops_the_mode_jargon(self):
        text = (REPO / "claude" / "hooks" / "grade-bash.py").read_text()
        self.assertIn("HARNESS_CONFIRMED=1", text)
        self.assertIn("Nothing can prompt in this permission mode", text)
        self.assertNotIn("No prompt exists in this mode", text)

    def test_the_gate_message_says_where_the_command_came_from(self):
        text = (REPO / "claude" / "hooks" / "stop-gate.py").read_text()
        self.assertIn("check block this repository defines under `## Gate`", text)

    def test_the_untrusted_message_names_the_command_that_fixes_it(self):
        text = (REPO / "claude" / "hooks" / "stop-gate.py").read_text()
        self.assertIn("Run `citizen trust .` in this folder", text)


if __name__ == "__main__":
    unittest.main()
