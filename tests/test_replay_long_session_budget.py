"""Each long-session turn is capped at the session's remaining budget, and the whole run stops before
a turn whose budget could cross it (#1252). The figures are made up; no container is started and
no model called."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, Launch, options
from test_replay_long_session import MAIN, TIERS, FakeCli, run, running, scenario, turn_stream, workspace_scenario

SESSION = BENCH.replay_session


def session(outputs, cap, run_left=None):
    cli = FakeCli(running(outputs))
    rows = SESSION.run_session(scenario(), {"arm": "bare", "rep": 1}, cap, cli,
                               lambda *a: (True, "ok"), TIERS, run_left)
    return rows, cli


class TurnBudgetTests(unittest.TestCase):
    def test_a_turn_that_would_cross_the_session_cap_is_given_only_what_is_left(self):
        rows, cli, _ = run([turn_stream(3.7), turn_stream(0.25), turn_stream(0.1)], cap=4.0)
        self.assertEqual([c["budget"] for c in cli.calls], [4.0, 0.3, 0.05])

    def test_a_session_just_short_of_its_cap_gets_the_minimum_budget(self):
        self.assertEqual(SESSION.turn_budget(1.0, 0.995), SESSION.MIN_TURN_BUDGET_USD)
        self.assertEqual(SESSION.turn_budget(1.0, 0.25), 0.75)
        rows, cli = session([turn_stream(0.995), turn_stream(0.01)], cap=1.0)
        self.assertEqual([c["budget"] for c in cli.calls], [1.0, 0.01])

    def test_an_unpriced_turn_counts_at_its_whole_budget(self):
        rows, cli = session([turn_stream(0.995), {"stdout": "", "timeout": True}], cap=1.0)
        self.assertEqual((rows[-1]["cost_per_turn"], rows[-1]["cost_usd"]), ([0.995, 0.01], 1.005))

    def test_a_session_never_passes_its_ceiling_while_each_turn_holds_to_its_budget(self):
        item = scenario()
        cap = SESSION.session_cap(item)
        rows, cli = session([turn_stream(cap - 0.001), turn_stream(SESSION.MIN_TURN_BUDGET_USD)], cap=cap)
        self.assertLessEqual(rows[-1]["cost_usd"], SESSION.session_ceiling(item))
        self.assertGreater(rows[-1]["cost_usd"], cap)


class RunStopTests(unittest.TestCase):
    def test_a_turn_whose_budget_could_cross_the_run_stop_is_not_sent(self):
        rows, cli = session([turn_stream(0.995), turn_stream(0.01)], cap=1.0, run_left=1.0)
        self.assertEqual(len(cli.calls), 1)  # 0.995 spent and a 0.01 budget could reach 1.005
        last = rows[-1]
        self.assertEqual((last["stopped"], last["error"], last["error_kind"], last["passed"]),
                         (SESSION.STOP_SPEND, True, SESSION.STOP_SPEND, None))

    def test_a_turn_that_fits_the_run_stop_is_sent(self):
        rows, cli = session([turn_stream(0.995), turn_stream(0.01)], cap=1.0, run_left=1.005)
        self.assertEqual(len(cli.calls), 2)
        self.assertEqual(rows[-1]["stopped"], SESSION.STOP_CAP)

    def test_the_replay_loop_refuses_a_session_whose_last_turn_could_cross_the_run_stop(self):
        stopped, calls, lefts = self.replay(spend_cap=4.0)  # a 4 USD session can reach 4.01
        self.assertTrue(stopped)
        self.assertEqual((calls, lefts), ([], []))

    def test_the_replay_loop_gives_each_session_what_is_left_of_the_run_stop(self):
        stopped, calls, lefts = self.replay(spend_cap=8.02)
        self.assertFalse(stopped)
        self.assertEqual(calls, [1, 2, 3, 4] * 2)  # 1 USD a turn: each session stops at its 4 USD cap
        self.assertEqual(lefts, [8.02, 4.02])

    def replay(self, spend_cap):
        with tempfile.TemporaryDirectory() as tmp:
            item = workspace_scenario(tmp)
            calls, lefts = [], []
            real = SESSION.run_session

            def driver(number, prompt, budget, resume):
                calls.append(number)
                return {"stdout": turn_stream(float(number)), "returncode": 0}  # 1 USD a turn

            def spy(*args):
                lefts.append(round(args[6], 6))
                return real(*args)

            opts = options(tmp, model=MAIN, run_cap=None, reps=1, spend_cap=spend_cap, session_driver=driver,
                           stamp={"date": "2026-01-01", "model": MAIN},
                           session_scorer=lambda task, workdir, repo, stream, baseline: (True, "", None))
            with mock.patch.object(BENCH, "probe_workdirs"), mock.patch.object(BENCH.arms, "admit"), \
                    mock.patch.object(BENCH.arms, "admit_pair"), mock.patch.object(SESSION, "run_session", spy):
                rows, stopped = BENCH.replay([item], opts, Launch([]), out=Path(tmp) / "results.jsonl")
                self.assertEqual(BENCH.read_jsonl(Path(tmp) / "results.jsonl"), json.loads(json.dumps(rows)))
        return stopped, calls, lefts


if __name__ == "__main__":
    unittest.main()
