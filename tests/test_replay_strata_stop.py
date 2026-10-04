"""A stratum that fails stops a multi-model run (#1182): the strata after it never start, its exit
status is the run's, and what was spent so far is reported. No test builds an image or calls a model."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, GREEN, TASK, Launch, gate_reply, options
from test_cost_bench_tags import FakeArms, harness_repo, replay_args
from test_preflight_budget_stop import STOPPED_AT, stream
from test_replay_strata import MODELS, fake_replay

THREE = MODELS + ("claude-c",)


def failing_on(model, stop):
    """A replay that writes and charges its rows, then fails the way `stop` says on `model`."""
    def replay(tasks, opts, launch=None, out=None):
        rows, stopped = fake_replay(tasks, opts, launch, out)
        opts["spend_ledger"].extend(row["cost_usd"] for row in rows)
        if opts["model"] == model:
            if isinstance(stop, SystemExit):
                raise stop
            return rows, True  # stopped at the spend cap
        return rows, stopped
    return replay


class StratumStopTests(unittest.TestCase):
    def run_failing(self, model, stop):
        """(exit code, stdout, stderr, the strata that wrote results) of a three-model run."""
        fake, out, err = FakeArms(), io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = replay_args(tmp, model=[",".join(THREE)])
            with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                    mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                    mock.patch.object(BENCH.arms, "egress", fake.egress), \
                    mock.patch.object(BENCH, "replay", failing_on(model, stop)), \
                    mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                    redirect_stdout(out), redirect_stderr(err):
                code = BENCH.cmd_replay(args)
            ran = [m for m in THREE if (Path(tmp) / "out" / "v1" / m / BENCH.RESULTS).is_file()]
        return code, out.getvalue(), err.getvalue(), ran

    def test_a_refused_stratum_stops_the_strata_after_it(self):
        code, out, err, ran = self.run_failing("claude-b", SystemExit(2))
        self.assertEqual(code, 2)
        self.assertEqual(ran, ["claude-a", "claude-b"])
        self.assertNotIn("stratum 3 of 3", out)
        # Two strata of two runs at 1 USD each were charged before the stop.
        self.assertIn("stratum 2 of 3 (model claude-b) failed with exit 2; stopping the run. Not run: "
                      "claude-c. Spent so far: 4.0000 USD", err)

    def test_an_error_message_exits_one_and_is_printed(self):
        code, _, err, ran = self.run_failing("claude-a", SystemExit("cost-bench: stopping the set: drift"))
        self.assertEqual(code, 1)
        self.assertEqual(ran, ["claude-a"])
        self.assertIn("cost-bench: stopping the set: drift", err)
        self.assertIn("Not run: claude-b, claude-c. Spent so far: 2.0000 USD", err)

    def test_a_stop_at_the_spend_cap_stops_the_run(self):
        code, _, err, ran = self.run_failing("claude-a", None)
        self.assertEqual(code, 1)
        self.assertEqual(ran, ["claude-a"])
        self.assertIn("failed with exit 1", err)

    def test_every_stratum_runs_when_none_fails(self):
        code, out, err, ran = self.run_failing("none-of-them", None)
        self.assertEqual(code, 0)
        self.assertEqual(ran, list(THREE))
        self.assertNotIn("stopping the run", err)

    def test_a_dry_run_lists_every_stratum_and_exits_with_the_worst(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            # The demo task is same-repository, so the contamination control refuses it in each stratum.
            args = replay_args(tmp, model=list(THREE), dry_run=True)
            with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), redirect_stdout(out), redirect_stderr(err):
                code = BENCH.cmd_replay(args)
        self.assertEqual(code, 2)
        for number, model in enumerate(THREE, 1):
            self.assertIn("stratum %d of 3: model %s" % (number, model), out.getvalue())
        self.assertNotIn("stopping the run", err.getvalue())


class SpendLedgerTests(unittest.TestCase):
    def test_a_refused_preflight_records_what_it_spent(self):
        ledger = []
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN), stream()])
            opts = options(tmp, reps=1, skip_preflight=False, preflight_cap=STOPPED_AT, spend_ledger=ledger)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                BENCH.replay([TASK], opts, launch)
        self.assertAlmostEqual(sum(ledger), 0.1 + 0.055585)

    def test_each_scored_run_is_charged(self):
        ledger = []
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN)] * 4)
            rows, _ = BENCH.replay([TASK], options(tmp, spend_ledger=ledger), launch)
        self.assertEqual(len(ledger), len(rows))
        self.assertAlmostEqual(sum(ledger), sum(row["cost_usd"] for row in rows))


if __name__ == "__main__":
    unittest.main()
