"""The compare view's direction table takes a pack's declared metric directions (#990, #1143).

A row of a task declaring named metrics carries `metric_directions`; the comparison's `preferred`
table uses those directions for those names and falls back to `compare.PREFERRED` for the rest.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402,F401  puts lib/ on the import path
from harness_core.studio import compare  # noqa: E402


def side(*directions):
    return {"_rows": [{"task": "a", "arm": "harness", **({"metric_directions": d} if d else {})}
                      for d in directions]}


class MetricDirectionTests(unittest.TestCase):
    def test_rows_without_metrics_keep_the_static_table(self):
        self.assertEqual(compare.preferred(side(None), side(None)), compare.PREFERRED)

    def test_a_declared_metric_takes_the_packs_direction(self):
        declared = {"latency_ms": "lower", "accuracy": "higher"}
        got = compare.preferred(side(declared), side(declared))
        self.assertEqual(got, dict(compare.PREFERRED, latency_ms="lower", accuracy="higher"))

    def test_a_declared_direction_overrides_the_static_one_for_its_name(self):
        got = compare.preferred(side({"pass_rate": "lower"}), side({"pass_rate": "lower"}))
        self.assertEqual(got["pass_rate"], "lower")

    def test_conflicting_or_unknown_directions_give_no_direction(self):
        got = compare.preferred(side({"recall": "higher", "pass_rate": "lower"}),
                                side({"recall": "lower", "odd": "sideways", "pass_rate": "higher"}))
        self.assertNotIn("recall", got)
        self.assertNotIn("odd", got)
        self.assertNotIn("pass_rate", got)
        self.assertEqual(got["cost_per_passed"], "lower")


if __name__ == "__main__":
    unittest.main()
