# SPDX-License-Identifier: MIT
"""A non-finite `raw_vs_deduped` is unknown in `harness usage`, not a measured run.

`json.loads` accepts `NaN` and `Infinity`, so a malformed ledger line reaches the report as a
float. Counted as measured, it adds a run to the footer's count and poisons the weighted ratio;
these tests hold it on the unknown side in both the text footer and `--json`.

Run: python3 -m unittest discover tests
"""
import argparse
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from test_usage import harness


class NonFiniteRatios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        prior_home = os.environ.get("HOME")
        prior_harness_home = os.environ.get("HARNESS_HOME")
        prior_quiet = os.environ.pop("HARNESS_QUIET", None)
        os.environ["HOME"] = str(self.home)
        os.environ["HARNESS_HOME"] = str(self.home)
        self.addCleanup(self._restore, prior_home, prior_harness_home, prior_quiet)
        self.path = self.home / ".local" / "state" / "agent-harness" / "usage.jsonl"
        self.path.parent.mkdir(parents=True)

    @staticmethod
    def _restore(prior_home, prior_harness_home, prior_quiet):
        for name, prior in (("HOME", prior_home), ("HARNESS_HOME", prior_harness_home)):
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior
        if prior_quiet is not None:
            os.environ["HARNESS_QUIET"] = prior_quiet

    def row(self, session_id, ratio):
        return {"kind": "session", "runtime": "claude-code", "session_id": session_id,
                "ended": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "models": ["claude-fable-5-1"], "input": 100, "output": 900,
                "cache_read": 0, "cache_write": 0, "raw_vs_deduped": ratio}

    def mixed(self):
        """One finite row and three a ledger can hold but no measurement can produce."""
        return [self.row("s-1", 1.5), self.row("s-2", float("nan")),
                self.row("s-3", float("inf")), self.row("s-4", float("-inf"))]

    def report(self, rows, as_json):
        # json.dumps writes NaN and Infinity by default, as a malformed ledger line would hold.
        self.path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        args = argparse.Namespace(days=30, by="day", rules=False, rescan=False, stance=None,
                                  json=as_json)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(harness.cmd_usage(args), 0)
        return buf.getvalue()

    def test_the_footer_counts_only_the_finite_row_as_measured(self):
        text = self.report(self.mixed(), as_json=False)
        self.assertIn("raw_vs_deduped: raw totals were 1.50x the counted totals over 1 run(s), "
                      "weighted by counted tokens; 3 run(s) unknown", text)
        self.assertNotIn("nan", text.lower())

    def test_the_json_block_counts_only_the_finite_row_as_measured(self):
        report = json.loads(self.report(self.mixed(), as_json=True))
        self.assertEqual(report["raw_vs_deduped"],
                         {"ratio": 1.5, "runs": 1, "unknown_runs": 3})

    def test_a_window_of_only_non_finite_rows_says_unknown(self):
        rows = [self.row("s-1", float("nan")), self.row("s-2", float("inf"))]
        text = self.report(rows, as_json=False)
        self.assertIn("raw_vs_deduped: unknown, no run in this window recorded a raw figure",
                      text)
        report = json.loads(self.report(rows, as_json=True))
        self.assertEqual(report["raw_vs_deduped"],
                         {"ratio": None, "runs": 0, "unknown_runs": 2})

    def test_a_non_finite_row_sliced_by_day_adds_nothing_to_the_ratio(self):
        """The `--by day` slice path accumulates the ratio separately, so it is held too."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        sliced = self.row("s-2", float("nan"))
        sliced["days"] = {today: {"input": 100, "output": 900, "turns": 1}}
        text = self.report([self.row("s-1", 1.5), sliced], as_json=False)
        self.assertIn("raw_vs_deduped: raw totals were 1.50x the counted totals over 1 run(s), "
                      "weighted by counted tokens; 1 run(s) unknown", text)


if __name__ == "__main__":
    unittest.main()
