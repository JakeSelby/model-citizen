# SPDX-License-Identifier: MIT
"""The decision log's retention: size-capped rotation, a configurable cap, N rotated files kept.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import isolation  # noqa: F401  (moves the process onto a disposable home)

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "policy" / "hooks"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decisions = _load("decisions_rotation", HOOKS / "decisions.py")
telemetry = _load("telemetry_rotation", HOOKS / "telemetry.py")


class RotationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "decisions.jsonl"

    def fill(self, text):
        with self.log.open("a") as stream:
            stream.write(text)

    def test_a_file_below_the_cap_is_left_alone(self):
        self.fill("x\n")
        self.assertFalse(decisions.rotate(self.log, cap=100, count=3))
        self.assertEqual(self.log.read_text(), "x\n")

    def test_a_full_file_moves_to_dot_one_and_older_files_move_up(self):
        for generation in ("a", "b", "c", "d", "e"):
            self.fill(generation * 10 + "\n")
            self.assertTrue(decisions.rotate(self.log, cap=10, count=3))
        self.assertFalse(self.log.exists())
        kept = [p.read_text()[0] for p in decisions.rotated(self.log, 3)]
        self.assertEqual(kept, ["e", "d", "c"])
        self.assertFalse(self.log.with_name("decisions.jsonl.4").exists())

    def test_keep_zero_removes_the_full_file(self):
        self.fill("x" * 20)
        self.assertTrue(decisions.rotate(self.log, cap=10, count=0))
        self.assertFalse(self.log.exists())
        self.assertFalse(self.log.with_name("decisions.jsonl.1").exists())

    def test_a_cap_of_zero_never_rotates(self):
        self.fill("x" * 20)
        self.assertFalse(decisions.rotate(self.log, cap=0, count=3))
        self.assertTrue(self.log.exists())

    def test_appending_rotates_and_history_reads_every_kept_row(self):
        decisions._CONFIG[:] = [{"telemetry": {"decision_log_max_bytes": 1, "decision_log_keep": 2}}]
        self.addCleanup(decisions._CONFIG.clear)
        for n in range(4):
            decisions.record("stop-gate", "blocked", "t%d" % n, {"session_id": "s"}, target=self.log)
        current = decisions.read_rows(self.log)
        self.assertEqual([r["input"] for r in current], ["t3"])
        history = decisions.read_rows(self.log, history=True)
        self.assertEqual([r["input"] for r in history], ["t1", "t2", "t3"])

    def test_the_settings_default_and_refuse_a_bad_value(self):
        self.assertEqual(decisions.max_bytes({}), decisions.DEFAULT_MAX_BYTES)
        self.assertEqual(decisions.keep({}), decisions.DEFAULT_KEEP)
        self.assertEqual(decisions.max_bytes({"telemetry": {"decision_log_max_bytes": 5}}), 5)
        self.assertEqual(decisions.keep({"telemetry": {"decision_log_keep": True}}),
                         decisions.DEFAULT_KEEP)
        self.assertEqual(telemetry.DEFAULT_DECISION_LOG_MAX_BYTES, decisions.DEFAULT_MAX_BYTES)
        self.assertEqual(telemetry.DEFAULT_DECISION_LOG_KEEP, decisions.DEFAULT_KEEP)
        parsed = telemetry.settings({"telemetry": {"decision_log_max_bytes": 1024,
                                                   "decision_log_keep": 5}})
        self.assertEqual((parsed["decision_log_max_bytes"], parsed["decision_log_keep"]), (1024, 5))
        for bad in (-1, "big", 1.5):
            with self.assertRaises(ValueError):
                telemetry.settings({"telemetry": {"decision_log_keep": bad}})

    def test_rows_survive_rotation_unchanged(self):
        decisions._CONFIG[:] = [{"telemetry": {"decision_log_max_bytes": 1, "decision_log_keep": 1}}]
        self.addCleanup(decisions._CONFIG.clear)
        decisions.record("stop-gate", "blocked", "first", {"session_id": "s"}, target=self.log)
        before = self.log.read_text()
        decisions.record("stop-gate", "blocked", "second", {"session_id": "s"}, target=self.log)
        self.assertEqual(self.log.with_name("decisions.jsonl.1").read_text(), before)
        self.assertEqual(json.loads(self.log.read_text())["input"], "second")


if __name__ == "__main__":
    unittest.main()
