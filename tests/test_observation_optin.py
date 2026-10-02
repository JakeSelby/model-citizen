# SPDX-License-Identifier: MIT
"""Observation registration is opt-in and reversible in both native homes."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest import mock

from isolation import isolate_home
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

    def runtime_files(self):
        return (harness.claude_dir() / "settings.json", harness.codex_dir() / "hooks.json")

    def test_never_opted_sync_persists_the_pre_opt_in_files_byte_for_byte(self):
        self.sync()
        opted_out = [path.read_bytes() for path in self.runtime_files()]
        with tempfile.TemporaryDirectory() as other:
            isolate_home(Path(other))
            try:
                # The sync as it was before the opt-in existed: no observation step at all.
                with mock.patch.object(harness.observation, "with_observation",
                                       lambda template, repo, runtime, cfg: template):
                    self.sync()
                before = [path.read_bytes() for path in self.runtime_files()]
            finally:
                isolate_home(self.home)
        self.assertEqual(opted_out, before)
        for raw in opted_out:
            self.assertNotIn(b"observe.py", raw)

    def test_invalid_observation_refuses_before_creating_any_state(self):
        config = harness.config_path()
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"observation": {"enabled": "false"}}))
        state = harness.state_dir()
        self.assertFalse(state.exists())
        with self.assertRaises(SystemExit):
            self.sync()
        self.assertFalse(state.exists())

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
        for path, runtime in zip(files, ("claude-code", "codex")):
            hooks = json.loads(path.read_text())["hooks"]
            self.assertIn(user_entry, hooks["Stop"])
            for event in observation.events(runtime):
                entries = hooks[event]
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
