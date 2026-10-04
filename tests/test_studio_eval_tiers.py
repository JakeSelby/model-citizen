"""The evaluation tiers and unit evals run from the Studio (AH-S305).

The engines' output reaches the route unchanged: the unit eval's analysis equals
`cost_bench.py summarise --json` for the same rows and the committed fixture
`studio/tests/fixtures/eval-unit-analysis.json`, and the hook matrix result is the engine's own
expansion of its matrix, committed as `studio/tests/fixtures/eval-hook-matrix.json` for the
TypeScript grid test. Regenerate both fixtures with:

    python3 tests/test_studio_eval_tiers.py --write
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import eval_tiers, replay, runs, server, spend_guard  # noqa: E402
from studio_target_support import FixtureTargetService  # noqa: E402
import test_studio_security as studio_security  # noqa: E402

UNIT_FIXTURE = REPO / "tests" / "fixtures" / "unit-economy" / "results.jsonl"
UNIT_ANALYSIS = REPO / "studio" / "tests" / "fixtures" / "eval-unit-analysis.json"
HOOK_RESULT = REPO / "studio" / "tests" / "fixtures" / "eval-hook-matrix.json"
RAW = REPO / "tests" / "fixtures" / "replay-detect" / "raw"
REVISION = "c" * 40
RUN_ID = "11111111-2222-4333-8444-555555555555"


def cli_summarise(results):
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "cost_bench.py"), "summarise",
                           "--results", str(results), "--json"], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    return json.loads(done.stdout)


def row_charge(rows_from=UNIT_FIXTURE, run_cap="2"):
    rows = [json.loads(line) for line in Path(rows_from).read_text().splitlines() if line.strip()]
    return round(float(replay.charged_spend(rows, run_cap)), 6)


def fake_replay(rows_from=UNIT_FIXTURE, charged=None, stopped=False, returncode=0,
                writes=True, sidecar=True, edit=None):
    """A `launch` that stands in for the paid `cost_bench.py replay` and runs the rest for real."""
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        if command[2] != "replay":
            return subprocess.run(command, **kwargs)
        out = Path(command[command.index("--out") + 1]) / command[command.index("--tag") + 1]
        if writes:
            out.mkdir(parents=True)
            shutil.copyfile(str(rows_from), str(out / replay.RESULTS_NAME))
            scored = row_charge(rows_from, command[command.index("--run-cap") + 1])
            scored = scored if charged is None else charged
            spend = {"schema_version": 1, "tag": command[command.index("--tag") + 1],
                     "run_cap_usd": float(command[command.index("--run-cap") + 1]),
                     "spend_cap_usd": float(command[command.index("--spend-cap") + 1]),
                     "preflight_spend_usd": 0.0, "scored_spend_usd": scored,
                     "charged_spend_usd": scored, "stopped_at_cap": stopped}
            if edit:
                edit(spend)
            if sidecar:
                (out / replay.SPEND_NAME).write_text(json.dumps(spend))
                os.chmod(str(out / replay.SPEND_NAME), 0o600)
        return SimpleNamespace(returncode=returncode)
    return launch, calls


def run_unit_eval(root, **fake):
    run_dir = Path(root) / RUN_ID
    run_dir.mkdir(parents=True)
    launch, calls = fake_replay(**fake)
    code = eval_tiers.run_paid("unit-eval", REPO, REVISION, "2", "50",
                               run_dir / spend_guard.RESULT_NAME, RUN_ID, launch,
                               unit="rules.secrets", model="claude-test")
    return code, run_dir, calls


class FakeSupervisor:
    def __init__(self, root):
        self.root = Path(root)
        self.records = {}
        self.previews = []
        self.starts = []

    def show(self, run_id):
        if run_id not in self.records:
            raise runs.RunError("unknown run")
        return dict(self.records[run_id])

    def _run_path(self, run_id):
        return self.root / run_id / "run.json"

    def spend_preview(self, suite_id, parameters, kind, ref, maximum, cap, pricing, **_kw):
        self.previews.append((suite_id, dict(parameters), kind, ref, maximum, cap, pricing))
        return {"estimate": {"amount_usd": None}, "caps": {"max_budget_usd": maximum,
                                                         "spend_cap_usd": cap},
                "pricing": {"source": pricing}, "confirmation_required": True,
                "confirmation_token": "token", "cost_class": "spends_usage",
                "case_identities": [suite_id]}

    def start(self, suite_id, parameters, kind, ref, **kwargs):
        self.starts.append((suite_id, dict(parameters), kind, ref, kwargs))
        return {"run_id": RUN_ID, "status": "queued"}


class FakeReplayAdmission:
    revision = REVISION

    def _resolve(self, kind, ref):
        if ref == "dirty":
            raise replay.ReplayRefusal("replay_worktree_dirty", "dirty")
        return {"kind": kind, "ref": ref, "revision": self.revision, "version": None,
                "draft": None, "config_digest": replay.DEFAULT_CONFIG_DIGEST}


def admission(supervisor, repository=REPO):
    value = eval_tiers.EvalAdmission.__new__(eval_tiers.EvalAdmission)
    value.repository = Path(repository).resolve()
    value.supervisor = supervisor
    value.replay = FakeReplayAdmission()
    value.confirmed_target = None
    return value


def paid_request(**changes):
    value = {"suite": "unit-eval", "target": {"kind": "branch", "ref": "main"},
             "unit": "rules.secrets", "max_budget_usd": "2", "spend_cap_usd": "50"}
    value.update(changes)
    return value


def partial_repository(root, omit):
    """A repository holding every tier engine file except `omit`."""
    for tier in eval_tiers.TIERS.values():
        for name in tier.engines:
            if name != omit:
                path = Path(root) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
    return Path(root)


def hook_engine_detect():
    return eval_tiers._load_file_module(REPO / "scripts" / "replay_detect.py", "detect_test")


def hook_engine(name="hook_matrix_test"):
    return eval_tiers._load_file_module(
        REPO / "tests" / "fixtures" / "hook-calls" / "hook_matrix.py", name)


class CatalogTests(unittest.TestCase):
    def test_every_tier_is_present_here_and_parses_from_the_shared_catalog(self):
        self.assertEqual(eval_tiers.available(REPO), list(eval_tiers.TIERS))
        catalog = runs.SuiteCatalog.load(runs.default_catalog_path(REPO))
        for suite, tier in eval_tiers.TIERS.items():
            spec = catalog.get(suite)
            self.assertEqual(spec.cost_class, tier.cost_class)
            self.assertEqual(spec.argv[:3], ("python3", "-m", "harness_core.studio.eval_tiers"))
        payload = eval_tiers.catalog(REPO)
        self.assertEqual([item["id"] for item in payload["tiers"]], list(eval_tiers.TIERS))
        self.assertIn("rules.secrets", payload["units"])

    def test_an_engine_not_yet_merged_leaves_its_suite_absent_not_broken(self):
        for suite, tier in eval_tiers.TIERS.items():
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as tmp:
                root = partial_repository(tmp, tier.engines[-1])
                self.assertNotIn(suite, eval_tiers.available(root))
                self.assertNotIn(suite, [item["id"] for item in eval_tiers.catalog(root)["tiers"]])
                with self.assertRaises(eval_tiers.EvalTierError) as caught:
                    eval_tiers.require_available(root, suite)
                self.assertEqual(caught.exception.code, "eval_engine_absent")
        with tempfile.TemporaryDirectory() as tmp:
            root = partial_repository(tmp, "scripts/unit_economy.py")
            self.assertEqual(eval_tiers.catalog(root)["units"], [])

    def test_one_dated_model_is_shared_with_the_replay_and_read_from_the_micro_manifest(self):
        pinned = json.loads((REPO / "benchmarks" / "micro" / "tasks.json").read_text())["model"]
        self.assertRegex(pinned, r"-[0-9]{8}$")
        self.assertEqual((replay.DEFAULT_MODEL, eval_tiers.DEFAULT_MODEL), (pinned, pinned))
        # No packs: discovery beside this checkout would read the user's own repositories.
        with mock.patch.object(replay.packs, "discover", return_value={"packs": [], "default_digest": None, "skipped": []}):
            self.assertEqual(replay.task_catalog(REPO)["default_model"], pinned)
        self.assertEqual(eval_tiers.catalog(REPO)["unit_model"], pinned)
        with mock.patch.object(replay.Path, "read_text", side_effect=OSError):
            self.assertEqual(replay._pinned_model(), "claude-haiku-4-5-20251001")

    def test_the_paid_suites_render_with_the_guards_flags_appended_once(self):
        catalog = runs.SuiteCatalog.load(runs.default_catalog_path(REPO))
        parameters = eval_tiers.PaidRequest.parse(
            dict(paid_request(), revision=REVISION), resolved=True).parameters(REPO)
        argv = catalog.get("unit-eval").render(parameters, "branch", "main")
        plan = spend_guard.plan([], "unit-eval", ["unit-eval"], "2", "50", "api_credit")
        guarded = spend_guard.guarded_argv(argv, plan)
        self.assertEqual(guarded[-4:], ["--max-budget-usd", "2", "--spend-cap", "50"])
        self.assertEqual(guarded[guarded.index("--unit") + 1], "rules.secrets")


class RequestTests(unittest.TestCase):
    def test_bad_requests_are_refused_by_name(self):
        cases = [
            paid_request(suite="hook-matrix"),
            paid_request(unit="skills.x"),
            paid_request(unit="rules.--x"[:6]),
            paid_request(model="claude-test"),
            paid_request(repetitions=3),
            paid_request(max_budget_usd="60"),
            paid_request(spend_cap_usd="nan"),
            paid_request(target={"kind": "branch"}),
            paid_request(target={"kind": "nowhere", "ref": "x"}),
            paid_request(revision=REVISION),
            dict(paid_request(), extra=1),
            {"suite": "micro-tier", "target": {"kind": "branch", "ref": "main"},
             "unit": "rules.secrets", "max_budget_usd": "1", "spend_cap_usd": "5"},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(eval_tiers.EvalTierError) as caught:
                eval_tiers.PaidRequest.parse(value)
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_the_micro_tier_takes_only_a_target_and_caps(self):
        request = eval_tiers.PaidRequest.parse({
            "suite": "micro-tier", "target": {"kind": "release", "ref": "v0.15.0"},
            "max_budget_usd": "0.5", "spend_cap_usd": "5"})
        self.assertEqual(set(request.as_dict()), {"suite", "target", "max_budget_usd",
                                                  "spend_cap_usd"})


class PaidRunnerTests(unittest.TestCase):
    def test_the_unit_eval_records_the_engines_analysis_and_a_guard_readable_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, run_dir, calls = run_unit_eval(tmp)
            self.assertEqual(code, 0)
            replay_call = calls[0]
            self.assertEqual(replay_call[2:6], ["replay", "--tag", REVISION, "--design"])
            self.assertIn("--exploratory", replay_call)
            self.assertEqual(replay_call[replay_call.index("--unit") + 1], "rules.secrets")
            self.assertNotIn("--reps", replay_call)
            descriptor = os.open(str(run_dir), os.O_RDONLY)
            try:
                spent = spend_guard.read_result(descriptor, RUN_ID, ["unit-eval"])
            finally:
                os.close(descriptor)
            self.assertEqual(spent["spend_usd"], row_charge())
            self.assertIsNone(spent["stop_reason"])
            self.assertEqual(spent["cases"][0]["status"], "completed")
            analysis = json.loads((run_dir / "eval" / eval_tiers.ANALYSIS_NAME).read_text())
        self.assertIsNone(analysis["error"])
        self.assertEqual(analysis["result"], cli_summarise(UNIT_FIXTURE))
        self.assertEqual(analysis["result"]["design"], "unit-economy-2x2")
        self.assertEqual(analysis["result"]["schema"], 1)
        self.assertEqual(json.loads(UNIT_ANALYSIS.read_text()), analysis["result"])

    def test_a_refusal_before_spend_reports_nothing_run_and_no_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, run_dir, _calls = run_unit_eval(tmp, writes=False, returncode=2)
            self.assertEqual(code, 2)
            descriptor = os.open(str(run_dir), os.O_RDONLY)
            try:
                spent = spend_guard.read_result(descriptor, RUN_ID, ["unit-eval"])
            finally:
                os.close(descriptor)
            analysis = json.loads((run_dir / "eval" / eval_tiers.ANALYSIS_NAME).read_text())
        self.assertEqual((spent["spend_usd"], spent["stop_reason"]), (0.0, "runner_failure"))
        self.assertEqual(spent["cases"][0]["status"], "not_run")
        self.assertIsNone(analysis["result"])
        self.assertIn("wrote no results", analysis["error"])

    def test_a_run_stopped_at_its_cap_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, run_dir, _calls = run_unit_eval(tmp, stopped=True, returncode=1)
            value = json.loads((run_dir / spend_guard.RESULT_NAME).read_text())
        self.assertEqual((code, value["stop_reason"]), (1, "spend_cap"))

    def spent(self, run_dir):
        descriptor = os.open(str(run_dir), os.O_RDONLY)
        try:
            return spend_guard.read_result(descriptor, RUN_ID, ["unit-eval"])
        finally:
            os.close(descriptor)

    def test_unknown_spend_charges_the_whole_cap_and_still_writes_a_result(self):
        cases = {
            "missing": dict(sidecar=False, returncode=2),
            "malformed": dict(edit=lambda spend: spend.pop("run_cap_usd")),
            "no caps": dict(edit=lambda spend: spend.update(run_cap_usd=None)),
            "disagrees with the rows": dict(charged=0.01),
            "other revision": dict(edit=lambda spend: spend.update(tag="d" * 40)),
            "exit disagrees": dict(returncode=1),
        }
        for name, fake in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                code, run_dir, _calls = run_unit_eval(tmp, **fake)
                spent = self.spent(run_dir)
                analysis = json.loads((run_dir / "eval" / eval_tiers.ANALYSIS_NAME).read_text())
                self.assertEqual((code, spent["spend_usd"], spent["stop_reason"]),
                                 (2, 50.0, "runner_failure"))
                self.assertEqual(spent["cases"][0]["status"], "completed")
                self.assertIn("whole cap was charged", analysis["spend_error"])

    def test_an_exit_cost_bench_never_gives_charges_the_whole_cap(self):
        for code in (-9, 3, 137):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                exit_code, run_dir, _calls = run_unit_eval(tmp, returncode=code)
                spent = self.spent(run_dir)
                self.assertEqual((exit_code, spent["spend_usd"], spent["stop_reason"]),
                                 (2, 50.0, "runner_failure"))
        target = replay.ReplayTarget.parse({"kind": "branch", "ref": "main", "revision": REVISION})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(replay.ReplayError) as caught:
            replay._verify_target_output(None, target, Path(tmp) / "absent", -9, "5")
        self.assertNotIsInstance(caught.exception, replay._NothingSpent)

    def test_a_cancel_mid_launch_still_writes_a_whole_cap_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / RUN_ID
            run_dir.mkdir()
            launch, _ = fake_replay()

            def cancelled(command, **kwargs):
                if command[2] == "replay":
                    launch(command, **kwargs)
                    eval_tiers._terminate_cleanly(15, None)
                return subprocess.run(command, **kwargs)
            with self.assertRaises(SystemExit):
                eval_tiers.run_paid("unit-eval", REPO, REVISION, "2", "50",
                                    run_dir / spend_guard.RESULT_NAME, RUN_ID, cancelled,
                                    unit="rules.secrets", model="claude-test")
            spent = self.spent(run_dir)
            analysis = json.loads((run_dir / "eval" / eval_tiers.ANALYSIS_NAME).read_text())
        self.assertEqual((spent["spend_usd"], spent["stop_reason"]), (50.0, "runner_failure"))
        self.assertIn("stopped", analysis["error"])

    def test_the_runner_writes_a_result_when_a_cancel_interrupts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / spend_guard.RESULT_NAME
            env = {"CITIZEN_STUDIO_RESULT": str(result), "CITIZEN_STUDIO_RUN_ID": RUN_ID}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(eval_tiers, "run_paid", side_effect=SystemExit(143)), \
                    self.assertRaises(SystemExit):
                eval_tiers.main(["unit-eval", "--repository", str(REPO), "--revision",
                                 REVISION, "--unit", "rules.secrets", "--model", "m",
                                 "--max-budget-usd", "2", "--spend-cap", "50"])
            self.assertEqual(json.loads(result.read_text())["spend_usd"], 50.0)

    def test_a_launch_that_raises_is_settled_from_what_it_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / RUN_ID
            run_dir.mkdir()

            def broken(command, **kwargs):
                if command[2] == "replay":
                    raise RuntimeError("launch failed")
                return subprocess.run(command, **kwargs)
            code = eval_tiers.run_paid("unit-eval", REPO, REVISION, "2", "50",
                                       run_dir / spend_guard.RESULT_NAME, RUN_ID, broken,
                                       unit="rules.secrets", model="claude-test")
            spent = self.spent(run_dir)
        self.assertEqual((code, spent["spend_usd"], spent["stop_reason"]),
                         (2, 0.0, "runner_failure"))

    def test_the_runner_writes_a_whole_cap_result_when_it_fails_unexpectedly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / spend_guard.RESULT_NAME
            env = {"CITIZEN_STUDIO_RESULT": str(result), "CITIZEN_STUDIO_RUN_ID": RUN_ID}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(eval_tiers, "run_paid", side_effect=ArithmeticError("x")):
                code = eval_tiers.main(["unit-eval", "--repository", str(REPO), "--revision",
                                        REVISION, "--unit", "rules.secrets", "--model", "m",
                                        "--max-budget-usd", "2", "--spend-cap", "50"])
            value = json.loads(result.read_text())
        self.assertEqual((code, value["spend_usd"], value["stop_reason"]),
                         (2, 50.0, "runner_failure"))


class FreeRunnerTests(unittest.TestCase):
    def test_rule_detection_matches_the_cli_and_never_writes_the_saved_directory(self):
        before = sorted(path.name for path in RAW.iterdir())
        result = eval_tiers.run_rule_detection(REPO, RAW)
        self.assertEqual(sorted(path.name for path in RAW.iterdir()), before)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "raw"
            shutil.copytree(str(RAW), str(copy))
            subprocess.run([sys.executable, str(REPO / "scripts" / "cost_bench.py"), "detect",
                            "--raw", str(copy)], check=True, stdout=subprocess.PIPE)
            expected = [json.loads(line) for line in
                        (copy / "detections.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(result["detections"], expected)
        self.assertTrue(result["detections"])

    def test_rule_detection_copies_only_top_level_run_files_within_its_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            (raw / "nested").mkdir(parents=True)
            shutil.copyfile(str(RAW / "gate-run-bare-1.json"), str(raw / "gate-run-bare-1.json"))
            (raw / "nested" / "deep-bare-1.json").write_text("{}")
            (raw / "notes.txt").write_text("x")
            (raw / "linked-bare-2.json").symlink_to(RAW / "gate-run-harness-1.json")
            copy = Path(tmp) / "copy"
            parse_name = hook_engine_detect().parse_name
            self.assertEqual(eval_tiers.copy_run_files(raw, copy, parse_name), 1)
            self.assertEqual(sorted(p.name for p in copy.iterdir()), ["gate-run-bare-1.json"])
            with mock.patch.object(eval_tiers, "MAX_RAW_BYTES", 1), \
                    self.assertRaises(eval_tiers.EvalTierError):
                eval_tiers.copy_run_files(raw, Path(tmp) / "copy-2", parse_name)
            empty = Path(tmp) / "empty"
            (empty / "nested").mkdir(parents=True)
            with self.assertRaises(eval_tiers.EvalTierError):
                eval_tiers.run_rule_detection(REPO, empty)

    def test_the_copy_filter_uses_the_detect_commands_arms(self):
        bench = eval_tiers._load_file_module(REPO / "scripts" / "cost_bench.py", "bench_test")
        self.assertEqual(eval_tiers.DETECT_ARMS, bench.DETECT_ARMS)

    def test_the_byte_cap_counts_what_is_actually_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "a-bare-1.json").write_bytes(b"x" * 10)
            real_open = os.open

            def grows(path, flags, *args):
                # The file grows between the scan and the copy.
                if str(path).endswith("a-bare-1.json") and not flags & os.O_WRONLY:
                    (raw / "a-bare-1.json").write_bytes(b"x" * 40)
                return real_open(path, flags, *args)
            with mock.patch.object(eval_tiers, "MAX_RAW_BYTES", 20), \
                    mock.patch.object(eval_tiers.os, "open", side_effect=grows), \
                    self.assertRaises(eval_tiers.EvalTierError):
                eval_tiers.copy_run_files(raw, Path(tmp) / "copy", hook_engine_detect().parse_name)

    def test_a_terminated_detection_removes_its_copy(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(tempfile, "tempdir", tmp), \
                mock.patch.object(eval_tiers.subprocess, "run",
                                  side_effect=lambda *a, **k: eval_tiers._terminate_cleanly(15, None)):
            with self.assertRaises(SystemExit):
                eval_tiers.run_rule_detection(REPO, RAW)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_the_hook_matrix_is_the_engines_own_expansion_and_is_committed(self):
        env = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
        env["PYTHONPATH"] = str(REPO / "lib")
        done = subprocess.run([sys.executable, "-m", "harness_core.studio.eval_tiers",
                               "hook-matrix", "--root", str(REPO)], cwd=str(REPO), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = eval_tiers.free_result(done.stdout)
        engine = hook_engine()
        committed = json.loads(engine.MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(result["moved"], [])
        expanded = engine.expand(committed)
        for row in committed["rows"]:
            for line in result["grid"][row]:
                for variant, cell in zip(committed["variants"], line["cells"]):
                    self.assertEqual(cell, expanded[(row, line["call"], variant)])
        self.assertEqual(sum(len(line["cells"]) for lines in result["grid"].values()
                             for line in lines), len(expanded))
        self.assertEqual(json.loads(HOOK_RESULT.read_text()), result)


class ResultTests(unittest.TestCase):
    def test_results_are_read_per_suite_and_only_once_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = FakeSupervisor(tmp)
            run_dir = Path(tmp) / RUN_ID
            (run_dir / "eval").mkdir(parents=True)
            supervisor.records[RUN_ID] = {"run_id": RUN_ID, "suite_id": "hook-matrix",
                                          "status": "running"}
            self.assertIsNone(eval_tiers.result_payload(supervisor, RUN_ID)["result"])
            supervisor.records[RUN_ID]["status"] = "succeeded"
            missing = eval_tiers.result_payload(supervisor, RUN_ID)
            self.assertEqual(missing["analysis_error"], "the engine printed no result for this run")
            (run_dir / "stdout.log").write_text("noise\n" + json.dumps(
                {"marker": eval_tiers.RESULT_MARKER, "suite": "hook-matrix",
                 "result": {"grid": {}}}) + "\n")
            self.assertEqual(eval_tiers.result_payload(supervisor, RUN_ID)["result"],
                             {"grid": {}})
            supervisor.records[RUN_ID]["suite_id"] = "unit-eval"
            self.assertEqual(eval_tiers.result_payload(supervisor, RUN_ID)["analysis_error"],
                             "no engine analysis was recorded for this run")
            (run_dir / "eval" / eval_tiers.ANALYSIS_NAME).write_text(
                json.dumps({"result": {"design": "unit-economy-2x2"}, "error": None}))
            self.assertEqual(eval_tiers.result_payload(supervisor, RUN_ID)["result"]["result"],
                             {"design": "unit-economy-2x2"})
            supervisor.records[RUN_ID]["suite_id"] = "lint"
            with self.assertRaises(runs.RunError):
                eval_tiers.result_payload(supervisor, RUN_ID)


class AdmissionTests(unittest.TestCase):
    def test_a_paid_tier_previews_through_the_guard_and_starts_on_the_same_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = FakeSupervisor(tmp)
            gate = admission(supervisor)
            resolved = gate.resolve(paid_request())
            preview = gate.preview_resolved(resolved)
            self.assertEqual(preview["request"]["revision"], REVISION)
            self.assertEqual(preview["evidence"], "exploratory")
            self.assertIn("--design unit-economy --unit rules.secrets", preview["command"])
            suite, parameters, kind, ref, maximum, cap, pricing = supervisor.previews[0]
            self.assertEqual((suite, kind, ref, maximum, cap, pricing),
                             ("unit-eval", "branch", "main", "2", "50", "api_credit"))
            self.assertEqual(parameters["revision"], REVISION)
            confirmed = gate.confirm(preview["request"])
            started = gate.start_confirmed(confirmed, "token")
            self.assertEqual(started["run_id"], RUN_ID)
            self.assertEqual(supervisor.starts[0][4]["confirmed"], "token")
            self.assertEqual(supervisor.starts[0][4]["expected_target"]["revision"], REVISION)
            self.assertNotIn("model", preview["request"])
            self.assertEqual(parameters["model"], eval_tiers.DEFAULT_MODEL)
            gate.replay.revision = "e" * 40
            with self.assertRaises(eval_tiers.EvalTierError) as caught:
                gate.confirm(preview["request"])
            self.assertEqual(caught.exception.code, "eval_target_changed")
            with self.assertRaises(eval_tiers.EvalTierError) as caught:
                gate.resolve(paid_request(target={"kind": "worktree", "ref": "dirty"}))
            self.assertEqual(caught.exception.code, "replay_worktree_dirty")

    def test_free_tiers_start_without_spend_and_refuse_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = FakeSupervisor(tmp)
            gate = admission(supervisor)
            started = gate.start_free("hook-matrix")
            self.assertEqual(supervisor.starts[0][:4],
                             ("hook-matrix", {"root": str(REPO.resolve())}, "installed",
                              str(REPO.resolve())))
            self.assertIn("hook_matrix.py --check", started["command"])
            gate.start_free("rule-detection", str(RAW))
            self.assertEqual(supervisor.starts[1][1]["raw"], str(RAW))
            for suite, raw in (("rule-detection", None), ("rule-detection", "relative"),
                               ("hook-matrix", str(RAW)), ("unit-eval", None)):
                with self.subTest(suite=suite, raw=raw), \
                        self.assertRaises(eval_tiers.EvalTierError):
                    gate.start_free(suite, raw)
            partial = partial_repository(Path(tmp) / "repo", "scripts/replay_detect.py")
            with self.assertRaises(eval_tiers.EvalTierError) as caught:
                admission(supervisor, partial).start_free("rule-detection", str(RAW))
            self.assertEqual(caught.exception.code, "eval_engine_absent")


class Handler:
    def __init__(self, body, supervisor=None):
        self.server = SimpleNamespace(run_supervisor=supervisor, repo_root=REPO,
                                      store=SimpleNamespace(path=REPO), target_service=None,
                                      mutations=SimpleNamespace(call=lambda fn: fn()))
        self.request_json = body
        self.response = None

    def _json(self, code, payload):
        self.response = (code, payload)

    def _error(self, code, name):
        self.response = (code, {"error": name})


def call(path, body, supervisor=None):
    route = next(item for item in server.ROUTES.entries if item.path == path)
    handler = Handler(body, supervisor)
    route.handler(handler, route)
    return handler.response


class RouteTests(unittest.TestCase):
    def test_the_routes_refuse_bad_input_and_unknown_runs_with_their_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = FakeSupervisor(tmp)
            self.assertEqual(call("/api/evals/preview", {}), (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/preview", {"request": {"suite": "nope"}}),
                             (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/start", {"request": {}, "confirmation_token": 1}),
                             (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/run", {"suite": "hook-matrix", "x": 1}),
                             (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/run", {"suite": ["hook-matrix"]}, supervisor),
                             (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/result", {"run_id": 7}),
                             (400, {"error": "invalid_request"}))
            self.assertEqual(call("/api/evals/result", {"run_id": RUN_ID}, supervisor),
                             (404, {"error": "eval_not_found"}))
            status, payload = call("/api/evals/catalog", {})
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["tiers"]), 4)
            self.assertEqual(call("/api/evals/catalog", {"x": 1}),
                             (400, {"error": "invalid_request"}))
            refusal = eval_tiers.EvalTierError("dirty", "replay_worktree_dirty")
            with mock.patch.object(eval_tiers.EvalAdmission, "resolve", side_effect=refusal):
                self.assertEqual(call("/api/evals/preview", {"request": paid_request()}),
                                 (409, {"error": "replay_worktree_dirty"}))
            with mock.patch.object(eval_tiers, "available", return_value=[]):
                self.assertEqual(call("/api/evals/run", {"suite": "hook-matrix"}, supervisor),
                                 (404, {"error": "eval_engine_absent"}))


class RealSupervisorTokenTests(unittest.TestCase):
    """The run supervisor, not the admission, refuses a paid start without its own token."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.service = FixtureTargetService()
        self.supervisor = runs.RunSupervisor(root / "studio", runs.default_catalog_path(REPO), 1,
                                             target_service=self.service)
        self.addCleanup(self.supervisor.close)
        self.server = SimpleNamespace(run_supervisor=self.supervisor, repo_root=REPO,
                                      store=SimpleNamespace(path=root / "studio"),
                                      target_service=self.service,
                                      mutations=SimpleNamespace(call=lambda fn: fn()))
        # Admission stays queued: nothing is launched, so nothing spends.
        patcher = mock.patch.object(runs.RunSupervisor, "_admit_locked")
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, path, body):
        route = next(item for item in server.ROUTES.entries if item.path == path)
        handler = Handler(body)
        handler.server = self.server
        route.handler(handler, route)
        return handler.response

    def preview(self, request):
        status, payload = self.route("/api/evals/preview", {"request": request})
        self.assertEqual(status, 200, payload)
        return payload

    def start(self, request, token):
        return self.route("/api/evals/start", {"request": request, "confirmation_token": token})

    def runs_recorded(self):
        return [path for path in (self.server.store.path / "runs").iterdir()]

    def test_missing_forged_reused_and_other_suite_tokens_are_refused(self):
        unit = self.preview(paid_request())
        micro = self.preview({"suite": "micro-tier", "target": {"kind": "branch", "ref": "main"},
                              "max_budget_usd": "2", "spend_cap_usd": "50"})
        self.assertEqual(self.start(unit["request"], "")[0], 400)
        self.assertEqual(self.route("/api/evals/start", {"request": unit["request"]}),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(self.start(unit["request"], "f" * 43),
                         (400, {"error": "eval_refused"}))
        self.assertEqual(self.start(unit["request"], micro["confirmation_token"]),
                         (400, {"error": "eval_refused"}))
        self.assertEqual(self.runs_recorded(), [])
        status, started = self.start(unit["request"], unit["confirmation_token"])
        self.assertEqual(status, 200, started)
        with self.supervisor.lock():
            record = self.supervisor._read(started["run_id"])
        self.assertEqual(record["target"]["revision"], "a" * 40)
        self.assertEqual(self.start(unit["request"], unit["confirmation_token"]),
                         (400, {"error": "eval_refused"}))
        self.assertEqual(len(self.runs_recorded()), 1)


class MovingTargetService(FixtureTargetService):
    """Builds the confirmed revision until `moved`, then another, as a branch that moved would."""

    def __init__(self):
        self.moved = False

    def build(self, kind, ref, destination):
        built = super().build(kind, ref, destination)
        if self.moved:
            built["revision"] = "b" * 40
        return built


class MovedTargetTests(RealSupervisorTokenTests):
    def setUp(self):
        super().setUp()
        self.service = MovingTargetService()
        self.supervisor.target_service = self.service
        self.server.target_service = self.service

    def test_missing_forged_reused_and_other_suite_tokens_are_refused(self):
        pass  # run by the parent class; this class tests only a target that moved

    def test_a_target_that_moves_after_confirm_is_refused_as_changed(self):
        unit = self.preview(paid_request())
        gate = eval_tiers.EvalAdmission(REPO, self.server.store.path, self.supervisor, self.service)
        confirmed = gate.confirm(unit["request"])
        self.service.moved = True
        with self.assertRaises(eval_tiers.EvalTierError) as caught:
            gate.start_confirmed(confirmed, unit["confirmation_token"])
        self.assertEqual(caught.exception.code, "eval_target_changed")
        self.assertEqual(self.runs_recorded(), [])
        status, payload = self.start(unit["request"], unit["confirmation_token"])
        self.assertEqual((status, payload), (409, {"error": "eval_target_changed"}))


class EvalRouteSecurityTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, path, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", path, supplied, body)

    def test_each_route_requires_a_session_csrf_and_origin_then_validates_its_input(self):
        bodies = {"/api/evals/catalog": {},"/api/evals/preview": {"request": paid_request()},
                  "/api/evals/start": {"request": paid_request(), "confirmation_token": "x"},
                  "/api/evals/run": {"suite": "hook-matrix"},
                  "/api/evals/result": {"run_id": RUN_ID}}
        for path, valid in bodies.items():
            with self.subTest(path=path):
                body = json.dumps(valid).encode()
                status, _headers, _body = self.request("POST", path, {
                    "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
                self.assertEqual(status, 401)
                status, _headers, _body = self.post(path, valid, {"X-Studio-CSRF": "wrong"})
                self.assertEqual(status, 403)
                status, _headers, _body = self.post(path, valid, {"Origin": "http://evil.example"})
                self.assertEqual(status, 403)
                status, _headers, body = self.post(path, {"unexpected": True})
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body), {"error": "invalid_request"})


def write_fixtures():
    with tempfile.TemporaryDirectory() as tmp:
        _code, run_dir, _calls = run_unit_eval(tmp)
        analysis = json.loads((run_dir / "eval" / eval_tiers.ANALYSIS_NAME).read_text())
    UNIT_ANALYSIS.write_text(json.dumps(analysis["result"], indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    # The runner's own result for the committed matrix: the engine's `expand`, nothing moved.
    engine = hook_engine("hook_matrix_write")
    matrix = json.loads(engine.MATRIX.read_text(encoding="utf-8"))
    result = {"variants": matrix["variants"], "rows": matrix["rows"], "calls": matrix["calls"],
              "grid": eval_tiers.hook_grid(matrix, engine.expand(matrix)), "moved": [],
              "runtime": matrix["runtime"]}
    HOOK_RESULT.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
                           encoding="utf-8")
    print("wrote %s and %s" % (UNIT_ANALYSIS, HOOK_RESULT))


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_fixtures()
    else:
        unittest.main()
