"""The definitive evaluation in the Studio (AH-S345): strata, config arms and long-session rows in
the run store, the layer scorecard and judge on run detail, pack sets in the replay form, and the
registered budget behind the spend guard. Synthetic rows and fakes only; nothing spends.

`studio/tests/fixtures/definitive-evaluation.json` is the engine's own scorecard and judge section
for the `test_layer_scorecard` fixture, which `studio/tests/definitive-evaluation.test.ts` feeds
through the run detail display. Regenerate it with:

    python3 tests/test_studio_definitive_evaluation.py --write
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import definitive, evaluation, packs, replay, run_store, runs  # noqa: E402
import test_layer_scorecard as scorecard_fixture  # noqa: E402
from test_replay_pack import make_pack, task_spec  # noqa: E402
from test_studio_replay_packs import harness, target  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "definitive-evaluation.json"
SHA = "a" * 40
STAMP = {"evidence": "pre-registered", "pre_registration": "plan.md", "harness_sha": SHA,
         "tag": "v1.0.0", "date": "2026-10-06", "schema_version": 1}
CONFIG = {"name": "maintainer", "schema": 1, "sha256": "c" * 64, "stances": {"voice": "concise"}}


def row(task, arm, rep, model, **extra):
    return dict(STAMP, task=task, arm=arm, rep=rep, model=model, stratum=model,
                strata=["m-one", "m-two"], passed=True, error=False, cost_usd=0.5,
                cache_basis="cold", cache_nonce="n-%s-%s-%d" % (model, arm, rep),
                metrics={"accuracy": 0.75}, metric_directions={"accuracy": "higher"},
                metric_errors=[], diff_path="streams/%s-%s-%d.diff" % (task, arm, rep), **extra)


def long_session(model):
    """One scripted session: two checkpoint rows and the session row, under one task, arm and rep."""
    base = dict(STAMP, task="s1", scenario="s1", arm="harness", rep=1, model=model, stratum=model,
                tier="long-session", session_id="session-" + model, cache_basis="cold",
                cache_nonce=None)
    rows = [dict(base, row_kind="checkpoint", checkpoint="cp%d" % index, checkpoint_index=index,
                 reached=True, passed=index == 1, error=False, cost_usd=0.25) for index in (1, 2)]
    rows.append(dict(base, row_kind="session", checkpoint=None, checkpoint_index=None,
                     passed=False, error=False, cost_usd=0.5, stopped="completed"))
    return rows


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in rows), encoding="utf-8")


def engine_documents(root):
    """`(scorecard, judge)` exactly as the engine prints them for the scorecard test's fixture."""
    fixture = scorecard_fixture.Fixture(root)
    code, out, err = scorecard_fixture.run(fixture.argv("--json"))
    if code != 0:
        raise AssertionError(err)
    judge = subprocess.run(definitive.judge_command(fixture.judge.parent),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return json.loads(out), json.loads(judge.stdout), fixture


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        tag = self.repository / "benchmarks" / "1.0.0" / "v1.0.0"
        self.rows = {}
        for model in ("m-one", "m-two"):
            rows = [row("t1", arm, 1, model, **({"arm_config": CONFIG} if arm == "maintainer" else {}))
                    for arm in ("bare", "harness", "maintainer")] + long_session(model)
            self.rows[model] = rows
            write_rows(tag / model / "results.jsonl", rows)
        self.catalog = self.root / "suites.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": []}), encoding="utf-8")
        self.supervisor = runs.RunSupervisor(self.root / "state", self.catalog,
                                             repository=self.repository)
        self.addCleanup(self.supervisor.close)

    def records(self):
        return [record for record in self.supervisor.history.list(limit=200)
                if record["source"]["kind"] == "benchmark-result"]

    def test_every_stratum_folder_is_read_with_each_arm_as_the_engine_wrote_it(self):
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        records = self.records()
        self.assertEqual(len(records), 12)
        paths = sorted({record["source"]["path"] for record in records})
        self.assertEqual(paths, ["benchmarks/1.0.0/v1.0.0/m-one/results.jsonl",
                                 "benchmarks/1.0.0/v1.0.0/m-two/results.jsonl"])
        arms = sorted({(record["raw"]["stratum"], record["arms"][0]) for record in records})
        self.assertEqual(arms, [(model, arm) for model in ("m-one", "m-two")
                                for arm in ("bare", "harness", "maintainer")])
        config = next(record for record in records if record["arms"] == ["maintainer"])
        self.assertEqual(config["evaluation"]["shape"], "config-arm")
        self.assertEqual(config["evaluation"]["arms"], ["bare", "harness", "maintainer"])
        self.assertEqual(config["evaluation"]["arm_config"], CONFIG)

    def test_a_two_arm_row_keeps_the_identity_it_always_had(self):
        plain = dict(STAMP, task="t1", arm="harness", rep=1, model="m")
        contract = evaluation.row_contract(plain)
        self.assertEqual(evaluation.identity_fields(plain, contract),
                         {name: plain.get(name) for name in ("task", "arm", "rep", "harness_sha", "tag")})
        self.assertNotIn("arm_config", evaluation.identity_fields(plain, contract))

    def test_long_session_rows_index_under_their_session_without_colliding(self):
        self.supervisor.reindex(self.repository)
        sessions = [record for record in self.records() if record["raw"].get("row_kind")]
        self.assertEqual(len({record["run_id"] for record in sessions}), 6)
        cases = sorted(case for record in sessions for case in record["cases"])
        self.assertEqual(cases, sorted("session-%s/%s" % (model, key) for model in ("m-one", "m-two")
                                       for key in ("checkpoint/1", "checkpoint/2", "session/-")))
        # A replay row for the same task, arm and rep stays its own run with its own case.
        replay_row = next(record for record in self.records()
                          if record["raw"]["stratum"] == "m-one" and record["arms"] == ["harness"]
                          and not record["raw"].get("row_kind"))
        self.assertEqual(list(replay_row["cases"]), ["t1"])
        self.assertNotIn(replay_row["run_id"], {record["run_id"] for record in sessions})

    def test_run_detail_carries_the_engine_row_fields_verbatim(self):
        self.supervisor.reindex(self.repository)
        record = next(record for record in self.records() if record["arms"] == ["maintainer"]
                      and record["raw"]["stratum"] == "m-two")
        detail = self.supervisor.run_detail(record["run_id"])
        source = self.rows["m-two"][2]
        self.assertEqual(detail["engine_row"], {name: source[name] for name in evaluation.ENGINE_ROW_FIELDS
                                                if name in source})
        self.assertEqual(detail["engine_row"]["metric_directions"], {"accuracy": "higher"})
        self.assertEqual(detail["engine_row"]["cache_basis"], "cold")
        self.assertEqual(detail["engine_reports"], {"scorecard": None, "judge": None, "errors": []})
        self.assertNotIn(str(self.repository), json.dumps(detail))


class EngineReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.scorecard, self.judge, self.fixture = engine_documents(self.root / "engine")
        self.repository = self.root / "repository"
        self.tag = self.repository / "benchmarks" / "1.0.0" / "v1.0.0"
        write_rows(self.tag / "m-one" / "results.jsonl", [row("t1", "harness", 1, "m-one")])
        code, _out, err = scorecard_fixture.run(self.fixture.argv("--out", str(self.tag)))
        self.assertEqual(code, 0, err)
        judge = self.tag / definitive.JUDGE_FOLDER
        judge.mkdir()
        for name in definitive.JUDGE_FILES:
            (judge / name).write_bytes((self.fixture.judge / name).read_bytes())
        self.relative = "benchmarks/1.0.0/v1.0.0/m-one/results.jsonl"

    def test_a_stratum_finds_its_tags_scorecard_and_judge_exactly_as_the_engine_made_them(self):
        reports = definitive.engine_reports(self.repository, self.relative)
        self.assertEqual(reports["errors"], [])
        self.assertEqual(reports["scorecard"], self.scorecard)
        self.assertEqual(reports["judge"], self.judge)

    def test_the_committed_display_fixture_is_the_engines_output(self):
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(committed["scorecard"], self.scorecard,
                         "regenerate: python3 tests/test_studio_definitive_evaluation.py --write")
        self.assertEqual(committed["judge"], self.judge)

    def test_a_document_of_another_kind_or_schema_is_an_error_never_shown(self):
        path = self.tag / definitive.SCORECARD_NAME
        for document, reason in (({"kind": "other", "schema": 1}, "is not a layer scorecard"),
                                 (dict(self.scorecard, schema=2), "unsupported layer scorecard schema: 2")):
            path.write_text(json.dumps(document), encoding="utf-8")
            reports = definitive.engine_reports(self.repository, self.relative)
            self.assertIsNone(reports["scorecard"])
            self.assertEqual(reports["errors"], ["layer scorecard: " + definitive.SCORECARD_NAME + " " + reason]
                             if reason.startswith("is") else ["layer scorecard: " + reason])

    def test_a_judge_report_the_engine_refuses_is_named_not_shown(self):
        (self.tag / definitive.JUDGE_FOLDER / "pairs.key.json").write_text("{}", encoding="utf-8")
        reports = definitive.engine_reports(self.repository, self.relative)
        self.assertIsNone(reports["judge"])
        self.assertEqual(len(reports["errors"]), 1)
        self.assertTrue(reports["errors"][0].startswith("diff-quality judge: "))

    def test_a_source_outside_benchmarks_reads_nothing(self):
        for relative in ("runs/x/results.jsonl", "../benchmarks/x/results.jsonl", None):
            self.assertEqual(definitive.engine_reports(self.repository, relative),
                             {"scorecard": None, "judge": None, "errors": []})


class PackSetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = harness(self.root / "harness")
        tasks = [task_spec("short-one"), task_spec("long-one", True, 12), task_spec("rule-one")]
        self.pack = make_pack(self.root / "model-citizen-evals", tasks=tasks, name="model-citizen-evals",
                              version="1.1.0", sets={
                                  "production": {"tasks": ["short-one", "long-one"]},
                                  "rule-targeted": {"tier": "production", "tasks": ["rule-one"]},
                                  "outcome-public": {"tier": "production", "tasks": ["short-one"]}})

    def test_every_production_tier_set_is_offered_by_name_version_digest_and_set(self):
        catalog = replay.task_catalog(self.source)
        listed = [(item["name"], item["version"], item["set"], [task["id"] for task in item["tasks"]])
                  for item in catalog["packs"]]
        self.assertEqual(listed, [("model-citizen-evals", "1.1.0", "production", ["short-one", "long-one"]),
                                  ("model-citizen-evals", "1.1.0", "outcome-public", ["short-one"]),
                                  ("model-citizen-evals", "1.1.0", "rule-targeted", ["rule-one"])])
        self.assertEqual(len({item["digest"] for item in catalog["packs"]}), 1)

    def test_a_chosen_set_is_pinned_into_the_native_command(self):
        chosen = next(item for item in packs.discover(self.source)["packs"] if item["set"] == "rule-targeted")
        request = replay.resolve_request({
            "targets": [{"kind": "draft", "ref": "c"}, {"kind": "draft", "ref": "d"}],
            "model": "m", "repetitions": 1, "tasks": ["rule-one"], "max_budget_usd": "1",
            "spend_cap_usd": "2", "pre_registration": None,
            "pack": {"name": chosen["name"], "digest": chosen["digest"], "set": "rule-targeted"}},
            target, lambda name, digest, set_name: packs.select(self.source, name, digest, set_name))
        self.assertEqual(request.pack["set"], "rule-targeted")
        self.assertEqual(replay.ReplayRequest.parse(request.as_dict()), request)
        command = replay.command_for_target(request, request.targets[1], self.source, self.root / "out")
        at = command.index("--pack-set")
        self.assertEqual(command[at:at + 2], ["--pack-set", "rule-targeted"])
        replay.validate_task_selection(self.source, request)
        self.assertTrue(replay.whole_set(self.source, request))

    def test_a_selection_naming_no_set_keeps_its_identity_and_command(self):
        chosen = packs.discover(self.source)["packs"][0]
        request = replay.resolve_request({
            "targets": [{"kind": "draft", "ref": "a"}, {"kind": "draft", "ref": "b"}],
            "model": "m", "repetitions": 1, "tasks": ["short-one"], "max_budget_usd": "1",
            "spend_cap_usd": "2", "pre_registration": None,
            "pack": {"name": chosen["name"], "digest": chosen["digest"]}},
            target, lambda name, digest: packs.select(self.source, name, digest))
        self.assertEqual(set(request.pack), replay.PACK_KEYS)
        self.assertNotIn("--pack-set", replay.command_for_target(
            request, request.targets[0], self.source, self.root / "out"))

    def test_an_unknown_set_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not available at that digest and set"):
            packs.select(self.source, "model-citizen-evals", packs.discover(self.source)["packs"][0]["digest"],
                         "held-out")


PLAN = """# Plan

## Guardrails

- **Spend:** {spend}

## Sample size

- **Tasks:** 2, of which 1 are long multi-turn tasks.
- **Trials per task and arm:** 5
"""


class RegisteredBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "benchmarks").mkdir()
        (self.root / "benchmarks" / "tasks.json").write_text(json.dumps(
            {"schema_version": 1, "tasks": [{"id": "one", "long": True}, {"id": "two"}]}), encoding="utf-8")
        self.plan("2 USD per run (soft), 0.25 USD per arm preflight, and a whole-run\n  stop of 40 USD.")

    def plan(self, spend):
        (self.root / "plan.md").write_text(PLAN.format(spend=spend), encoding="utf-8")

    def request(self, maximum="2", cap="40"):
        return replay.ReplayRequest.parse({
            "targets": [{"kind": "release", "ref": "v1.0.0", "revision": "a" * 40, "version": "1.0.0",
                         "draft": None, "config_digest": None},
                        {"kind": "release", "ref": "v1.1.0", "revision": "b" * 40, "version": "1.1.0",
                         "draft": None, "config_digest": None}],
            "model": "m", "repetitions": 5, "tasks": ["one", "two"], "max_budget_usd": maximum,
            "spend_cap_usd": cap, "pre_registration": "plan.md", "evidence": "exploratory"})

    def admission(self, estimate):
        supervisor = SimpleNamespace(spend_preview=lambda *args, **kwargs: {
            "estimate": {"amount_usd": estimate, "basis": "history", "sample_count": 3},
            "caps": {}, "confirmation_token": "token"},
            start=lambda *args, **kwargs: self.fail("a paid run was started"))
        admission = replay.ReplayAdmission(self.root, self.root / "state", supervisor, None)
        admission._confirm_resolved = lambda request: None  # the targets are fixed here
        return admission

    def test_the_registered_budget_is_read_from_the_plans_spend_field(self):
        budget = replay.registered_budget(self.root, "plan.md")
        self.assertEqual((budget["per_run_usd"], budget["whole_run_cap_usd"]), ("2", "40"))
        shipped = replay.registered_budget(REPO, "benchmarks/preregistrations/2026-10-01-power-pilot.md")
        self.assertEqual((shipped["per_run_usd"], shipped["whole_run_cap_usd"]), ("2", "140.50"))

    def test_a_registered_launch_without_a_readable_budget_is_refused_with_no_default(self):
        for spend in ("<the per-trial budget and the whole-run cap>", "2 USD per run, no cap", "none"):
            self.plan(spend)
            self.assertIsNone(replay.registered_budget(self.root, "plan.md"))
            with self.assertRaises(replay.ReplayRefusal) as caught:
                replay.label_evidence(self.root, self.request())
            self.assertEqual(caught.exception.code, "replay_budget_unregistered")

    def test_a_launch_over_the_registered_budget_is_refused(self):
        for maximum, cap, named in (("2.5", "40", "per-run budget 2.5"), ("2", "41", "spend cap 41")):
            with self.assertRaises(replay.ReplayRefusal) as caught:
                replay.label_evidence(self.root, self.request(maximum, cap))
            self.assertEqual(caught.exception.code, "replay_budget_exceeded")
            self.assertIn(named, str(caught.exception))
            # The launch re-labels before it starts, so the same request is refused there too.
            with self.assertRaises(replay.ReplayRefusal):
                self.admission(1.0).confirm(dict(self.request(maximum, cap).as_dict(),
                                                 evidence="pre-registered"))

    def test_the_spend_guards_estimate_over_the_registered_cap_is_refused_at_preview(self):
        labelled = replay.label_evidence(self.root, self.request())
        self.assertEqual(labelled.evidence, replay.PREREGISTERED)
        with self.assertRaises(replay.ReplayRefusal) as caught:
            self.admission(40.01).preview_resolved(labelled)
        self.assertEqual(caught.exception.code, "replay_budget_exceeded")
        self.assertIn("estimated spend 40.01", str(caught.exception))
        preview = self.admission(39.0).preview_resolved(labelled)
        self.assertEqual(preview["sampling"]["registered"]["budget"]["whole_run_cap_usd"], "40")

    def test_an_exploratory_replay_needs_no_registered_budget(self):
        self.plan("none")
        subset = replay.ReplayRequest.parse(dict(self.request().as_dict(), tasks=["one"]))
        self.assertEqual(replay.label_evidence(self.root, subset).evidence, replay.EXPLORATORY)


def write_fixture():
    with tempfile.TemporaryDirectory() as root:
        scorecard, judge, _fixture = engine_documents(Path(root))
    FIXTURE.write_text(json.dumps({
        "_source": "python3 tests/test_studio_definitive_evaluation.py --write: scripts/layer_scorecard.py "
                   "--json and scripts/replay_judge.py report --json over the test_layer_scorecard fixture",
        "scorecard": scorecard, "judge": judge}, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_fixture()
    else:
        unittest.main()
