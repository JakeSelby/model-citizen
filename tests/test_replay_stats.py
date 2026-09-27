# SPDX-License-Identifier: MIT
"""SM-2's analysis of a finished replay, from saved rows only: Cost-of-Pass, the paired,
task-clustered intervals, the verdict and the Pareto view. No test here launches an agent or
calls a model. Run: python3 -m unittest discover tests"""
import io
import json
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH, TASK, Launch, options, result

STATS = BENCH.replay_stats


def rows_for(spec, long=()):
    """Rows from `{task: {arm: [(passed, cost), ...]}}`; `None` for passed marks an errored run."""
    out = []
    for task, arms in spec.items():
        for arm, trials in arms.items():
            for rep, (passed, cost) in enumerate(trials, 1):
                out.append({"task": task, "arm": arm, "rep": rep, "error": passed is None,
                            "passed": passed, "cost_usd": cost, "task_long": task in long})
    return out


def cheaper_set(tasks=7, trials=5, harness_cost=0.5, bare_cost=1.0):
    """Both arms pass every trial; the harness costs a fixed share of bare, with a little spread."""
    spec = {}
    for t in range(tasks):
        wobble = 1 + 0.05 * t
        spec["t%d" % t] = {"bare": [(True, bare_cost * wobble)] * trials,
                           "harness": [(True, harness_cost * wobble)] * trials}
    return spec


class BootstrapTests(unittest.TestCase):
    def test_a_seed_gives_the_same_intervals_whatever_the_row_order(self):
        rows = rows_for({"a": {"bare": [(True, 1.0), (False, 1.2)], "harness": [(True, 0.7), (True, 0.9)]},
                         "b": {"bare": [(True, 2.0), (True, 1.5)], "harness": [(False, 1.0), (True, 1.1)]},
                         "c": {"bare": [(False, 0.8), (True, 0.9)], "harness": [(True, 0.6), (True, 0.5)]}})
        first = STATS.analyse(rows, seed=7, resamples=2000)
        shuffled = list(rows)
        random.Random(1).shuffle(shuffled)
        self.assertEqual(STATS.analyse(shuffled, seed=7, resamples=2000), first)
        self.assertEqual((first["seed"], first["resamples"]), (7, 2000))
        self.assertEqual(first["method"], STATS.METHOD)

    def test_resampling_keeps_both_arms_of_a_task_together(self):
        """Paired: every task has the harness at exactly half of bare, so every resample does too,
        and the interval collapses on 0.5 however the tasks are drawn."""
        rows = rows_for({"t%d" % i: {"bare": [(True, 1.0 + i)], "harness": [(True, 0.5 + i / 2.0)]}
                         for i in range(6)})
        got = STATS.analyse(rows, resamples=500)
        self.assertEqual((got["ratio"], got["ratio_interval"]), (0.5, [0.5, 0.5]))
        self.assertEqual(got["difference_interval"], [0.0, 0.0])

    def test_a_task_in_one_arm_only_cannot_be_paired(self):
        rows = rows_for({"a": {"bare": [(True, 1.0)], "harness": [(True, 1.0)]}, "b": {"bare": [(True, 1.0)]}})
        with self.assertRaisesRegex(ValueError, "task b has attempts in one arm only"):
            STATS.analyse(rows)


class CostOfPassTests(unittest.TestCase):
    def test_cost_of_pass_pools_every_attempt_over_every_pass(self):
        rows = rows_for({"a": {"bare": [(True, 1.0), (False, 3.0)], "harness": [(True, 1.0), (True, 1.0)]},
                         "b": {"bare": [(True, 2.0), (True, 2.0)], "harness": [(False, 2.0), (True, 2.0)]}})
        got = STATS.analyse(rows, resamples=200)
        self.assertEqual(got["arms"]["bare"]["cost_of_pass"], round(8.0 / 3, 6))
        self.assertEqual(got["arms"]["harness"]["cost_of_pass"], 2.0)
        self.assertEqual(got["ratio"], round(2.0 / (8.0 / 3), 4))
        self.assertEqual(got["arms"]["bare"]["pass_rate"], 0.75)
        self.assertEqual(got["difference"], 0.0)

    def test_an_errored_run_is_a_failed_attempt_with_its_cost(self):
        """Intention to treat: the crash is in the attempts and its cost in the numerator."""
        rows = rows_for({"a": {"bare": [(True, 1.0), (True, 1.0)], "harness": [(True, 0.5), (None, 1.5)]}})
        got = STATS.analyse(rows, resamples=200)
        harness = got["arms"]["harness"]
        self.assertEqual((harness["attempts"], harness["passes"], harness["errors"]), (2, 1, 1))
        self.assertEqual(harness["cost_of_pass"], 2.0)
        self.assertEqual(got["difference"], -0.5)

    def test_a_run_with_no_readable_cost_leaves_the_ratio_undefined_not_cheaper(self):
        rows = rows_for({"a": {"bare": [(True, 1.0), (False, 1.0)], "harness": [(None, None), (True, 0.1)]}})
        got = STATS.analyse(rows, resamples=200)
        self.assertIsNone(got["ratio"])
        self.assertIsNone(got["ratio_interval"])
        self.assertIn("no readable cost", got["ratio_undefined"])
        self.assertEqual(got["verdict"], STATS.INCONCLUSIVE)


class UndefinedRatioTests(unittest.TestCase):
    def assert_never_zero_or_infinite(self, got):
        text = json.dumps(got, allow_nan=False)  # raises on an infinity
        self.assertNotIn("Infinity", text)
        for bound in got["ratio_interval"] or []:
            self.assertTrue(bound is None or 0 < bound, bound)

    def test_an_arm_that_passes_nothing_has_an_undefined_ratio(self):
        for arm, other in (("harness", "bare"), ("bare", "harness")):
            spec = {"t%d" % i: {arm: [(False, 1.0)] * 3, other: [(True, 1.0)] * 3} for i in range(4)}
            got = STATS.analyse(rows_for(spec), resamples=300)
            self.assertIsNone(got["ratio"])
            self.assertEqual(got["ratio_undefined"], "the %s arm passed nothing" % arm)
            self.assertIsNone(got["arms"][arm]["cost_of_pass"])
            self.assert_never_zero_or_infinite(got)
            self.assertIn("undefined", STATS.render(got))

    def test_a_resample_where_an_arm_passes_nothing_leaves_that_bound_undefined(self):
        """The harness passes on one task of seven, so many resamples hold no harness pass: the
        interval's top is undefined, never infinity, and nothing is claimed."""
        spec = cheaper_set()
        for t in range(1, 7):
            spec["t%d" % t]["harness"] = [(False, 0.5)] * 5
        got = STATS.analyse(rows_for(spec), resamples=1000)
        self.assertIsNotNone(got["ratio"])
        self.assertIsNone(got["ratio_interval"][1])
        self.assertGreater(got["undefined_resamples"], 0)
        self.assertNotEqual(got["verdict"], STATS.SUPPORTED)
        self.assert_never_zero_or_infinite(got)


class WilsonTests(unittest.TestCase):
    def test_bounds_match_known_values(self):
        for (passes, n), (low, high) in {(5, 10): (0.2366, 0.7634), (0, 10): (0.0, 0.2775),
                                         (10, 10): (0.7225, 1.0), (1, 2): (0.0945, 0.9055)}.items():
            got = STATS.wilson(passes, n)
            self.assertAlmostEqual(got[0], low, places=4, msg=(passes, n))
            self.assertAlmostEqual(got[1], high, places=4, msg=(passes, n))
        self.assertEqual(STATS.wilson(0, 0), (None, None))

    def test_each_pass_rate_interval_is_labelled_descriptive(self):
        got = STATS.analyse(rows_for(cheaper_set(tasks=2, trials=2)), resamples=100)
        self.assertIn("pass_rate_interval_descriptive", got["arms"]["bare"])
        self.assertIn("(descriptive)", STATS.render(got))


class VerdictTests(unittest.TestCase):
    decide = staticmethod(STATS.decide)

    def test_supported_needs_both_conditions_and_claims_a_magnitude_only_at_or_below_085(self):
        self.assertEqual(self.decide(0.8, [0.7, 0.95], [-0.1, 0.1]),
                         (STATS.SUPPORTED, "both conditions of the decision rule hold", "cheaper"))
        self.assertEqual(self.decide(0.7, [0.6, 0.85], [-0.1, 0.1])[2], "at least 15% cheaper")
        self.assertEqual(self.decide(0.7, [0.6, 0.851], [-0.1, 0.1])[2], "cheaper")

    def test_not_supported_when_the_data_rule_the_claim_out(self):
        self.assertEqual(self.decide(1.2, [1.0, 1.4], [-0.1, 0.1])[0], STATS.NOT_SUPPORTED)
        verdict, reason, claim = self.decide(0.5, [0.4, 0.6], [-0.5, -0.2])
        self.assertEqual((verdict, claim), (STATS.NOT_SUPPORTED, None))
        self.assertIn("pass-rate difference", reason)

    def test_inconclusive_when_either_interval_is_too_wide_or_the_ratio_undefined(self):
        verdict, reason, _ = self.decide(0.9, [0.7, 1.1], [-0.1, 0.1])
        self.assertEqual(verdict, STATS.INCONCLUSIVE)
        self.assertIn("wholly below 1.0", reason)
        verdict, reason, _ = self.decide(0.8, [0.7, 0.9], [-0.2, 0.05])
        self.assertEqual(verdict, STATS.INCONCLUSIVE)
        self.assertIn("lower bound", reason)
        self.assertEqual(self.decide(0.8, [0.7, 0.9], [-0.125, 0.05])[0], STATS.INCONCLUSIVE)  # "above", strictly
        self.assertEqual(self.decide(None, None, [-0.1, 0.1])[0], STATS.INCONCLUSIVE)
        self.assertEqual(self.decide(0.8, [0.5, None], [-0.1, 0.1])[0], STATS.INCONCLUSIVE)

    def test_a_long_task_subset_must_also_win_before_a_saving_is_claimed(self):
        verdict, reason, claim = self.decide(0.7, [0.6, 0.8], [-0.1, 0.1], [0.8, 1.05], True)
        self.assertEqual((verdict, claim), (STATS.SUPPORTED, None))
        self.assertIn("long-task subset", reason)
        self.assertEqual(self.decide(0.7, [0.6, 0.8], [-0.1, 0.1], [0.6, 0.9], True)[2], "at least 15% cheaper")

    def test_a_clearly_cheaper_set_is_supported_end_to_end_and_its_long_subset_is_reported(self):
        got = STATS.analyse(rows_for(cheaper_set(), long=("t5", "t6")), resamples=1000)
        self.assertEqual((got["verdict"], got["claim"]), (STATS.SUPPORTED, "at least 15% cheaper"))
        self.assertEqual(got["long"]["tasks"], ["t5", "t6"])
        self.assertLess(got["long"]["ratio_interval"][1], 1.0)
        self.assertIn("long-task subset (t5, t6)", STATS.render(got))

    def test_an_unmarked_set_says_no_task_is_long(self):
        got = STATS.analyse(rows_for(cheaper_set(tasks=3)), resamples=200)
        self.assertIsNone(got["long"])
        self.assertIn("no task is marked long", STATS.render(got))


class ParetoTests(unittest.TestCase):
    def test_a_cheaper_arm_that_passes_as_often_dominates(self):
        got = STATS.analyse(rows_for(cheaper_set(tasks=2, trials=2)), resamples=100)
        self.assertEqual([p[3] for p in STATS.pareto(got)], ["dominated by harness", "frontier"])
        self.assertIn("| harness | ", STATS.render(got))

    def test_a_cheaper_arm_that_passes_less_leaves_both_on_the_frontier(self):
        spec = {"a": {"bare": [(True, 1.0), (True, 1.0)], "harness": [(True, 0.5), (False, 0.5)]}}
        got = STATS.analyse(rows_for(spec), resamples=100)
        self.assertEqual([p[3] for p in STATS.pareto(got)], ["frontier", "frontier"])


class RederivationTests(unittest.TestCase):
    def test_every_replay_row_carries_what_the_analysis_needs(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result(cost=0.4))] * 2)
            rows, _ = BENCH.replay([dict(TASK, long=True)], options(tmp, reps=1), launch)
        for row in rows:
            self.assertEqual((row["task"], row["rep"], row["outcome"], row["task_long"], row["cost_usd"]),
                             ("demo", 1, "pass", True, 0.4))
        self.assertEqual(STATS.attempts(rows)[0]["passed"], True)

    def test_an_errored_row_is_recorded_as_a_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch(["garbage"] * 2))
        self.assertEqual([r["outcome"] for r in rows], ["fail", "fail"])
        self.assertEqual([r["task_long"] for r in rows], [False, False])

    def test_summarise_re_derives_every_figure_from_the_saved_rows(self):
        rows = rows_for(cheaper_set(tasks=3, trials=2), long=("t2",))
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            out = io.StringIO()
            with redirect_stdout(out):
                code = BENCH.main(["summarise", "--results", tmp, "--json", "--seed", "3", "--resamples", "400"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue()), STATS.analyse(rows, seed=3, resamples=400))
            out = io.StringIO()
            with redirect_stdout(out):
                BENCH.main(["summarise", "--results", str(Path(tmp) / BENCH.RESULTS)])
            self.assertIn("verdict: ", out.getvalue())

    def test_rows_that_saved_no_pass_or_fail_are_refused_not_scored(self):
        """The 2026-09-23 rows saved no pass or fail: re-deriving from them must fail loudly."""
        rows = [{"task": "a", "arm": "bare", "rep": 1, "error": False, "passed": None, "cost_usd": 1.0}]
        with self.assertRaisesRegex(ValueError, "neither a pass nor a fail"):
            STATS.attempts(rows)
        with self.assertRaisesRegex(ValueError, "has no task"):
            STATS.attempts([{"arm": "bare", "rep": 1, "passed": True, "cost_usd": 1.0}])
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            with self.assertRaisesRegex(SystemExit, "cannot derive SM-2"):
                BENCH.main(["summarise", "--results", tmp])
        self.assertIn("unavailable", BENCH.sm2(rows))

    def test_the_history_row_carries_the_sm2_result_and_the_ledger_prints_it(self):
        rows = rows_for(cheaper_set(tasks=3, trials=2))
        for r in rows:
            r.update(date="2026-01-01", harness_version="9.9.9", harness_sha="a" * 40, tag="v9.9.9",
                     model="claude-test", cli_version="1.0", cost_normalised_usd=r["cost_usd"])
        made = BENCH.history_row(rows, "s1")
        self.assertEqual(made["sm2"]["verdict"], STATS.SUPPORTED)
        self.assertIn("    SM-2: supported, ratio 0.500 [0.500, 0.500]", BENCH.render_history([made]))


class DefaultTests(unittest.TestCase):
    def test_the_default_is_five_trials_and_the_cap_fits_a_full_default_set(self):
        """7 tasks x 5 trials x 2 arms, each at the per-run cap, after both preflights at theirs,
        must all launch under the replay's own stop rule."""
        self.assertEqual(BENCH.DEFAULT_REPS, 5)
        spent = len(BENCH.ARMS) * BENCH.PREFLIGHT_CAP_USD
        for _ in range(7 * BENCH.DEFAULT_REPS * len(BENCH.ARMS)):
            self.assertLessEqual(spent + BENCH.RUN_CAP_USD, BENCH.SPEND_CAP_USD)
            spent += BENCH.RUN_CAP_USD
        self.assertGreater(spent + BENCH.RUN_CAP_USD, BENCH.SPEND_CAP_USD)  # and not an eighth task

    def test_a_task_long_mark_must_be_a_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            for value, ok in ((True, True), (False, True), ("yes", False)):
                path.write_text(json.dumps({"tasks": [dict(TASK, long=value)]}), encoding="utf-8")
                if ok:
                    self.assertEqual(BENCH.load_tasks(path)[0]["long"], value)
                else:
                    with self.assertRaisesRegex(SystemExit, "long must be true or false"):
                        BENCH.load_tasks(path)


if __name__ == "__main__":
    unittest.main()
