"""The power command (#796) sizes a set for SM-2 from pilot rows or stated variances; no model call."""
import contextlib
import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_power", REPO / "scripts" / "replay_power.py")
POWER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POWER)

INPUTS = {"tau2": 0.02, "cv2": 0.1, "pass_rate": 0.8, "tau2_pass": 0.005, "long_tau2": None}


def pilot_rows(tasks=6, reps=5, long_tasks=3):
    """Deterministic pilot rows: the harness a little cheaper, both arms passing four in five."""
    rows = []
    for t in range(tasks):
        for arm, scale in (("bare", 1.0), ("harness", 0.9)):
            for rep in range(1, reps + 1):
                cost = scale * (1.0 + 0.1 * t) * (1.0 + 0.05 * ((rep * 7 + t) % 5 - 2))
                rows.append({"task": "t%d" % t, "arm": arm, "rep": rep, "cost_usd": round(cost, 6),
                             "passed": (rep + t + (arm == "harness")) % 5 != 0, "error": False,
                             "task_long": t < long_tasks})
    return rows


class NormalTests(unittest.TestCase):
    def test_the_quantile_inverts_the_cdf(self):
        self.assertAlmostEqual(POWER.z_of(0.975), 1.959964, places=5)
        self.assertAlmostEqual(POWER.phi(POWER.z_of(0.8)), 0.8, places=9)


class SizingTests(unittest.TestCase):
    def test_the_design_meets_the_target_and_one_task_fewer_does_not(self):
        design = POWER.size(INPUTS)
        self.assertGreaterEqual(design["m"], 5)
        self.assertGreaterEqual(design["power"]["claim"], 0.8)
        self.assertGreaterEqual(design["power"]["decision"], 0.8)
        self.assertLessEqual(design["n"], design["k"])
        fewer = POWER.design_power(design["k"] - 1, min(design["n"], design["k"] - 1), design["m"], INPUTS)
        self.assertLess(min(fewer["claim"], fewer["decision"]), 0.8)
        fewer_long = POWER.design_power(design["k"], design["n"] - 1, design["m"], INPUTS)
        self.assertLess(fewer_long["claim"], 0.8)
        self.assertLessEqual(design["mde"], 0.15)

    def test_the_formula_matches_a_hand_computation(self):
        se = math.sqrt((0.02 + 2 * (0.1 + 0.2 / 0.8) / 5) / 10)
        expected = POWER.phi(-math.log(0.85) / se - POWER.z_of(0.975))
        self.assertAlmostEqual(POWER.design_power(10, 4, 5, INPUTS)["ratio"], expected, places=12)
        se_diff = math.sqrt((0.005 + 2 * 0.8 * 0.2 / 5) / 10)
        self.assertAlmostEqual(POWER.design_power(10, 4, 5, INPUTS)["pass"],
                               POWER.phi(0.125 / se_diff - POWER.z_of(0.975)), places=12)

    def test_more_variance_needs_more_tasks_and_a_smaller_effect_needs_more_still(self):
        base = POWER.size(INPUTS)
        noisier = POWER.size(dict(INPUTS, tau2=0.08))
        smaller = POWER.size(INPUTS, effect=0.10)
        self.assertGreater(noisier["k"] * noisier["m"], base["k"] * base["m"])
        self.assertGreater(smaller["k"] * smaller["m"], base["k"] * base["m"])

    def test_the_long_tasks_own_variance_moves_only_the_long_subset(self):
        plain = POWER.design_power(20, 6, 5, INPUTS)
        noisy = POWER.design_power(20, 6, 5, dict(INPUTS, long_tau2=0.2))
        self.assertEqual(plain["decision"], noisy["decision"])
        self.assertLess(noisy["long"], plain["long"])

    def test_an_effect_above_fifteen_percent_and_degenerate_inputs_are_refused(self):
        with self.assertRaisesRegex(ValueError, "caps the minimum detectable effect at 15%"):
            POWER.size(INPUTS, effect=0.2)
        with self.assertRaisesRegex(ValueError, "pass rate"):
            POWER.size(dict(INPUTS, pass_rate=1.0))
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            POWER.size(dict(INPUTS, tau2=-0.1))

    def test_no_design_within_the_bounds_is_none(self):
        self.assertIsNone(POWER.size(INPUTS, max_tasks=3, max_reps=5))


class PilotTests(unittest.TestCase):
    def test_pilot_rows_give_every_input_and_the_icc(self):
        inputs = POWER.estimate(pilot_rows())
        self.assertEqual((inputs["tasks"], inputs["long_tasks"], inputs["reps"]), (6, 3, 5))
        self.assertAlmostEqual(inputs["pass_rate"], 0.8)
        self.assertGreater(inputs["cv2"], 0)
        self.assertGreaterEqual(inputs["tau2"], 0)
        self.assertIsNotNone(inputs["long_tau2"])
        self.assertEqual(sorted(inputs["icc_pass"]), ["bare", "harness"])

    def test_few_long_tasks_borrow_the_whole_set_s_variance(self):
        self.assertIsNone(POWER.estimate(pilot_rows(long_tasks=2))["long_tau2"])

    def test_a_task_with_no_pass_in_an_arm_is_counted_not_used(self):
        rows = pilot_rows()
        for row in rows:
            if row["task"] == "t0" and row["arm"] == "harness":
                row["passed"] = False
        self.assertEqual(POWER.estimate(rows)["unusable_tasks"], 1)

    def test_an_unpriced_row_or_a_one_task_pilot_is_refused(self):
        rows = pilot_rows()
        rows[0]["cost_usd"] = None
        with self.assertRaisesRegex(ValueError, "no cost"):
            POWER.estimate(rows)
        with self.assertRaisesRegex(ValueError, "at least two tasks"):
            POWER.estimate([r for r in pilot_rows() if r["task"] == "t0"])


class CommandTests(unittest.TestCase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = POWER.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_pilot_directory_prints_the_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "results.jsonl").write_text("\n".join(json.dumps(r) for r in pilot_rows()) + "\n",
                                                  encoding="utf-8")
            code, out, _ = self.main("--pilot", tmp)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"design: k = \d+ task\(s\), n = \d+ long, m = \d+ trial\(s\) per task and arm")
        self.assertIn("pilot: 6 task(s), 3 long", out)

    def test_stated_inputs_and_a_set_that_falls_short(self):
        code, out, _ = self.main("--tau2", "0.02", "--cv2", "0.1", "--pass-rate", "0.8", "--tau2-pass",
                                 "0.005", "--have", "7", "3", "5", "--json")
        self.assertEqual(code, 1)  # seven tasks are far short at these variances
        result = json.loads(out)
        self.assertFalse(result["have"]["meets"])
        self.assertGreater(result["design"]["k"], 7)
        design = result["design"]
        code, _, _ = self.main("--tau2", "0.02", "--cv2", "0.1", "--pass-rate", "0.8", "--tau2-pass", "0.005",
                               "--have", str(design["k"]), str(design["n"]), str(design["m"]))
        self.assertEqual(code, 0)

    def test_missing_or_mixed_inputs_exit_2(self):
        self.assertEqual(self.main("--tau2", "0.1")[0], 2)
        self.assertEqual(self.main("--pilot", "x", "--tau2", "0.1")[0], 2)

    def test_a_missing_pilot_path_exits_2(self):
        self.assertEqual(self.main("--pilot", "/nonexistent/pilot-results")[0], 2)
        self.assertEqual(self.main("--tau2", "0.02", "--cv2", "0.1", "--pass-rate", "0.8", "--tau2-pass",
                                   "0.005", "--effect", "0.3")[0], 2)


if __name__ == "__main__":
    unittest.main()
