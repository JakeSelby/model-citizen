"""Native acceptance Studio selection, durable evidence and paid-run accounting."""
import importlib.util
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO
from harness_core.studio import native_acceptance as studio_native
from harness_core.studio import runs, server, spend_guard, targets
from studio_target_support import FixtureTargetService


def load_runner():
    path = REPO / "scripts" / "native_acceptance.py"
    spec = importlib.util.spec_from_file_location("studio_native_runner_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_runner()
CLIENT = "claude-code-cli-macos"
COMMIT = subprocess.run(
    ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
).stdout.strip()


class StudioNativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.selection = studio_native.Selection.parse(REPO, {
            "client": CLIENT,
            "cases": ["installation", "stance-switch", "cost-posture"],
            "model": "claude-haiku-4-5",
            "source_commit": COMMIT,
            "progress_id": "run-123",
        })

    def line(self, case, result, observation, **extra):
        return json.dumps({
            "kind": "native", "client": CLIENT, "source_commit": COMMIT,
            "model_run": "claude-haiku-4-5", "case": case, "result": result,
            "observation": observation, **extra,
        })

    def write_progress(self, text):
        path = studio_native.progress_path(self.directory, self.selection.progress_id)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def resolved_target(self):
        target = self.directory / "resolved-target"
        if not target.exists():
            subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(REPO),
                            str(target)], check=True)
            subprocess.run(["git", "-C", str(target), "checkout", "-q", "--detach", COMMIT],
                           check=True)
        return {"kind": "worktree", "root": str(target), "source_commit": COMMIT,
                "version": "0.18.0", "draft": None, "config_digest": None}

    class FixtureNativeTargets:
        def __init__(self):
            self.repository = REPO
            self.delegate = FixtureTargetService()
            self.identities = {}
            self.progress = {}

        def validate(self, kind, ref, expected=None):
            return COMMIT

        def build(self, kind, ref, destination):
            value = self.delegate.build(kind, ref, destination)
            shutil.rmtree(value["source_path"])
            subprocess.run(["git", "clone", "-q", str(REPO), value["source_path"]], check=True)
            self.identities[destination.name] = {
                "kind": kind, "ref": ref, "source_commit": value["revision"],
                "version": value["version"], "draft": value.get("draft"),
                "config_digest": value.get("config_digest"), "root": value["source_path"],
            }
            return value

        @staticmethod
        def has_snapshot(revision):
            return False

        def bind_progress(self, progress_id, run_id):
            self.progress[progress_id] = run_id

        def progress_run(self, progress_id):
            return self.progress[progress_id]

        def identity(self, run_id):
            return self.identities[run_id]

    def test_malformed_case_objects_are_refused_without_type_errors(self):
        for cases in ([{}], ["installation"], ["installation", "stance-switch",
                                                "cost-posture", "hook-composition"]):
            with self.subTest(cases=cases), self.assertRaisesRegex(
                    studio_native.NativeAcceptanceError, "exactly three"):
                studio_native.Selection.parse(REPO, {
                    **self.selection.public(), "cases": cases,
                })

    def test_progress_drops_non_finite_duration_and_invalid_session_counts(self):
        lines = [
            self.line("installation", "passed", "nan", seconds=float("nan"), sessions=True),
            self.line("stance-switch", "failed", "negative", seconds=-1, sessions=-2),
        ]
        snapshot = studio_native.progress_snapshot(
            REPO, self.write_progress("\n".join(lines) + "\n"), self.selection)
        self.assertIsNone(snapshot["cases"][0]["seconds"])
        self.assertIsNone(snapshot["cases"][0]["sessions"])
        self.assertIsNone(snapshot["cases"][1]["seconds"])
        self.assertIsNone(snapshot["cases"][1]["sessions"])
        json.dumps(snapshot, allow_nan=False)

    def test_three_selected_cases_are_the_only_cases_in_the_command_and_snapshot(self):
        argv = studio_native.command(REPO, self.directory, self.selection)
        self.assertEqual(argv[argv.index("--cases") + 1],
                         "installation,stance-switch,cost-posture")
        snapshot = studio_native.progress_snapshot(
            REPO, self.write_progress(self.line("installation", "passed", "installed") + "\n"),
            self.selection)
        self.assertEqual([item["id"] for item in snapshot["cases"]],
                         ["installation", "stance-switch", "cost-posture"])
        self.assertEqual([item["status"] for item in snapshot["cases"]],
                         ["passed", "pending", "pending"])

    def test_progress_keeps_evidence_and_filters_other_commit_client_and_model(self):
        lines = [
            self.line("installation", "passed", "old"),
            self.line("installation", "failed", "latest", seconds=3.5, sessions=2,
                      spend_usd=0.12),
            self.line("stance-switch", "passed", "wrong commit", source_commit="b" * 40),
            self.line("cost-posture", "passed", "wrong model", model_run="other"),
        ]
        snapshot = studio_native.progress_snapshot(
            REPO, self.write_progress("\n".join(lines) + "\n"), self.selection)
        first = snapshot["cases"][0]
        self.assertEqual((first["status"], first["observation"], first["spend_usd"]),
                         ("failed", "latest", 0.12))
        self.assertEqual(snapshot["resume_cases"], ["stance-switch", "cost-posture"])

    def test_interrupted_log_keeps_complete_lines_and_names_torn_tail(self):
        path = self.write_progress(
            self.line("installation", "passed", "kept evidence") + "\n" + '{"case":')
        snapshot = studio_native.progress_snapshot(REPO, path, self.selection, interrupted=True)
        self.assertTrue(snapshot["interrupted"])
        self.assertEqual(snapshot["cases"][0]["observation"], "kept evidence")
        self.assertEqual(snapshot["warnings"], ["Ignored a torn final progress line."])

    def test_progress_never_unions_evidence_from_two_client_versions_or_routings(self):
        old = self.line("installation", "passed", "old identity",
                        client_version="1", runtime_version="1", tier_routing={"a": 1})
        declaration = json.dumps({
            "kind": "native", "client": CLIENT, "source_commit": COMMIT,
            "model_run": "claude-haiku-4-5", "declared": "tier_routing",
            "client_version": "2", "runtime_version": "2", "tier_routing": {"a": 2},
        })
        current = self.line("stance-switch", "failed", "current identity",
                            client_version="2", runtime_version="2", tier_routing={"a": 2})
        snapshot = studio_native.progress_snapshot(
            REPO, self.write_progress("\n".join([old, declaration, current]) + "\n"),
            self.selection)
        self.assertEqual([item["status"] for item in snapshot["cases"]],
                         ["pending", "failed", "pending"])
        self.assertIn("latest matching client and routing identity", snapshot["warnings"][0])

    def test_resume_reuses_settled_verdicts_and_failed_retry_gets_a_fresh_identity(self):
        snapshot = studio_native.progress_snapshot(
            REPO, self.write_progress(
                self.line("installation", "passed", "kept") + "\n" +
                self.line("stance-switch", "failed", "kept failure") + "\n" +
                self.line("cost-posture", "unverified", "try again") + "\n"),
            self.selection)
        self.assertEqual(snapshot["resume_cases"], ["cost-posture"])
        retry = studio_native.failed_retry(self.selection, snapshot, "stance-switch")
        self.assertEqual(list(retry.cases), ["stance-switch"])
        self.assertNotEqual(retry.progress_id, self.selection.progress_id)
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "only a failed"):
            studio_native.failed_retry(self.selection, snapshot, "installation")

    def test_codex_is_visible_but_refused_without_an_in_flight_cap(self):
        found = next(item for item in studio_native.catalog(REPO)["clients"]
                     if item["runtime"] == "codex")
        self.assertFalse(found["spend_cap_supported"])
        self.assertIn("no in-flight dollar cap", found["unavailable_reason"])
        selection = studio_native.Selection.parse(REPO, {
            **self.selection.public(), "client": found["id"],
        })
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "no in-flight"):
            studio_native.command(REPO, self.directory, selection)

    def test_launch_refuses_when_the_resolved_target_commit_changed(self):
        changed = studio_native.Selection(
            self.selection.client, self.selection.cases, self.selection.model,
            "b" * 40, self.selection.progress_id,
        )
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "commit changed"):
            studio_native.command(REPO, self.directory, changed)

    def test_adapter_previews_then_launches_exactly_three_cases_at_the_pinned_target(self):
        class Admission:
            def __init__(self):
                self.previews = []
                self.starts = []

            def preview(self, **value):
                self.previews.append(value)
                return {
                    "estimate": {"amount_usd": None, "basis": "no_history",
                                 "sample_count": 0, "suite_id": "native", "case_count": 3},
                    "caps": {"max_budget_usd": "0.20", "spend_cap_usd": "1.00"},
                    "pricing": {"source": "api_credit", "basis": "money charged"},
                    "confirmation_required": True, "confirmation_token": "f" * 64,
                    "case_identities": list(self_selection.cases),
                }

            def start(self, **value):
                self.starts.append(value)
                return {"run_id": "native-run", "status": "queued", "native_target": {
                    "kind": "installed", "ref": "current", "source_commit": COMMIT,
                    "version": "0.18.0", "draft": None, "config_digest": "a" * 64,
                    "root": str(REPO),
                }}

        self_selection = self.selection
        admission = Admission()
        adapter = studio_native.NativeRunAdapter(REPO, self.directory, admission)
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}
        preview = adapter.preview(self.selection.public(), spend)
        launched = adapter.start(self.selection.public(), spend, "f" * 64)

        self.assertEqual(preview["case_identities"], list(self.selection.cases))
        self.assertEqual(launched["run_id"], "native-run")
        for request in admission.previews + admission.starts:
            self.assertEqual(request["case_identities"], list(self.selection.cases))
            cases_index = request["argv"].index("--cases")
            self.assertEqual(request["argv"][cases_index + 1],
                             "installation,stance-switch,cost-posture")
            self.assertEqual(request["target"]["kind"], "installed")
            self.assertEqual(request["target"]["ref"], "current")
        self.assertEqual(admission.starts[0]["confirmation_token"], "f" * 64)

    def test_adapter_fails_closed_on_bad_caps_codex_or_incomplete_preview(self):
        class Admission:
            @staticmethod
            def preview(**value):
                return {"confirmation_required": True}

        adapter = studio_native.NativeRunAdapter(REPO, self.directory, Admission())
        good = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                "pricing_source": "api_credit"}
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "cannot exceed"):
            adapter.preview(self.selection.public(), {**good, "max_budget_usd": "2.00"})
        codex = next(item for item in studio_native.catalog(REPO)["clients"]
                     if item["runtime"] == "codex")
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "no in-flight"):
            adapter.preview({**self.selection.public(), "client": codex["id"]}, good)
        with self.assertRaisesRegex(studio_native.NativeAcceptanceError, "incomplete"):
            adapter.preview(self.selection.public(), good)

    def test_supervisor_admission_wires_preview_and_start_to_the_paid_suite(self):
        supervisor = mock.Mock()
        supervisor.spend_preview.return_value = {"confirmation_required": True}
        supervisor.start.return_value = {"run_id": "run", "status": "queued"}
        target_service = mock.Mock()
        target_service.repository = REPO
        target_service.has_snapshot.return_value = False
        admission = studio_native.SupervisorAdmission(
            supervisor, target_service, self.directory / "native-evidence")
        admission.identity = mock.Mock(return_value={"source_commit": COMMIT})
        admission._validate_target = mock.Mock()
        target = {"kind": "worktree", "ref": "/target", "source_commit": None}
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}
        parameters = {"cases": "installation,stance-switch,cost-posture"}
        parameters["progress_id"] = "run-123"

        admission.preview(target=target, spend=spend, parameters=parameters,
                          case_identities=list(self.selection.cases))
        run_root = self.directory / "targets" / "run" / "source"
        run_root.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(REPO), str(run_root)], check=True)
        admission.start(target=target, spend=spend, parameters=parameters,
                        confirmation_token="f" * 64)

        supervisor.spend_preview.assert_called_once_with(
            "native-acceptance", parameters, "worktree", "/target",
            "0.20", "1.00", "api_credit")
        supervisor.start.assert_called_once_with(
            "native-acceptance", parameters, "worktree", "/target",
            confirmed="f" * 64, max_budget_usd="0.20",
            spend_cap_usd="1.00", pricing_source="api_credit")
        admission._validate_target.assert_called_once_with(
            target_service, "worktree", "/target", None)

    def test_native_routes_bind_catalog_preview_start_and_progress_to_one_target(self):
        target_root = Path(self.resolved_target()["root"])
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}

        class Supervisor:
            def __init__(self):
                self.records = []
                self.target_service = TargetService()

            def spend_preview(self, suite_id, parameters, kind, root, maximum, cap, pricing):
                self.parameters = parameters
                return {
                    "estimate": {"amount_usd": None, "basis": "no_history",
                                 "sample_count": 0, "suite_id": suite_id,
                                 "case_count": 3},
                    "caps": {"max_budget_usd": maximum, "spend_cap_usd": cap},
                    "pricing": {"source": pricing, "basis": "money charged"},
                    "confirmation_required": True,
                    "confirmation_token": "f" * 64,
                    "case_identities": self.parameters["cases"].split(","),
                }

            def check_start(self, suite_id, _parameters, kind, ref, **kwargs):
                # The request check a start route makes before it asks the person at the Mac.
                return {"suite_id": suite_id, "target_kind": kind, "target_ref": ref,
                        "case_count": len(kwargs.get("case_identities") or ()), "estimate_usd": None,
                        "max_budget_usd": kwargs["max_budget_usd"],
                        "spend_cap_usd": kwargs["spend_cap_usd"],
                        "pricing_source": kwargs["pricing_source"]}

            def start(self, *args, **kwargs):
                self.parameters = args[1]
                run_id = "native-run" if not self.records else "retry-run"
                root = self_directory / "targets" / run_id / "source"
                root.parent.mkdir(parents=True)
                subprocess.run(["git", "clone", "-q", str(REPO), str(root)], check=True)
                self.records.append({
                    "run_id": run_id, "suite_id": "native-acceptance",
                    "parameters": self.parameters, "queue_sequence": len(self.records) + 1,
                    "status": "running", "target": {
                        "kind": "installed", "ref": "current", "revision": COMMIT,
                        "version": "0.18.0", "draft": None,
                        "config_digest": "a" * 64, "source_path": str(root)}})
                return {"run_id": run_id, "status": "queued"}

            @staticmethod
            def lock():
                return contextlib.nullcontext()

            @staticmethod
            def _recover_locked():
                return None

            def _records(self):
                return self.records

            def show(self, run_id):
                found = next(item for item in self.records if item["run_id"] == run_id)
                return {"run_id": run_id, "suite_id": "native-acceptance",
                        "status": found["status"]}

        class TargetService:
            progress = {}
            repository = REPO

            @staticmethod
            def build(kind, ref, destination):
                return {"revision": COMMIT}

            @staticmethod
            def validate(kind, ref, expected=None):
                return COMMIT

            @staticmethod
            def has_snapshot(revision):
                return False

            @classmethod
            def bind_progress(cls, progress_id, run_id):
                cls.progress[progress_id] = run_id

            @classmethod
            def progress_run(cls, progress_id):
                return cls.progress[progress_id]

            @staticmethod
            def identity(run_id):
                return {"kind": "installed", "ref": "current",
                        "source_commit": COMMIT, "version": "0.18.0", "draft": None,
                        "config_digest": "a" * 64, "root": str(target_root)}

        class Mutations:
            @staticmethod
            def call(callback):
                return callback()

        class Handler:
            def __init__(self):
                self.server = mock.Mock(
                    repo_root=REPO, store=mock.Mock(path=self_directory),
                    run_supervisor=Supervisor(), target_service=TargetService(),
                    mutations=Mutations())
                self.request_json = {}
                self.response = None
                self.error = None

            def _json(self, code, payload):
                self.response = (code, payload)

            def _error(self, code, name):
                self.error = (code, name)

        self_directory = self.directory
        handler = Handler()
        server._native_catalog(handler, server.Route(
                "GET", "/catalog", "application/json", server.NATIVE_CATALOG,
                server._native_catalog, None, cli_command=("native",)))
        initial = handler.response[1]["initial"]
        self.assertEqual(len(initial["cases"]), 3)
        self.assertEqual(initial["source_commit"], "")

        handler.request_json = {"selection": self.selection.public(), "spend": spend}
        server._native_preview(handler, server.Route(
                "POST", "/preview", "application/json", server.NATIVE_PREVIEW,
                server._native_preview, None, "application/json", ("native",)))
        preview = handler.response[1]
        self.assertEqual(preview["case_identities"], list(self.selection.cases))

        handler.request_json = {"selection": self.selection.public(), "spend": spend,
                                    "confirmation_token": preview["confirmation_token"]}
        server._native_start(handler, server.Route(
                "POST", "/start", "application/json", server.NATIVE_RUN,
                server._native_start, None, "application/json", ("native",)))
        self.assertEqual(handler.response[1]["run_id"], "native-run")
        launched_selection = handler.response[1]["selection"]

        handler.request_json = {"selection": launched_selection}
        server._native_progress(handler, server.Route(
                "POST", "/progress", "application/json", server.NATIVE_SNAPSHOT,
                server._native_progress, None, "application/json", ("native",)))
        self.assertEqual(handler.response[1]["run_status"], "running")

        progress = studio_native.progress_path(
            self.directory / "native-evidence", launched_selection["progress_id"])
        progress.parent.mkdir(mode=0o700, exist_ok=True)
        progress.write_text(self.line("installation", "failed", "completed failure") + "\n",
                            encoding="utf-8")
        progress.chmod(0o600)
        handler.server.run_supervisor.records[0]["status"] = "failed"
        handler.request_json = {"selection": launched_selection}
        server._native_progress(handler, server.Route(
                "POST", "/progress", "application/json", server.NATIVE_SNAPSHOT,
                server._native_progress, None, "application/json", ("native",)))
        self.assertFalse(handler.response[1]["interrupted"])
        self.assertEqual(handler.response[1]["cases"][0]["status"], "failed")

        handler.request_json = {"selection": launched_selection, "case": "installation"}
        server._native_retry(handler, server.Route(
                "POST", "/retry", "application/json", server.NATIVE_SELECTION,
                server._native_retry, None, "application/json", ("native",)))
        retry_selection = handler.response[1]
        self.assertEqual(retry_selection["cases"], ["installation"])
        self.assertEqual(retry_selection["retry_source"], launched_selection["progress_id"])

        handler.request_json = {"selection": retry_selection, "spend": spend}
        server._native_preview(handler, server.Route(
                "POST", "/preview", "application/json", server.NATIVE_PREVIEW,
                server._native_preview, None, "application/json", ("native",)))
        retry_preview = handler.response[1]
        self.assertEqual(retry_preview["case_identities"], ["installation"])
        handler.request_json = {"selection": retry_selection, "spend": spend,
                                "confirmation_token": retry_preview["confirmation_token"]}
        server._native_start(handler, server.Route(
                "POST", "/start", "application/json", server.NATIVE_RUN,
                server._native_start, None, "application/json", ("native",)))
        self.assertEqual(handler.response[1]["run_id"], "retry-run")
        self.assertEqual(handler.response[1]["selection"]["cases"], ["installation"])

        handler.request_json = {
            "selection": {**self.selection.public(), "cases": [{}]}, "spend": spend,
        }
        server._native_preview(handler, server.Route(
                "POST", "/preview", "application/json", server.NATIVE_PREVIEW,
                server._native_preview, None, "application/json", ("native",)))
        self.assertEqual(handler.error, (400, "native_acceptance_refused"))

    def test_dirty_worktree_snapshot_is_reused_by_its_synthetic_commit(self):
        root = Path(os.path.realpath(self.directory))
        repository = root / "snapshot-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "test"], check=True)
        (repository / "bin").mkdir()
        harness = repository / "bin" / "harness"
        harness.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
        harness.chmod(0o755)
        (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "target"], check=True)
        worktree = root / "dirty-worktree"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q",
                        "--detach", str(worktree)], check=True)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
            capture_output=True, check=False))
        (worktree / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (repository / "VERSION").write_text("0.18.0\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "VERSION"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "version"], check=True)
        subprocess.run(["git", "-C", str(worktree), "reset", "--hard", "-q", "main"], check=True)
        (worktree / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        state = root / "state"
        service = targets.TargetService(repository)
        supervisor = runs.RunSupervisor(
            state, REPO / "policy" / "studio" / "suites.json", target_service=service)
        self.addCleanup(supervisor.close)
        adapter = studio_native.NativeRunAdapter(
            REPO, state, studio_native.SupervisorAdmission(
                supervisor, service, state / "native-evidence"))
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}
        selected = {**self.selection.public(), "source_commit": "",
                    "target_kind": "worktree", "target_ref": str(worktree)}
        preview = adapter.preview(selected, spend)
        with mock.patch.object(supervisor, "_admit_locked"):
            first = adapter.start(selected, spend, preview["confirmation_token"])
        resumed_preview = adapter.preview(first["selection"], spend)
        with mock.patch.object(supervisor, "_admit_locked"):
            resumed = adapter.start(
                first["selection"], spend, resumed_preview["confirmation_token"])

        self.assertEqual(first["selection"]["target_kind"], "branch")
        self.assertEqual(first["selection"]["target_ref"], first["selection"]["source_commit"])
        self.assertEqual(resumed["selection"]["source_commit"],
                         first["selection"]["source_commit"])

    def test_commit_lookup_remains_reliable_after_more_than_one_hundred_runs(self):
        admission = studio_native.SupervisorAdmission(
            mock.Mock(), targets.TargetService(REPO), self.directory / "native-evidence")
        desired = "c" * 40
        expected = self.directory / "retained-source"
        expected.mkdir()
        for index in range(101):
            progress_id = "p%03d" % index
            root = expected if index == 0 else self.directory / ("source-%03d" % index)
            root.mkdir(exist_ok=True)
            admission._write_private(admission._record_path(progress_id), {
                "run_id": "run-%03d" % index, "root": str(root),
                "source_commit": desired if index == 0 else ("%040x" % (index + 1)),
                "version": "0.18.0",
            })
        self.assertEqual(admission._source_for_commit(desired), expected)

    def test_catalog_paid_suite_keeps_native_selection_behind_one_aggregate_case(self):
        suite = runs.SuiteCatalog.load(REPO / "policy" / "studio" / "suites.json").get(
            "native-acceptance")
        parameters = {
            "client": self.selection.client,
            "cases": ",".join(self.selection.cases),
            "model": self.selection.model,
            "source_commit": self.selection.source_commit,
            "progress_id": self.selection.progress_id,
            "progress": str(studio_native.progress_path(
                self.directory, self.selection.progress_id)),
            "out": str(studio_native.evidence_path(
                self.directory, self.selection.progress_id)),
            "retry_source": "fresh", "retry_case": "fresh",
        }
        argv = suite.render(parameters, "installed", str(REPO))
        self.assertIn("--progress-id", argv)
        self.assertEqual(list(suite.cases), ["native-acceptance"])

    def test_real_supervisor_preview_and_start_queue_exactly_three_cases(self):
        state = Path(os.path.realpath(self.directory)) / "studio-state"
        target_service = self.FixtureNativeTargets()
        supervisor = runs.RunSupervisor(
            state, REPO / "policy" / "studio" / "suites.json",
            target_service=target_service)
        self.addCleanup(supervisor.close)
        adapter = studio_native.NativeRunAdapter(
            REPO, state, studio_native.SupervisorAdmission(
                supervisor, target_service, state / "native-evidence"))
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}

        initial = {**self.selection.public(), "source_commit": ""}
        preview = adapter.preview(initial, spend)
        with mock.patch.object(supervisor, "_admit_locked"):
            started = adapter.start(
                initial, spend, preview["confirmation_token"])

        self.assertEqual(preview["case_identities"], list(self.selection.cases))
        self.assertEqual(started["status"], "queued")
        with mock.patch.object(supervisor, "_admit_locked"):
            self.assertEqual(adapter.admission.progress(
                self.selection.progress_id)["status"], "queued")
        with supervisor.lock():
            record = supervisor._read(started["run_id"])
        self.assertEqual(record["case_identities"], ["native-acceptance"])
        self.assertEqual(record["parameters"]["progress_id"], self.selection.progress_id)

    def test_real_target_service_previews_and_starts_every_supported_target_kind(self):
        root = Path(os.path.realpath(self.directory))
        repository = root / "target-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "test"], check=True)
        (repository / "bin").mkdir()
        harness = repository / "bin" / "harness"
        harness.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        harness.chmod(0o755)
        (repository / "VERSION").write_text("0.18.0\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "target"], check=True)
        commit = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], capture_output=True,
            text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(repository), "tag", "v0.18.0"], check=True)
        worktree = root / "managed-target"
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-q",
                        "--detach", str(worktree), commit], check=True)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)],
            capture_output=True, check=False))
        requested = [
            ("installed", "current"), ("release", "v0.18.0"),
            ("branch", commit), ("worktree", str(worktree)), ("draft", "candidate"),
        ]
        spend = {"max_budget_usd": "0.20", "spend_cap_usd": "1.00",
                 "pricing_source": "api_credit"}
        draft = {"name": "candidate", "revision": commit, "actual_revision": commit}

        with mock.patch.object(targets.drafts, "read_config",
                               return_value={"draft": draft, "config": {}}), \
                mock.patch.object(targets.drafts, "find", return_value=(repository, {})):
            for index, (kind, ref) in enumerate(requested):
                with self.subTest(kind=kind):
                    state = root / ("target-state-%d" % index)
                    target_service = targets.TargetService(repository)
                    supervisor = runs.RunSupervisor(
                        state, REPO / "policy" / "studio" / "suites.json",
                        target_service=target_service)
                    self.addCleanup(supervisor.close)
                    adapter = studio_native.NativeRunAdapter(
                        REPO, state, studio_native.SupervisorAdmission(
                            supervisor, target_service, state / "native-evidence"))
                    selection = {**self.selection.public(), "source_commit": "",
                                 "progress_id": "target-%d" % index,
                                 "target_kind": kind, "target_ref": ref}
                    preview = adapter.preview(selection, spend)
                    with mock.patch.object(supervisor, "_admit_locked"):
                        started = adapter.start(
                            selection, spend, preview["confirmation_token"])
                    self.assertEqual(started["target"]["kind"], "branch")
                    self.assertEqual(started["target"]["ref"],
                                     started["selection"]["source_commit"])
                    self.assertRegex(started["selection"]["source_commit"], r"^[0-9a-f]{40}$")

        invalid_state = root / "invalid-target-state"
        target_service = targets.TargetService(repository)
        supervisor = runs.RunSupervisor(
            invalid_state, REPO / "policy" / "studio" / "suites.json",
            target_service=target_service)
        self.addCleanup(supervisor.close)
        parameters = {
            "client": self.selection.client, "cases": ",".join(self.selection.cases),
            "model": self.selection.model, "source_commit": "resolve-at-launch",
            "progress_id": "invalid-target",
            "progress": str(root / "native-invalid-target.partial.jsonl"),
            "out": str(root / "native-invalid-target.json"),
            "retry_source": "fresh", "retry_case": "fresh",
        }
        admission = studio_native.SupervisorAdmission(
            supervisor, target_service, invalid_state / "native-evidence")
        with mock.patch.object(spend_guard, "plan") as plan:
            with self.assertRaisesRegex(studio_native.NativeAcceptanceError, r"exact v\* tag"):
                admission.preview(
                    target={"kind": "release", "ref": "latest", "source_commit": None},
                    spend=spend, parameters=parameters,
                    case_identities=list(self.selection.cases))
        plan.assert_not_called()


class NativeSpendAccountingTests(unittest.TestCase):
    def claude_home(self, budget):
        home = RUNNER.Home.__new__(RUNNER.Home)
        home.command = "claude"
        home.model = "claude-haiku-4-5"
        home.project = Path(".")
        home.keychain_error = None
        home.launched = 0
        home.spend_budget = budget
        home.spend_report = []
        home.spend_price_as_of = []
        home.env = lambda extra=None: {}
        return home

    def test_claude_native_turn_receives_the_remaining_cap_and_records_actual_spend(self):
        budget = RUNNER.SpendBudget("0.25", "0.40")
        home = self.claude_home(budget)
        result = subprocess.CompletedProcess([], 0, json.dumps({
            "result": "ok", "is_error": False, "total_cost_usd": 0.1,
        }), "")
        with mock.patch.object(RUNNER, "run", return_value=result) as launched:
            home.session("test", tools=())
        argv = launched.call_args.args[0]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.25")
        self.assertEqual(float(budget.total), 0.1)

    def test_missing_or_malformed_spend_is_unknown_and_never_zero(self):
        for value in (None, "free", float("nan")):
            budget = RUNNER.SpendBudget("0.25", "0.40")
            home = self.claude_home(budget)
            payload = {"result": "ok", "is_error": False}
            if value is not None:
                payload["total_cost_usd"] = value
            result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
            with mock.patch.object(RUNNER, "run", return_value=result), \
                    self.assertRaises(RUNNER.SpendAccountingError):
                home.session("test", tools=())
            self.assertTrue(budget.error)
            self.assertEqual(budget.total, 0)

    def test_spend_result_preserves_completed_cases_and_stops_before_the_next_case(self):
        budget = RUNNER.SpendBudget("0.20", "0.30")
        budget.begin_case()
        budget.add(0.3)
        budget.finish_case("installation", {"spend_usd": 0.3})
        result = budget.result("run-id", ["installation", "stance-switch"])
        self.assertEqual(result["spend_usd"], 0.3)
        self.assertEqual([item["status"] for item in result["cases"]],
                         ["completed", "not_run"])
        self.assertEqual(result["stop_reason"], "spend_cap")

    def test_resume_seeds_prior_case_spend_before_the_next_case_and_reports_the_full_set(self):
        with tempfile.TemporaryDirectory() as directory:
            first_process = RUNNER.SpendBudget("0.80", "1.00")
            first_process.begin_case()
            first_process.add(0.8)
            first_process.finish_case("installation", {"spend_usd": 0.8})
            first_result = first_process.result("first-run", ["installation"])
            progress = Path(directory) / "resume.partial.jsonl"
            routing = {"execution_model": "claude-haiku-4-5",
                       "assessment_model": "different-model"}
            header = {
                "kind": "native", "client": CLIENT, "harness_version": RUNNER.VERSION,
                "runtime_version": "1", "client_version": "1",
                "platform": RUNNER.CLIENTS[CLIENT]["platform"],
                "source_commit": "a" * 40, "tier_routing": routing,
                "model_run": "claude-haiku-4-5",
            }
            progress.write_text(json.dumps(dict(
                header, case="installation", result="passed", observation="kept",
                spend_usd=0.8,
            )) + "\n", encoding="utf-8")
            progress.chmod(0o600)
            budget = RUNNER.SpendBudget("0.50", "1.00")

            def runner(client, name, model, keep, confirmed, budget=None):
                self.assertEqual(name, "stance-switch")
                self.assertEqual(float(budget.turn_limit()), 0.2)
                budget.begin_case()
                budget.add(0.2)
                return {"case": name, "result": "passed", "observation": "new",
                        "seconds": 1, "sessions": 1, "spend_usd": 0.2}

            def git(*arguments):
                return "" if arguments == ("status", "--porcelain") else "a" * 40

            with mock.patch.object(RUNNER, "git", side_effect=git), \
                    mock.patch.object(RUNNER, "client_version", return_value="1"):
                RUNNER.record(
                    CLIENT, ["installation", "stance-switch"], "claude-haiku-4-5", False,
                    runner=runner, progress=progress, confirmed=(CLIENT,),
                    tier_routing=routing, budget=budget,
                )
            result = budget.result("run-id", ["installation", "stance-switch"])
            self.assertEqual(float(budget.total), 1.0)  # cumulative set cap/evidence total
            self.assertEqual(float(budget.incurred), 0.2)
            self.assertEqual(result["spend_usd"], 0.2)  # this process's usage-ledger amount
            self.assertEqual([item["spend_usd"] for item in result["cases"]], [0.0, 0.2])
            self.assertIsNone(result["stop_reason"])
            self.assertEqual([first_result["spend_usd"], result["spend_usd"]], [0.8, 0.2])

    def test_native_estimator_samples_cumulative_progress_after_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            progress = evidence / "native-resume.partial.jsonl"
            rows = [
                {"case": "installation", "result": "passed", "spend_usd": 0.8},
                {"case": "stance-switch", "result": "passed", "spend_usd": 0.2},
            ]
            progress.write_text("".join(json.dumps(row) + "\n" for row in rows),
                                encoding="utf-8")
            progress.chmod(0o600)
            (evidence / "native-resume.json").write_text("{}\n", encoding="utf-8")
            estimate = studio_native.native_estimate(evidence, 2)
            self.assertEqual(estimate["amount_usd"], 1.0)
            self.assertEqual(estimate["sample_count"], 1)

    def test_progress_identity_is_exclusive_while_running_and_reusable_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "round.partial.jsonl"
            first = RUNNER.reserve_progress(path, studio_owned=True)
            try:
                with self.assertRaisesRegex(RUNNER.SpendAccountingError, "already owned"):
                    RUNNER.reserve_progress(path, studio_owned=True)
            finally:
                os.close(first)
            resumed = RUNNER.reserve_progress(path, studio_owned=True)
            os.close(resumed)

    def test_progress_and_final_evidence_refuse_symlinks_and_unsafe_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / "real"
            real.mkdir(mode=0o700)
            linked = base / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(RUNNER.SpendAccountingError, "directory"):
                RUNNER.reserve_progress(linked / "round.partial.jsonl", studio_owned=True)

            progress = real / "round.partial.jsonl"
            progress.write_text("", encoding="utf-8")
            progress.chmod(0o644)
            with self.assertRaisesRegex(RUNNER.SpendAccountingError, "mode 0600"):
                RUNNER.reserve_progress(progress, studio_owned=True)

            destination = real / "evidence.json"
            outside = base / "outside"
            outside.write_text("keep", encoding="utf-8")
            destination.symlink_to(outside)
            with self.assertRaisesRegex(RUNNER.SpendAccountingError, "mode 0600"):
                RUNNER.write_private_text(destination, "replace")
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_ordinary_cli_outputs_allow_tmp_checkout_and_mode_0755_record_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            base.chmod(0o755)
            progress = base / "round.partial.jsonl"
            progress.write_text("", encoding="utf-8")
            progress.chmod(0o644)
            descriptor = RUNNER.reserve_progress(progress, studio_owned=False)
            os.close(descriptor)

            output = base / "native.json"
            output.write_text("old", encoding="utf-8")
            output.chmod(0o644)
            RUNNER.write_private_text(output, "new", studio_owned=False)
            self.assertEqual(output.read_text(encoding="utf-8"), "new")

            result = base / "spend-result.json"
            RUNNER.write_spend_result(result, {
                "schema_version": 1, "run_id": "run-id", "spend_usd": 0.2,
                "cases": [{"id": "case", "status": "completed", "spend_usd": 0.2}],
                "stop_reason": None,
            })
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)

    def test_codex_usage_is_priced_for_reporting_but_budgeted_launch_is_refused(self):
        events = [{"info": {"total_token_usage": {
            "input_tokens": 1000, "cached_input_tokens": 200,
            "output_tokens": 100, "cache_write_input_tokens": 0, "total_tokens": 1100,
        }}}]
        priced = RUNNER.codex_spend(events, "gpt-5.6-sol")
        self.assertGreater(priced["amount_usd"], 0)
        self.assertRegex(priced["price_as_of"], r"^20\d\d-\d\d-\d\d$")
        with self.assertRaisesRegex(SystemExit, "no in-flight dollar cap"):
            RUNNER.main(["--client", "codex-cli-macos", "--cases", "installation",
                         "--max-budget-usd", "0.2", "--spend-cap", "1"])

    def test_spend_result_is_mode_0600_and_keeps_exact_run_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spend-result.json"
            value = {"schema_version": 1, "run_id": "run-id", "spend_usd": 0.1,
                     "cases": [], "stop_reason": None}
            RUNNER.write_spend_result(path, value)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_budgeted_main_publishes_the_spend_guard_schema_after_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "spend-result.json"

            def record(*args, **kwargs):
                budget = kwargs["budget"]
                budget.begin_case()
                budget.add(0.1)
                budget.finish_case("installation", {"spend_usd": 0.1})
                return {"cases": {"installation": "passed"}}

            environment = {"CITIZEN_STUDIO_RESULT": str(result_path),
                           "CITIZEN_STUDIO_RUN_ID": "run-id"}
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(RUNNER, "routing", return_value={
                        "execution_model": "claude-haiku-4-5",
                        "assessment_model": "different-model",
                    }), mock.patch.object(RUNNER, "host_mismatch", return_value=""), \
                    mock.patch.object(RUNNER, "record", side_effect=record):
                code = RUNNER.main([
                    "--client", CLIENT, "--cases", "installation",
                    "--model", "claude-haiku-4-5", "--max-budget-usd", "0.2",
                    "--spend-cap", "1",
                ])
            self.assertEqual(code, 0)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result, {
                "schema_version": 1, "run_id": "run-id", "spend_usd": 0.1,
                "cases": [{"id": "native-acceptance", "status": "completed",
                           "spend_usd": 0.1}],
                "stop_reason": None,
            })

    def test_budgeted_main_refuses_settlement_when_case_accounting_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "spend-result.json"

            def record(*args, **kwargs):
                kwargs["budget"].error = "accounting unknown"
                return {"cases": {"installation": "unverified"}}

            environment = {"CITIZEN_STUDIO_RESULT": str(result_path),
                           "CITIZEN_STUDIO_RUN_ID": "run-id"}
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(RUNNER, "routing", return_value={
                        "execution_model": "claude-haiku-4-5",
                        "assessment_model": "different-model",
                    }), mock.patch.object(RUNNER, "host_mismatch", return_value=""), \
                    mock.patch.object(RUNNER, "record", side_effect=record):
                code = RUNNER.main([
                    "--client", CLIENT, "--cases", "installation",
                    "--model", "claude-haiku-4-5", "--max-budget-usd", "0.2",
                    "--spend-cap", "1",
                ])
            self.assertEqual(code, 2)
            self.assertFalse(result_path.exists())


if __name__ == "__main__":
    unittest.main()
