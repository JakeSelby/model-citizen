"""Terminal free-suite case evidence is normalized into the durable run record."""
import json
import tempfile
import unittest
import os
from unittest import mock
from pathlib import Path

from test_harness import REPO  # noqa: F401 - adds lib/ to the test import path
from harness_core.studio import run_worker, runs
from studio_target_support import FixtureTargetService


class FreeSuiteHistoryTests(unittest.TestCase):
    def test_terminal_partial_outcome_reaches_detail_and_case_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            catalog = root / "suites.json"
            script = "import sys; print('test_one (test_a.C.test_one) ... ok', file=sys.stderr)"
            catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
                "id": "unit-tests", "version": 1, "argv": ["python3", "-c", script],
                "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
                "timeout_seconds": 10, "targets": ["installed"],
            }]}))
            supervisor = runs.RunSupervisor(root / "state", catalog,
                                            target_service=FixtureTargetService())
            self.addCleanup(supervisor.close)
            with mock.patch.object(
                    runs.free_suites, "resolve_case_identities",
                    return_value=["test_a.C.test_one"]), \
                    mock.patch.object(supervisor, "_admit_locked"):
                created = supervisor.start("unit-tests", {}, "installed", "current")
            token = "a" * 32
            with supervisor.lock():
                record = supervisor._read(created["run_id"])
                record["status"] = "admitted"
                record["admission_token"] = token
                supervisor._write(record)
                record["status"] = "starting"
                record["runner_pid"] = os.getpid()
                record["runner_identity"] = runs.process_identity(os.getpid())
                supervisor._write(record)
            self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
            detail = supervisor.run_detail(created["run_id"])
            self.assertEqual(detail["cases"][0]["outcome"], "passed")
            history = supervisor.case_history("test_a.C.test_one")
            self.assertEqual(history["items"][0]["case"]["outcome"], "passed")

    def test_unit_output_becomes_case_results_without_changing_the_native_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            catalog = root / "suites.json"
            catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
                "id": "unit-tests", "version": 1, "argv": ["python3", "-c", "pass"],
                "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
                "timeout_seconds": 10, "targets": ["installed"],
            }]}))
            supervisor = runs.RunSupervisor(root / "state", catalog,
                                            target_service=FixtureTargetService())
            self.addCleanup(supervisor.close)
            run_id = "a5ea48e9-ef64-4737-a267-84f431ca6da1"
            run_dir = supervisor.runs_dir / run_id
            run_dir.mkdir()
            output = "test_one (test_a.C.test_one) ... ok\n"
            path = run_dir / "stderr.log"
            path.write_text(output)
            path.chmod(0o600)
            record = {"suite_id": "unit-tests", "case_identities": ["test_a.C.test_one"]}
            result = run_worker._free_case_results(supervisor, run_id, record, 0)
            self.assertEqual(result, [{"id": "test_a.C.test_one", "status": "passed",
                                       "detail": ""}])
            self.assertEqual(path.read_text(), output)

    def test_partial_results_survive_timeout_but_unsafe_or_unknown_results_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            catalog = root / "suites.json"
            catalog.write_text(json.dumps({"schema_version": 1, "suites": []}))
            supervisor = runs.RunSupervisor(root / "state", catalog,
                                            target_service=FixtureTargetService())
            self.addCleanup(supervisor.close)
            run_id = "a5ea48e9-ef64-4737-a267-84f431ca6da2"
            run_dir = supervisor.runs_dir / run_id
            run_dir.mkdir()
            path = run_dir / "stderr.log"
            path.write_text("test_one (test_a.C.test_one) ... ok\n"
                            "test_unknown (evil.C.test_unknown) ... ok\n")
            path.chmod(0o600)
            record = {"suite_id": "unit-tests", "case_identities": ["test_a.C.test_one"]}
            self.assertEqual(run_worker._free_case_results(supervisor, run_id, record, -9),
                             [{"id": "test_a.C.test_one", "status": "passed", "detail": ""}])
            path.chmod(0o644)
            self.assertIsNone(run_worker._free_case_results(supervisor, run_id, record, -9))
            self.assertIsNone(run_worker._free_case_results(
                supervisor, run_id, {"suite_id": "lint", "case_identities": None}, 0))


if __name__ == "__main__":
    unittest.main()
