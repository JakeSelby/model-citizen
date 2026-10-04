"""A paid run that is timed out or cancelled still records the spend its runner reported."""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_harness import harness  # noqa: F401  (puts lib/ on the path)
from harness_core.studio import run_worker, runs
from studio_target_support import FixtureTargetService

_SOURCE = (
    "import json,os,pathlib,time\n"
    "path = pathlib.Path(os.environ['CITIZEN_STUDIO_RESULT'])\n"
    "path.write_text(json.dumps(dict(schema_version=1, run_id=os.environ['CITIZEN_STUDIO_RUN_ID'],"
    " spend_usd=0.3, cases=[dict(id='case-one', status='completed', spend_usd=0.3)],"
    " stop_reason='runner_failure')))\n"
    "path.chmod(0o600)\n"
    "time.sleep(60)\n")
# The catalog refuses braces in argv, so the runner's source travels base64-encoded.
REPORT_THEN_WAIT = ("import base64; exec(base64.b64decode('%s'))"
                    % base64.b64encode(_SOURCE.encode()).decode("ascii"))


class InterruptedPaidRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.catalog = self.root / "suites.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "paid-suite", "version": 1, "argv": [sys.executable, "-c", REPORT_THEN_WAIT],
            "parameters": {}, "cost_class": "spends_usage", "expected_duration_seconds": 1,
            "timeout_seconds": 2, "targets": ["installed"], "cases": ["case-one"]}]}))
        self.supervisor = runs.RunSupervisor(self.root / "studio", self.catalog, 1,
                                             target_service=FixtureTargetService())
        self.addCleanup(self.supervisor.close)

    def created(self):
        preview = self.supervisor.spend_preview("paid-suite", {}, "installed", "current",
                                                "0.2", "1", "api_credit")
        with mock.patch.object(self.supervisor, "_admit_locked"):
            return self.supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"], max_budget_usd="0.2",
                spend_cap_usd="1", pricing_source="api_credit")["run_id"]

    def starting(self, run_id):
        token = "a" * 32  # the worker's admitted-then-starting handoff, as the spend guard tests set it
        with self.supervisor.lock():
            record = self.supervisor._read(run_id)
            record.update(status="admitted", admission_token=token)
            self.supervisor._write(record)
            record.update(status="starting", runner_pid=os.getpid(),
                          runner_identity=runs.process_identity(os.getpid()))
            self.supervisor._write(record)
        return token

    def test_a_timed_out_run_keeps_the_spend_its_runner_reported(self):
        run_id = self.created()
        run_worker.execute(self.supervisor, run_id, self.starting(run_id))
        final = self.supervisor.show(run_id)
        self.assertEqual(final["status"], "timed_out")
        self.assertEqual(final["spend_actual"], 0.3)
        self.assertEqual(final["spend_stop_reason"], "runner_failure")
        self.assertEqual(final["usage_ledger_state"], "recorded")

    def test_a_cancelled_run_keeps_the_spend_its_runner_reported(self):
        run_id = self.created()
        token = self.starting(run_id)
        def work():
            # A worker runs in its own process with its own supervisor; one per thread here.
            own = runs.RunSupervisor(self.root / "studio", self.catalog, 1,
                                     target_service=FixtureTargetService())
            try:
                run_worker.execute(own, run_id, token)
            finally:
                own.close()
        worker = threading.Thread(target=work)
        worker.start()
        result = self.root / "studio" / "runs" / run_id / "spend-result.json"
        deadline = time.monotonic() + 20
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.supervisor.cancel(run_id)
        worker.join(30)
        final = self.supervisor.show(run_id)
        self.assertEqual((final["status"], final["spend_actual"]), ("cancelled", 0.3))


if __name__ == "__main__":
    unittest.main()
