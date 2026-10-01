"""The unit-by-economy two-by-two (#797): four cells from one base profile, the parity that refuses a
grid differing in anything but its two factors, and the report of the unit's effect, the economy
concern's and their interaction on Cost-of-Pass, pass rate and rule adherence, each from one
task-clustered paired bootstrap. Every row here is fixed; nothing launches and no model is called."""
import copy
import importlib.util
import io
import json
import random
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "lib"))
import unit_economy as UE  # noqa: E402
from harness_core import catalog  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "unit-economy"
MANIFEST = REPO / "benchmarks" / "unit-economy.json"
DESIGN = {"name": UE.DESIGN, "schema": 1, "manifest": "unit-economy", "manifest_sha256": "ab" * 32,
          "unit": "rules/secrets", "instruments": ["secrets/git-add-secret-file", "secrets/secret-in-write"],
          "economy": ["hooks/tier-agent-spawns", "hooks/usage-feed", "stances/cost", "stances/delegation"],
          "base_selection_sha256": "cd" * 32}
# Per arm: flat cost per trial, then per task (a, b) the trials that pass and the trials compliant.
PLAN = {"bare": (1.0, (2, 2), (1, 1)), "base": (1.0, (3, 2), (1, 1)), "unit": (0.9, (3, 3), (4, 4)),
        "economy": (0.6, (3, 2), (1, 1)), "both": (0.63, (4, 3), (3, 3))}


def row(task, arm, rep, cost, passed, adherence, **extra):
    with_unit, with_economy = UE.FACTORS.get(arm, (None, None))
    data = {"task": task, "arm": arm, "rep": rep, "cost_usd": cost, "passed": passed, "error": False,
            "outcome": "pass" if passed else "fail", "task_long": False, "model": "claude-test",
            "cli_version": "1.0", "harness_sha": "c" * 40, "effort": "medium", "schedule_seed": 7,
            "surface_drift_allowed": False, "design": DESIGN, "unit": DESIGN["unit"],
            "cell_unit": with_unit, "cell_economy": with_economy, "rule_adherence": adherence}
    data.update(extra)
    return data


def fixed_rows(plan=PLAN, trials=5):
    """The step-2 rows: two tasks, five trials per arm; trial r passes when r <= the task's count."""
    rows = []
    for arm, (cost, passes, compliant) in plan.items():
        for index, task in enumerate(("task-a", "task-b")):
            for rep in range(1, trials + 1):
                rows.append(row(task, arm, rep, cost, rep <= passes[index],
                                "compliant" if rep <= compliant[index] else "hit"))
    return rows


def figure(result, metric, contrast):
    return result["effects"][metric][contrast]


class FixedRowArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = UE.analyse(fixed_rows(), resamples=2000)

    def test_cost_of_pass_ratios_and_their_interaction(self):
        for contrast, expected in (("unit_alone", 0.75), ("unit_with_economy", 0.75), ("economy_alone", 0.6),
                                   ("interaction", 1.0)):
            self.assertAlmostEqual(figure(self.result, "cost_of_pass", contrast)["value"], expected, places=6)

    def test_pass_rate_differences_and_their_interaction(self):
        for contrast, expected in (("unit_alone", 0.1), ("unit_with_economy", 0.2), ("economy_alone", 0.0),
                                   ("interaction", 0.1)):
            self.assertAlmostEqual(figure(self.result, "pass_rate", contrast)["value"], expected, places=6)

    def test_adherence_differences_and_their_interaction(self):
        for contrast, expected in (("unit_alone", 0.6), ("unit_with_economy", 0.4), ("economy_alone", 0.0),
                                   ("interaction", -0.2)):
            self.assertAlmostEqual(figure(self.result, "rule_adherence", contrast)["value"], expected, places=6)

    def test_every_figure_carries_an_interval_around_its_value(self):
        for metric in UE.METRICS:
            for contrast in UE.EFFECTS:
                found = figure(self.result, metric, contrast)
                low, high = found["interval"]
                self.assertLessEqual(low, found["value"] + 1e-9, (metric, contrast))
                self.assertGreaterEqual(high, found["value"] - 1e-9, (metric, contrast))

    def test_cells_bare_and_the_default_primary(self):
        cells = self.result["cells"]
        self.assertEqual([cells[c]["passes"] for c in UE.CELLS], [5, 6, 5, 7])
        self.assertEqual(cells["unit"]["rule_adherence"]["compliant"], 8)
        self.assertEqual(self.result["bare"]["attempts"], 10)
        self.assertAlmostEqual(cells["base"]["cost_of_pass_ratio_to_bare"]["value"], 2.0 / 2.5, places=6)
        self.assertEqual(self.result["primary"], {"metric": "cost_of_pass", "contrast": "unit_alone",
                                                  "source": "default"})
        self.assertTrue(self.result["sm2_eligible"])
        self.assertEqual(self.result["estimand"], "intention to treat")
        self.assertEqual(self.result["schema"], 1)


class BootstrapTests(unittest.TestCase):
    def test_paired_draws_hold_a_constant_ratio_exactly(self):
        # Task b costs three times task a in every cell, and each task passes equally across cells.
        rows = []
        for arm, scale in (("bare", 1.0), ("base", 1.0), ("unit", 0.75), ("economy", 1.0), ("both", 0.75)):
            for task, cost in (("task-a", 1.0), ("task-b", 3.0)):
                for rep in range(1, 6):
                    rows.append(row(task, arm, rep, cost * scale, rep <= 2, "compliant"))
        result = UE.analyse(rows, resamples=500)
        self.assertEqual(figure(result, "cost_of_pass", "unit_alone")["interval"], [0.75, 0.75])

    def test_a_cell_passing_nothing_leaves_its_ratios_undefined_with_the_reason(self):
        plan = dict(PLAN, base=(1.0, (0, 0), (1, 1)))
        result = UE.analyse(fixed_rows(plan), resamples=300)
        found = figure(result, "cost_of_pass", "unit_alone")
        self.assertIsNone(found["value"])
        self.assertIn("base", found["undefined_reason"])
        self.assertIsNone(found["interval"][0])  # every draw is unit over an infinite base: a sentinel
        self.assertEqual(result["verdict"], "inconclusive")

    def test_an_indeterminate_resample_is_counted_and_only_widens(self):
        # Base and unit pass on task a only, so a draw of b twice is infinity over infinity.
        plan = dict(PLAN, base=(1.0, (3, 0), (1, 1)), unit=(0.9, (3, 0), (4, 4)))
        result = UE.analyse(fixed_rows(plan), resamples=400)
        self.assertGreater(result["indeterminate_resamples"]["cost_of_pass"]["unit_alone"], 0)
        self.assertIsNone(figure(result, "cost_of_pass", "unit_alone")["interval"][1])

    def test_fewer_than_five_trials_is_exploratory_and_gives_no_verdict(self):
        result = UE.analyse(fixed_rows(trials=4), resamples=200)
        self.assertFalse(result["sm2_eligible"])
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertIn("exploratory", result["limitation"])

    def test_a_seed_is_deterministic_whatever_the_row_order(self):
        rows = fixed_rows()
        shuffled = list(rows)
        random.Random(3).shuffle(shuffled)
        self.assertEqual(UE.analyse(rows, resamples=300), UE.analyse(shuffled, resamples=300))
        self.assertEqual(UE.analyse(rows, seed=1, resamples=300)["seed"], 1)

    def test_a_unit_without_detectors_is_unmeasured_never_zero(self):
        rows = [dict(r, rule_adherence="unmeasured") for r in fixed_rows()]
        result = UE.analyse(rows, resamples=200)
        self.assertEqual(result["effects"]["rule_adherence"], "unmeasured")
        self.assertEqual(result["cells"]["unit"]["rule_adherence"], "unmeasured")
        self.assertIn("rule_adherence: unmeasured", UE.render(dict(result, parity={"ok": True, "reasons": []})))

    def test_unknown_runs_are_counted_apart_and_not_scored(self):
        rows = fixed_rows()
        rows[0] = dict(rows[0], rule_adherence="unknown")  # bare, task a, rep 1
        result = UE.analyse(rows, resamples=200)
        self.assertEqual(result["bare"]["rule_adherence"]["unknown"], 1)
        self.assertEqual(result["bare"]["rule_adherence"]["scored"], 9)

    def test_malformed_rows_are_refused(self):
        rows = fixed_rows()
        cases = (([dict(r, rule_adherence="maybe") if i == 3 else r for i, r in enumerate(rows)], "rule_adherence"),
                 ([dict(r, rule_adherence="unmeasured") if i == 3 else r for i, r in enumerate(rows)], "unmeasured"),
                 ([r for r in rows if r["arm"] != "both"], "both"),
                 ([dict(r, design=dict(DESIGN, unit="rules/other")) if i == 0 else r for i, r in enumerate(rows)],
                  "design record"))
        for broken, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ValueError) as caught:
                UE.analyse(broken, resamples=50)
            self.assertIn(expected, str(caught.exception))

    def test_a_named_primary_is_used_and_a_metric_other_than_cost_is_refused(self):
        result = UE.analyse(fixed_rows(), primary={"metric": "cost_of_pass", "contrast": "economy_alone"},
                            resamples=200)
        self.assertEqual(result["primary"]["source"], "pre-registration")
        with self.assertRaises(ValueError):
            UE.analyse(fixed_rows(), primary={"metric": "pass_rate", "contrast": "unit_alone"}, resamples=50)


def resolved_grid():
    base = {"rules": {"secrets": "off", "verification": "off"}, "stances": {"cost": "off", "voice": "off"},
            "hooks": {"usage-feed": "off", "stop-gate": "on"}}
    out = {}
    for cell, (with_unit, with_economy) in UE.FACTORS.items():
        selection = copy.deepcopy(base)
        if with_unit:
            selection["rules"]["secrets"] = "on"
        if with_economy:
            selection["stances"]["cost"] = "balanced"
            selection["hooks"]["usage-feed"] = "on"
        out[cell] = selection
    return out


ECONOMY = ("stances/cost", "hooks/usage-feed")
INPUTS = {cell: {"harness_commit": "c" * 40, "model": "claude-test", "claude_code_version": "1.0"}
          for cell in UE.CELLS}


class ParityTests(unittest.TestCase):
    def test_a_clean_grid_is_admitted(self):
        self.assertEqual(UE.parity(resolved_grid(), "rules/secrets", ECONOMY, INPUTS), [])

    def test_an_extra_stance_change_is_refused_naming_it(self):
        grid = resolved_grid()
        grid["both"]["stances"]["voice"] = "concise"
        reasons = UE.parity(grid, "rules/secrets", ECONOMY, INPUTS)
        self.assertIn("economy and both also differ in stances/voice, which is neither factor", reasons)
        self.assertIn("base and both also differ in stances/voice, which is neither factor", reasons)

    def test_a_unit_inside_the_economy_set_is_refused(self):
        reasons = UE.parity(resolved_grid(), "stances/cost", ECONOMY, INPUTS)
        self.assertTrue(any("stances/cost is a member of the economy concern" in r for r in reasons), reasons)

    def test_a_second_commit_or_a_second_model_is_refused_naming_its_key(self):
        for key, value in (("harness_commit", "d" * 40), ("model", "claude-other")):
            inputs = copy.deepcopy(INPUTS)
            inputs["economy"][key] = value
            with self.subTest(key=key):
                reasons = UE.parity(resolved_grid(), "rules/secrets", ECONOMY, inputs)
                self.assertEqual(len(reasons), 1)
                self.assertTrue(reasons[0].startswith("the cells differ in %s:" % key), reasons)

    def test_a_factor_the_resolver_does_not_move_is_refused(self):
        grid = resolved_grid()
        grid["unit"]["rules"]["secrets"] = "off"
        reasons = UE.parity(grid, "rules/secrets", ECONOMY, INPUTS)
        self.assertIn("base and unit do not differ in rules/secrets, so the factor does not move it", reasons)

    def test_built_cells_with_different_declarations_or_one_profile_are_refused(self):
        decl = {"arm": "harness", "claude_code_version": "1.0", "components": [{"name": "selection", "version": "x"}],
                "selection": {"rules": {"secrets": "off"}}}
        records = {cell: {"declaration": dict(copy.deepcopy(decl), selection={"c": {cell: "on"}})}
                   for cell in UE.CELLS}
        prints = {cell: "fp-" + cell for cell in UE.CELLS}
        self.assertIsNone(UE.admit_cells(records, prints))
        records["both"]["declaration"]["claude_code_version"] = "2.0"
        with self.assertRaises(SystemExit) as caught:
            UE.admit_cells(records, dict(prints, both="fp-base"))
        self.assertIn("declaration claude_code_version", str(caught.exception))
        self.assertIn("two cells resolve to one profile", str(caught.exception))

    def test_rows_from_a_second_model_or_with_wrong_factor_levels_are_refused_after_the_run(self):
        rows = fixed_rows()
        self.assertEqual(UE.row_parity(rows), [])
        rows[5] = dict(rows[5], model="claude-other")
        rows[12] = dict(rows[12], cell_unit=True)
        reasons = UE.row_parity(rows)
        self.assertTrue(any("2 values of model" in r for r in reasons), reasons)
        self.assertTrue(any("row 13" in r for r in reasons), reasons)


class GridTests(unittest.TestCase):
    """The base and the four cells, derived from this checkout's catalog and resolved by its resolver."""

    @classmethod
    def setUpClass(cls):
        cls.posture = catalog.posture_module(REPO)
        cls.manifest = UE.load(MANIFEST)

    def grid(self, unit):
        return UE.grid(self.manifest, unit, self.posture, REPO)

    def test_the_shipped_manifest_loads_and_its_grid_for_a_rule_is_clean(self):
        spec = self.grid("rules/secrets")
        self.assertEqual(spec["errors"], [])
        base, unit, economy, both = (spec["selections"][c] for c in UE.CELLS)
        self.assertEqual(base["rules"]["secrets"], "off")
        self.assertEqual(unit["rules"]["secrets"], "on")
        self.assertEqual((base["stances"]["cost"], economy["stances"]["cost"]), ("off", "balanced"))
        self.assertEqual((base["stances"]["delegation"], both["stances"]["delegation"]), ("session-model", "tiered"))
        self.assertEqual(both["hooks"]["usage-feed"], "on")
        for core in self.posture.CORE_HOOKS:
            self.assertEqual(base["hooks"][core], "on")
        self.assertTrue(all(v == "off" for v in base["skills"].values()))
        self.assertEqual(spec["instruments"], ["secrets/git-add-secret-file", "secrets/secret-in-write"])

    def test_a_units_dependencies_are_on_in_every_cell(self):
        spec = self.grid("workflows.build")
        self.assertEqual(spec["errors"], [])
        self.assertEqual(spec["dependencies"], ["roles/builder"])
        for cell in UE.CELLS:
            self.assertEqual(spec["selections"][cell]["roles"]["builder"], "on")

    def test_units_the_design_cannot_separate_are_refused_by_name(self):
        for unit, expected in (("hooks/usage-feed", "member of the economy concern"),
                               ("hooks/stop-gate", "core hook"), ("stances/voice", "not a rule, skill"),
                               ("rules/no-such-rule", "not one the tag ships"),
                               ("skills/design-loop", "dependency cycle")):
            with self.subTest(unit=unit):
                self.assertTrue(any(expected in e for e in self.grid(unit)["errors"]), self.grid(unit)["errors"])

    def with_dependencies(self, graph):
        """The grid for rules/secrets with `graph` ({entry: [dependencies]}) as the declared dependencies."""
        real = self.posture

        class Posture:
            def __getattr__(self, name):
                return getattr(real, name)

            def manifests(self, config, root):
                found = real.manifests(config, root)
                declared = copy.deepcopy(found[0])
                for entry, dependencies in graph.items():
                    kind, name = entry.split("/", 1)
                    declared.setdefault(kind, {}).setdefault(name, {})["dependencies"] = dependencies
                return (declared,) + tuple(found[1:])

        return UE.grid(self.manifest, "rules/secrets", Posture(), REPO)

    def test_a_cycle_the_unit_reaches_is_refused_even_when_it_excludes_the_unit(self):
        spec = self.with_dependencies({"rules/secrets": ["rules/b"], "rules/b": ["rules/c"], "rules/c": ["rules/b"]})
        self.assertTrue(any("rules/b -> rules/c -> rules/b" in e for e in spec["errors"]), spec["errors"])

    def test_a_diamond_of_dependencies_is_accepted(self):
        spec = self.with_dependencies({"rules/secrets": ["rules/b", "rules/c"], "rules/b": ["rules/d"],
                                       "rules/c": ["rules/d"], "rules/d": []})
        self.assertEqual(spec["errors"], [])
        self.assertEqual(spec["dependencies"], ["rules/b", "rules/d", "rules/c"])

    def test_the_unit_flag_takes_the_config_set_form(self):
        self.assertEqual(UE.unit_entry("rules.secrets"), "rules/secrets")
        with self.assertRaises(SystemExit):
            UE.unit_entry("secrets")

    def test_malformed_manifests_are_refused_naming_each_problem(self):
        errors = UE.structure_errors({"schema": 2, "design": "x", "name": "N", "planning": {}, "economy": {
            "cost": {"off": "a", "on": "b"}, "stances/voice": {"off": "a", "on": "a"}}, "extra": 1})
        for expected in ("unknown key(s) extra", "schema must be 1", "design must be unit-economy",
                         "name must be", "planning must be", "'cost' is not a kind/unit", "stances/voice must be"):
            self.assertTrue(any(expected in e for e in errors), (expected, errors))


class ResultSchemaTests(unittest.TestCase):
    def test_the_committed_rows_are_the_fixed_rows(self):
        saved = [json.loads(line) for line in (FIXTURE / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(saved, fixed_rows())

    def test_the_committed_v1_result_regenerates_from_the_committed_rows(self):
        bench = load_bench()
        out = io.StringIO()
        with redirect_stdout(out):
            status = bench.main(["summarise", "--results", str(FIXTURE), "--json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out.getvalue()), json.loads((FIXTURE / "result.v1.json").read_text(encoding="utf-8")))

    def test_summarise_prints_every_effect_with_its_interval_and_the_verdict(self):
        bench = load_bench()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bench.main(["summarise", "--results", str(FIXTURE)]), 0)
        text = out.getvalue()
        for metric in UE.METRICS:
            self.assertIn("%s (" % metric, text)
        self.assertEqual(text.count("  interaction "), 3)
        self.assertIn("primary (default): unit_alone on cost_of_pass", text)
        self.assertIn("post-run parity: the cells differed only in their factors", text)


_BENCH = []


def load_bench():
    if not _BENCH:
        spec = importlib.util.spec_from_file_location("cost_bench_unit_economy", REPO / "scripts" / "cost_bench.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BENCH.append(module)
    return _BENCH[0]


if __name__ == "__main__":
    unittest.main()
