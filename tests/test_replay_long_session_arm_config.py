"""A long-session set with config arms: each config arm runs its own sessions beside bare and harness,
the dry run prices them into the ceiling, a real run names its own --spend-cap, and every session row
records its `arm_config`. No image is built, no container started and no model called."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench_tags import harness_repo
import test_replay_long_session as long_session
from test_replay_long_session import BENCH, turn_stream

SHIPPED = Path(__file__).resolve().parent.parent / "benchmarks" / "arms"


class LongSessionConfigArmTests(unittest.TestCase):
    args, replay_cli = long_session.DryRunTests.args, long_session.DryRunTests.replay_cli  # the helpers only, not their tests

    def config_args(self, tmp, **over):
        args = self.args(tmp, **over)
        args.arm_config = ["maintainer=%s" % (SHIPPED / "maintainer.json")]
        args.stance_cost = None
        return args

    def test_the_dry_run_schedules_and_prices_every_config_arm_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.config_args(tmp, dry_run=True, exploratory=True, reps=1)
            with mock.patch.object(BENCH, "arm_config_errors", lambda configs, commit, tmp=None: []):
                code, out, _, fake = self.replay_cli(tmp, args)
        self.assertEqual((code, fake.built), (0, []))
        self.assertIn("long-session tier: 3 session(s): 1 scenario(s) x 3 arm(s) x 1 rep(s)", out)
        self.assertIn("ceiling, before any spend: 12.00 USD", out)
        for arm in ("bare", "harness", "maintainer"):
            self.assertIn("demo-session rep 1 %s" % arm, out)
        # The tier's ceiling default is sized without the config arms, so none is filled in.
        self.assertIsNone(args.spend_cap)

    def test_a_real_run_with_config_arms_names_its_spend_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.config_args(tmp, exploratory=True, dry_run=False)
            with mock.patch.object(BENCH, "arm_config_errors", lambda configs, commit, tmp=None: []), \
                    self.assertRaisesRegex(SystemExit, "a run with config arms needs --spend-cap"):
                self.replay_cli(tmp, args)


class SessionRowConfigStampTests(unittest.TestCase):
    run_session = long_session.ContainerSessionTests.run_session

    def test_every_session_row_records_the_arms_config(self):
        stamp = {"name": "maintainer", "stances": {"voice": "concise"}}
        rows, _, _ = self.run_session([turn_stream(0.5)] * 5, arm_configs={"harness": stamp})
        self.assertTrue(rows)
        self.assertEqual({r["arm"] for r in rows}, {"harness"})
        for row in rows:
            self.assertEqual(row["arm_config"], stamp)

    def test_a_session_without_config_arms_records_none(self):
        rows, _, _ = self.run_session([turn_stream(0.5)] * 5)
        self.assertEqual({r["arm_config"] for r in rows}, {None})


if __name__ == "__main__":
    unittest.main()
