"""pass^k and the all-rules-at-once rate (#1180), from synthetic saved rows and detections only; no
test here launches an agent or calls a model."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_reliability", REPO / "scripts" / "replay_reliability.py")
REL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REL)


def rows_for(spec):
    """Rows from `{task: {arm: [passed, ...]}}`; `None` for passed marks an errored run."""
    out = []
    for task, arms in spec.items():
        for arm, trials in arms.items():
            for rep, passed in enumerate(trials, 1):
                out.append({"task": task, "arm": arm, "rep": rep, "error": passed is None,
                            "passed": passed, "cost_usd": 1.0})
    return out


def det(task, arm, rep, detector, count, rule="r"):
    row = {"task": task, "arm": arm, "rep": rep, "detector": detector, "rule": rule, "count": count,
           "turns": None if count is None else [1] * count}
    if count is None:
        row["error"] = "unreadable: no stream"
    return row


class PassHatK(unittest.TestCase):
    def test_matches_the_closed_form(self):
        self.assertEqual(REL.pass_hat_k(5, 5, 3), 1.0)
        self.assertEqual(REL.pass_hat_k(5, 2, 3), 0.0)
        self.assertAlmostEqual(REL.pass_hat_k(5, 4, 3), math.factorial(4) / 6 / (math.factorial(5) / 12))
        self.assertAlmostEqual(REL.pass_hat_k(4, 3, 1), 0.75)

    def test_is_unbiased_for_k_trials_drawn_from_n(self):
        """Averaging all-pass over every k-subset of the trials gives the estimate exactly."""
        import itertools
        trials = [True, False, True, True, False, True]
        for k in range(1, len(trials) + 1):
            subsets = list(itertools.combinations(trials, k))
            expected = sum(all(s) for s in subsets) / len(subsets)
            self.assertAlmostEqual(REL.pass_hat_k(len(trials), sum(trials), k), expected)

    def test_too_few_trials_has_no_estimate(self):
        self.assertIsNone(REL.pass_hat_k(2, 2, 3))

    def test_impossible_counts_raise(self):
        for n, c, k in ((3, 4, 1), (3, -1, 1), (3, 1, 0), (3.0, 1, 1)):
            with self.assertRaises(ValueError):
                REL.pass_hat_k(n, c, k)


class PassK(unittest.TestCase):
    def test_flaky_task_shows_below_the_mean_pass_rate(self):
        rows = rows_for({"steady": {"bare": [True] * 4, "harness": [True] * 4},
                         "flaky": {"bare": [True, False, True, True], "harness": [True] * 4}})
        result = REL.pass_k(rows)
        self.assertEqual(result["k"], 4)
        bare = result["arms"]["bare"]
        self.assertEqual((bare["tasks"], bare["all_passed"], bare["all_passed_rate"]), (2, 1, 0.5))
        self.assertAlmostEqual(bare["pass_1"], 0.875)
        self.assertAlmostEqual(bare["pass_hat_k"], 0.5)
        self.assertEqual(bare["per_task"]["flaky"], {"trials": 4, "passes": 3, "all_passed": False,
                                                     "pass_hat_k": 0.0})
        self.assertEqual(result["arms"]["harness"]["all_passed_rate"], 1.0)
        self.assertEqual(list(result["arms"]), ["bare", "harness"])

    def test_k_defaults_to_the_fewest_trials_and_can_be_set(self):
        rows = rows_for({"a": {"bare": [True, False, True]}, "b": {"bare": [True, True]}})
        self.assertEqual(REL.pass_k(rows)["k"], 2)
        cell = REL.pass_k(rows, k=3)["arms"]["bare"]
        self.assertEqual(cell["estimated_tasks"], 1)
        self.assertEqual(cell["per_task"]["b"]["pass_hat_k"], None)

    def test_an_errored_run_is_a_failed_trial(self):
        rows = rows_for({"a": {"harness": [True, None]}})
        cell = REL.pass_k(rows)["arms"]["harness"]
        self.assertEqual((cell["all_passed"], cell["per_task"]["a"]["passes"]), (0, 1))

    def test_malformed_rows_and_k_raise(self):
        with self.assertRaises(ValueError):
            REL.pass_k([{"task": "a", "arm": "bare", "rep": 1, "cost_usd": 1.0}])
        with self.assertRaises(ValueError):
            REL.pass_k(rows_for({"a": {"bare": [True]}}), k=0)


class JointCompliance(unittest.TestCase):
    ROWS = rows_for({"a": {"bare": [True, True], "harness": [True, True]}})

    def test_joint_rate_sits_below_every_per_rule_rate(self):
        detections = [
            det("a", "harness", 1, "x/one", 1), det("a", "harness", 1, "y/two", 0),
            det("a", "harness", 2, "x/one", 0), det("a", "harness", 2, "y/two", 2),
            det("a", "bare", 1, "x/one", 0), det("a", "bare", 1, "y/two", 0),
            det("a", "bare", 2, "x/one", 0), det("a", "bare", 2, "y/two", 0),
        ]
        result = REL.joint_compliance(self.ROWS, detections)
        harness = result["arms"]["harness"]
        self.assertEqual((harness["clean"], harness["hit"], harness["unknown"], harness["rate"]), (0, 2, 0, 0.0))
        self.assertEqual([r["rate"] for r in harness["per_rule"].values()], [0.5, 0.5])
        bare = result["arms"]["bare"]
        self.assertEqual((bare["clean"], bare["rate"]), (2, 1.0))
        low, high = bare["interval"]
        self.assertTrue(0 < low < 1.0 and high == 1.0)
        self.assertEqual(result["unmatched_runs"], 0)

    def test_an_unread_run_is_unknown_never_clean(self):
        detections = [det("a", "harness", 1, "x/one", 0), det("a", "harness", 1, "y/two", None),
                      det("a", "harness", 2, "x/one", None), det("a", "harness", 2, "y/two", 3)]
        harness = REL.joint_compliance(self.ROWS, detections)["arms"]["harness"]
        self.assertEqual((harness["clean"], harness["hit"], harness["unknown"]), (0, 1, 1))
        self.assertEqual(harness["rate"], 0.0)
        self.assertEqual(harness["per_rule"]["x/one"], {"rule": "r", "measured": 1, "fired": 0,
                                                        "unknown": 1, "rate": 1.0})

    def test_a_run_without_detection_rows_is_unknown(self):
        result = REL.joint_compliance(self.ROWS, [])
        self.assertEqual(result["arms"]["bare"]["unknown"], 2)
        self.assertIsNone(result["arms"]["bare"]["rate"])
        self.assertIsNone(result["arms"]["bare"]["interval"])

    def test_a_run_missing_one_detectors_row_is_unknown_never_clean(self):
        detections = [det("a", "bare", 1, "x/one", 0),
                      det("a", "bare", 2, "x/one", 0), det("a", "bare", 2, "y/two", 0)]
        bare = REL.joint_compliance(self.ROWS, detections)["arms"]["bare"]
        self.assertEqual((bare["clean"], bare["hit"], bare["unknown"]), (1, 0, 1))
        self.assertEqual(bare["per_rule"]["y/two"], {"rule": "r", "measured": 1, "fired": 0,
                                                     "unknown": 1, "rate": 1.0})
        self.assertEqual(REL.classify_run([det("a", "bare", 1, "x/one", 0)], {"x/one", "y/two"}), REL.UNKNOWN)
        self.assertEqual(REL.classify_run([det("a", "bare", 1, "x/one", 2)], {"x/one", "y/two"}), REL.HIT)

    def test_detections_for_no_saved_row_are_counted_and_left_out(self):
        detections = [det("a", "bare", 1, "x/one", 0), det("ghost", "bare", 1, "x/one", 5)]
        result = REL.joint_compliance(self.ROWS, detections)
        self.assertEqual(result["unmatched_runs"], 1)
        self.assertEqual(result["arms"]["bare"]["hit"], 0)


class JointSummary(unittest.TestCase):
    ROWS = rows_for({"a": {"bare": [True, True], "harness": [True, True]}})
    DETECTIONS = [det("a", arm, rep, "x/one", 1 if (arm, rep) == ("harness", 2) else 0)
                  for arm in ("bare", "harness") for rep in (1, 2)]

    def summary(self):
        return REL.joint_summary(REL.joint_compliance(self.ROWS, self.DETECTIONS))

    def test_summary_carries_rate_and_interval_without_per_rule_cells(self):
        summary = self.summary()
        self.assertEqual(set(summary["arms"]["harness"]), set(REL.SUMMARY_KEYS))
        self.assertEqual(summary["arms"]["harness"]["rate"], 0.5)
        self.assertEqual(REL.summary_problems(self.ROWS, json.loads(json.dumps(summary))), [])

    def test_summary_that_disagrees_with_the_rows_or_its_counts_is_refused(self):
        def changed(arm, **fields):
            summary = self.summary()
            summary["arms"][arm].update(fields)
            return REL.summary_problems(self.ROWS, summary)
        self.assertTrue(changed("bare", runs=3, unknown=1))
        self.assertTrue(changed("bare", rate=0.9))
        self.assertTrue(changed("harness", interval=[0.0, 1.0]))
        self.assertTrue(changed("harness", clean=-1, hit=3))
        missing = self.summary()
        del missing["arms"]["bare"]
        self.assertTrue(REL.summary_problems(self.ROWS, missing))
        self.assertTrue(REL.summary_problems(self.ROWS, {"arms": {}, "per_rule": {}}))


class Section(unittest.TestCase):
    def test_section_is_json_ready_and_rendered(self):
        rows = rows_for({"a": {"bare": [True, False], "harness": [True, True]}})
        detections = [det("a", arm, rep, "x/one", 0) for arm in ("bare", "harness") for rep in (1, 2)]
        section, text = REL.reliability_section(rows, detections)
        self.assertEqual(json.loads(json.dumps(section)), section)
        self.assertTrue(text.endswith("\n"))
        self.assertIn("Reliability: pass^2", text)
        self.assertIn("bare: every trial passed in 0 of 1 task(s)", text)
        self.assertIn("harness: 1.000", text)
        self.assertIn("x/one: 1.000 (0 fired of 2 measured, 0 unknown)", text)

    def test_without_detections_the_joint_reading_says_so(self):
        section, text = REL.reliability_section(rows_for({"a": {"bare": [True]}}))
        self.assertIsNone(section["joint"])
        self.assertIn("no detections recorded", text)

    def test_detections_beside_reads_the_runner_file_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            self.assertIsNone(REL.detections_beside(results))
            row = det("a", "bare", 1, "x/one", 0)
            (Path(tmp) / "detections.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertEqual(REL.detections_beside(results), [row])

    def test_unreadable_detections_leave_pass_k_and_report_runs_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            (Path(tmp) / "detections.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.object(REL.replay_detect, "read_jsonl", side_effect=PermissionError("denied")):
                detections = REL.detections_beside(results)
            self.assertEqual(detections, [])
            section, _ = REL.reliability_section(rows_for({"a": {"bare": [True, True]}}), detections)
            self.assertEqual(section["pass_k"]["arms"]["bare"]["all_passed"], 1)
            self.assertEqual(section["joint"]["arms"]["bare"]["unknown"], 2)


if __name__ == "__main__":
    unittest.main()
