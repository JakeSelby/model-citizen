# SPDX-License-Identifier: MIT
"""Sizing from a pilot at the pass-rate ceiling or floor (#1181): only with a pre-registered assumed
pass rate, which every report names. No test here calls a model."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_replay_power import POWER, pilot_rows

STATED = ("--tau2", "0.02", "--cv2", "0.1", "--tau2-pass", "0.005", "--long-tau2", "0.02")


def ceiling_rows():
    return [dict(r, passed=True) for r in pilot_rows()]


class AssumedPassRateTests(unittest.TestCase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = POWER.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def pilot(self, tmp):
        Path(tmp, "results.jsonl").write_text("\n".join(json.dumps(r) for r in ceiling_rows()) + "\n",
                                              encoding="utf-8")
        return tmp

    def test_a_ceiling_pilot_without_an_assumption_is_refused_and_names_the_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.main("--pilot", self.pilot(tmp))
        self.assertEqual(code, 2)
        self.assertIn("--assumed-pass-rate", err)

    def test_a_ceiling_pilot_sizes_with_the_assumption_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.main("--pilot", self.pilot(tmp), "--assumed-pass-rate", "0.7", "--mde", "0.15")
            self.assertEqual(code, 0)
            self.assertIn("assumption: pass rate 0.7000 is the pre-registered assumption", out)
            self.assertIn("the measured pass rate is 1.0000", out)
            self.assertIn("minimum detectable effect 0.15", out)
            self.assertRegex(out, r"design: k = \d+ task\(s\)")
            code, out, _ = self.main("--pilot", tmp, "--assumed-pass-rate", "0.7", "--json")
        result = json.loads(out)
        self.assertTrue(result["inputs"]["pass_rate_assumed"])
        self.assertEqual(result["inputs"]["pass_rate"], 0.7)
        self.assertEqual(result["inputs"]["measured_pass_rate"], 1.0)

    def test_the_assumed_rate_is_the_one_the_formula_uses(self):
        inputs = POWER.estimate(ceiling_rows())
        assumed = POWER.assume_pass_rate(inputs, 0.7)
        self.assertEqual(POWER.design_power(20, 10, 5, assumed),
                         POWER.design_power(20, 10, 5, dict(inputs, pass_rate=0.7)))
        lower = POWER.size(POWER.assume_pass_rate(inputs, 0.5))
        higher = POWER.size(POWER.assume_pass_rate(inputs, 0.9))
        self.assertGreater(lower["k"] * lower["m"], higher["k"] * higher["m"])

    def test_an_assumption_outside_zero_and_one_is_refused(self):
        with self.assertRaisesRegex(ValueError, "strictly between 0 and 1"):
            POWER.assume_pass_rate({"pass_rate": 1.0}, 1.0)
        code, out, err = self.main(*STATED + ("--assumed-pass-rate", "0"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("strictly between 0 and 1", err)

    def test_stated_inputs_take_the_assumption_in_place_of_a_pass_rate_but_not_both(self):
        code, out, _ = self.main(*STATED + ("--assumed-pass-rate", "0.8"))
        self.assertEqual(code, 0)
        self.assertIn("pre-registered assumption", out)
        stated = self.main(*STATED + ("--pass-rate", "0.8"))[1]
        self.assertEqual(stated.splitlines()[-2:], out.splitlines()[-2:])
        self.assertEqual(self.main(*STATED + ("--pass-rate", "0.8", "--assumed-pass-rate", "0.8"))[0], 2)

    def test_mde_is_the_effect(self):
        self.assertEqual(self.main(*STATED + ("--pass-rate", "0.8", "--mde", "0.12", "--json"))[1],
                         self.main(*STATED + ("--pass-rate", "0.8", "--effect", "0.12", "--json"))[1])
        self.assertEqual(self.main(*STATED + ("--pass-rate", "0.8", "--mde", "0.2"))[0], 2)


if __name__ == "__main__":
    unittest.main()
