# SPDX-License-Identifier: MIT
"""A partial set's claim follows why the run stopped (#1238): the runner records its stop reason
beside the results, a plan's `Partial set` permission may name the stop reasons it covers, a value
outside that grammar is refused naming it, and a set balancing leaves with no paired cell is still
summarised. Synthetic rows and fake launches only; no agent and no model call."""
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, options
from test_replay_loaded_surface import first_call, init, run
from test_replay_parity import stream
from test_replay_stats import cheaper_set, rows_for
from test_replay_stats_partial import registered, summarise

STATS = BENCH.replay_stats
PLAN = "# Plan\n\n## Stopping rule\n\n- **Fixed sample:** six trials.\n- **Partial set:** %s\n\n## Multiplicity\n"


def claimable_stopped_run():
    """A registered set stopped one trial short whose balanced rows support SM-2's claim."""
    spec = cheaper_set(tasks=7, trials=6)
    spec["t0"]["harness"] = spec["t0"]["harness"][:5]
    return registered(rows_for(spec, long=("t1", "t2", "t3")))


def plan_says(value):
    return mock.patch.object(BENCH.experiment_protocol, "_git", return_value=(0, PLAN % value))


class GrammarTests(unittest.TestCase):
    def test_each_documented_form_parses(self):
        self.assertEqual(BENCH.partial_set_permission("allowed"), BENCH.ANY_STOP)
        self.assertEqual(BENCH.partial_set_permission("allowed when spend-cap"), {"spend-cap"})
        self.assertEqual(BENCH.partial_set_permission("allowed when spend-cap, surface-drift"),
                         {"spend-cap", "surface-drift"})
        self.assertEqual(BENCH.partial_set_permission("not allowed"), frozenset())
        self.assertEqual(BENCH.partial_set_permission(None), frozenset())

    def test_a_value_outside_the_grammar_is_refused_naming_it(self):
        for value in ("allowed, the cap is the sample size", "allowed when timeout", "allowed when",
                      "allowed only after the spend cap stops the run", "yes"):
            with self.assertRaisesRegex(ValueError, "Partial set value %r" % value):
                BENCH.partial_set_permission(value)


class ConditionTests(unittest.TestCase):
    def allowed(self, value, reason):
        with plan_says(value):
            return BENCH.partial_claim_allowed(claimable_stopped_run(), reason)

    def test_a_conditional_permission_covers_only_the_stop_reasons_it_names(self):
        self.assertTrue(self.allowed("allowed when spend-cap", "spend-cap"))
        self.assertFalse(self.allowed("allowed when spend-cap", "effort"))
        self.assertFalse(self.allowed("allowed when spend-cap", "surface-drift"))
        self.assertFalse(self.allowed("allowed when spend-cap", None))  # no recorded reason
        self.assertTrue(self.allowed("allowed when effort, surface-drift", "surface-drift"))

    def test_a_plain_permission_covers_any_stop_recorded_or_not(self):
        for reason in BENCH.STOP_REASONS + (None,):
            self.assertTrue(self.allowed("allowed", reason))
        self.assertFalse(self.allowed("not allowed", "spend-cap"))


class SummariseTests(unittest.TestCase):
    def verdict(self, value, reason):
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, claimable_stopped_run())
            if reason is not None:
                (Path(tmp) / "stop.json").write_text(json.dumps({"stop_reason": reason}), encoding="utf-8")
            with plan_says(value):
                code, text = summarise(tmp)
        self.assertEqual(code, 0)
        return [line for line in text.splitlines() if "verdict:" in line][0]

    def test_a_run_stopped_for_another_reason_than_the_permission_names_makes_no_claim(self):
        self.assertIn("verdict: partial: no claim", self.verdict("allowed when spend-cap", "effort"))
        self.assertIn("verdict: partial: no claim", self.verdict("allowed when spend-cap", "surface-drift"))

    def test_a_run_stopped_for_the_named_reason_keeps_the_sm2_verdict(self):
        line = self.verdict("allowed when spend-cap", "spend-cap")
        self.assertIn("verdict: partial: ", line)
        self.assertNotIn("no claim", line)

    def test_summarise_refuses_an_ungrammatical_permission_naming_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, claimable_stopped_run())
            with plan_says("allowed only after the spend cap"), self.assertRaises(SystemExit) as caught:
                summarise(tmp)
        self.assertIn("'allowed only after the spend cap'", str(caught.exception))


class StopRecordTests(unittest.TestCase):
    def test_the_spend_cap_stop_is_recorded_beside_the_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / BENCH.RESULTS
            _, stopped = BENCH.replay([TASK], options(tmp, spend_cap=1.5), Launch([]), out=out)
            self.assertTrue(stopped)
            self.assertEqual(BENCH.stop_beside(out), "spend-cap")

    def test_a_surface_drift_stop_is_recorded_beside_the_results(self):
        drifted = subprocess.TimeoutExpired("claude", 1, output=stream(init(slash=84, effort="high"), first_call()))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / BENCH.RESULTS
            with self.assertRaises(SystemExit):
                BENCH.replay([TASK], options(tmp), Launch([run(), run(), drifted]), out=out)
            self.assertEqual(BENCH.stop_beside(out), "surface-drift")

    def test_an_effort_stop_is_recorded_beside_the_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / BENCH.RESULTS
            with self.assertRaisesRegex(SystemExit, "ran at effort low"):
                BENCH.replay([TASK], options(tmp), Launch([run(effort="low")]), out=out)
            self.assertEqual(BENCH.stop_beside(out), "effort")

    def test_a_complete_set_records_no_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / BENCH.RESULTS
            _, stopped = BENCH.replay([TASK], options(tmp, reps=1), Launch([run(), run()]), out=out)
            self.assertFalse(stopped)
            self.assertFalse((Path(tmp) / BENCH.STOP).exists())
            self.assertIsNone(BENCH.stop_beside(out))


class AdmissionTests(unittest.TestCase):
    def test_replay_refuses_an_ungrammatical_permission_before_anything_runs(self):
        protocol = {"evidence": "pre-registered", "pre_registration": "benchmarks/preregistrations/2026-10-01-p.md",
                    "pre_registration_commit": "a" * 40}
        err = io.StringIO()
        with mock.patch.object(BENCH.experiment_protocol, "admit", return_value=protocol), \
                plan_says("allowed when timeout"), \
                mock.patch.object(BENCH, "replay", side_effect=AssertionError("ran")), \
                redirect_stderr(err), redirect_stdout(io.StringIO()), tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.json"
            tasks.write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                BENCH.main(["replay", "--tasks", str(tasks), "--model", "claude-test", "--tag", "v1",
                            "--pre-registration", "x.md"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("'allowed when timeout'", err.getvalue())


class UnpairedTests(unittest.TestCase):
    ONE_ARM_EACH = {"a": {"bare": [(True, 1.0)]}, "b": {"harness": [(False, 1.0)]}}

    def test_a_set_with_no_paired_cell_is_a_partial_result_with_no_claim(self):
        rows = registered(rows_for(self.ONE_ARM_EACH))
        result = STATS.analyse_set(rows, resamples=50, registered=True, claim_allowed=True)
        self.assertEqual(result["verdict"], STATS.PARTIAL_NO_CLAIM)
        self.assertIsNone(result["claim"])
        self.assertEqual((result["partial"]["cells_used"], result["partial"]["cells_seen"]), (0, 2))
        self.assertFalse(result["partial"]["claim_allowed"])
        self.assertIn("0 of 2 cell(s) used, so no paired cell is left to analyse", result["reason"])
        self.assertIn("partial set: 0 of 2 cell(s) used", STATS.render(result))

    def test_summarise_reports_a_set_with_no_paired_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows_for(self.ONE_ARM_EACH))
            code, text = summarise(tmp)
            self.assertEqual(code, 0)
            self.assertIn("verdict: partial, because a partial set, 0 of 2 cell(s) used", text)
            code, out = summarise(tmp, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["partial"]["cells_used"], 0)


if __name__ == "__main__":
    unittest.main()
