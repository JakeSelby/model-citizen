"""A stance-gated rule detector scores only the runs whose arm selected its stance (#1237): an
applicable run, an inapplicable one, the bare arm, and the all-rules-at-once rate over them. The
streams are synthetic; nothing here calls a model."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH

DETECT = BENCH.replay_detect
REL = BENCH.replay_reliability
MODULE = DETECT.load_detectors()
GATED = sorted(d.id for d in MODULE.DETECTORS.values() if d.gate is not None)
UNGATED = sorted(d.id for d in MODULE.DETECTORS.values() if d.gate is None)
LEAK = "voice/scaffold-leak"


def stream(final_text):
    """A one-call stream whose only reply is `final_text`."""
    messages = [
        {"type": "system", "subtype": "init", "session_id": "s", "model": "m"},
        {"type": "assistant", "parent_tool_use_id": None, "session_id": "s",
         "message": {"id": "msg_1", "model": "m", "role": "assistant",
                     "content": [{"type": "text", "text": final_text}]}},
        {"type": "result", "subtype": "success", "session_id": "s", "total_cost_usd": 0.1},
    ]
    return "\n".join(json.dumps(m) for m in messages) + "\n"


# A reply wearing a section label: what the concise voice forbids and the scannable one asks for.
LABELLED = stream("The fix is in.\n\nWhy: the cache was stale.")


def rows_of(row, text=LABELLED):
    """`row`'s detection rows over `text`, its arm's selection read as `detect` reads it."""
    return dict((r["detector"], r) for r in DETECT.run_rows(
        dict(row), text, BENCH.cli_messages, MODULE, stances=BENCH.recorded_stances(row)))


class ApplicabilityTests(unittest.TestCase):
    def test_a_concise_config_arm_is_scored_by_the_concise_only_detector(self):
        found = rows_of({"task": "t", "arm": "terse", "rep": 1,
                         "arm_config": {"name": "terse", "stances": {"voice": "concise"}}})
        self.assertEqual(found[LEAK]["count"], 1)
        self.assertNotIn(DETECT.NOT_APPLICABLE, found[LEAK])

    def test_the_harness_arms_default_voice_makes_the_concise_only_detector_not_applicable(self):
        found = rows_of({"task": "t", "arm": "harness", "rep": 1})
        self.assertIs(found[LEAK][DETECT.NOT_APPLICABLE], True)
        self.assertIsNone(found[LEAK]["count"])
        # Gated on any voice, so the default selection still scores it.
        self.assertEqual(found["voice/second-table"]["count"], 0)
        self.assertEqual(found["commits/missing-trailer"]["count"], 0)

    def test_the_bare_arm_has_no_stances_so_every_gated_detector_is_not_applicable(self):
        found = rows_of({"task": "t", "arm": "bare", "rep": 1})
        self.assertTrue(GATED and UNGATED)
        for ident in GATED:
            self.assertIs(found[ident].get(DETECT.NOT_APPLICABLE), True, ident)
            self.assertIsNone(found[ident]["count"], ident)
        for ident in UNGATED:
            self.assertEqual(found[ident]["count"], 0, ident)
            self.assertNotIn(DETECT.NOT_APPLICABLE, found[ident])

    def test_a_pair_arms_session_selection_decides_applicability(self):
        found = rows_of({"task": "t", "arm": "treatment", "rep": 1,
                         "selection": {"HARNESS_STANCE_VOICE": "concise"}})
        self.assertEqual(found[LEAK]["count"], 1)
        off = rows_of({"task": "t", "arm": "treatment", "rep": 1, "selection": {"HARNESS_STANCE_VOICE": "off"}})
        self.assertIs(off["voice/second-table"][DETECT.NOT_APPLICABLE], True)

    def test_an_arm_whose_selection_is_not_recorded_leaves_gated_detectors_unknown(self):
        found = rows_of({"task": "t", "arm": "no-hooks", "rep": 1})
        self.assertIsNone(found[LEAK]["count"])
        self.assertNotIn(DETECT.NOT_APPLICABLE, found[LEAK])
        self.assertTrue(found[LEAK]["error"].startswith("applicability unknown"))
        self.assertEqual(found["verification/no-verify"]["count"], 0)

    def test_an_unread_stream_stays_unreadable_and_a_gated_row_still_says_not_applicable(self):
        rows = DETECT.missing_rows({"task": "t", "arm": "bare", "rep": 1}, "no raw output",
                                   DETECT.ungated(MODULE), MODULE, {})
        self.assertTrue(all(r["error"] == "no raw output" and r["count"] is None for r in rows))
        self.assertEqual(DETECT.unreadable(rows, lambda r: r["rep"]), 1)
        self.assertEqual(set(r["detector"] for r in rows if r.get(DETECT.NOT_APPLICABLE)), set(GATED))

    def test_the_replay_resolves_each_arms_stances_from_the_tags_checkout(self):
        opts = {"profile_root": BENCH.ROOT, "ablation_selections": {"terse": {"stances": {"voice": "concise"}}}}
        self.assertEqual(BENCH.arm_stances("bare", opts), {})
        self.assertEqual(BENCH.arm_stances("harness", opts)["voice"], "scannable")
        self.assertEqual(BENCH.arm_stances("terse", opts)["voice"], "concise")
        pair = {"profile_root": BENCH.ROOT, "selections": {"treatment": {"HARNESS_STANCE_VOICE": "answer-card"}}}
        self.assertEqual(BENCH.arm_stances("treatment", pair)["voice"], "answer-card")

    def test_detect_raw_marks_the_bare_and_harness_arms_by_their_selections(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            for name in ("t-bare-1.json", "t-harness-1.json"):
                (raw / name).write_text(LABELLED, encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(BENCH.main(["detect", "--raw", str(raw)]), 0)
            rows = BENCH.read_jsonl(raw / BENCH.DETECTIONS)
        leak = dict((r["arm"], r) for r in rows if r["detector"] == LEAK)
        self.assertIs(leak["bare"][DETECT.NOT_APPLICABLE], True)
        self.assertIs(leak["harness"][DETECT.NOT_APPLICABLE], True)


class JointRateTests(unittest.TestCase):
    def test_a_not_applicable_row_is_never_a_hit_and_never_makes_a_run_unknown(self):
        runs = [{"task": "t", "arm": "harness", "rep": 1}, {"task": "t", "arm": "terse", "rep": 1,
                                                             "arm_config": {"stances": {"voice": "concise"}}}]
        detections = []
        for row in runs:
            detections.extend(rows_of(row).values())
        joint = REL.joint_compliance(runs, detections)["arms"]
        self.assertEqual((joint["harness"]["clean"], joint["harness"]["hit"], joint["harness"]["unknown"]),
                         (1, 0, 0))
        self.assertEqual(joint["harness"]["per_rule"][LEAK],
                         {"rule": "voice-and-format", "measured": 0, "fired": 0, "unknown": 0,
                          "not_applicable": 1, "rate": None})
        self.assertEqual((joint["terse"]["clean"], joint["terse"]["hit"]), (0, 1))
        self.assertIn("1 not applicable", "\n".join(REL.render({"pass_k": REL.pass_k(
            [dict(r, passed=True, error=False, cost_usd=1.0) for r in runs]), "joint": REL.joint_compliance(runs, detections)})))

    def test_a_run_with_only_not_applicable_rows_is_unknown_not_clean(self):
        row = {"task": "t", "arm": "bare", "rep": 1, "detector": LEAK, "rule": "voice-and-format",
               "count": None, "turns": None, DETECT.NOT_APPLICABLE: True}
        self.assertEqual(REL.classify_run([row], {LEAK: "voice-and-format"}), REL.UNKNOWN)

    def test_mechanisms_count_a_not_applicable_row_as_neither_measured_nor_an_error(self):
        detections = list(rows_of({"task": "t", "arm": "harness", "rep": 1}).values())
        cell = DETECT.mechanisms(detections)["t"]
        self.assertEqual((cell["runs"], cell["fired"], cell["errors"]), (1, {}, {}))


if __name__ == "__main__":
    unittest.main()
