"""A unit's rule adherence read from a replay run's saved stream (#797): its manifest's `detector:`
instruments over the stream `--raw` kept. A firing is `hit`, a clean read `compliant`, an unreadable
stream `unknown` and never `hit`, and a unit with no detector `unmeasured`, never zero. No model call."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
import rule_adherence as RA  # noqa: E402
import replay_detect  # noqa: E402
from test_unit_economy import load_bench  # noqa: E402

SECRETS = ("secrets/git-add-secret-file", "secrets/secret-in-write")


def stream(command):
    """A one-call `stream-json` whose only tool use is a Bash command."""
    call = {"type": "assistant", "parent_tool_use_id": None,
            "message": {"model": "claude-test", "role": "assistant", "usage": {"input_tokens": 5},
                        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}}
    done = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "num_turns": 1,
            "result": "done", "usage": {"input_tokens": 5, "output_tokens": 1}}
    return "".join(json.dumps(m) + "\n" for m in (call, done))


class ReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = replay_detect.load_detectors()
        cls.reader = staticmethod(load_bench().cli_messages)

    def read(self, text, ids=SECRETS):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(text, encoding="utf-8")
            return RA.read(path, ids, self.reader, self.module)

    def test_a_staged_secret_file_is_a_hit(self):
        found = self.read(stream("git add .env"))
        self.assertEqual(found["value"], "hit")
        self.assertEqual(found["hits"], ["secrets/git-add-secret-file"])

    def test_a_clean_run_is_compliant(self):
        self.assertEqual(self.read(stream("git status"))["value"], "compliant")

    def test_a_stream_with_no_model_call_is_unknown_never_a_hit(self):
        done = json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": ""}) + "\n"
        self.assertEqual(self.read(done)["value"], "unknown")

    def test_a_missing_file_is_unknown(self):
        found = RA.read(Path(tempfile.gettempdir()) / "no-such-run-797.json", SECRETS, self.reader, self.module)
        self.assertEqual(found["value"], "unknown")

    def test_no_detector_instrument_is_unmeasured_and_unknown_ids_are_named(self):
        self.assertEqual(self.read(stream("git add .env"), ())["value"], "unmeasured")
        found = self.read(stream("git status"), ("secrets/no-such-detector",))
        self.assertEqual((found["value"], found["unsupported"]), ("unmeasured", ["secrets/no-such-detector"]))


class ClassifyTests(unittest.TestCase):
    def test_a_hit_wins_over_an_unread_detector_and_an_unread_one_is_unknown(self):
        rows = [{"detector": SECRETS[0], "count": 1}, {"detector": SECRETS[1], "count": None, "error": "x"}]
        self.assertEqual(RA.classify(rows, SECRETS, SECRETS)["value"], "hit")
        self.assertEqual(RA.classify(rows[1:], SECRETS, SECRETS)["value"], "unknown")
        self.assertEqual(RA.classify([], SECRETS, SECRETS)["value"], "unknown")

    def test_stamp_matches_each_row_to_its_own_runs_detections(self):
        rows = [{"task": "t", "arm": "unit", "rep": 1}, {"task": "t", "arm": "base", "rep": 1},
                {"task": "t", "arm": "both", "rep": 1}]
        detections = [{"task": "t", "arm": "unit", "rep": 1, "detector": d, "count": 0} for d in SECRETS] + \
                     [{"task": "t", "arm": "base", "rep": 1, "detector": SECRETS[0], "count": 2},
                      {"task": "t", "arm": "base", "rep": 1, "detector": SECRETS[1], "count": 0}]
        stamped = RA.stamp(rows, detections, SECRETS, SECRETS, "ff" * 32)
        self.assertEqual([r["rule_adherence"] for r in stamped], ["compliant", "hit", "unknown"])
        self.assertEqual(stamped[1]["rule_adherence_hits"], [SECRETS[0]])
        self.assertEqual(stamped[0]["rule_detectors_sha256"], "ff" * 32)

    def test_every_manifest_detector_is_readable_from_a_saved_stream(self):
        found = RA.coverage(REPO, RA.registry_ids())
        self.assertEqual(found["unsupported"], [])
        self.assertIn("secrets/git-add-secret-file", found["readable"])
        self.assertEqual(RA.detector_ids(RA.unit_manifest(REPO, "rules/secrets")), SECRETS)
        self.assertIsNone(RA.unit_manifest(REPO, "rules/no-such-rule"))


if __name__ == "__main__":
    unittest.main()
