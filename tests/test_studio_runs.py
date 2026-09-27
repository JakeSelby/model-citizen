"""The Studio run boundary admits catalog commands only and supervises them durably."""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import run_worker, runs


def wait_for(read, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    value = read()
    while time.monotonic() < deadline:
        if predicate(value):
            return value
        time.sleep(0.03)
        value = read()
    raise AssertionError("condition did not become true; last value: " + repr(value))


class StudioRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS exposes /var through a system symlink; resolve that fixture alias so
        # the tests exercise the run-state boundary rather than the temp provider.
        self.root = Path(self.temporary.name).resolve()
        self.catalog_path = self.root / "suites.json"
        self.state = self.root / "state"
        self.started = []

    def tearDown(self):
        owners = []
        for supervisor, run_id in self.started:
            with contextlib.suppress(Exception):
                record = supervisor.cancel(run_id)
                owners.append((record.get("runner_pid"), record.get("runner_identity")))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            workers_done = all(process.poll() is not None
                               for process in runs._DETACHED_WORKERS.values())
            owners_done = all(not runs.process_matches(pid, identity)
                              for pid, identity in owners)
            if workers_done and owners_done:
                break
            time.sleep(0.02)
        for pid, process in list(runs._DETACHED_WORKERS.items()):
            if process.poll() is not None:
                runs._DETACHED_WORKERS.pop(pid, None)

    def write_catalog(self, suites):
        self.catalog_path.write_text(json.dumps({"schema_version": 1, "suites": suites}))

    def suite(self, suite_id="fixture", script="import time; time.sleep(30)", timeout=60,
              parameters=None, argv=None):
        return {
            "id": suite_id,
            "version": 1,
            "argv": argv or [sys.executable, "-c", script],
            "parameters": parameters or {},
            "cost_class": "free",
            "expected_duration_seconds": 1,
            "timeout_seconds": timeout,
            "targets": ["installed", "draft"],
        }

    def supervisor(self, maximum=3):
        return runs.RunSupervisor(self.state, self.catalog_path, maximum)

    def start(self, supervisor, suite="fixture", parameters=None):
        record = supervisor.start(suite, parameters or {}, "installed", "current")
        self.started.append((supervisor, record["run_id"]))
        return record

    def authenticated_starting_record(self, supervisor, run_id):
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

    def cli_json(self, arguments, expected=0):
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.state), \
             mock.patch.object(runs, "default_catalog_path", return_value=self.catalog_path), \
             contextlib.redirect_stdout(output):
            code = harness.main(arguments + ["--json"])
        self.assertEqual(code, expected)
        return json.loads(output.getvalue())

    def test_catalog_validation_refuses_every_user_controlled_command_escape(self):
        choices = {"case": {"kind": "choice", "values": ["ok", "; touch escaped"]}}
        self.write_catalog([self.suite(parameters=choices,
                                       argv=[sys.executable, "-c", "print('safe')", "{param:case}"])])
        catalog = runs.SuiteCatalog.load(self.catalog_path)
        rendered = catalog.get("fixture").render({"case": "; touch escaped"}, "installed", "current")
        self.assertEqual(rendered[-1], "; touch escaped")
        self.assertEqual(rendered[0], sys.executable)
        with mock.patch.object(runs.subprocess, "Popen") as launch:
            supervisor = self.supervisor()
            for suite, parameters, kind in (
                    ("missing", {}, "installed"),
                    ("fixture", {"case": "not-allowed"}, "installed"),
                    ("fixture", {"case": "ok", "executable": "sh"}, "installed"),
                    ("fixture", {"case": "ok"}, "release")):
                with self.subTest(suite=suite, parameters=parameters, target=kind):
                    with self.assertRaises(runs.RunError):
                        supervisor.start(suite, parameters, kind, "current")
            launch.assert_not_called()
        bad = self.suite(argv=["{param:command}", "--version"],
                         parameters={"command": {"kind": "choice", "values": ["python"]}})
        self.write_catalog([bad])
        with self.assertRaisesRegex(runs.RunError, "executable must be literal"):
            runs.SuiteCatalog.load(self.catalog_path)

    def test_state_root_refuses_a_symlinked_component(self):
        target = self.root / "target"
        target.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(target, target_is_directory=True)
        self.write_catalog([])
        with self.assertRaises(runs.RunError):
            runs.RunSupervisor(linked / "studio", self.catalog_path)

    def test_state_files_are_opened_no_follow_below_the_validated_root(self):
        self.write_catalog([])
        supervisor = self.supervisor()
        target = self.root / "target"
        target.write_text("outside")
        supervisor.lock_path.symlink_to(target)
        with self.assertRaises(runs.RunError):
            with supervisor.lock():
                pass
        self.assertEqual(target.read_text(), "outside")

    def test_suite_gets_an_isolated_home(self):
        script = ("import json,os,pathlib; pathlib.Path.home().joinpath('touched').write_text('run'); "
                  "print(json.dumps(dict((name,os.environ[name]) for name in "
                  "['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME',"
                  "'XDG_CACHE_HOME'])),flush=True)")
        self.write_catalog([self.suite(script=script)])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        terminal = wait_for(lambda: supervisor.show(created["run_id"]),
                            lambda item: item["status"] in runs.TERMINAL)
        self.assertEqual(terminal["status"], "succeeded")
        self.assertFalse(any(name.endswith("_path") for name in terminal))
        profile = self.state / "runs" / created["run_id"] / "profile"
        self.assertEqual((profile / "touched").read_text(), "run")
        environment = json.loads(supervisor.read_output(created["run_id"], "stdout")["chunk"])
        self.assertEqual(environment, {
            "HOME": str(profile),
            "XDG_CONFIG_HOME": str(profile / ".config"),
            "XDG_DATA_HOME": str(profile / ".local" / "share"),
            "XDG_STATE_HOME": str(profile / ".local" / "state"),
            "XDG_CACHE_HOME": str(profile / ".cache"),
        })
        self.assertFalse((self.root / "touched").exists())

    def test_cancel_stops_the_whole_process_group_within_five_seconds(self):
        script = ("import signal,subprocess,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "subprocess.Popen([sys.executable,'-c',"
                  "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); "
                  "print('ready', flush=True); time.sleep(30)")
        self.write_catalog([self.suite(script=script)])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        running = wait_for(lambda: supervisor.show(created["run_id"]),
                           lambda item: item["status"] == "running" and "command_pid" in item)
        started = time.monotonic()
        cancelled = supervisor.cancel(created["run_id"])
        self.assertLess(time.monotonic() - started, 5.1)
        self.assertEqual(cancelled["status"], "cancelled")
        wait_for(lambda: runs._group_exists(running["command_pid"]), lambda alive: not alive)

    def test_fourth_run_queues_and_fifo_admission_starts_it_next(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor()
        created = [self.start(supervisor) for _ in range(4)]
        states = wait_for(supervisor.list,
                          lambda items: sum(item["status"] == "running" for item in items) == 3
                          and sum(item["status"] == "queued" for item in items) == 1)
        queued = [item for item in states if item["status"] == "queued"]
        self.assertEqual([item["run_id"] for item in queued], [created[3]["run_id"]])
        supervisor.cancel(created[0]["run_id"])
        fourth = wait_for(lambda: supervisor.show(created[3]["run_id"]),
                          lambda item: item["status"] == "running")
        self.assertEqual(fourth["queue_sequence"], created[3]["queue_sequence"])

    def test_restart_marks_an_unowned_live_command_orphaned(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        running = wait_for(lambda: supervisor.show(created["run_id"]),
                           lambda item: item["status"] == "running")
        os.killpg(running["runner_pid"], signal.SIGKILL)
        recovered = wait_for(lambda: self.supervisor().show(created["run_id"]),
                             lambda item: item["status"] == "orphaned")
        self.assertEqual(recovered["status"], "orphaned")
        self.assertFalse(runs._group_exists(running["command_pid"]))

    def test_timeout_captures_output_and_reaches_a_terminal_state(self):
        script = "import sys,time; print('out',flush=True); print('err',file=sys.stderr,flush=True); time.sleep(30)"
        self.write_catalog([self.suite(script=script, timeout=1)])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        terminal = wait_for(lambda: supervisor.show(created["run_id"]),
                            lambda item: item["status"] in runs.TERMINAL, timeout=8)
        self.assertEqual(terminal["status"], "timed_out")
        self.assertEqual(supervisor.read_output(created["run_id"], "stdout")["chunk"].strip(), "out")
        self.assertEqual(supervisor.read_output(created["run_id"], "stderr")["chunk"].strip(), "err")

    def test_normal_and_timeout_completion_reap_the_entire_process_group(self):
        child = ("import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 "time.sleep(30)")
        normal = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," +
                  repr(child) + "]); print('spawned',flush=True); time.sleep(.3)")
        timed = ("import signal,subprocess,sys,time; "
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 "subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); "
                 "print('spawned',flush=True); time.sleep(30)")
        self.write_catalog([self.suite("normal", normal), self.suite("timed", timed, timeout=1)])
        supervisor = self.supervisor()
        for suite, expected in (("normal", "succeeded"), ("timed", "timed_out")):
            with self.subTest(suite=suite):
                created = self.start(supervisor, suite)
                running = wait_for(lambda: supervisor.show(created["run_id"]),
                                   lambda item: item["status"] == "running")
                terminal = wait_for(lambda: supervisor.show(created["run_id"]),
                                    lambda item: item["status"] in runs.TERMINAL, timeout=12)
                self.assertEqual(terminal["status"], expected)
                self.assertFalse(runs._group_exists(running["command_pid"]))

    def test_unreadable_run_state_fails_admission_closed(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        corrupt_id = "00000000-0000-4000-8000-000000000000"
        corrupt = self.state / "runs" / corrupt_id
        corrupt.mkdir(mode=0o700)
        (corrupt / "run.json").write_text("not json")
        (corrupt / "run.json").chmod(0o600)
        with mock.patch.object(runs.subprocess, "Popen") as launch:
            with self.assertRaises(runs.RunError):
                supervisor.start("fixture", {}, "installed", "current")
            launch.assert_not_called()

    def test_parseable_but_incomplete_run_state_also_fails_closed(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        corrupt_id = "00000000-0000-4000-8000-000000000001"
        corrupt = self.state / "runs" / corrupt_id
        corrupt.mkdir(mode=0o700)
        (corrupt / "run.json").write_text(json.dumps({
            "schema_version": 1,
            "run_id": corrupt_id,
            "status": "queued",
        }))
        (corrupt / "run.json").chmod(0o600)
        with mock.patch.object(runs.subprocess, "Popen") as launch:
            with self.assertRaises(runs.RunError):
                supervisor.start("fixture", {}, "installed", "current")
            launch.assert_not_called()

    def test_linux_process_identity_parses_parenthesized_command_names(self):
        stat_line = "42 (worker ) name with spaces) " + " ".join(["S"] + ["0"] * 18 + ["98765"])
        with mock.patch.object(Path, "read_text", return_value=stat_line):
            self.assertEqual(runs.process_identity(42), "proc:98765")

    def test_ps_process_identity_uses_a_stable_c_locale(self):
        completed = subprocess.CompletedProcess([], 0, stdout="Mon Jan  1 00:00:00 2024\n")
        with mock.patch.object(Path, "read_text", side_effect=OSError), \
             mock.patch.object(runs.subprocess, "run", return_value=completed) as ps:
            self.assertEqual(runs.process_identity(42), "ps:Mon Jan  1 00:00:00 2024")
        self.assertEqual(ps.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(ps.call_args.kwargs["env"]["LANG"], "C")

    def test_worker_launch_failure_drains_the_next_fifo_run(self):
        bad = self.suite("bad", argv=["/definitely/not/a/model-citizen-command"])
        good = self.suite("good")
        self.write_catalog([bad, good])
        supervisor = self.supervisor(maximum=1)
        failed = self.start(supervisor, "bad")
        admitted = self.start(supervisor, "good")
        failed = wait_for(lambda: supervisor.show(failed["run_id"]),
                          lambda item: item["status"] == "failed")
        admitted = wait_for(lambda: supervisor.show(admitted["run_id"]),
                            lambda item: item["status"] == "running")
        self.assertIn("could not start", failed["reason"])
        self.assertEqual(admitted["status"], "running")

    def test_cancel_without_a_command_never_signals_the_worker_group(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("fixture", {}, "installed", "current")
        self.authenticated_starting_record(supervisor, created["run_id"])
        with mock.patch.object(runs, "terminate_group") as terminate:
            cancelled = supervisor.cancel(created["run_id"])
        terminate.assert_not_called()
        self.assertEqual(cancelled["status"], "cancel_requested")
        with supervisor.lock():
            record = supervisor._read(created["run_id"])
            record["status"] = "cancelled"
            record["completed_at"] = runs.utc_now()
            supervisor._write(record)

    def test_missing_command_identity_reaps_before_failure(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("fixture", {}, "installed", "current")
        token = self.authenticated_starting_record(supervisor, created["run_id"])
        launched = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with mock.patch.object(run_worker.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(run_worker, "process_identity", return_value=None):
            self.assertEqual(run_worker.execute(supervisor, created["run_id"], token), 1)
        self.assertEqual(supervisor.show(created["run_id"])["status"], "failed")
        self.assertFalse(runs._group_exists(launched[0].pid))

    def test_post_spawn_state_failure_reaps_the_command_before_failing(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("fixture", {}, "installed", "current")
        token = self.authenticated_starting_record(supervisor, created["run_id"])
        launched = []
        real_popen = subprocess.Popen
        original_write = supervisor._write

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        def fail_running(record):
            if record.get("status") == "running":
                raise runs.RunError("fixture state failure")
            return original_write(record)

        with mock.patch.object(run_worker.subprocess, "Popen", side_effect=capture), \
             mock.patch.object(supervisor, "_write", side_effect=fail_running):
            with self.assertRaises(runs.RunError):
                run_worker.execute(supervisor, created["run_id"], token)
        supervisor.fail(created["run_id"], "fixture state failure")
        self.assertFalse(runs._group_exists(launched[0].pid))
        self.assertEqual(supervisor.show(created["run_id"])["status"], "failed")

    def test_failed_starting_handoff_never_executes_the_suite(self):
        marker = self.root / "must-not-exist"
        script = "import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('escaped')"
        self.write_catalog([self.suite(script=script)])
        supervisor = self.supervisor(maximum=1)
        original_write = supervisor._write

        def fail_starting(record):
            if record.get("status") == "starting":
                raise runs.RunError("injected starting write failure")
            return original_write(record)

        with mock.patch.object(supervisor, "_write", side_effect=fail_starting):
            with self.assertRaises(runs.RunError):
                supervisor.start("fixture", {}, "installed", "current")
        records = wait_for(supervisor._records,
                           lambda items: items and items[0]["status"] in runs.TERMINAL)
        self.assertIn(records[0]["status"], ("failed", "orphaned"))
        self.assertFalse(marker.exists())

    def test_targeted_cancel_ignores_an_unrelated_corrupt_record(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        created = self.start(supervisor)
        running = wait_for(lambda: supervisor.show(created["run_id"]),
                           lambda item: item["status"] == "running")
        corrupt_id = "00000000-0000-4000-8000-000000000002"
        corrupt = self.state / "runs" / corrupt_id
        corrupt.mkdir(mode=0o700)
        (corrupt / "run.json").write_text("not json")
        (corrupt / "run.json").chmod(0o600)
        cancelled = supervisor.cancel(created["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(runs._group_exists(running["command_pid"]))

    def test_orphan_capacity_releases_only_after_its_group_disappears(self):
        self.write_catalog([self.suite()])
        supervisor = self.supervisor(maximum=1)
        with mock.patch.object(supervisor, "_admit_locked"):
            reserved = supervisor.start("fixture", {}, "installed", "current")
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 start_new_session=True)
        try:
            with supervisor.lock():
                record = supervisor._read(reserved["run_id"])
                record["status"] = "admitted"
                record["admission_token"] = "b" * 32
                supervisor._write(record)
                record["status"] = "starting"
                record["runner_pid"] = owner.pid
                record["runner_identity"] = runs.process_identity(owner.pid)
                supervisor._write(record)
                record["status"] = "orphaned"
                record.pop("admission_token")
                record["command_pid"] = owner.pid
                record["command_identity"] = runs.process_identity(owner.pid)
                record["started_at"] = runs.utc_now()
                record["completed_at"] = runs.utc_now()
                record["reason"] = "fixture orphan"
                record["capacity_reserved"] = True
                supervisor._write(record)
            queued = self.start(supervisor)
            self.assertEqual(queued["status"], "queued")
            self.assertTrue(supervisor.show(reserved["run_id"])["capacity_reserved"])
            self.assertEqual(supervisor.show(queued["run_id"])["status"], "queued")

            self.assertTrue(runs.terminate_owned_group(owner.pid, process=owner))
            running = wait_for(lambda: supervisor.show(queued["run_id"]),
                               lambda item: item["status"] == "running")
            self.assertEqual(running["status"], "running")
            self.assertNotIn("capacity_reserved", supervisor.show(reserved["run_id"]))
        finally:
            if owner.poll() is None:
                runs.terminate_owned_group(owner.pid, process=owner)

    def test_output_is_bounded_and_cursor_readable_while_the_run_is_active(self):
        script = ("import time; print('alpha',flush=True); time.sleep(2); "
                  "print('omega',flush=True)")
        self.write_catalog([self.suite(script=script)])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        first = wait_for(lambda: supervisor.read_output(created["run_id"], "stdout", limit=3),
                         lambda item: item["chunk"] == "alp")
        self.assertEqual(first["cursor"], 0)
        self.assertEqual(first["next_cursor"], 3)
        self.assertFalse(first["eof"])
        self.assertNotIn("path", first)
        self.assertEqual(supervisor.show(created["run_id"])["status"], "running")

        second = supervisor.read_output(created["run_id"], "stdout",
                                        cursor=first["next_cursor"], limit=3)
        self.assertEqual(second["chunk"], "ha\n")
        self.assertEqual(second["next_cursor"], 6)
        self.assertFalse(second["eof"])
        wait_for(lambda: supervisor.show(created["run_id"]),
                 lambda item: item["status"] in runs.TERMINAL)
        third = supervisor.read_output(created["run_id"], "stdout",
                                       cursor=second["next_cursor"], limit=64)
        self.assertEqual(third["chunk"], "omega\n")
        self.assertTrue(third["eof"])
        final = supervisor.read_output(created["run_id"], "stdout",
                                       cursor=third["next_cursor"], limit=64)
        self.assertEqual(final["chunk"], "")
        self.assertEqual(final["next_cursor"], third["next_cursor"])
        self.assertTrue(final["eof"])

        with self.assertRaises(runs.RunError):
            supervisor.read_output(created["run_id"], "worker")
        with self.assertRaises(runs.RunError):
            supervisor.read_output(created["run_id"], "stdout", limit=65537)

    def test_output_byte_cursors_never_split_utf8_code_points(self):
        script = "import sys; sys.stdout.write('A€B'); sys.stdout.flush()"
        self.write_catalog([self.suite(script=script)])
        supervisor = self.supervisor()
        created = self.start(supervisor)
        wait_for(lambda: supervisor.show(created["run_id"]),
                 lambda item: item["status"] in runs.TERMINAL)
        first = supervisor.read_output(created["run_id"], "stdout", limit=2)
        self.assertEqual(first["chunk"], "A€")
        self.assertEqual(first["next_cursor"], 4)
        self.assertFalse(first["eof"])
        second = supervisor.read_output(created["run_id"], "stdout",
                                        cursor=first["next_cursor"], limit=2)
        self.assertEqual(second["chunk"], "B")
        self.assertTrue(second["eof"])
        with self.assertRaisesRegex(runs.RunError, "UTF-8 boundary"):
            supervisor.read_output(created["run_id"], "stdout", cursor=2, limit=2)

    def test_cli_list_and_validation_share_the_core_json_contract(self):
        self.write_catalog([])
        self.assertEqual(self.cli_json(["runs", "list"]), [])
        error = self.cli_json(["runs", "start", "unknown", "--target-kind", "installed",
                               "--target-ref", "current"], expected=2)
        self.assertIn("unknown suite", error["error"])

    def test_cli_reports_supervisor_construction_errors_as_json(self):
        self.write_catalog([])
        target = self.root / "unsafe-target"
        target.mkdir()
        linked = self.root / "unsafe-state"
        linked.symlink_to(target, target_is_directory=True)
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=linked), \
             mock.patch.object(runs, "default_catalog_path", return_value=self.catalog_path), \
             contextlib.redirect_stdout(output):
            code = harness.main(["runs", "list", "--json"])
        self.assertEqual(code, 2)
        error = json.loads(output.getvalue())
        self.assertIn("symlink", error["error"])

        output = io.StringIO()
        with mock.patch.object(runs, "RunSupervisor", side_effect=OSError("private path")), \
             contextlib.redirect_stdout(output):
            code = harness.main(["runs", "list", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "run state I/O failed"})

    def test_cli_successes_round_trip_the_same_persisted_run_record(self):
        self.write_catalog([self.suite()])
        started = self.cli_json(["runs", "start", "fixture", "--target-kind", "installed",
                                 "--target-ref", "/private/worktree"])
        supervisor = runs.RunSupervisor(self.state / "studio", self.catalog_path)
        self.started.append((supervisor, started["run_id"]))
        self.assertEqual(started["suite_id"], "fixture")
        self.assertEqual(started["target"], {"kind": "installed", "reference_set": True})
        self.assertEqual(started["command"], {"argument_count": 3})
        self.assertFalse(any(name.endswith("_path") for name in started))
        self.assertNotIn("/private/worktree", json.dumps(started))
        self.assertNotIn(sys.executable, json.dumps(started))

        shown = wait_for(
            lambda: self.cli_json(["runs", "show", started["run_id"]]),
            lambda item: item["status"] == "running")
        listed = self.cli_json(["runs", "list"])
        self.assertEqual([item for item in listed if item["run_id"] == started["run_id"]], [shown])

        cancelled = self.cli_json(["runs", "cancel", started["run_id"]])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled, supervisor.show(started["run_id"]))


if __name__ == "__main__":
    unittest.main()
