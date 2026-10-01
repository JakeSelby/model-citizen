"""The unit-by-economy grid on the replay runner (#797): `replay --design unit-economy --unit <kind>.<id>`
resolves and checks the grid against the tag before planning, states the effect and nominal cost
before the schedule, runs bare and four declared-selection cells in a seeded schedule whose leading
arm rotates, and stamps every row with its design and factor levels. Every launch is a fake; no test
builds an image or calls a model."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, options
from test_cost_bench_ablations import FIXTURE_TASKS, run_output, selected_record
from test_harness import REPO

UE = BENCH.unit_economy
ARMS = BENCH.arms
MANIFEST = REPO / "benchmarks" / "unit-economy.json"


class DryRunTests(unittest.TestCase):
    def run_main(self, *extra, unit="rules.secrets", reps="5"):
        out, err = io.StringIO(), io.StringIO()
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        argv = ["replay", "--tasks", str(FIXTURE_TASKS), "--design", "unit-economy", "--tag", head,
                "--model", "claude-test", "--exploratory", "--dry-run", "--reps", reps, "--schedule-seed", "11"]
        argv += ["--unit", unit] if unit else []
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                redirect_stdout(out), redirect_stderr(err):
            status = BENCH.main(argv + list(extra))
        return status, out.getvalue()

    def test_two_tasks_five_arms_five_trials_is_fifty_runs_each_arm_leading_once_per_task(self):
        status, text = self.run_main()
        self.assertEqual(status, 0)
        self.assertIn("50 run(s): 2 task(s) x 5 arm(s)", text)
        runs = [line.split() for line in text.splitlines() if line.startswith("    fixture-")]
        self.assertEqual(len(runs), 50)
        for task in ("fixture-one", "fixture-two"):
            leads = [arms[0][3] for arms in (
                [r for r in runs if r[0] == task and r[2] == str(rep)] for rep in range(1, 6))]
            self.assertEqual(sorted(leads), sorted(UE.ARM_NAMES))
        self.assertLess(text.index("minimum detectable effect, before any spend"), text.index("    fixture-one rep 1"))
        self.assertIn("schedule seed 11", text)
        for cell in UE.CELLS:
            self.assertIn("  cell %s (unit " % cell, text)
        self.assertIn("unit detectors secrets/git-add-secret-file, secrets/secret-in-write", text)

    def test_a_unit_the_design_cannot_separate_is_refused_before_anything_is_planned(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(unit="hooks.usage-feed")
        self.assertIn("refusing the grid before any spend", str(caught.exception))
        self.assertIn("member of the economy concern", str(caught.exception))

    def test_flags_the_grid_refuses(self):
        for kwargs, extra, expected in (({"unit": None}, (), "needs --unit"),
                                        ({}, ("--tag", "v0.1.0"), "exactly one --tag"),
                                        ({}, ("--stance-cost", "frugal"), "--design is refused with"),
                                        ({}, ("--ablations", str(REPO / "benchmarks" / "ablations.json")),
                                         "--design is refused with")):
            with self.subTest(expected=expected), self.assertRaises(SystemExit) as caught:
                self.run_main(*extra, **kwargs)
            self.assertIn(expected, str(caught.exception))

    def test_a_unit_without_the_design_is_refused(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        with self.assertRaises(SystemExit) as caught, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            BENCH.main(["replay", "--tasks", str(FIXTURE_TASKS), "--unit", "rules.secrets", "--tag", head,
                        "--model", "claude-test", "--exploratory", "--dry-run"])
        self.assertIn("needs --design unit-economy", str(caught.exception))


def design_options(tmp, reps=1):
    spec = UE.grid(UE.load(MANIFEST), "rules/secrets", BENCH.catalog.posture_module(REPO), REPO)
    manifest = UE.load(MANIFEST)
    opts = options(tmp, reps=reps)
    opts.update(arm_names=UE.ARM_NAMES, design=UE.stamp_of(manifest, spec), schedule_seed=7,
                ablation_selections=spec["selections"],
                arms=dict({"bare": opts["arms"]["bare"]},
                          **{cell: selected_record(spec["selections"][cell], cell) for cell in UE.CELLS}))
    return opts, spec


class FakeRunTests(unittest.TestCase):
    def test_every_row_carries_its_design_and_factor_levels_in_the_seeded_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts, spec = design_options(tmp)
            rows, stopped = BENCH.replay([TASK], opts, Launch([run_output() for _ in UE.ARM_NAMES]))
        self.assertFalse(stopped)
        self.assertEqual([r["arm"] for r in rows],
                         [arm for _, _, arm in BENCH.ablations.schedule([TASK], 1, UE.ARM_NAMES, 7)])
        for row in rows:
            self.assertEqual(row["design"]["name"], UE.DESIGN)
            self.assertEqual(row["unit"], "rules/secrets")
            self.assertEqual((row["cell_unit"], row["cell_economy"]), UE.FACTORS.get(row["arm"], (None, None)))
        by_arm = {r["arm"]: r for r in rows}
        self.assertIsNone(by_arm["bare"]["selection_sha256"])
        self.assertEqual(by_arm["both"]["selection_sha256"], UE.selection_sha256(spec["selections"]["both"]))
        self.assertEqual(len({by_arm[c]["profile_fingerprint"] for c in UE.CELLS}), 4)

    def test_a_cell_built_differently_beyond_its_selection_is_refused_before_any_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts, _spec = design_options(tmp)
            cell = opts["arms"]["economy"]
            cell["declaration"]["claude_code_version"] = "2.0"
            cell["declaration_sha256"] = ARMS.digest(cell["declaration"])
            with self.assertRaises(SystemExit) as caught:
                BENCH.admit_design_cells(opts)
        self.assertIn("declaration claude_code_version: base '1.0', unit '1.0', economy '2.0'", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
