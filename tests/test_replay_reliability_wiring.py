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

    def _bundle(self):
        bundle = test_evidence_bundle.EvidenceBundleTest("test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        bundle.setUp()
        self.addCleanup(bundle.tearDown)
        return bundle

    def _carry(self, bundle, summary):
        path = bundle.root / "artifacts" / "joint.json"
        bundle._write_json(path, summary)
        index = bundle._index()
        index["artifacts"]["joint_compliance"] = bundle._ref("artifacts/joint.json")
        bundle._save_index(index)

    def _summary(self, bundle):
        rows = [json.loads(line) for line in (bundle.root / bundle._index()["artifacts"]["rows"]["path"])
                .read_text().splitlines() if line.strip()]
        detections = [det(row["task"], row["arm"], row["rep"], "d1",
                          1 if (row["arm"], row["rep"]) == ("harness", 1) else 0) for row in rows]
        return REL.joint_summary(REL.joint_compliance(rows, detections))

    def test_a_carried_joint_summary_is_checked_and_reported(self):
        bundle = self._bundle()
        summary = self._summary(bundle)
        self._carry(bundle, summary)
        verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
        self.assertTrue(verified["ok"], verified["errors"])
        joint = verified["derived"]["reliability"]["joint"]
        self.assertEqual(joint, summary)
        self.assertEqual((joint["arms"]["harness"]["clean"], joint["arms"]["harness"]["hit"]), (8, 2))
        self.assertEqual(joint["arms"]["harness"]["rate"], 0.8)

    def test_a_carried_joint_summary_off_its_rows_fails_item_five(self):
        bundle = self._bundle()
        summary = self._summary(bundle)
        summary["arms"]["bare"]["runs"] += 1
        summary["arms"]["bare"]["unknown"] += 1
        self._carry(bundle, summary)
        verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
        self.assertFalse(verified["ok"])
        self.assertTrue(any("joint summary bare" in error for error in verified["errors"]), verified["errors"])
        self.assertIsNone(verified["derived"]["reliability"]["joint"])

    def test_a_tampered_joint_summary_fails_its_digest(self):
        bundle = self._bundle()
        self._carry(bundle, self._summary(bundle))
        (bundle.root / "artifacts" / "joint.json").write_text("{}\n")
        verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
        self.assertFalse(verified["ok"])
        self.assertIn("joint_compliance sha256 does not match", verified["errors"][0])


if __name__ == "__main__":
    unittest.main()
