# SPDX-License-Identifier: MIT
"""Equivalence verdicts (#1181): a pre-registered margin per metric, read against the paired,
task-clustered interval the analysis already reports. No test here calls a model."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("equivalence", REPO / "scripts" / "equivalence.py")
EQ = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EQ)

TEMPLATE = (REPO / "docs" / "pre-registration-template.md").read_text(encoding="utf-8")
PLAN = """## Decision rule

Both conditions.

## Equivalence margins

- **Cost-of-Pass ratio:** 0.85 to 1.1765 (a 15% saving)
- **Pass-rate difference:** -0.125 to 0.125
- **Rule adherence:** -0.1 to 0.1
- **Tool calls:** none, no hypothesis on them
"""


def rows(harness_cost, harness_passes, tasks=6, reps=5):
    """Two arms over `tasks` tasks; bare costs 1.0 a trial and passes every trial."""
    out = []
    for t in range(tasks):
        for rep in range(1, reps + 1):
            out.append({"task": "t%d" % t, "arm": "bare", "rep": rep, "cost_usd": 1.0, "passed": True})
            out.append({"task": "t%d" % t, "arm": "harness", "rep": rep,
                        "cost_usd": harness_cost * (1 + 0.02 * ((rep + t) % 3)),
                        "passed": rep <= harness_passes})
    return out


class VerdictTests(unittest.TestCase):
    def test_inside_outside_and_crossing(self):
        margin = (0.85, 1.1765)
        self.assertEqual(EQ.verdict([0.9, 1.1], margin)[0], EQ.EQUIVALENT)
        self.assertEqual(EQ.verdict([1.34, 1.61], margin)[0], EQ.NOT_EQUIVALENT)
        self.assertEqual(EQ.verdict([0.5, 0.85], margin)[0], EQ.NOT_EQUIVALENT)
        self.assertEqual(EQ.verdict([0.9, 1.3], margin)[0], EQ.INCONCLUSIVE)
        self.assertEqual(EQ.verdict([0.7, 1.3], margin)[0], EQ.INCONCLUSIVE)

    def test_a_bound_on_the_margin_is_not_inside_it(self):
        self.assertEqual(EQ.verdict([0.85, 1.0], (0.85, 1.1765))[0], EQ.INCONCLUSIVE)
        self.assertEqual(EQ.verdict([-0.2, -0.125], (-0.125, 0.125))[0], EQ.NOT_EQUIVALENT)

    def test_an_undefined_interval_is_inconclusive(self):
        for interval in (None, [], [None, 1.0], [0.9, None]):
            self.assertEqual(EQ.verdict(interval, (0.85, 1.1765))[0], EQ.INCONCLUSIVE)

    def test_a_margin_must_bracket_no_effect(self):
        with self.assertRaisesRegex(ValueError, "must contain 1"):
            EQ.check_margin(EQ.RATIO, (1.05, 1.2))
        with self.assertRaisesRegex(ValueError, "must contain 0"):
            EQ.check_margin(EQ.DIFFERENCE, (0.01, 0.2))
        with self.assertRaisesRegex(ValueError, "lower bound must be below"):
            EQ.check_margin("Rule adherence", (0.1, -0.1))
        with self.assertRaisesRegex(ValueError, "must be positive"):
            EQ.check_margin(EQ.RATIO, (-0.5, 1.2))

    def test_a_metric_with_no_interval_is_inconclusive_not_dropped(self):
        assessed = EQ.assess({EQ.RATIO: [0.9, 1.1]}, {EQ.RATIO: (0.85, 1.1765), "Rule adherence": (-0.1, 0.1)})
        self.assertEqual(assessed[EQ.RATIO]["verdict"], EQ.EQUIVALENT)
        self.assertEqual(assessed["Rule adherence"]["verdict"], EQ.INCONCLUSIVE)
        self.assertIn("no interval", assessed["Rule adherence"]["reason"])

    def test_a_behaviour_score_is_judged_from_the_callers_interval(self):
        assessed = EQ.assess({"Rule adherence": [-0.04, 0.06]}, {"Rule adherence": (-0.1, 0.1)})
        self.assertEqual(assessed["Rule adherence"]["verdict"], EQ.EQUIVALENT)


class PlanTests(unittest.TestCase):
    def test_margins_are_read_and_none_registers_nothing(self):
        margins = EQ.margins_from_plan(PLAN)
        self.assertEqual(margins, {EQ.RATIO: (0.85, 1.1765), EQ.DIFFERENCE: (-0.125, 0.125),
                                   "Rule adherence": (-0.1, 0.1)})

    def test_the_templates_defaults_parse_once_the_behaviour_line_is_filled(self):
        section = TEMPLATE.split("## Equivalence margins", 1)[1].split("\n## ", 1)[0]
        start = section.index("- **Behaviour scores:**")
        filled = "## Equivalence margins" + section[:start] + "- **Behaviour scores:** none\n"
        self.assertEqual(EQ.margins_from_plan(filled), {EQ.RATIO: (0.85, 1.1765), EQ.DIFFERENCE: (-0.125, 0.125)})
        with self.assertRaisesRegex(ValueError, "Behaviour scores margin is unfilled"):
            EQ.margins_from_plan("## Equivalence margins" + section)

    def test_a_missing_section_a_malformed_margin_or_no_margin_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no 'Equivalence margins' section"):
            EQ.margins_from_plan("## Decision rule\n\nx\n")
        with self.assertRaisesRegex(ValueError, "is not '<lower> to <upper>'"):
            EQ.margins_from_plan("## Equivalence margins\n\n- **Cost-of-Pass ratio:** about 15%\n")
        with self.assertRaisesRegex(ValueError, "registers no equivalence margin"):
            EQ.margins_from_plan("## Equivalence margins\n\n- **Cost-of-Pass ratio:** none\n")


class AnalysedTests(unittest.TestCase):
    def test_the_analysis_intervals_give_each_verdict(self):
        import replay_stats
        margins = EQ.margins_from_plan(PLAN)
        same = EQ.assess(EQ.intervals_of(replay_stats.analyse(rows(1.0, 5), resamples=200)), margins)
        self.assertEqual(same[EQ.RATIO]["verdict"], EQ.EQUIVALENT)
        self.assertEqual(same[EQ.DIFFERENCE]["verdict"], EQ.EQUIVALENT)
        self.assertEqual(same["Rule adherence"]["verdict"], EQ.INCONCLUSIVE)
        dearer = EQ.assess(EQ.intervals_of(replay_stats.analyse(rows(1.5, 5), resamples=200)), margins)
        self.assertEqual(dearer[EQ.RATIO]["verdict"], EQ.NOT_EQUIVALENT)
        worse = EQ.assess(EQ.intervals_of(replay_stats.analyse(rows(1.0, 3), resamples=200)), margins)
        self.assertEqual(worse[EQ.DIFFERENCE]["verdict"], EQ.NOT_EQUIVALENT)


class CommandTests(unittest.TestCase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = EQ.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_rows_and_a_plan_print_the_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows(1.5, 5)) + "\n",
                                                  encoding="utf-8")
            Path(tmp, "plan.md").write_text(PLAN, encoding="utf-8")
            code, out, _ = self.main(tmp, "--plan", str(Path(tmp, "plan.md")), "--resamples", "200")
            self.assertEqual(code, 0)
            self.assertIn("Cost-of-Pass ratio: not equivalent", out)
            self.assertIn("Pass-rate difference: equivalent", out)
            code, out, _ = self.main(tmp, "--plan", str(Path(tmp, "plan.md")), "--resamples", "200", "--json")
            self.assertEqual(json.loads(out)[EQ.RATIO]["margin"], [0.85, 1.1765])

    def test_a_plan_without_margins_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "plan.md").write_text("## Decision rule\n\nx\n", encoding="utf-8")
            code, _, err = self.main(tmp, "--plan", str(Path(tmp, "plan.md")))
        self.assertEqual(code, 2)
        self.assertIn("no 'Equivalence margins' section", err)


if __name__ == "__main__":
    unittest.main()
