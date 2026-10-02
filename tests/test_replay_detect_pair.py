"""`cost_bench.py detect` over a one-policy pair's stored set (#1112): the reference and treatment
streams are read beside the bare one, by `--raw` and by `--backfill`. The set under
tests/fixtures/replay-detect/pair-set reuses that directory's synthetic streams."""
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replay-detect" / "pair-set"
PAIR_ARMS = {"bare", "reference", "treatment"}


def pair_set(tmp):
    target = Path(tmp) / "evidence" / "pair-set"
    shutil.copytree(str(FIXTURE), str(target))
    return target


def arms_read(rows):
    """The arms with a row whose stream was read, a zero count included."""
    return set(r["arm"] for r in rows if r.get("count") is not None)


class PairDetectionTests(unittest.TestCase):
    def test_detect_raw_reads_every_arm_of_a_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pair_set(tmp) / "transcripts"
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(BENCH.main(["detect", "--raw", str(raw)]), 0)
            rows = BENCH.read_jsonl(raw / BENCH.DETECTIONS)
        self.assertEqual(arms_read(rows), PAIR_ARMS)
        self.assertIn("detected over 3 run(s), 0 unreadable", out.getvalue())
        treatment = dict((r["detector"], r) for r in rows if r["arm"] == "treatment")
        self.assertEqual(treatment["verification/no-verify"]["turns"], [4])

    def test_detect_backfill_reads_every_arm_of_a_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pair_set(tmp).parent
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(BENCH.main(["detect", "--backfill", str(root)]), 0)
            rows = BENCH.read_jsonl(root / "pair-set" / "results" / BENCH.DETECTIONS)
        self.assertEqual(arms_read(rows), PAIR_ARMS)
        self.assertIn("3 run(s), 0 without a readable stream", out.getvalue())
        reference = dict((r["detector"], r) for r in rows if r["arm"] == "reference")
        self.assertEqual(reference["verification/no-verify"]["tag"], "v1")

    def test_the_detect_arms_cover_the_replay_and_pair_arms_once(self):
        arms = BENCH.DETECT_ARMS
        self.assertEqual(set(arms), set(BENCH.ARMS) | set(BENCH.replay_pair.ARMS))
        self.assertEqual(len(arms), len(set(arms)))


if __name__ == "__main__":
    unittest.main()
