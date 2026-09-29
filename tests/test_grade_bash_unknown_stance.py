# SPDX-License-Identifier: MIT
"""Unknown autonomy variants fail closed without changing known thresholds."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import without_harness_vars


REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "policy" / "hooks" / "grade-bash.py"
CWD = "/work/repo"


class UnknownAutonomyStanceTests(unittest.TestCase):
    def decision(self, command, home, **extra):
        env = dict(without_harness_vars(), HOME=str(home), HARNESS_HOME=str(home), **extra)
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_name": "Bash", "cwd": CWD, "permission_mode": "default",
                              "tool_input": {"command": command}}),
            capture_output=True, text=True, env=env, check=True,
        )
        if not result.stdout.strip():
            return None, None
        output = json.loads(result.stdout)["hookSpecificOutput"]
        return output["permissionDecision"], output["permissionDecisionReason"]

    def user_config(self, home, **data):
        directory = home / ".config" / "agent-harness"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(json.dumps(data), encoding="utf-8")

    def assert_unknown_asks(self, home, **env):
        decision, reason = self.decision("touch notes.md", home, **env)
        self.assertEqual(decision, "ask")
        self.assertIn("grade 1, local write", reason)
        self.assertIn("autonomy=execut", reason)

    def test_unknown_environment_variant_uses_the_strictest_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_unknown_asks(Path(directory), HARNESS_STANCE_AUTONOMY="execut")

    def test_unknown_user_config_variant_uses_the_strictest_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.user_config(home, stances={"autonomy": "execut"})
            self.assert_unknown_asks(home)

    def test_unknown_session_variant_uses_the_strictest_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            session = home / "session.json"
            session.write_text(json.dumps({"stances": {"autonomy": "execut"}}), encoding="utf-8")
            self.assert_unknown_asks(home, HARNESS_SESSION_CONFIG=str(session))

    def test_unknown_mode_variant_uses_the_strictest_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            roots = home / "roots"
            modes = roots / "modes"
            modes.mkdir(parents=True)
            (modes / "typo.json").write_text(json.dumps({
                "schema_version": 1,
                "description": "mode with a mistyped autonomy variant",
                "stances": {"autonomy": "execut"},
            }), encoding="utf-8")
            self.user_config(home, primitive_roots=[str(roots)], mode="typo")
            self.assert_unknown_asks(home)

    def test_known_variants_keep_their_existing_thresholds(self):
        expected = {
            "execute": (None, None, "ask"),
            "confirm-writes": (None, "ask", "ask"),
            "ask": ("ask", "ask", "ask"),
        }
        commands = ("touch notes.md", "gh pr create --fill", "git push --force origin main")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for variant, wanted in expected.items():
                with self.subTest(variant=variant):
                    actual = tuple(self.decision(command, home,
                                                 HARNESS_STANCE_AUTONOMY=variant)[0]
                                   for command in commands)
                    self.assertEqual(actual, wanted)


if __name__ == "__main__":
    unittest.main()
