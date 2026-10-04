"""pass^k and the all-rules-at-once rate (#1180) reach `cost_bench summarise` and the evidence
bundle's derived result; synthetic rows only, no agent and no model call."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH
import test_evidence_bundle  # a module import, so discovery does not collect its tests twice
from test_replay_reliability import REL, det, rows_for

SPEC = {"t%d" % t: {"bare": [True, t != 1], "harness": [True, True]} for t in range(4)}


def summarise(results, as_json):
    out = io.StringIO()
    args = ["summarise", "--results", str(results), "--seed", "3", "--resamples", "50"]
    with redirect_stdout(out):
        code = BENCH.main(args + (["--json"] if as_json else []))
    return code, out.getvalue()


class SummariseCarriesReliability(unittest.TestCase):
    def test_json_and_text_carry_pass_k_and_the_joint_rate_from_the_detections_beside(self):
        rows = rows_for(SPEC)
        detections = [det(row["task"], row["arm"], row["rep"], "d1", 1 if row["task"] == "t2" else 0)
                      for row in rows]
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            BENCH.write_jsonl(Path(tmp) / BENCH.replay_detect.DETECTIONS, detections)
            code, printed = summarise(tmp, True)
            self.assertEqual(code, 0)
            code, text = summarise(tmp, False)
            self.assertEqual(code, 0)
        reliability = json.loads(printed)["reliability"]
        self.assertEqual(reliability, REL.reliability_section(rows, detections)[0])
        self.assertEqual(reliability["pass_k"]["arms"]["bare"]["all_passed"], 3)
        self.assertEqual(reliability["joint"]["arms"]["harness"]["clean"], 6)
        self.assertEqual(reliability["joint"]["arms"]["harness"]["hit"], 2)
        self.assertIn(REL.reliability_section(rows, detections)[1], text)
        self.assertIn("Reliability: pass^2 per task and arm", text)
        self.assertIn("All rules at once:", text)

    def test_a_set_without_detections_reports_pass_k_and_no_joint_rate(self):
        rows = rows_for(SPEC)
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            printed = json.loads(summarise(tmp, True)[1])
            text = summarise(tmp, False)[1]
        self.assertIsNone(printed["reliability"]["joint"])
        self.assertEqual(printed["reliability"]["pass_k"], REL.pass_k(rows))
        self.assertIn("All rules at once: no detections recorded for this set", text)


class EvidenceBundleCarriesReliability(unittest.TestCase):
    def test_the_derived_result_holds_pass_k_from_the_bundled_rows(self):
        bundle = test_evidence_bundle.EvidenceBundleTest("test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        bundle.setUp()
        self.addCleanup(bundle.tearDown)
        verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
        self.assertTrue(verified["ok"], verified["errors"])
        reliability = verified["derived"]["reliability"]
        self.assertIsNone(reliability["joint"])
        self.assertEqual(set(reliability["pass_k"]["arms"]), {"bare", "harness"})
        self.assertEqual(reliability["pass_k"]["arms"]["bare"]["tasks"],
                         len(verified["derived"]["per_task"]))


if __name__ == "__main__":
    unittest.main()
