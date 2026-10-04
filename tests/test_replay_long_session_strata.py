"""The long-session tier with several models: a set that pins no model runs each model as its own
stratum, a set that pins one is refused before any stratum starts, and summarise reports the
long-session section once per stratum. No image is built and no model called."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import test_replay_long_session as long_session
from test_cost_bench import BENCH
from test_cost_bench_tags import harness_repo

OTHER = "claude-opus-4-1"


class LongSessionStrataReplayTests(unittest.TestCase):
    args = long_session.DryRunTests.args
    replay_cli = long_session.DryRunTests.replay_cli

    def test_a_set_pinning_no_model_runs_each_model_as_its_own_stratum(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            long_session.scenario_pack(Path(tmp) / "pack", extra_sets={
                "long-session": {"tier": "long-session", "scenarios": ["demo-session"]}})
            args = self.args(tmp, dry_run=True, exploratory=True, model="%s,%s" % (long_session.MAIN, OTHER))
            code, out, _, fake = self.replay_cli(tmp, args)
        self.assertEqual((code, fake.built), (0, []))
        self.assertIn("2 strata, each run and reported on its own: %s, %s" % (long_session.MAIN, OTHER), out)
        for number, model in ((1, long_session.MAIN), (2, OTHER)):
            self.assertIn("stratum %d of 2: model %s" % (number, model), out)
            self.assertIn("stratum %s: worst case" % model, out)
        self.assertEqual(out.count("long-session tier: 6 session(s)"), 2)

    def test_a_set_pinning_its_model_is_refused_before_any_stratum_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.args(tmp, dry_run=True, exploratory=True, model=[long_session.MAIN, OTHER])
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    self.assertRaisesRegex(SystemExit, "pins model %s; it takes no strata" % long_session.MAIN):
                self.replay_cli(tmp, args)
        self.assertNotIn("stratum 1 of 2", out.getvalue())


class LongSessionStrataSummaryTests(unittest.TestCase):
    def write_strata(self, tmp):
        rows = long_session.SummaryTests.rows(None)
        tag = Path(tmp) / "tag"
        for model in (long_session.MAIN, OTHER):
            (tag / BENCH.strata.directory(model)).mkdir(parents=True)
            BENCH.write_jsonl(tag / BENCH.strata.directory(model) / BENCH.RESULTS,
                              [dict(row, model=model, stratum=model) for row in rows])
        return tag / BENCH.RESULTS

    def summarise(self, path, as_json):
        args = argparse.Namespace(results=str(path), seed=1, resamples=50, plot=None, json=as_json,
                                  break_even=3, correction=None, pool=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(BENCH.cmd_summarise(args), 0)
        return out.getvalue()

    def test_the_long_session_section_appears_once_per_stratum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_strata(tmp)
            text = self.summarise(path, False)
            document = json.loads(self.summarise(path, True))
        for model in (long_session.MAIN, OTHER):
            self.assertIn("== stratum %s ==" % model, text)
            self.assertIn("long-session tier: per arm on %s" % model, text)
        self.assertEqual(text.count("long-session tier: per arm"), 2)
        self.assertEqual(text.count("Reliability: pass^2"), 2)
        self.assertEqual(sorted(document["strata"]), sorted([long_session.MAIN, OTHER]))
        for report in document["strata"].values():
            self.assertEqual(report["tier"], "long-session")
            self.assertEqual(report["reliability"]["pass_k"]["arms"]["harness"]["all_passed"], 3)


if __name__ == "__main__":
    unittest.main()
