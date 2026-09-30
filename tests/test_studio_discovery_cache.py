# SPDX-License-Identifier: MIT
"""Unit-test discovery is reused while the target's ``tests/`` tree is unchanged."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import free_suites  # noqa: E402

CASE = {"id": "test_sample.SampleTests.test_passes", "module": "test_sample",
        "class_name": "SampleTests", "test_name": "test_passes", "label": "test passes"}


class DiscoveryCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        (self.root / "tests").mkdir()
        (self.root / "bin").mkdir()
        (self.root / "bin" / "harness").write_text("", encoding="utf-8")
        self.sample = self.root / "tests" / "test_sample.py"
        self.sample.write_text("# one\n", encoding="utf-8")
        self.calls = []
        self.outputs = [[CASE]]
        patcher = mock.patch.object(free_suites.subprocess, "run", side_effect=self._run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(free_suites._DISCOVERY_CACHE.pop, self.root, None)

    def _run(self, argv, **_kwargs):
        self.calls.append(argv)
        output = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if output is None:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        return subprocess.CompletedProcess(argv, 0, json.dumps(output), "")

    def test_an_unchanged_tests_tree_reuses_the_last_discovery(self):
        first = free_suites.discover_unit_tests(self.root)
        first.append({"id": "mutated"})
        second = free_suites.discover_unit_tests(self.root)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second, [CASE])

    def test_a_changed_or_added_test_file_discovers_again(self):
        free_suites.discover_unit_tests(self.root)
        self.sample.write_text("# one, and a longer second revision\n", encoding="utf-8")
        free_suites.discover_unit_tests(self.root)
        self.assertEqual(len(self.calls), 2)
        (self.root / "tests" / "test_other.py").write_text("# two\n", encoding="utf-8")
        free_suites.discover_unit_tests(self.root)
        self.assertEqual(len(self.calls), 3)

    def test_a_failed_discovery_is_not_reused(self):
        self.outputs = [None, [CASE]]
        with self.assertRaises(free_suites.FreeSuiteError):
            free_suites.discover_unit_tests(self.root)
        self.assertEqual(free_suites.discover_unit_tests(self.root), [CASE])
        self.assertEqual(len(self.calls), 2)

    def test_a_launch_after_the_catalog_resolves_without_discovering_again(self):
        free_suites.discover_unit_tests(self.root)
        cases = free_suites.resolve_case_identities(
            "unit-tests", {"root": str(self.root), "case": CASE["id"]},
            "installed", str(self.root))
        self.assertEqual(cases, [CASE["id"]])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
