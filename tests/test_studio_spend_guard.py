"""Paid Studio suites show, confirm, enforce and record their spend contract."""
import contextlib
import base64
import concurrent.futures
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_harness import harness
from harness_core.studio import run_worker, runs, spend_guard, targets
from studio_target_support import FixtureTargetService


class StudioSpendGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "studio"
        self.catalog = self.root / "suites.json"

    def write_suite(self, script, cases=None, parameters=None):
        value = {
            "schema_version": 1,
            "suites": [{
                "id": "paid-suite",
                "version": 1,
                "argv": [sys.executable, "-c", script],
                "parameters": parameters or {},
                "cost_class": "spends_usage",
                "expected_duration_seconds": 1,
                "timeout_seconds": 20,
                "targets": ["installed"],
                "cases": cases or ["case-one", "case-two"],
            }],
        }
        self.catalog.write_text(json.dumps(value), encoding="utf-8")

    def supervisor(self):
        return runs.RunSupervisor(self.state, self.catalog, 1,
                                  target_service=FixtureTargetService())

    def preview(self, supervisor, maximum=0.2, cap=1.0, pricing="api_credit",
                parameters=None, target="current"):
        return supervisor.spend_preview(
            "paid-suite", parameters or {}, "installed", target, maximum, cap, pricing)

    def starting(self, supervisor, run_id):
        token = "a" * 32
        with supervisor.lock():
            record = supervisor._read(run_id)
            record["status"] = "admitted"
            record["admission_token"] = token
            supervisor._write(record)
            record["status"] = "starting"
            record["runner_pid"] = os.getpid()
            record["runner_identity"] = runs.process_identity(os.getpid())
            supervisor._write(record)
        return token

    @staticmethod
    def result_script(stop_reason, spend, completed, exit_code=0):
        payload = {
            "schema_version": 1,
            "run_id": None,
            "spend_usd": spend,
            "cases": [{"id": case_id, "status": "completed", "spend_usd": amount}
                      for case_id, amount in completed],
            "stop_reason": stop_reason,
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return (
            "import base64,json,os,pathlib,sys; value=json.loads(base64.b64decode('" +
            encoded + "')); "
            "value['run_id']=os.environ['CITIZEN_STUDIO_RUN_ID']; "
            "path=pathlib.Path(os.environ['CITIZEN_STUDIO_RESULT']); "
            "path.write_text(json.dumps(value)); path.chmod(0o600); sys.exit(" +
            str(exit_code) + ")"
        )

    def test_estimate_uses_only_the_same_suite_and_exact_case_count(self):
        records = [
            {"suite_id": "wanted", "status": "succeeded", "case_identities": ["a", "b"],
             "case_results": [{"status": "completed"}, {"status": "completed"}],
             "spend_actual": 1.0},
            {"suite_id": "wanted", "status": "succeeded", "case_identities": ["c", "d"],
             "case_results": [{"status": "completed"}, {"status": "completed"}],
             "spend_actual": 3.0},
            {"suite_id": "wanted", "status": "succeeded", "case_identities": ["a"],
             "case_results": [{"status": "completed"}], "spend_actual": 99.0},
            {"suite_id": "other", "status": "succeeded", "case_identities": ["a", "b"],
             "case_results": [{"status": "completed"}, {"status": "completed"}],
             "spend_actual": 99.0},
        ]
        self.assertEqual(spend_guard.estimate(records, "wanted", ["x", "y"]), {
            "amount_usd": 2.0, "basis": "median_same_suite_scale", "sample_count": 2,
            "suite_id": "wanted", "case_count": 2,
        })

    def test_no_paid_process_or_run_record_exists_before_confirmation(self):
        self.write_suite("raise SystemExit('must not run')")
        supervisor = self.supervisor()
        preview = self.preview(supervisor, 0.25, 1.0)
        self.assertTrue(preview["confirmation_required"])
        self.assertRegex(preview["confirmation_token"], r"^[0-9a-f]{64}$")
        self.assertEqual(preview["estimate"]["basis"], "no_history")
        self.assertIn("money charged", preview["pricing"]["basis"])
        with mock.patch.object(runs.subprocess, "Popen") as launch, \
             self.assertRaisesRegex(runs.RunError, "confirmation token"):
            supervisor.start("paid-suite", {}, "installed", "current",
                             max_budget_usd=0.25, spend_cap_usd=1.0,
                             pricing_source="api_credit")
        launch.assert_not_called()
        self.assertEqual(list((self.state / "runs").iterdir()), [])
        with mock.patch.object(runs.subprocess, "Popen") as launch, \
             self.assertRaisesRegex(runs.RunError, "confirmation token"):
            supervisor.start("paid-suite", {}, "installed", "current", confirmed="wrong",
                             max_budget_usd=0.25, spend_cap_usd=1.0,
                             pricing_source="api_credit")
        launch.assert_not_called()

    def test_confirmation_is_bound_to_the_full_request_and_consumed_once(self):
        self.write_suite("pass")
        supervisor = self.supervisor()
        preview = self.preview(supervisor, "999999.6", "1000000.1")
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"], max_budget_usd="999999.6",
                spend_cap_usd="1000000.1", pricing_source="api_credit")
        self.assertEqual(supervisor._read(created["run_id"])["argv"][-4:], [
            "--max-budget-usd", "999999.6", "--spend-cap", "1000000.1"])
        with self.assertRaisesRegex(runs.RunError, "already used"):
            supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"], max_budget_usd="999999.6",
                spend_cap_usd="1000000.1", pricing_source="api_credit")
        changed = self.preview(supervisor, "999999.6", "1000000.1", target="other")
        with self.assertRaisesRegex(runs.RunError, "does not match"):
            supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=changed["confirmation_token"], max_budget_usd="999999.6",
                spend_cap_usd="1000000.1", pricing_source="api_credit")

    def test_build_or_durable_record_failure_does_not_consume_confirmation(self):
        self.write_suite("pass")
        supervisor = self.supervisor()
        preview = self.preview(supervisor)
        arguments = {
            "confirmed": preview["confirmation_token"],
            "max_budget_usd": 0.2,
            "spend_cap_usd": 1.0,
            "pricing_source": "api_credit",
        }
        with mock.patch.object(supervisor.target_service, "build",
                               side_effect=targets.TargetError("fixture build failed")):
            with self.assertRaisesRegex(runs.RunError, "fixture build failed"):
                supervisor.start("paid-suite", {}, "installed", "current", **arguments)

        with mock.patch.object(supervisor, "_write",
                               side_effect=runs.RunError("fixture durable write failed")):
            with self.assertRaisesRegex(runs.RunError, "fixture durable write failed"):
                supervisor.start("paid-suite", {}, "installed", "current", **arguments)

        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("paid-suite", {}, "installed", "current", **arguments)
        self.assertEqual(supervisor._read(created["run_id"])["status"], "queued")

    def test_crash_after_durable_record_recovers_confirmation_consumption(self):
        self.write_suite("pass")
        supervisor = self.supervisor()
        preview = self.preview(supervisor)
        arguments = {
            "confirmed": preview["confirmation_token"],
            "max_budget_usd": 0.2,
            "spend_cap_usd": 1.0,
            "pricing_source": "api_credit",
        }
        with mock.patch.object(supervisor, "_consume_confirmation_locked",
                               side_effect=runs.RunError("simulated crash after durable record")), \
                self.assertRaisesRegex(runs.RunError, "simulated crash"):
            supervisor.start("paid-suite", {}, "installed", "current", **arguments)
        run_directory = next((self.state / "runs").iterdir())
        run_id = run_directory.name
        (run_directory / "run.json").unlink()
        self.assertEqual(len(list((self.state / "preparations").iterdir())), 1)
        target_directory = self.state / "targets" / run_id
        self.assertTrue(target_directory.is_dir())

        with mock.patch.object(supervisor, "_consume_confirmation_locked",
                               side_effect=runs.RunError("confirmation storage unavailable")), \
                mock.patch.object(supervisor, "_spawn_locked") as spawn, \
                self.assertRaisesRegex(runs.RunError, "storage unavailable"):
            supervisor.reconcile_and_drain()
        spawn.assert_not_called()
        self.assertEqual(len(list((self.state / "preparations").iterdir())), 1)
        self.assertTrue(target_directory.is_dir())

        launches = []

        def launch_once(record):
            launches.append(record["run_id"])
            record["status"] = "admitted"
            record["admission_token"] = "a" * 32
            supervisor._write(record)
            record["status"] = "starting"
            record["runner_pid"] = os.getpid()
            record["runner_identity"] = runs.process_identity(os.getpid())
            supervisor._write(record)

        with mock.patch.object(supervisor, "_spawn_locked", side_effect=launch_once):
            supervisor.reconcile_and_drain()
            supervisor.reconcile_and_drain()
        self.assertEqual(launches, [run_id])
        self.assertEqual(supervisor._records()[0]["status"], "starting")
        self.assertEqual(list((self.state / "preparations").iterdir()), [])
        with self.assertRaisesRegex(runs.RunError, "already used"):
            supervisor.start("paid-suite", {}, "installed", "current", **arguments)

    def test_concurrent_starts_cannot_reuse_one_confirmation(self):
        self.write_suite("pass")
        preview = self.preview(self.supervisor())

        def start():
            supervisor = self.supervisor()
            try:
                return supervisor.start(
                    "paid-suite", {}, "installed", "current",
                    confirmed=preview["confirmation_token"], max_budget_usd=0.2,
                    spend_cap_usd=1.0, pricing_source="api_credit")["run_id"]
            except runs.RunError as exc:
                return str(exc)

        with mock.patch.object(runs.RunSupervisor, "_admit_locked"), \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: start(), range(2)))
        self.assertEqual(sum("already used" in value for value in outcomes), 1)
        self.assertEqual(sum(len(value) == 36 for value in outcomes), 1)

    def test_preview_and_admission_read_history_only_under_the_supervisor_lock(self):
        self.write_suite("pass")
        supervisor = self.supervisor()
        original_lock = supervisor.lock
        original_records = supervisor._records
        active = [False]

        @contextlib.contextmanager
        def tracked_lock():
            with original_lock():
                active[0] = True
                try:
                    yield
                finally:
                    active[0] = False

        def checked_records():
            self.assertTrue(active[0])
            return original_records()

        with mock.patch.object(supervisor, "lock", tracked_lock), \
             mock.patch.object(supervisor, "_records", side_effect=checked_records):
            preview = self.preview(supervisor)
            with mock.patch.object(supervisor, "_admit_locked"):
                supervisor.start(
                    "paid-suite", {}, "installed", "current",
                    confirmed=preview["confirmation_token"], max_budget_usd=0.2,
                    spend_cap_usd=1.0, pricing_source="api_credit")

    def test_cli_shows_subscription_estimate_and_caps_before_confirming(self):
        self.write_suite("raise SystemExit('must not run')")
        output = io.StringIO()
        # The paid fixture suite is not one the Studio's free start admits; set that refusal
        # aside to read the confirmation the CLI shows before any paid start.
        with mock.patch.object(harness, "state_dir", return_value=self.root), \
             mock.patch.object(runs, "default_catalog_path", return_value=self.catalog), \
             mock.patch.object(harness.studio_free_suites, "start_refusal", return_value=None), \
             contextlib.redirect_stdout(output):
            code = harness.main([
                "runs", "start", "paid-suite", "--target-kind", "installed",
                "--target-ref", "current", "--max-budget-usd", "0.25",
                "--spend-cap", "1", "--pricing-source", "subscription", "--json",
            ])
        self.assertEqual(code, 3)
        shown = json.loads(output.getvalue())
        self.assertTrue(shown["confirmation_required"])
        self.assertRegex(shown["confirmation_token"], r"^[0-9a-f]{64}$")
        self.assertIn("subscription limits", shown["pricing"]["basis"])
        self.assertFalse(any((self.root / "studio" / "runs").iterdir()))

    def test_cap_keeps_completed_cases_marks_the_rest_not_run_and_writes_usage(self):
        self.write_suite(self.result_script("spend_cap", 0.4, [("case-one", 0.4)]))
        supervisor = self.supervisor()
        preview = self.preview(supervisor, 0.2, 0.4)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"],
                max_budget_usd=0.2, spend_cap_usd=0.4, pricing_source="api_credit")
        private = supervisor._read(created["run_id"])
        self.assertEqual(private["argv"][-4:],
                         ["--max-budget-usd", "0.2", "--spend-cap", "0.4"])
        token = self.starting(supervisor, created["run_id"])
        self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
        final = supervisor.show(created["run_id"])
        self.assertEqual(final["status"], "capped")
        self.assertEqual([row["status"] for row in final["case_results"]],
                         ["completed", "not_run"])
        self.assertEqual(final["usage_ledger_state"], "recorded")
        rows = [json.loads(line) for line in
                (self.root / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((rows[-1]["run_id"], rows[-1]["spend_usd"],
                          rows[-1]["pricing_source"]),
                         (created["run_id"], 0.4, "api_credit"))

    def test_usage_limit_is_a_limit_not_a_failure(self):
        self.write_suite(self.result_script("usage_limit", 0.1, [("case-one", 0.1)], 9))
        supervisor = self.supervisor()
        preview = self.preview(supervisor, 0.2, 1.0, "subscription")
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"],
                max_budget_usd=0.2, spend_cap_usd=1.0, pricing_source="subscription")
        token = self.starting(supervisor, created["run_id"])
        self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
        final = supervisor.show(created["run_id"])
        self.assertEqual(final["status"], "limited")
        self.assertEqual(final["returncode"], 9)
        self.assertEqual([row["status"] for row in final["case_results"]],
                         ["completed", "not_run"])
        self.assertIn("usage limit", final["reason"])

    def test_runner_failure_settles_reported_spend_even_with_a_zero_exit(self):
        self.write_suite(self.result_script(
            "runner_failure", 0.1, [("case-one", 0.1)], 0))
        supervisor = self.supervisor()
        preview = self.preview(supervisor, 0.2, 1.0, "api_credit")
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"],
                max_budget_usd=0.2, spend_cap_usd=1.0, pricing_source="api_credit")
        token = self.starting(supervisor, created["run_id"])
        self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
        final = supervisor.show(created["run_id"])
        self.assertEqual((final["status"], final["returncode"], final["spend_actual"]),
                         ("failed", 0, 0.1))
        self.assertEqual(final["spend_stop_reason"], "runner_failure")
        self.assertEqual(final["usage_ledger_state"], "recorded")
        rows = [json.loads(line) for line in
                (self.root / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((rows[-1]["run_id"], rows[-1]["spend_usd"]),
                         (created["run_id"], 0.1))

    def test_explicit_not_run_case_without_a_stop_reason_is_rejected(self):
        run_id = "00000000-0000-4000-8000-000000000001"
        directory = self.root / "result"
        directory.mkdir(mode=0o700)
        result = directory / spend_guard.RESULT_NAME
        result.write_text(json.dumps({
            "schema_version": 1, "run_id": run_id, "spend_usd": 0,
            "cases": [{"id": "case-one", "status": "not_run", "spend_usd": 0}],
            "stop_reason": None,
        }), encoding="utf-8")
        result.chmod(0o600)
        descriptor = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(spend_guard.SpendGuardError, "stop reason"):
                spend_guard.read_result(descriptor, run_id, ["case-one"])
        finally:
            os.close(descriptor)

    def test_terminal_state_precedes_idempotent_ledger_settlement_and_normalizes(self):
        self.write_suite(self.result_script(None, 0.1,
                                            [("case-one", 0.05), ("case-two", 0.05)]))
        supervisor = self.supervisor()
        preview = self.preview(supervisor)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"], max_budget_usd=0.2,
                spend_cap_usd=1.0, pricing_source="api_credit")
        token = self.starting(supervisor, created["run_id"])
        observed = []

        def unavailable(path, record):
            authoritative = supervisor._read(record["run_id"])
            observed.append((authoritative["status"], authoritative["spend_actual"],
                             authoritative["usage_ledger_state"]))
            return False

        with mock.patch.object(spend_guard, "upsert_usage", side_effect=unavailable):
            self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
        self.assertTrue(observed)
        self.assertTrue(all(item == ("succeeded", 0.1, "pending") for item in observed))
        self.assertEqual(supervisor._read(created["run_id"])["usage_ledger_state"], "pending")
        with supervisor.lock():
            supervisor._recover_locked()
            supervisor._recover_locked()
        final = supervisor._read(created["run_id"])
        self.assertEqual(final["usage_ledger_state"], "recorded")
        rows = [json.loads(line) for line in
                (self.root / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["run_id"] for row in rows], [created["run_id"]])
        indexed = supervisor.history.get(created["run_id"])
        self.assertEqual(indexed["cost"]["amount_usd"], 0.1)
        self.assertEqual([row["status"] for row in indexed["cases"]],
                         ["completed", "completed"])

    def test_invalid_caps_and_unreported_spend_fail_closed(self):
        self.write_suite("pass")
        supervisor = self.supervisor()
        for maximum, cap in ((0, 1), (2, 1), (float("nan"), 1)):
            with self.subTest(maximum=maximum, cap=cap), self.assertRaises(runs.RunError):
                self.preview(supervisor, maximum, cap)
        preview = self.preview(supervisor)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start(
                "paid-suite", {}, "installed", "current",
                confirmed=preview["confirmation_token"],
                max_budget_usd=0.2, spend_cap_usd=1.0, pricing_source="api_credit")
        token = self.starting(supervisor, created["run_id"])
        self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 0)
        final = supervisor.show(created["run_id"])
        self.assertEqual(final["status"], "failed")
        self.assertIn("did not report spend", final["reason"])


if __name__ == "__main__":
    unittest.main()
