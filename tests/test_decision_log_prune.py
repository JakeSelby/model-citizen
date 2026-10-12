# SPDX-License-Identifier: MIT
"""A lowered `decision_log_keep` prunes the rotated files numbered past it at the next rotation.

Run: python3 -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path

from test_decision_log_rotation import decisions


class PruneTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "decisions.jsonl"

    def test_files_past_a_lowered_keep_are_deleted_and_the_rest_kept(self):
        for generation in "abcde":
            self.log.write_text(generation * 10 + "\n")
            decisions.rotate(self.log, cap=10, count=5)
        self.log.with_name("decisions.jsonl.lock").touch()
        self.log.with_name("decisions.jsonl.1.tally").touch()
        self.log.write_text("f" * 10 + "\n")
        self.assertTrue(decisions.rotate(self.log, cap=10, count=2))
        names = sorted(p.name for p in self.log.parent.iterdir())
        self.assertEqual(names, ["decisions.jsonl.1", "decisions.jsonl.1.tally",
                                 "decisions.jsonl.2", "decisions.jsonl.lock"])
        self.assertEqual([p.read_text()[0] for p in decisions.rotated(self.log, 2)], ["f", "e"])

    def test_keep_zero_deletes_every_rotated_file(self):
        for generation in "ab":
            self.log.write_text(generation * 10 + "\n")
            decisions.rotate(self.log, cap=10, count=2)
        self.log.write_text("c" * 10 + "\n")
        self.assertTrue(decisions.rotate(self.log, cap=10, count=0))
        self.assertEqual([p.name for p in self.log.parent.iterdir()
                          if not p.name.endswith(".lock")], [])


if __name__ == "__main__":
    unittest.main()
