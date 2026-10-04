# SPDX-License-Identifier: MIT
"""A run stopped at its spend cap leaves a partial set (#1238): `summarise` balances each task's
arms down to the smaller trial count, leaves out a task with an empty arm, and marks the verdict
partial. Synthetic rows only; no agent and no model call."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH
from test_replay_reliability import det
from test_replay_stats import cheaper_set, rows_for

STATS = BENCH.replay_stats


def stopped_run():
    """The 2026-10-04 calibration's shape: every task at three trials but one, stopped with one bare
    and two harness trials."""
    spec = {"t%d" % t: {"bare": [(t % 2 == 0, 1.0)] * 3, "harness": [(True, 0.8)] * 3} for t in range(4)}
    spec["money"] = {"bare": [(False, 1.0)], "harness": [(False, 0.9), (True, 0.7)]}
    return rows_for(spec)


def registered(rows, plan="benchmarks/preregistrations/2026-10-01-plan.md", commit="a" * 40):
    return [dict(r, evidence="pre-registered", pre_registration=plan, pre_registration_commit=commit)
            for r in rows]


def summarise(tmp, *extra):
    out = io.StringIO()
    with redirect_stdout(out):
        code = BENCH.main(["summarise", "--results", tmp, "--seed", "3", "--resamples", "200"] + list(extra))
    return code, out.getvalue()


class BalanceTests(unittest.TestCase):
    def test_a_cell_is_cut_to_the_smaller_arm_by_dropping_the_latest_trials(self):
        rows = list(reversed(stopped_run()))  # saved order must not decide which trial goes
        kept, notes = STATS.balance(rows)
        money = [(r["arm"], r["rep"]) for r in kept if r["task"] == "money"]
        self.assertEqual(sorted(money), [("bare", 1), ("harness", 1)])
        self.assertEqual(notes, [{"task": "money", "action": "balanced",
                                  "trials": {"bare": 1, "harness": 2}, "kept": 1,
                                  "dropped": [["harness", 2]],
                                  "reason": "the run stopped with 1 bare and 2 harness trials; "
                                            "the latest are dropped"}])
        self.assertEqual(len(kept), len(rows) - 1)

    def test_a_task_with_an_empty_arm_is_left_out_with_its_reason(self):
        rows = rows_for({"a": {"bare": [(True, 1.0)], "harness": [(True, 1.0)]},
                         "b": {"bare": [(True, 1.0), (False, 1.0)]}})
        kept, notes = STATS.balance(rows)
        self.assertEqual({r["task"] for r in kept}, {"a"})
        self.assertEqual(notes[0]["action"], "left out")
        self.assertIn("no harness trial (2 bare and 0 harness)", notes[0]["reason"])

    def test_a_malformed_row_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid trial id"):
            STATS.balance([{"task": "a", "arm": "bare", "rep": 0, "passed": True, "cost_usd": 1.0}])

    def test_the_relaxed_analysis_still_refuses_an_unbalanced_task(self):
        with self.assertRaisesRegex(ValueError, "unequal trial counts"):
            STATS.analyse(stopped_run(), resamples=50, fixed_sample=False)


class AnalyseSetTests(unittest.TestCase):
    def test_a_complete_set_is_exactly_the_strict_analysis(self):
        rows = rows_for(cheaper_set(tasks=3, trials=2))
        self.assertEqual(STATS.analyse_set(rows, resamples=100), STATS.analyse(rows, resamples=100))
        self.assertNotIn("partial", STATS.analyse_set(rows, resamples=100))

    def test_an_exploratory_partial_set_reads_partial_with_its_cells_used(self):
        rows = stopped_run() + rows_for({"gone": {"harness": [(True, 1.0)]}})
        result = STATS.analyse_set(rows, resamples=100)
        self.assertEqual(result["verdict"], STATS.PARTIAL)
        self.assertIsNone(result["claim"])
        self.assertEqual((result["partial"]["cells_used"], result["partial"]["cells_seen"]), (5, 6))
        self.assertTrue(result["partial"]["uneven"])
        self.assertEqual(result["arms"]["harness"]["attempts"], 4 * 3 + 1)
        self.assertIn("5 of 6 cell(s) used", result["reason"])
        text = STATS.render(result)
        self.assertIn("partial set: 5 of 6 cell(s) used", text)
        self.assertIn("money: balanced to 1 trial(s) per arm, dropped harness trial 2, because", text)
        self.assertIn("gone: left out, because the run recorded no bare trial", text)

    def test_a_registered_partial_set_supports_no_claim_by_default(self):
        result = STATS.analyse_set(stopped_run(), resamples=100, registered=True)
        self.assertEqual(result["verdict"], "partial: no claim")
        self.assertIsNone(result["claim"])
        self.assertIn("stopping rule does not let a partial set support a claim", result["reason"])

    def test_a_stopping_rule_that_allows_a_partial_set_keeps_the_sm2_verdict_marked_partial(self):
        spec = cheaper_set(tasks=7, trials=6)
        spec["t0"]["harness"] = spec["t0"]["harness"][:5]  # stopped one trial short
        rows = rows_for(spec, long=("t1", "t2", "t3"))  # a claim needs the long-task subset's win
        strict = STATS.analyse(STATS.balance(rows)[0], resamples=200, fixed_sample=False)
        result = STATS.analyse_set(rows, resamples=200, registered=True, claim_allowed=True)
        self.assertEqual(result["verdict"], "partial: " + strict["verdict"])
        self.assertEqual(result["claim"], strict["claim"])
        self.assertIsNotNone(result["claim"])
        # allowed without registration means nothing: an exploratory set never claims
        self.assertEqual(STATS.analyse_set(rows, resamples=200, claim_allowed=True)["verdict"], STATS.PARTIAL)


class SummariseTests(unittest.TestCase):
    def test_summarise_reports_a_stopped_run_instead_of_refusing_it(self):
        rows = stopped_run()
        detections = [det(r["task"], r["arm"], r["rep"], "d1", 0) for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            BENCH.write_jsonl(Path(tmp) / BENCH.replay_detect.DETECTIONS, detections)
            code, text = summarise(tmp)
            self.assertEqual(code, 0)
            self.assertIn("money: balanced to 1 trial(s) per arm", text)
            self.assertIn("verdict: partial, because a partial set, 5 of 5 cell(s) used", text)
            self.assertNotIn("match no saved row", text)  # the dropped trial's detections went with it
            code, out = summarise(tmp, "--json")
        printed = json.loads(out)
        self.assertEqual(printed["partial"]["cells_used"], 5)
        self.assertEqual(printed["reliability"]["joint"]["unmatched_runs"], 0)
        self.assertEqual(printed["reliability"]["joint"]["arms"]["harness"]["runs"], 13)
        self.assertEqual(printed["reliability"]["pass_k"]["arms"]["harness"]["per_task"]["money"]["trials"], 1)

    def test_a_registered_stopped_run_reads_partial_no_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, registered(stopped_run()))
            with mock.patch.object(BENCH.experiment_protocol, "_git", return_value=(128, "")):
                code, text = summarise(tmp)
        self.assertEqual(code, 0)
        self.assertIn("verdict: partial: no claim, because", text)


class StoppingRuleTests(unittest.TestCase):
    PLAN = "# Plan\n\n## Stopping rule\n\n- **Fixed sample:** five trials.\n%s\n## Multiplicity\n"

    def allowed(self, line, code=0):
        with mock.patch.object(BENCH.experiment_protocol, "_git",
                               return_value=(code, self.PLAN % line)) as git:
            answer = BENCH.partial_claim_allowed(registered(stopped_run()))
        if code == 0:
            git.assert_called_once_with(BENCH.ROOT, "show",
                                        "%s:benchmarks/preregistrations/2026-10-01-plan.md" % ("a" * 40))
        return answer

    def test_the_plan_field_decides_and_the_default_is_no(self):
        self.assertTrue(self.allowed("- **Partial set:** allowed, the cap is the sample size"))
        self.assertFalse(self.allowed("- **Partial set:** not allowed"))
        self.assertFalse(self.allowed(""))
        self.assertFalse(self.allowed("- **Partial set:** allowed", code=128))  # unreadable plan

    def test_a_run_naming_no_plan_is_never_allowed(self):
        self.assertFalse(BENCH.partial_claim_allowed(stopped_run()))
        self.assertFalse(BENCH.partial_claim_allowed([]))


if __name__ == "__main__":
    unittest.main()
