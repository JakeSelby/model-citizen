"""Ablation arms against control (#514): each arm reports cost, output tokens, the standing prefix,
turns, tool calls and pass rate separately with n, spread and a task-clustered interval; an
interval spanning no effect reads inconclusive; fewer than five trials reads exploratory; the
prefix figure is used only where same-arm runs agree within 2%. Fixed rows only."""
import importlib.util
import unittest

from test_harness import REPO

spec = importlib.util.spec_from_file_location("replay_stats_ablations", REPO / "scripts" / "replay_stats.py")
STATS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(STATS)

RESAMPLES = 400


def row(task, arm, rep, cost=1.0, passed=True, output=100, prefix=20000, turns=4, tools=3,
        modules=None, date="2026-10-01"):
    return {"task": task, "arm": arm, "rep": rep, "cost_usd": cost, "passed": passed, "error": False,
            "output_tokens": output, "first_call_context": prefix, "turns": turns,
            "tool_counts": {"Bash": tools}, "date": date,
            "context_attribution": {"estimand": "soft estimate", "modules": {"rules/a": 100} if modules is None else modules}}


def sweep(trials=5, tasks=6, arm_cost=1.0, arm_prefix=20000, arms=("no-a",), jitter=True):
    """Bare, control and `arms`, every task and trial; costs vary by task so intervals are real."""
    rows = []
    for t in range(tasks):
        for rep in range(1, trials + 1):
            noise = 0.02 * ((t * 7 + rep * 3) % 5) if jitter else 0.0
            rows.append(row("t%d" % t, "bare", rep, cost=0.5 + noise))
            rows.append(row("t%d" % t, "harness", rep, cost=1.0 + noise + 0.1 * t))
            for arm in arms:
                rows.append(row("t%d" % t, arm, rep, cost=arm_cost + noise + 0.1 * t, prefix=arm_prefix,
                                modules={}))
    return rows


class CompareTests(unittest.TestCase):
    def test_every_measure_is_reported_separately_with_n_spread_and_an_interval(self):
        result = STATS.compare(sweep(arm_cost=0.7), resamples=RESAMPLES, removed={"no-a": "rules/a"})
        arm = result["arms"][0]
        self.assertEqual(arm["removed"], "rules/a")
        self.assertEqual([k for k, _l, _k in STATS.MEASURES],
                         ["cost_usd", "output_tokens", "first_call_context", "turns", "tool_calls",
                          "pass_rate", "cost_per_passed"])
        for key in ("cost_usd", "output_tokens", "first_call_context", "turns", "tool_calls", "pass_rate"):
            measure = arm["measures"][key]
            self.assertEqual(measure["n"], 30)
            self.assertIn("sd", measure["spread"])
            self.assertIn("max_over_min", measure["spread"])
            self.assertEqual(len(measure["interval"]), 2)
        self.assertEqual(arm["measures"]["cost_usd"]["reading"], "lower")
        self.assertEqual(arm["measures"]["output_tokens"]["reading"], "inconclusive")

    def test_an_interval_spanning_no_effect_prints_inconclusive(self):
        result = STATS.compare(sweep(arm_cost=1.0), resamples=RESAMPLES)
        cost = result["arms"][0]["measures"]["cost_usd"]
        self.assertLessEqual(cost["interval"][0], 0)
        self.assertGreaterEqual(cost["interval"][1], 0)
        self.assertEqual(cost["reading"], "inconclusive")
        self.assertIn("cost: effect +0.0%", STATS.render_compare(result))
        self.assertIn(": inconclusive (the interval spans no effect)", STATS.render_compare(result))

    def test_fewer_than_five_trials_prints_exploratory(self):
        result = STATS.compare(sweep(trials=4, arm_cost=0.7), resamples=RESAMPLES)
        self.assertTrue(result["arms"][0]["exploratory"])
        self.assertIn("exploratory: fewer than 5 paired trials per task (4)", STATS.render_compare(result))
        five = STATS.compare(sweep(trials=5, arm_cost=0.7), resamples=RESAMPLES)
        self.assertFalse(five["arms"][0]["exploratory"])

    def test_several_arms_are_exploratory_unless_a_correction_is_named(self):
        rows = sweep(arm_cost=0.7, arms=("no-a", "no-b"))
        loose = STATS.compare(rows, resamples=RESAMPLES)
        self.assertTrue(all(a["exploratory"] for a in loose["arms"]))
        corrected = STATS.compare(rows, resamples=RESAMPLES, correction="bonferroni")
        self.assertFalse(any(a["exploratory"] for a in corrected["arms"]))
        self.assertAlmostEqual(corrected["confidence"], 0.975)
        with self.assertRaises(ValueError):
            STATS.compare(rows, correction="holm")

    def test_arms_are_ranked_by_the_size_of_their_cost_effect(self):
        rows = [r for r in sweep(arm_cost=0.9, arms=("small",))]
        rows += [dict(r, arm="large", cost_usd=r["cost_usd"] - 0.5) for r in rows if r["arm"] == "small"]
        result = STATS.compare(rows, resamples=RESAMPLES, correction="bonferroni")
        self.assertEqual([a["arm"] for a in result["arms"]], ["large", "small"])
        text = STATS.render_compare(result)
        self.assertLess(text.index("1. large"), text.index("2. small"))

    def test_cost_per_passed_is_one_measure_and_never_the_headline(self):
        text = STATS.render_compare(STATS.compare(sweep(arm_cost=0.7), resamples=RESAMPLES))
        first = text.splitlines()[0]
        self.assertNotIn("cost per passed", first)
        self.assertLess(text.index("     cost:"), text.index("     cost per passed attempt:"))
        self.assertLess(text.index("     pass rate:"), text.index("     cost per passed attempt:"))

    def test_a_measure_a_row_lacks_is_unavailable_never_zero(self):
        rows = sweep()
        rows[2]["turns"] = None
        turns = STATS.compare(rows, resamples=RESAMPLES)["arms"][0]["measures"]["turns"]
        self.assertEqual(turns["reading"], "unavailable")
        self.assertIsNone(turns["estimate"])

    def test_bare_and_control_are_not_ranked_and_control_is_required(self):
        result = STATS.compare(sweep(), resamples=RESAMPLES)
        self.assertEqual([a["arm"] for a in result["arms"]], ["no-a"])
        with self.assertRaises(ValueError):
            STATS.compare([r for r in sweep() if r["arm"] != "harness"], resamples=RESAMPLES)

    def test_analyse_is_unchanged_by_the_ablation_report(self):
        rows = [r for r in sweep(arm_cost=0.7) if r["arm"] in ("bare", "harness")]
        self.assertEqual(STATS.analyse(rows, resamples=RESAMPLES)["arms"]["harness"]["attempts"], 30)


class PrefixTests(unittest.TestCase):
    def test_agreeing_runs_are_usable_and_their_prefix_is_compared(self):
        result = STATS.compare(sweep(arm_prefix=18000), resamples=RESAMPLES)
        prefix = result["arms"][0]["measures"]["first_call_context"]
        self.assertEqual(prefix["estimand"], "measured")
        self.assertEqual(prefix["effect"], -0.1)
        self.assertTrue(result["prefix"]["no-a"]["usable"])

    def test_runs_more_than_two_percent_apart_make_the_arm_unusable(self):
        rows = sweep()
        for r in rows:
            if r["arm"] == "no-a" and r["rep"] == 1:
                r["first_call_context"] = 25000
        clean = STATS.prefix_clean(rows)
        self.assertFalse(clean["no-a"]["usable"])
        self.assertTrue(clean["harness"]["usable"])
        self.assertGreater(clean["no-a"]["worst_spread"], 0.02)

    def test_runs_within_two_percent_on_one_task_and_date_agree(self):
        rows = [row("t", "harness", 1, prefix=10000), row("t", "harness", 2, prefix=10150),
                row("t", "harness", 3, prefix=9900)]
        self.assertTrue(STATS.prefix_clean(rows)["harness"]["usable"])

    def test_an_unusable_prefix_falls_back_to_the_labelled_soft_estimate(self):
        rows = sweep()
        rows[0]["first_call_context"] = None
        rows[1]["first_call_context"] = None
        result = STATS.compare(rows, resamples=RESAMPLES)
        prefix = result["arms"][0]["measures"]["first_call_context"]
        self.assertEqual(prefix["estimand"], "soft estimate")
        self.assertEqual(prefix["effect"], -1.0)  # the arm's attribution has no module at all
        self.assertIn("standing prefix tokens (soft estimate)", STATS.render_compare(result))
        self.assertIn("prefix figures of harness unusable: 1 run(s) lack", STATS.render_compare(result))


class MinimumDetectableEffectTests(unittest.TestCase):
    def test_more_attempts_resolve_a_smaller_effect_and_more_comparisons_a_larger_one(self):
        small = STATS.minimum_detectable_effect(100, 0.25)
        large = STATS.minimum_detectable_effect(10, 0.25)
        self.assertLess(small, large)
        self.assertGreater(STATS.minimum_detectable_effect(100, 0.25, comparisons=16), small)

    def test_the_formula_matches_a_hand_computed_value(self):
        # z(0.975) + z(0.8) = 1.95996 + 0.84162; cv 0.25, n 50: exp(2.80158 * 0.25 * 0.2) - 1
        self.assertAlmostEqual(STATS.minimum_detectable_effect(50, 0.25), 0.15036, places=4)

    def test_no_attempts_or_no_variation_gives_none(self):
        self.assertIsNone(STATS.minimum_detectable_effect(0, 0.25))
        self.assertIsNone(STATS.minimum_detectable_effect(10, 0))


if __name__ == "__main__":
    unittest.main()
