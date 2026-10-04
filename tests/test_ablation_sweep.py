"""The extended layer sweep (#1184): a removal arm for every layer that costs tokens or turns, each
mapped to its own evaluator-pack tasks and the outcome subset, listed and priced before any spend,
and judged by the pre-registered justification rule. Fixed rows and a local pack only; nothing here
builds an image or calls a model."""
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH
from test_harness import REPO

ABL = BENCH.ablations
ARMS = BENCH.arms
SHIPPED = REPO / "benchmarks" / "ablations.json"
FIXTURE_TASKS = REPO / "tests" / "fixtures" / "ablation-tasks.json"
POSTURE = importlib.util.spec_from_file_location("posture_sweep", REPO / "policy" / "hooks" / "posture.py")
POSTURE_MODULE = importlib.util.module_from_spec(POSTURE)
POSTURE.loader.exec_module(POSTURE_MODULE)
RESAMPLES = 200
OUTCOME = {"tasks": ["o1", "o2", "o3"], "metric": "Pass-rate difference", "bounds": [-0.125, 0.125]}


def sweep(*arms, outcome=None):
    data = {"schema": 2, "name": "sweep", "planning": {"cv": 0.25, "source": "assumed"},
            "outcome": outcome or OUTCOME, "arms": list(arms)}
    return dict(data, sha256="cd" * 32)


def arm(ident="no-secrets", removes="rules/secrets", tasks=("own1", "own2"), scores=(), **extra):
    return dict({"id": ident, "removes": removes, "tasks": list(tasks),
                 "scores": [{"metric": m, "bounds": b} for m, b in scores]}, **extra)


def rows_for(name, tasks, trials=5, cost=1.0, passed=True, metrics=None):
    """`trials` rows per task for one arm; `metrics` maps a metric to (direction, value)."""
    out = []
    for task in tasks:
        for rep in range(1, trials + 1):
            row = {"task": task, "arm": name, "rep": rep, "cost_usd": cost, "error": False,
                   "passed": passed(task, rep) if callable(passed) else passed}
            if metrics:
                row["metric_directions"] = {m: d for m, (d, _) in metrics.items()}
                row["metrics"] = {m: (v(task, rep) if callable(v) else v) for m, (_, v) in metrics.items()}
            out.append(row)
    return out


class ShippedSweepTests(unittest.TestCase):
    def setUp(self):
        self.data = ABL.load(SHIPPED)
        self.entries = {ABL.entry_of(a) for a in self.data["arms"]}

    def test_every_named_layer_has_a_removal_arm(self):
        for entry in ("hooks/tier-agent-spawns", "hooks/filter-output", "hooks/usage-feed", "hooks/stop-gate",
                      "hooks/grade-bash", "skills/*", "roles/*", "workflows/*", "stances/cost",
                      "rules/secrets", "stances/voice"):
            self.assertIn(entry, self.entries)
        unbuilt = " ".join(item["what"] for item in self.data["unbuilt"])
        for layer in ("CLAUDE.md", "autonomy", "plan-ceremony"):
            self.assertIn(layer, unbuilt)

    def test_every_arm_names_its_layer_what_it_removes_its_tasks_and_its_scores(self):
        for spec in self.data["arms"]:
            with self.subTest(arm=spec["id"]):
                self.assertIn(spec["layer"], ABL.LAYERS)
                self.assertTrue(spec["what"].strip())
                self.assertIsInstance(spec["tasks"], list)
                self.assertIsInstance(spec["scores"], list)
                self.assertIn("into its image", ABL.verification(spec))

    def test_the_outcome_subset_is_the_production_tasks_plus_rule_tasks(self):
        tasks = self.data["outcome"]["tasks"]
        self.assertEqual(len(tasks), 10)
        self.assertEqual(len([t for t in tasks if t.startswith("rt-")]), 3)
        self.assertEqual(self.data["pack"], {"name": "model-citizen-evals", "ref": "v1.3.0"})

    def test_every_shipped_arm_resolves_at_this_commit(self):
        self.assertEqual(ABL.check_entries(self.data, POSTURE_MODULE, REPO), [])


class StructureTests(unittest.TestCase):
    def errors(self, data):
        return ABL.structure_errors({k: v for k, v in data.items() if k != "sha256"})

    def test_a_well_formed_sweep_has_no_errors(self):
        self.assertEqual(self.errors(sweep(arm(scores=[("secret_in_tree", [-0.1, 0.1])]))), [])

    def test_a_margin_that_excludes_no_effect_or_names_no_metric_is_refused(self):
        bad = self.errors(sweep(arm(scores=[("secret_in_tree", [0.1, 0.2])])))
        self.assertTrue(any("must contain 0" in line for line in bad), bad)
        bad = self.errors(sweep(arm(scores=[("Cost-of-Pass ratio", [0.0, 1.1])])))
        self.assertTrue(any("positive" in line for line in bad), bad)
        bad = self.errors(sweep(arm(scores=[("Not A Metric", [-0.1, 0.1])])))
        self.assertTrue(any("names no metric" in line for line in bad), bad)

    def test_the_outcome_must_be_a_pass_rate_margin_over_named_tasks(self):
        bad = self.errors(sweep(arm(), outcome=dict(OUTCOME, metric="Cost-of-Pass ratio")))
        self.assertTrue(any("outcome metric" in line for line in bad), bad)
        bad = self.errors(sweep(arm(), outcome=dict(OUTCOME, tasks=[])))
        self.assertTrue(any("must not be empty" in line for line in bad), bad)

    def test_an_unknown_layer_or_arm_key_is_refused(self):
        self.assertTrue(any("layer must be" in line for line in self.errors(sweep(arm(layer="widget")))))
        self.assertTrue(any("exactly one" in line for line in self.errors(sweep(arm(colour="red")))))

    def test_a_listing_is_keyed_by_its_kind(self):
        listing = arm("no-skills", ["skills/a", "skills/b", "roles/c"])
        self.assertEqual(ABL.entry_of(listing), "skills/*")
        self.assertEqual(ABL.selection(listing), {"skills": {"a": "off", "b": "off"}, "roles": {"c": "off"}})
        self.assertIsNone(ABL.entry_of(arm(removes=["skills/a", "skills/a"])))


class SelectionTests(unittest.TestCase):
    def test_a_core_hook_removal_acknowledges_the_core_switch(self):
        self.assertEqual(ABL.selection(arm(removes="hooks/stop-gate")),
                         {"hooks": {"stop-gate": "off"}, ABL.CORE_ACK: True})
        self.assertEqual(ABL.selection(arm(removes="hooks/usage-feed")), {"hooks": {"usage-feed": "off"}})

    def test_the_arm_declaration_accepts_the_acknowledged_selection(self):
        selection = ABL.selection(arm(removes="hooks/grade-bash"))
        decl = ARMS.declaration("harness", ARMS.qualification_inputs(),
                                {"ref": "v1", "commit": "a" * 40}, "1.0.0", selection=selection)
        self.assertEqual(decl["selection"], selection)
        with self.assertRaises(SystemExit):
            ARMS.declaration("harness", ARMS.qualification_inputs(), {"ref": "v1", "commit": "a" * 40},
                             "1.0.0", selection={"hooks": {"grade-bash": "off"}, ABL.CORE_ACK: "yes"})

    def test_the_resolver_refuses_a_core_hook_switched_off_without_the_acknowledgement(self):
        env = {"HOME": str(REPO / ".ablation-empty-home")}
        with self.assertRaises(ValueError):
            POSTURE_MODULE.selection(env, strict=True, config={"hooks": {"stop-gate": "off"}}, root=REPO)
        POSTURE_MODULE.selection(env, strict=True, config=ABL.selection(arm(removes="hooks/stop-gate")), root=REPO)


class ListingCheckTests(unittest.TestCase):
    def check(self, *arms):
        return ABL.check_entries(sweep(*arms), POSTURE_MODULE, REPO)

    def skills(self):
        return ["skills/" + p.parent.name for p in sorted((REPO / "primitives" / "skills").glob("*/SKILL.md"))]

    def test_a_listing_that_leaves_a_unit_on_is_refused(self):
        errors = self.check(arm("no-skills", self.skills()[1:] + ["roles/designer", "roles/design-judge"]))
        self.assertTrue(any("leaves %s switched on" % self.skills()[0] in e for e in errors), errors)

    def test_a_listing_without_its_bound_dependents_is_refused_by_the_resolver(self):
        errors = self.check(arm("no-skills", self.skills()))
        self.assertTrue(any("resolver refuses" in e for e in errors), errors)

    def test_a_listing_may_carry_only_units_bound_to_it(self):
        errors = self.check(arm("no-skills", self.skills() + ["roles/designer", "roles/design-judge", "roles/gatherer"]))
        self.assertTrue(any("roles/gatherer, which the skills listing does not bind" in e for e in errors), errors)


class ScheduleFilterTests(unittest.TestCase):
    NAMES = ("bare", "harness", "a", "b")

    def test_a_named_task_runs_only_its_arms_and_an_unnamed_one_runs_every_arm(self):
        data = sweep(arm("a", "rules/secrets", tasks=["own"]), arm("b", "rules/verification", tasks=[]),
                     outcome=dict(OUTCOME, tasks=["out"]))
        runs = ABL.arm_task_filter(data)
        plan = ABL.schedule([{"id": "own"}, {"id": "out"}, {"id": "other"}], 2, self.NAMES, 5, runs)
        by_task = {}
        for task, _rep, name in plan:
            by_task.setdefault(task["id"], set()).add(name)
        self.assertEqual(by_task["own"], {"bare", "harness", "a"})
        self.assertEqual(by_task["out"], set(self.NAMES))
        self.assertEqual(by_task["other"], set(self.NAMES))

    def test_without_a_filter_the_schedule_is_unchanged(self):
        tasks = [{"id": "t1"}, {"id": "t2"}]
        self.assertEqual(ABL.schedule(tasks, 3, self.NAMES, 9, None), ABL.schedule(tasks, 3, self.NAMES, 9))
        filtered = ABL.schedule(tasks, 3, self.NAMES, 9, {"t1": {"bare", "harness", "b"}})
        whole = ABL.schedule(tasks, 3, self.NAMES, 9)
        self.assertEqual(filtered, [run for run in whole if run[0]["id"] != "t1" or run[2] != "a"])

    def test_the_dry_run_lists_each_arm_with_the_tasks_it_runs(self):
        data = sweep(arm("no-secrets", "rules/secrets", tasks=["fixture-one"]),
                     arm("no-verification", "rules/verification", tasks=["fixture-two"]),
                     outcome=dict(OUTCOME, tasks=["elsewhere"]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sweep.json"
            path.write_text(json.dumps({k: v for k, v in data.items() if k != "sha256"}), encoding="utf-8")
            out = io.StringIO()
            head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
            argv = ["replay", "--tasks", str(FIXTURE_TASKS), "--ablations", str(path), "--tag", head,
                    "--model", "claude-test", "--exploratory", "--dry-run", "--reps", "1"]
            with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                    redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(BENCH.main(argv), 0)
        text = out.getvalue()
        self.assertIn("6 run(s): 2 task(s) x 4 arm(s)", text)
        self.assertIn("all 6 run(s) reach 2 USD", text)
        self.assertRegex(text, r"arm no-secrets removes rules/secrets: \S+; runs fixture-one\n")
        self.assertNotIn("fixture-two rep 1 no-secrets", text)
        self.assertNotIn("fixture-one rep 1 no-verification", text)


class ParityTests(unittest.TestCase):
    def test_a_listing_may_move_every_listing_its_entries_touch_and_a_hook_none(self):
        stamp = {"ablation_removes": ["skills/a", "roles/b"]}
        rows = [{"arm": "no-skills", **stamp}, {"arm": "no-hook", "ablation_removes": "hooks/usage-feed"}]
        with mock.patch.object(ABL.replay_pair, "surface_parity", lambda rows, surface, control, allowed: allowed):
            allowed = ABL.surface_parity(rows)
        self.assertIn("init_agents", allowed["no-skills"])
        self.assertIn("init_skills", allowed["no-skills"])
        self.assertEqual(allowed["no-hook"], ())
        self.assertEqual(ABL.removed_of(rows), {"no-skills": "skills/*", "no-hook": "hooks/usage-feed"})

    def test_a_listed_entry_still_in_the_attribution_is_named(self):
        row = {"task": "t", "rep": 1, "arm": "no-skills", "ablation_removes": ["skills/a", "skills/b"],
               "context_attribution": {"modules": {"skills/b": 10}}}
        problems = ABL.attribution_problems([row])
        self.assertEqual(len(problems), 1)
        self.assertIn("skills/b", problems[0])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.data = sweep(arm("a", tasks=["own"], long_session=["deep"]), arm("b", "rules/verification", tasks=[]),
                          outcome=dict(OUTCOME, tasks=["out"]))
        self.plan = ABL.sweep_plan(self.data)

    def test_the_plan_lists_every_arm_with_its_tasks_and_bare_and_control_run_their_union(self):
        self.assertEqual([a["tasks"] for a in self.plan["arms"]], [["own", "out"], ["out"]])
        self.assertEqual(self.plan["control_tasks"], ["own", "out"])
        self.assertEqual(self.plan["long_session"], ["deep"])

    def test_a_run_is_priced_at_its_turns_and_never_above_the_run_cap(self):
        rates = {"input": 1.0, "cache_read": 0.1, "cache_write": 1.25, "output": 5.0}
        envelope = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 100000}  # 0.5 USD a turn
        price = ABL.price_plan(self.plan, "m", rates, envelope, {"own": 2, "out": 10}, {"deep": 15.0}, run_cap=2.0)
        self.assertEqual(price["per_turn_usd"], 0.5)
        # own: min(1.0, 2) = 1.0; out: min(5.0, 2) = 2.0; bare and control 3.0 + 15 each,
        # arm a 3.0 + 15, arm b 2.0.
        self.assertEqual(price["arms"], {"bare": 18.0, "harness": 18.0, "a": 18.0, "b": 2.0})
        self.assertEqual(price["total_usd"], 56.0)
        self.assertEqual((price["task_runs"], price["scenario_runs"]), (7, 3))
        self.assertEqual(price["scenario_usd"], 45.0)
        with self.assertRaises(ValueError):
            ABL.price_plan(self.plan, "m", rates, envelope, {"own": 2}, {"deep": 15.0})

    def test_a_score_no_scored_task_declares_is_refused(self):
        data = sweep(arm("a", tasks=["own"], scores=[("secret_in_tree", [-0.1, 0.1])]),
                     arm("b", "rules/verification", tasks=[], scores=[("Cost-of-Pass ratio", [0.9, 1.1])]))
        plan = ABL.sweep_plan(data)
        self.assertEqual(ABL.score_errors(plan, OUTCOME["tasks"], {"own": {"secret_in_tree"}}), [])
        errors = ABL.score_errors(plan, OUTCOME["tasks"], {"own": {"other"}})
        self.assertEqual(len(errors), 1)
        self.assertIn("declares secret_in_tree", errors[0])

    def test_the_cli_reads_the_pack_and_prices_each_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            for task, turns in (("own", 2), ("out", 10)):
                (pack / "tasks" / task).mkdir(parents=True)
                (pack / "tasks" / task / "task.json").write_text(json.dumps({"id": task, "max_turns": turns}))
            (pack / "scenarios" / "deep").mkdir(parents=True)
            (pack / "scenarios" / "deep" / "scenario.json").write_text(
                json.dumps({"caps": {"max_cost_usd_hint": 15.0}}))
            for command in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=pack",
                                                        "commit", "-q", "-m", "pack"], ["tag", "v9"]):
                subprocess.run(["git", "-C", str(pack)] + command, check=True)
            manifest = {k: v for k, v in self.data.items() if k != "sha256"}
            manifest.update(pack={"name": "pack", "ref": "v9"},
                            pricing={"turn": {"input": 0, "cache_read": 0, "cache_write": 0, "output": 1000},
                                     "source": "assumed"})
            path = Path(tmp) / "sweep.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                status = ABL.main(["plan", "--manifest", str(path), "--pack", str(pack),
                                   "--model", "claude-haiku-4-5", "--model", "claude-sonnet-5", "--json"])
        self.assertEqual(status, 0)
        result = json.loads(out.getvalue())
        self.assertEqual([p["model"] for p in result["prices"]], ["claude-haiku-4-5", "claude-sonnet-5"])
        haiku, sonnet = result["prices"]
        self.assertLess(haiku["task_usd"], sonnet["task_usd"])
        self.assertEqual(haiku["scenario_usd"], 45.0)


class JustifyTests(unittest.TestCase):
    OWN = ["own1", "own2"]

    def judge(self, spec, control, removed):
        return ABL.justify(control + removed, sweep(spec), resamples=RESAMPLES)[0]

    def control(self, secret=0):
        return (rows_for("harness", self.OWN, metrics={"secret_in_tree": ("lower", secret)})
                + rows_for("harness", OUTCOME["tasks"]))

    def test_a_layer_whose_removal_worsens_its_own_score_beyond_the_margin_is_kept(self):
        spec = arm(scores=[("secret_in_tree", [-0.125, 0.125])])
        removed = (rows_for("no-secrets", self.OWN, cost=0.8, metrics={"secret_in_tree": ("lower", 1)})
                   + rows_for("no-secrets", OUTCOME["tasks"], cost=0.8))
        verdict = self.judge(spec, self.control(), removed)
        self.assertEqual(verdict["verdict"], ABL.KEEP)
        self.assertTrue(verdict["scores"][0]["harmful"])
        self.assertEqual(verdict["entry"], "rules/secrets")
        self.assertAlmostEqual(verdict["marginal_cost"]["usd_per_attempt"], 0.2)

    def test_a_layer_whose_removal_lowers_the_outcome_beyond_the_margin_is_kept(self):
        removed = (rows_for("no-secrets", self.OWN, metrics={"secret_in_tree": ("lower", 0)})
                   + rows_for("no-secrets", OUTCOME["tasks"], passed=False))
        verdict = self.judge(arm(scores=[("secret_in_tree", [-0.125, 0.125])]), self.control(), removed)
        self.assertEqual(verdict["verdict"], ABL.KEEP)
        self.assertTrue(verdict["outcome"]["harmful"])
        self.assertFalse(verdict["scores"][0]["harmful"])

    def test_a_layer_whose_removal_changes_nothing_within_every_margin_is_trimmed(self):
        removed = (rows_for("no-secrets", self.OWN, cost=0.9, metrics={"secret_in_tree": ("lower", 0)})
                   + rows_for("no-secrets", OUTCOME["tasks"], cost=0.9))
        verdict = self.judge(arm(scores=[("secret_in_tree", [-0.125, 0.125])]), self.control(), removed)
        self.assertEqual(verdict["verdict"], ABL.TRIM)
        self.assertEqual(verdict["outcome"]["assessment"], "equivalent")
        self.assertFalse(verdict["exploratory"])

    def test_an_interval_crossing_a_bound_or_missing_rows_is_no_evidence(self):
        removed = (rows_for("no-secrets", self.OWN, metrics={"secret_in_tree": ("lower", 0)})
                   + rows_for("no-secrets", OUTCOME["tasks"], passed=lambda task, rep: task != "o1"))
        verdict = self.judge(arm(scores=[("secret_in_tree", [-0.125, 0.125])]), self.control(), removed)
        self.assertEqual(verdict["verdict"], ABL.NO_EVIDENCE)
        self.assertEqual(verdict["outcome"]["assessment"], "inconclusive")
        empty = self.judge(arm(scores=[("secret_in_tree", [-0.125, 0.125])]), self.control(), [])
        self.assertEqual(empty["verdict"], ABL.NO_EVIDENCE)
        self.assertIsNone(empty["marginal_cost"]["usd_per_attempt"])

    def test_a_cost_score_is_judged_on_the_outcome_subset_when_the_layer_has_no_task(self):
        spec = arm("no-usage-feed", "hooks/usage-feed", tasks=[], scores=[("Cost-of-Pass ratio", [0.9, 1.1])])
        control = rows_for("harness", OUTCOME["tasks"], cost=1.0)
        removed = rows_for("no-usage-feed", OUTCOME["tasks"], cost=1.5)
        verdict = self.judge(spec, control, removed)
        self.assertEqual(verdict["verdict"], ABL.KEEP)
        self.assertEqual(verdict["scores"][0]["tasks"], OUTCOME["tasks"])
        self.assertAlmostEqual(verdict["marginal_cost"]["usd_per_attempt"], -0.5)

    def test_few_trials_label_the_verdict_exploratory(self):
        control = rows_for("harness", OUTCOME["tasks"], trials=2)
        removed = rows_for("no-secrets", OUTCOME["tasks"], trials=2)
        verdict = self.judge(arm(tasks=[]), control, removed)
        self.assertTrue(verdict["exploratory"])

    def test_verdicts_are_keyed_by_entry_for_the_scorecard_and_need_an_outcome(self):
        removed = rows_for("no-secrets", OUTCOME["tasks"])
        verdicts = ABL.justify(rows_for("harness", OUTCOME["tasks"]) + removed, sweep(arm(tasks=[])),
                               resamples=RESAMPLES)
        self.assertEqual(set(ABL.verdicts_by_entry(verdicts)), {"rules/secrets"})
        self.assertIn("rules/secrets: trim", ABL.render_verdicts(verdicts))
        with self.assertRaises(ValueError):
            ABL.justify([], {k: v for k, v in sweep(arm()).items() if k != "outcome"})


if __name__ == "__main__":
    unittest.main()
