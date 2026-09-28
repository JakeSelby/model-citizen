"""Canonical reruns, bounded evidence and authenticated history transport."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO  # noqa: F401 - adds lib/ to the test import path
from harness_core.studio import runs
import test_studio_security as studio_security


class MutableTargetService:
    def __init__(self):
        self.revision = "a" * 40

    def build(self, kind, ref, destination):
        root = Path(destination)
        (root / "source").mkdir(parents=True)
        (root / "profile").mkdir(parents=True)
        return {"kind": kind, "ref": ref, "version": "fixture", "revision": self.revision,
                "draft": ref if kind == "draft" else None,
                "config_digest": hashlib.sha256(b"{}").hexdigest(),
                "source_path": str(root / "source"), "profile_path": str(root / "profile")}


class RunHistoryCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.catalog = root / "suites.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1, "argv": ["python3", "-c", "print('ok')"],
            "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["branch"],
        }]}))
        self.targets = MutableTargetService()
        self.supervisor = runs.RunSupervisor(root / "state", self.catalog,
                                             target_service=self.targets)
        self.addCleanup(self.supervisor.close)

    def terminal_run(self):
        with mock.patch.object(self.supervisor, "_admit_locked"):
            started = self.supervisor.start("fixture", {}, "branch", "feature/history")
        with self.supervisor.lock():
            record = self.supervisor._read(started["run_id"])
            record["status"] = "cancelled"
            record["completed_at"] = runs.utc_now()
            self.supervisor._write(record)
        return started["run_id"]

    def test_paid_terminal_results_require_the_complete_original_identity_order(self):
        run_id = str(uuid.uuid4())
        record = {
            "schema_version": 1, "run_id": run_id, "suite_id": "paid-suite",
            "suite_version": 1, "parameters": {},
            "target": {"kind": "branch", "ref": "main", "revision": "a" * 40,
                       "draft": None, "config_digest": None},
            "argv": ["paid-runner"], "cost_class": "spends_usage",
            "expected_duration_seconds": 1, "timeout_seconds": 10,
            "status": "failed", "queue_sequence": 1,
            "created_at": "2026-09-28T00:00:00Z",
            "completed_at": "2026-09-28T00:00:01Z",
            "case_identities": ["case-one", "case-two"],
            "spend_estimate": {"amount_usd": None, "basis": "no_history",
                               "sample_count": 0, "suite_id": "paid-suite", "case_count": 2},
            "spend_cap": {"max_budget_usd": "1", "spend_cap_usd": "2"},
            "pricing_identity": {"source": "api_credit", "basis": "fixture"},
            "case_results": [
                {"id": "case-one", "status": "completed", "spend_usd": 0.1},
                {"id": "case-two", "status": "not_run", "spend_usd": 0.0}],
        }
        record["canonical_run_digest"] = runs.run_store._canonical_run_digest(record)
        self.assertEqual(runs.RunSupervisor._validate_record(dict(record), run_id), record)
        for invalid in (record["case_results"][:1], list(reversed(record["case_results"]))):
            changed = dict(record, case_results=invalid)
            with self.assertRaisesRegex(runs.RunError, "invalid case results"):
                runs.RunSupervisor._validate_record(changed, run_id)

    def test_rerun_retains_request_and_refuses_target_drift_before_admission(self):
        run_id = self.terminal_run()
        self.targets.revision = "b" * 40
        with mock.patch.object(self.supervisor, "_admit_locked") as admit:
            with self.assertRaisesRegex(runs.RunError, "target changed"):
                self.supervisor.rerun(run_id)
        admit.assert_not_called()
        self.assertEqual(len(self.supervisor.history.history()["items"]), 1)

    def test_rerun_uses_the_same_request_and_records_lineage(self):
        run_id = self.terminal_run()
        with mock.patch.object(self.supervisor, "_admit_locked"):
            repeated = self.supervisor.rerun(run_id)
        raw = self.supervisor._read(repeated["run_id"])
        original = self.supervisor._read(run_id)
        self.assertEqual(raw["parameters"], original["parameters"])
        self.assertEqual(raw["target"]["revision"], original["target"]["revision"])
        self.assertEqual(raw["rerun_of"], run_id)
        self.assertEqual(self.supervisor.run_detail(run_id)["reruns"]["items"],
                         [repeated["run_id"]])

    def test_oversized_and_symlinked_evidence_is_unavailable_without_partial_content(self):
        run_id = self.terminal_run()
        run_dir = self.supervisor.runs_dir / run_id
        outside = Path(self.temporary.name) / "outside.log"
        outside.write_text("private")
        (run_dir / "stdout.log").symlink_to(outside)
        with self.assertRaisesRegex(runs.RunError, "missing or unsafe"):
            self.supervisor.evidence(run_id, "stdout")
        (run_dir / "stdout.log").unlink()
        (run_dir / "stdout.log").write_text("x" * (runs.MAX_OUTPUT_CHUNK + 1))
        (run_dir / "stdout.log").chmod(0o600)
        with self.assertRaisesRegex(runs.RunError, "unavailable"):
            self.supervisor.evidence(run_id, "stdout")

    def test_utf8_boundary_extension_cannot_exceed_the_evidence_bound(self):
        run_id = self.terminal_run()
        path = self.supervisor.runs_dir / run_id / "stdout.log"
        path.write_bytes(b"x" * (runs.MAX_OUTPUT_CHUNK - 1) + "€".encode("utf-8"))
        path.chmod(0o600)
        with self.assertRaisesRegex(runs.RunError, "unavailable"):
            self.supervisor.evidence(run_id, "stdout")

    def test_worker_evidence_requires_a_bounded_owned_mode_0600_file(self):
        run_id = self.terminal_run()
        path = self.supervisor.runs_dir / run_id / "worker.log"
        path.write_text("worker evidence")
        path.chmod(0o600)
        self.assertEqual(self.supervisor.evidence(run_id, "worker")["content"],
                         "worker evidence")
        path.chmod(0o644)
        with self.assertRaisesRegex(runs.RunError, "unavailable"):
            self.supervisor.evidence(run_id, "worker")

    def test_suite_command_or_case_drift_disables_and_refuses_rerun(self):
        run_id = self.terminal_run()
        catalog = json.loads(self.catalog.read_text())
        catalog["suites"][0]["argv"] = ["python3", "-c", "print('changed')"]
        self.catalog.write_text(json.dumps(catalog))
        detail = self.supervisor.run_detail(run_id)
        self.assertFalse(detail["rerun"]["available"])
        with self.assertRaisesRegex(runs.RunError, "command or discovered cases changed"):
            self.supervisor.rerun(run_id)

    def test_imported_declared_artifact_is_read_without_exposing_its_path(self):
        repository = Path(self.temporary.name).resolve() / "repository"
        repository.mkdir()
        artifact = repository / "evidence.json"
        artifact.write_text('{"ok":true}')
        artifact.chmod(0o600)
        supervisor = runs.RunSupervisor(Path(self.temporary.name).resolve() / "import-state",
                                        self.catalog, repository=repository)
        self.addCleanup(supervisor.close)
        run_id = str(uuid.uuid4())
        supervisor.history.upsert({
            "schema_version": 1, "run_id": run_id,
            "source": {"kind": "native", "path": "evidence.json", "record_identity": run_id},
            "suite": {"id": "native-acceptance", "version": 1},
            "target": {"kind": "commit", "ref": "a" * 40, "commit": "a" * 40,
                       "draft": None, "config_digest": None},
            "runtime": None, "model": None, "arms": [], "trials": None,
            "parameters": {}, "argv": [], "status": "succeeded", "times": {},
            "tokens": {}, "cost": None, "cases": [], "artifacts": ["evidence.json"],
            "raw": {},
        })
        detail = supervisor.run_detail(run_id)
        self.assertNotIn("evidence.json", json.dumps(detail))
        self.assertTrue(detail["artifacts"][0]["available"])
        self.assertEqual(supervisor.evidence(run_id, "imported-0")["content"], '{"ok":true}')

    def test_imported_run_detail_explains_why_rerun_is_unavailable(self):
        run_id = str(uuid.uuid4())
        self.supervisor.history.upsert({
            "schema_version": 1,
            "run_id": run_id,
            "source": {"kind": "native", "path": "compatibility/evidence/example.json",
                       "record_identity": run_id},
            "suite": {"id": "native-acceptance", "version": 1},
            "target": {"kind": "commit", "ref": "a" * 40, "commit": "a" * 40,
                       "draft": None, "config_digest": None},
            "runtime": None, "model": None, "arms": [], "trials": None,
            "parameters": {}, "argv": [], "status": "succeeded",
            "times": {"created_at": "2026-09-28T00:00:00Z"},
            "tokens": {}, "cost": None, "cases": [], "artifacts": [], "raw": {},
        })

        detail = self.supervisor.run_detail(run_id)

        self.assertEqual(detail["rerun"], {
            "available": False,
            "reason": "Imported runs have no canonical request.",
        })
        self.assertIsNone(detail["exact_command"])


class RunHistoryApiTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def request_history(self, headers=None):
        payload = {"limit": 50, "cursor": None, "suite_id": None, "target": None,
                   "status": None, "created_from": None, "created_to": None,
                   "min_cost_usd": None, "max_cost_usd": None,
                   "min_duration_ms": None, "max_duration_ms": None}
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        if headers is not None:
            supplied.update(headers)
        return self.request("POST", "/api/runs/history", supplied, body)

    def post_json(self, path, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", path, supplied, body)

    def test_history_route_requires_authentication_and_csrf_then_returns_a_page(self):
        body = json.dumps({}).encode()
        status, _headers, _body = self.request("POST", "/api/runs/history", {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)
        status, _headers, _body = self.request_history({"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, body = self.request_history()
        self.assertEqual(status, 200, body)
        self.assertEqual(set(json.loads(body)), {"items", "next_cursor"})

    def test_detail_case_evidence_and_rerun_round_trip_over_authenticated_http(self):
        completed = subprocess.run(
            [sys.executable, str(studio_security.CLI), "runs", "reindex", "--json"],
            env=self.env, capture_output=True, text=True, timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        status, _headers, body = self.request_history()
        self.assertEqual(status, 200, body)
        run_id = json.loads(body)["items"][0]["run_id"]

        status, _headers, body = self.post_json("/api/runs/detail", {
            "run_id": run_id, "lineage_limit": 1, "lineage_cursor": None})
        self.assertEqual(status, 200, body)
        detail = json.loads(body)
        self.assertEqual(detail["run_id"], run_id)
        self.assertIn("next_cursor", detail["reruns"])
        status, _headers, _body = self.post_json("/api/runs/rerun", {"run_id": run_id})
        self.assertEqual(status, 409)

        available = next((item for item in detail["artifacts"] if item.get("available")), None)
        if available is not None:
            status, _headers, body = self.post_json("/api/runs/evidence", {
                "run_id": run_id, "artifact": available["id"]})
            self.assertEqual(status, 200, body)
            self.assertIn("content", json.loads(body))
        status, _headers, _body = self.post_json("/api/runs/evidence", {
            "run_id": run_id, "artifact": "not-declared"})
        self.assertEqual(status, 404)

        case = next((item for item in detail["cases"]), None)
        if case is not None:
            status, _headers, body = self.post_json("/api/runs/case-history", {
                "case_id": case["id"], "limit": 1, "cursor": None})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["case_id"], case["id"])
        status, _headers, _body = self.post_json("/api/runs/detail", {
            "run_id": run_id, "lineage_limit": 1, "lineage_cursor": None},
            {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
