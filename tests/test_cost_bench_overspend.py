"""An overspend on a set's last planned run completes the set; one with work left stops it; and a
refusal beside an earlier stop record still records a zero spend. See `replay()` in
scripts/cost_bench.py."""
import json
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH, TASK, Launch, options, result


class OverspendTests(unittest.TestCase):
    def run_set(self, tmp, costs, **over):
        results = Path(tmp) / "results.jsonl"
        launch = Launch([json.dumps(result(cost=cost)) for cost in costs])
        rows, stopped = BENCH.replay([TASK], options(tmp, **over), launch, out=results)
        spend = json.loads((Path(tmp) / BENCH.SPEND).read_text(encoding="utf-8"))
        return rows, stopped, spend, len(launch.calls)

    def test_an_overspent_last_run_completes_the_set_and_records_the_charge(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Two planned runs: 0.5, then 0.5 + the 2.0 run cap fits the 2.5 cap, and it reports 2.75.
            rows, stopped, spend, launched = self.run_set(tmp, [0.5, 2.75], reps=1, spend_cap=2.5)
            self.assertEqual((len(rows), stopped, launched), (2, False, 2))
            self.assertFalse((Path(tmp) / BENCH.STOP).exists())
            self.assertEqual((spend["charged_spend_usd"], spend["spend_cap_usd"], spend["stopped_at_cap"]),
                             (3.25, 2.5, False))

    def test_an_overspent_run_with_work_left_stops_the_set_at_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, stopped, spend, launched = self.run_set(tmp, [2.75], reps=2, spend_cap=2.5)
            self.assertEqual((len(rows), stopped, launched), (1, True, 1))
            self.assertEqual(BENCH.stop_beside(Path(tmp) / "results.jsonl"), BENCH.STOP_SPEND_CAP)
            self.assertEqual((spend["charged_spend_usd"], spend["stopped_at_cap"]), (2.75, True))


class StaleStopRefusalTests(unittest.TestCase):
    def test_a_refusal_beside_an_earlier_stop_record_records_zero_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            BENCH.write_stop(results, BENCH.STOP_SPEND_CAP)
            launch = Launch([])
            with self.assertRaisesRegex(SystemExit, "existing stop record"):
                BENCH.replay([TASK], options(tmp), launch, out=results)
            self.assertEqual(launch.calls, [])
            self.assertFalse(results.exists())
            spend = json.loads((Path(tmp) / BENCH.SPEND).read_text(encoding="utf-8"))
            self.assertEqual((spend["charged_spend_usd"], spend["stopped_at_cap"]), (0.0, False))

    def test_an_earlier_spend_record_beside_the_stop_record_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            BENCH.write_stop(results, BENCH.STOP_SPEND_CAP)
            (Path(tmp) / BENCH.SPEND).write_text('{"charged_spend_usd": 3.5}\n', encoding="utf-8")
            with self.assertRaises(SystemExit):
                BENCH.replay([TASK], options(tmp), Launch([]), out=results)
            self.assertEqual(json.loads((Path(tmp) / BENCH.SPEND).read_text(encoding="utf-8")),
                             {"charged_spend_usd": 3.5})


if __name__ == "__main__":
    unittest.main()
