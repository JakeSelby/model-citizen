"""The definitive evaluation in the Studio (AH-S345): strata, config arms and long-session rows in
the run store, the layer scorecard and judge on run detail, and pack sets in the replay form. The
definitive launch behind its registered budget is in `test_studio_definitive_launch.py`.

`studio/tests/fixtures/definitive-evaluation.json` is the engine's own scorecard and judge section
for the `test_layer_scorecard` fixture, which `studio/tests/definitive-evaluation.test.ts` feeds
through the run detail display. Regenerate it with:

    python3 tests/test_studio_definitive_evaluation.py --write
"""
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import definitive, evaluation, packs, replay, runs  # noqa: E402
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


class JudgeReadTests(unittest.TestCase):
    """The judge report runs once per folder state, one child at a time, and never through a link."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.fixture = scorecard_fixture.Fixture(self.root / "engine")
        self.repository = self.root / "repository"
        self.tag = self.repository / "benchmarks" / "1.0.0" / "v1.0.0"
        write_rows(self.tag / "m-one" / "results.jsonl", [row("t1", "harness", 1, "m-one")])
        self.relative = "benchmarks/1.0.0/v1.0.0/m-one/results.jsonl"
        definitive._JUDGE_CACHE.clear()
        self.addCleanup(definitive._JUDGE_CACHE.clear)

    def copy_judge(self, destination):
        destination.mkdir(parents=True)
        for name in definitive.JUDGE_FILES:
            (destination / name).write_bytes((self.fixture.judge / name).read_bytes())

    def test_concurrent_reads_start_one_judge_process_and_a_reload_starts_none(self):
        self.copy_judge(self.tag / definitive.JUDGE_FOLDER)
        real, started, gate = subprocess.run, [], threading.Event()

        def counted(*args, **kwargs):
            started.append(args[0])
            gate.wait(10)
            return real(*args, **kwargs)

        with mock.patch.object(definitive.subprocess, "run", counted):
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                futures = [pool.submit(definitive.engine_reports, self.repository, self.relative)
                           for _ in range(4)]
                time.sleep(0.5)
                gate.set()
                results = [future.result(60) for future in futures]
            self.assertEqual(len(started), 1)
            self.assertTrue(all(item["judge"] == results[0]["judge"] for item in results))
            self.assertEqual(results[0]["errors"], [])
            definitive.engine_reports(self.repository, self.relative)
            self.assertEqual(len(started), 1)
            # A changed judge file is a new folder state, so it is read again.
            verdicts = self.tag / definitive.JUDGE_FOLDER / "verdicts.jsonl"
            verdicts.write_bytes(verdicts.read_bytes() + b"\n")
            definitive.engine_reports(self.repository, self.relative)
            self.assertEqual(len(started), 2)

    def test_a_judge_folder_that_is_a_link_or_resolves_outside_the_run_is_refused(self):
        outside = self.root / "elsewhere" / "judge"
        self.copy_judge(outside)
        (self.tag / definitive.JUDGE_FOLDER).symlink_to(outside, target_is_directory=True)
        with mock.patch.object(definitive.subprocess, "run") as launch:
            reports = definitive.engine_reports(self.repository, self.relative)
        launch.assert_not_called()
        self.assertIsNone(reports["judge"])
        self.assertEqual(reports["errors"],
                         ["diff-quality judge: the judge folder is a link; nothing is read through a link"])

    def test_a_folder_inside_a_linked_parent_resolves_outside_and_is_refused(self):
        real_tag = self.root / "real-tag"
        self.copy_judge(real_tag / definitive.JUDGE_FOLDER)
        linked = self.repository / "benchmarks" / "1.0.0" / "linked"
        linked.symlink_to(real_tag, target_is_directory=True)
        write_rows(real_tag / "m-one" / "results.jsonl", [row("t1", "harness", 1, "m-one")])
        self.assertEqual(definitive._judge_folder_problem(linked),
                         "the judge folder resolves outside the run folder")


class EngineOutputTests(unittest.TestCase):
    """The engine documents run detail and the replay analysis show generically: every arm with its
    comparisons and named primaries, a partial set with its cells used, and not-applicable
    detections. The committed display fixture is their output."""

    def test_the_committed_engine_outputs_are_what_the_engine_prints(self):
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        generated = engine_outputs()
        for name in ("every_arm", "partial", "joint_compliance"):
            self.assertEqual(committed[name], generated[name],
                             "regenerate: python3 tests/test_studio_definitive_evaluation.py --write")
        self.assertEqual(committed["every_arm"]["arm_names"], ["bare", "harness", "frugal"])
        self.assertIn("comparisons", committed["every_arm"])
        self.assertEqual(committed["every_arm"]["primary_named"], [])
        self.assertEqual(committed["partial"]["verdict"], "partial")
        self.assertEqual(committed["partial"]["partial"]["cells_used"], 5)
        rules = committed["joint_compliance"]["arms"]["harness"]["per_rule"]
        self.assertTrue(any(item.get("not_applicable") for item in rules.values()))


def engine_outputs():
    """`summarise --json` over every declared arm and over a partial set, and the joint
    compliance over a not-applicable detection, each from the engine's own test fixtures."""
    import test_detector_applicability as applicability
    import test_replay_every_arm as every_arm
    import test_replay_stats_partial as partial
    with tempfile.TemporaryDirectory() as root:
        arms = Path(root) / "arms" / "results.jsonl"
        every_arm.write_rows(arms, every_arm.arm_rows())
        _code, arms_out = every_arm.summarise(arms, True)
        cut = Path(root) / "partial" / "results.jsonl"
        every_arm.write_rows(cut, partial.stopped_run())
        _code, partial_out = partial.summarise(str(cut), "--json")
    runs_ = [{"task": "t", "arm": "harness", "rep": 1},
             {"task": "t", "arm": "terse", "rep": 1, "arm_config": {"stances": {"voice": "concise"}}}]
    detections = [item for run in runs_ for item in applicability.rows_of(run).values()]
    return {"every_arm": json.loads(arms_out), "partial": json.loads(partial_out),
            "joint_compliance": applicability.REL.joint_compliance(runs_, detections)}


def write_fixture():
    with tempfile.TemporaryDirectory() as root:
        scorecard, judge, _fixture = engine_documents(Path(root))
    FIXTURE.write_text(json.dumps(dict({
        "_source": "python3 tests/test_studio_definitive_evaluation.py --write: scripts/layer_scorecard.py "
                   "--json and scripts/replay_judge.py report --json over the test_layer_scorecard fixture; "
                   "cost_bench.py summarise --json over the every-arm and partial-set test rows; "
                   "replay_reliability.joint_compliance over a not-applicable detection",
        "scorecard": scorecard, "judge": judge}, **engine_outputs()), indent=1, sort_keys=True) + "\n",
        encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_fixture()
    else:
        unittest.main()
