# SPDX-License-Identifier: MIT
"""The watch scan stats each module directory once per poll, however many modules it holds."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_harness  # noqa: F401  loads the repository library path
from harness_core.studio import live_updates
from harness_core.studio.live_updates import WatchScanner


class ScanCostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / "home"
        self.rules = root / "extra-primitives" / "rules"
        self.rules.mkdir(parents=True)
        self.skills = root / "extra-primitives" / "skills"
        for index in range(3):
            skill = self.skills / ("skill-%d" % index)
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (skill / "notes.md").write_text("not a match\n", encoding="utf-8")
        for index in range(50):
            (self.rules / ("module-%d.md" % index)).write_text("module\n", encoding="utf-8")
        config = home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"primitive_roots": [str(root / "extra-primitives")]}),
                          encoding="utf-8")
        state = root / "state"
        state.mkdir()
        self.scanner = WatchScanner(Path(__file__).resolve().parent.parent, state.resolve(),
                                    {"HOME": str(home), "HARNESS_HOME": str(home)})

    def test_module_directory_is_fingerprinted_once_per_scan(self):
        calls = []
        original = live_updates._fingerprint

        def counting(path, allowed_root=None, info=None):
            calls.append(str(path))
            return original(path, allowed_root, info)

        with mock.patch.object(live_updates, "_fingerprint", counting):
            snapshot = self.scanner.scan()
        directory = str(self.rules.absolute())
        self.assertIn(directory, snapshot)
        self.assertEqual(calls.count(directory), 1)
        self.assertEqual(sum(1 for path in snapshot if path.startswith(directory + "/")), 50)

    def test_nested_pattern_records_each_matched_parent_once(self):
        calls = []
        original = live_updates._fingerprint

        def counting(path, allowed_root=None, info=None):
            calls.append(str(path))
            return original(path, allowed_root, info)

        with mock.patch.object(live_updates, "_fingerprint", counting):
            snapshot = self.scanner.scan()
        base = str(self.skills.absolute())
        self.assertEqual(calls.count(base), 1)
        for index in range(3):
            skill = str((self.skills / ("skill-%d" % index)).absolute())
            self.assertEqual(calls.count(skill), 1)
            self.assertEqual(snapshot[skill][1], ("library-index", "overview", "selection"))
            self.assertEqual(snapshot[skill + "/SKILL.md"][1], ("library", "overview"))
            self.assertNotIn(skill + "/notes.md", snapshot)


if __name__ == "__main__":
    unittest.main()
