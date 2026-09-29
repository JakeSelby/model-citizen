# SPDX-License-Identifier: MIT
"""Observation registration is opt-in and reversible in both native homes."""
import io
import json
import unittest
from contextlib import redirect_stdout
from copy import deepcopy

from test_harness import REPO, TempHome, harness
from harness_core import lifecycle, observation


class RegistrationTests(unittest.TestCase):
    def test_default_keeps_exact_existing_registration(self):
        for runtime in ("claude-code", "codex"):
            base = lifecycle.registration(REPO, runtime)
            self.assertEqual(observation.with_observation(base, REPO, runtime, {}), base)

    def test_enabled_adds_one_independent_recorder_per_event(self):
        for runtime in ("claude-code", "codex"):
            base = lifecycle.registration(REPO, runtime)
            original = deepcopy(base)
            result = observation.with_observation(base, REPO, runtime,
                                                   {"observation": {"enabled": True}})
            self.assertEqual(base, original)
            for event, entries in result["hooks"].items():
                self.assertEqual(len(entries), len(base["hooks"][event]) + 1)
                self.assertIn("observe.py", entries[-1]["hooks"][0]["command"])

    def test_only_boolean_opt_in_is_accepted(self):
        for value in ("false", "true", 1, None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                observation.enabled({"observation": {"enabled": value}})


class ObservationSyncTests(TempHome):
    def sync(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(harness.cmd_sync(harness.argparse.Namespace(
                dry_run=False, adopt=False, adopt_codex=False, print_only=False)), 0)

    def test_sync_on_off_is_idempotent_and_preserves_user_hooks(self):
        files = (harness.claude_dir() / "settings.json", harness.codex_dir() / "hooks.json")
        user_entry = {"hooks": [{"type": "command", "command": "echo user"}]}
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"hooks": {"Stop": [user_entry]}}))
        with redirect_stdout(io.StringIO()):
            harness.config_set("observation.enabled", "true")
        self.sync()
        first = [path.read_text() for path in files]
        self.sync()
        self.assertEqual(first, [path.read_text() for path in files])
        for path in files:
            entries = json.loads(path.read_text())["hooks"]["Stop"]
            self.assertIn(user_entry, entries)
            self.assertEqual(sum("observe.py" in e["hooks"][0]["command"] for e in entries), 1)
        with redirect_stdout(io.StringIO()):
            harness.config_set("observation.enabled", "false")
        self.sync()
        for path in files:
            self.assertNotIn("observe.py", path.read_text())
            self.assertIn(user_entry, json.loads(path.read_text())["hooks"]["Stop"])
        with redirect_stdout(io.StringIO()):
            harness.config_set("observation.enabled", "true")
        self.sync()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(harness.cmd_uninstall(harness.argparse.Namespace()), 0)
        for path in files:
            self.assertNotIn("observe.py", path.read_text())
            self.assertEqual(json.loads(path.read_text())["hooks"]["Stop"], [user_entry])


if __name__ == "__main__":
    unittest.main()
