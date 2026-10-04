"""The layer scorecard (#1186): one deterministic report per layer and overall, built from saved
rows of every role. Synthetic rows and a synthetic sweep only; nothing builds an image or calls a
model."""
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO
import test_evidence_bundle  # a module import, so discovery does not collect its tests twice

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("layer_scorecard", REPO / "scripts" / "layer_scorecard.py")
SC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SC)

RESAMPLES = 200
OUTCOME = ["o1", "o2", "o3"]
STAMP = {"evidence": "pre-registered", "pre_registration": "benchmarks/preregistrations/2026-10-04-x.md"}
PRICES = {"m1": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
          "m2": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25}}
MANIFEST = {
    "schema": 2, "name": "sweep", "sha256": "ab" * 32, "planning": {"cv": 0.25, "source": "assumed"},
    "justification": "the rule", "outcome": {"tasks": OUTCOME, "metric": "Pass-rate difference",
                                             "bounds": [-0.125, 0.125]},
    "arms": [
        {"id": "no-secrets", "layer": "rule", "what": "the secrets rule", "removes": "rules/secrets",
         "tasks": ["own1"], "scores": [{"metric": "secret_in_tree", "bounds": [-0.125, 0.125]}]},
        {"id": "no-cache", "layer": "rule", "what": "the cache rule", "removes": "rules/cache-hygiene",
         "tasks": [], "long_session": ["s1", "s2"], "scores": [{"metric": "Cost-of-Pass ratio", "bounds": [0.9, 1.1]}]},
        {"id": "no-skills", "layer": "listing", "what": "every skill", "removes": ["skills/a", "skills/b"],
         "tasks": [], "scores": []},
        {"id": "no-stop-gate", "layer": "hook", "what": "the stop gate", "removes": "hooks/stop-gate",
         "tasks": [], "scores": []},
    ],
    "unbuilt": [{"layer": "instructions", "what": "CLAUDE.md", "reason": "no switch withholds it"}],
    "excluded": [{"layer": "hook", "what": "usage-log", "reason": "puts nothing into the session"}],
}
STATIC = {"harness_version": "9.9.9", "sha256": "cd" * 32, "files": {
    "claude/CLAUDE.md": {"est_tokens": 100}, "claude/rules/secrets.md": {"est_tokens": 150},
    "claude/rules/cache-hygiene.md": {"est_tokens": 80}, "claude/skills/a/SKILL.md": {"est_tokens": 30},
    "claude/skills/b/SKILL.md": {"est_tokens": 40}, "claude/output-styles/plain.md": {"est_tokens": 500}}}
MODULES = {"rules/secrets": 200, "rules/cache-hygiene": 80, "skills/a": 30, "skills/b": 40}


def rows(arm, tasks, reps=5, cost=1.0, passed=True, model="m1", metrics=None, **extra):
    out = []
    for task in tasks:
        for rep in range(1, reps + 1):
            row = dict(STAMP, task=task, arm=arm, rep=rep, cost_usd=cost, error=False, passed=passed,
                       model=model, turns=3, **extra)
            if arm == "harness":
                row["context_attribution"] = {"modules": dict(MODULES)}
            if metrics:
                row["metric_directions"] = {m: d for m, (d, _) in metrics.items()}
                row["metrics"] = {m: v for m, (_, v) in metrics.items()}
            out.append(row)
    return out


def sessions(arm, cost, model="m1", kind=True):
    out = []
    for scenario in ("s1", "s2"):
        for rep in (1, 2, 3):
            row = dict(STAMP, task=scenario, scenario=scenario, arm=arm, rep=rep, cost_usd=cost, model=model)
            if kind:
                row["row_kind"] = "session"
            out.append(row)
    return out


def write(directory, results, detections=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results))
    if detections is not None:
        (directory / "detections.jsonl").write_text("".join(json.dumps(d, sort_keys=True) + "\n" for d in detections))
    return directory


def detections_for(results, fired):
    return [{"task": r["task"], "arm": r["arm"], "rep": r["rep"], "detector": "d1", "rule": "rules/secrets",
             "count": 1 if fired(r) else 0} for r in results]


def judge_dir(directory, wins=3, losses=1):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    verdicts, key = [], {}
    for index in range(wins + losses):
        ident = "p%d" % index
        key[ident] = {"first": "harness", "second": "bare", "task": "o%d" % (index % 3 + 1)}
        verdicts.append(dict(STAMP, id=ident, error=None, dimensions={
            "design": {"preference": "first" if index < wins else "second", "consistent": True}}))
    (directory / "verdicts.jsonl").write_text("".join(json.dumps(v, sort_keys=True) + "\n" for v in verdicts))
    (directory / "pairs.key.json").write_text(json.dumps({"schema": 1, "pairs": key}))
    (directory / "calibration.json").write_text(json.dumps(
        {"model": "judge", "rubric_sha256": "ef" * 32, "admitted": ["design"], "agreement": {"design": {}}}))
    return directory


class Fixture(object):
    """Every role's directory for one synthetic evaluation."""

    def __init__(self, root, exploratory=False, long_kind=True, stratum=False):
        self.root = Path(root)
        extra = {"stratum": "S-%s" % "m1"} if stratum else {}
        production = rows("bare", OUTCOME, cost=1.0, **extra) + rows("harness", OUTCOME, cost=2.0, **extra)
        if exploratory:
            production[0] = dict(production[0], evidence="exploratory", pre_registration=None)
        self.production = write(self.root / "production" / "tag", production,
                                detections_for(production, lambda r: r["arm"] == "bare" and r["rep"] <= 2))
        self.rules = write(self.root / "rules", rows("bare", ["own1"], metrics={"secret_in_tree": ("lower", 1)}, **extra)
                           + rows("harness", ["own1"], metrics={"secret_in_tree": ("lower", 0)}, **extra))
        sweep = (rows("harness", ["own1"], metrics={"secret_in_tree": ("lower", 0)}, **extra)
                 + rows("harness", OUTCOME, **extra)
                 + rows("no-secrets", ["own1"], metrics={"secret_in_tree": ("lower", 1)}, **extra)
                 + rows("no-secrets", OUTCOME, **extra) + rows("no-cache", OUTCOME, **extra))
        self.sweep = write(self.root / "sweep", sweep)
        self.long = write(self.root / "long", [dict(r, **extra) for r in sessions("harness", 1.0, kind=long_kind)
                                               + sessions("no-cache", 1.5, kind=long_kind)
                                               + sessions("bare", 0.5, kind=long_kind)])
        self.judge = judge_dir(self.root / "judge")
        self.manifest = self.root / "ablations.json"
        self.manifest.write_text(json.dumps({k: v for k, v in MANIFEST.items() if k != "sha256"}))
        self.static = self.root / "static.json"
        self.static.write_text(json.dumps({k: v for k, v in STATIC.items() if k != "sha256"}))
        self.prices = self.root / "prices.json"
        self.prices.write_text(json.dumps({"models": PRICES}))

    def argv(self, *extra):
        return ["--production", str(self.root / "production"), "--rules", str(self.rules), "--sweep", str(self.sweep),
                "--long-session", str(self.long), "--judge", str(self.judge), "--manifest", str(self.manifest),
                "--static", str(self.static), "--prices", str(self.prices), "--resamples", str(RESAMPLES)] + list(extra)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = SC.main(argv)
    return code, out.getvalue(), err.getvalue()


class ScorecardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(self.tmp.name)

    def scorecard(self, fx=None, *extra):
        code, out, err = run((fx or self.fx).argv("--json", *extra))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def card(self, document, entry):
        return next(c for c in document["layers"] if c["entry"] == entry)

    def test_headline_gives_every_reading_with_an_equivalence_verdict(self):
        head = self.scorecard()["headline"]["m1"]
        cop = head["cost_of_pass"]
        self.assertEqual(cop["ratio"], 2.0)
        self.assertEqual(cop["equivalence"]["verdict"], "not equivalent")
        self.assertEqual(head["pass_rate"]["equivalence"]["verdict"], "equivalent")
        self.assertEqual(head["pass_k"]["k"], 5)
        self.assertEqual(head["pass_k"]["difference"], 0.0)
        self.assertEqual(head["all_rules"]["arms"]["bare"]["rate"], 0.6)
        self.assertEqual(head["all_rules"]["arms"]["harness"]["rate"], 1.0)
        self.assertAlmostEqual(head["all_rules"]["difference"], 0.4)
        self.assertEqual(head["all_rules"]["equivalence"]["verdict"], "not equivalent")
        judged = head["judge"]["results"][0]["dimensions"][0]
        self.assertEqual((judged["dimension"], judged["win_rate"]), ("design", 0.75))
        self.assertIn(judged["equivalence"]["verdict"], ("inconclusive", "not equivalent"))
        self.assertEqual(head["long_session"]["ratio"], 2.0)

    def test_prefix_reads_attribution_then_the_static_figure_and_prices_a_run(self):
        document = self.scorecard()
        secrets = self.card(document, "rules/secrets")["prefix"]["m1"]
        self.assertEqual(secrets["tokens"], 200)
        self.assertIn("context_attribution", secrets["source"])
        self.assertAlmostEqual(secrets["usd_per_run"], 200 * 2.5 / 1e6 + 200 * 0.2 / 1e6 * 2)
        self.assertEqual(self.card(document, "skills/*")["prefix"]["m1"]["tokens"], 70)
        style = self.card(document, "output-styles/plain")
        self.assertEqual(style["layer"], "output style")
        self.assertEqual((style["prefix"]["m1"]["tokens"], style["prefix"]["m1"]["source"]),
                         (500, "benchmarks/static.json"))
        self.assertEqual(self.card(document, "hooks/stop-gate")["prefix"]["m1"]["tokens"], 0)
        self.assertEqual(self.card(document, "instructions/CLAUDE.md")["prefix"]["m1"]["tokens"], 100)

    def test_behaviour_against_bare_and_against_removal_and_the_keep_verdict(self):
        cell = self.card(self.scorecard(), "rules/secrets")["strata"]["m1"]
        bare = cell["behaviour_vs_bare"]["scores"][0]
        self.assertEqual((bare["metric"], bare["interval"], bare["harmful"]), ("secret_in_tree", [1.0, 1.0], True))
        removal = cell["behaviour_vs_removal"]["scores"][0]
        self.assertTrue(removal["harmful"])
        self.assertEqual(cell["outcome"]["assessment"], "equivalent")
        verdict = cell["verdict"]
        self.assertEqual(verdict["verdict"], "keep")
        self.assertEqual(verdict["deciding"]["metric"], "secret_in_tree")
        self.assertEqual(verdict["deciding"]["bounds"], [-0.125, 0.125])
        self.assertEqual(verdict["rows"]["sources"], ["sweep/1/results.jsonl"])
        self.assertEqual(verdict["rows"]["arms"], ["harness", "no-secrets"])
        self.assertNotIn("cost", cell)

    def test_a_cost_control_layer_carries_its_marginal_and_session_cost(self):
        card = self.card(self.scorecard(), "rules/cache-hygiene")
        self.assertTrue(card["cost_control"])
        cell = card["strata"]["m1"]
        self.assertEqual(cell["verdict"]["verdict"], "trim")
        self.assertIsNone(cell["verdict"]["deciding"])
        self.assertEqual(cell["cost"]["marginal"]["usd_per_attempt"], 0.0)
        self.assertEqual(cell["cost"]["long_session"]["ratio"], 1.5)
        self.assertEqual(cell["cost"]["long_session"]["arms"], ["harness", "no-cache"])

    def test_layers_without_an_arm_read_no_evidence_with_the_reason(self):
        document = self.scorecard()
        for entry, reason in (("instructions/CLAUDE.md", "no switch withholds it"),
                              ("hooks/usage-log", "puts nothing"), ("output-styles/plain", "no removal arm")):
            verdict = self.card(document, entry)["strata"]["m1"]["verdict"]
            self.assertEqual(verdict["verdict"], "no evidence")
            self.assertIn(reason, verdict["reason"])
        layers = [c["layer"] for c in document["layers"]]
        self.assertEqual(layers, sorted(layers, key=SC.LAYERS.index))

    def test_rows_without_row_kind_or_stratum_are_reported_as_not_measured_or_grouped_by_model(self):
        fx = Fixture(Path(self.tmp.name) / "plain", long_kind=False)
        document = self.scorecard(fx)
        self.assertFalse(document["strata_recorded"])
        self.assertEqual(document["headline"]["m1"]["long_session"]["status"], "not measured")
        cost = self.card(document, "rules/cache-hygiene")["strata"]["m1"]["cost"]["long_session"]
        self.assertIn("row_kind", cost["reason"])

    def test_a_recorded_stratum_groups_the_report(self):
        document = self.scorecard(Fixture(Path(self.tmp.name) / "strata", stratum=True))
        self.assertTrue(document["strata_recorded"])
        self.assertEqual(sorted(document["headline"]), ["S-m1"])

    def test_missing_judge_and_detections_are_not_measured(self):
        fx = self.fx
        (fx.production / "detections.jsonl").unlink()
        argv = [a for a in fx.argv("--json") if a not in ("--judge", str(fx.judge))]
        code, out, err = run(argv)
        self.assertEqual(code, 0, err)
        head = json.loads(out)["headline"]["m1"]
        self.assertEqual(head["all_rules"]["status"], "not measured")
        self.assertEqual(head["judge"]["status"], "not measured")

    def test_the_same_rows_give_byte_identical_output(self):
        outs = []
        for name in ("a", "b"):
            target = Path(self.tmp.name) / name
            code, _, err = run(self.fx.argv("--out", str(target)))
            self.assertEqual(code, 0, err)
            outs.append(((target / "scorecard.json").read_bytes(), (target / "scorecard.md").read_bytes()))
        self.assertEqual(outs[0], outs[1])
        self.assertIn(b"## Harness against bare", outs[0][1])

    def test_exploratory_rows_are_refused_without_the_flag(self):
        fx = Fixture(Path(self.tmp.name) / "explore", exploratory=True)
        code, out, err = run(fx.argv())
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("refusing exploratory rows", err)
        self.assertIn("production/1/tag/results.jsonl: 1 of 30", err)

    def test_the_flag_scores_exploratory_rows_and_marks_the_output(self):
        fx = Fixture(Path(self.tmp.name) / "explore", exploratory=True)
        document = self.scorecard(fx, "--allow-exploratory")
        self.assertTrue(document["exploratory"])
        self.assertTrue(document["headline"]["m1"]["cost_of_pass"]["equivalence"]["exploratory"])
        self.assertTrue(self.card(document, "rules/secrets")["strata"]["m1"]["verdict"]["exploratory"])
        code, out, _ = run(fx.argv("--allow-exploratory"))
        self.assertIn("**Exploratory: not evidence.**", out)

    def test_a_plan_overrides_the_default_margins(self):
        plan = Path(self.tmp.name) / "plan.md"
        plan.write_text("# Plan\n\n## Equivalence margins\n\n- **Cost-of-Pass ratio:** 0.5 to 3\n"
                        "- **Pass-rate difference:** -0.2 to 0.2\n")
        document = self.scorecard(None, "--plan", str(plan))
        self.assertEqual(document["margins"]["values"]["Cost-of-Pass ratio"], [0.5, 3.0])
        self.assertEqual(document["margins"]["source"], "plan.md")
        self.assertEqual(document["margins"]["values"][SC.PASS_K], [-0.125, 0.125])

    def test_a_set_passed_twice_is_refused(self):
        code, _, err = run(self.fx.argv("--sweep", str(self.fx.sweep), str(self.fx.sweep)))
        self.assertEqual(code, 2)
        self.assertIn("pass each set once", err)


class BundleCarriesScorecard(unittest.TestCase):
    def _bundle(self):
        bundle = test_evidence_bundle.EvidenceBundleTest("test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        bundle.setUp()
        self.addCleanup(bundle.tearDown)
        return bundle

    def _carry(self, bundle, card):
        bundle._write_json(bundle.root / "artifacts" / "scorecard.json", card)
        index = bundle._index()
        index["artifacts"]["scorecard"] = bundle._ref("artifacts/scorecard.json")
        bundle._save_index(index)

    def _card(self, bundle, **changes):
        rows = (bundle.root / bundle._index()["artifacts"]["rows"]["path"]).read_bytes()
        card = {"schema": 1, "kind": "layer-scorecard", "exploratory": False, "headline": {"m": {}}, "layers": [{}],
                "sources": [{"id": "production/1/results.jsonl", "role": "production",
                             "sha256": hashlib.sha256(rows).hexdigest()}]}
        card.update(changes)
        return card

    def test_a_scorecard_built_from_the_bundle_rows_is_reported(self):
        bundle = self._bundle()
        self._carry(bundle, self._card(bundle))
        verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
        self.assertTrue(verified["ok"], verified["errors"])
        self.assertEqual(verified["derived"]["scorecard"]["layers"], 1)

    def test_an_exploratory_or_foreign_scorecard_fails_item_five(self):
        for changes, message in (({"exploratory": True}, "scorecard is exploratory"),
                                 ({"sources": [{"role": "production", "sha256": "0" * 64}]}, "not built from"),
                                 ({"kind": "other"}, "is not a schema-1 layer-scorecard")):
            bundle = self._bundle()
            self._carry(bundle, self._card(bundle, **changes))
            verified = test_evidence_bundle.EVIDENCE.verify(bundle.root)
            self.assertFalse(verified["ok"])
            self.assertTrue(any(message in e for e in verified["errors"]), verified["errors"])
            self.assertIsNone(verified["derived"]["scorecard"])


if __name__ == "__main__":
    unittest.main()
