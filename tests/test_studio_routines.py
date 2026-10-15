# SPDX-License-Identifier: MIT
"""Routines: definitions, run history, status, Home items and the launchd agent (AH-S383, #1335).

Every launchd call here goes to a fake `launchctl`, in a temporary home; the real one is never
reached, and the guard that keeps it from a home other than the account's own is tested too."""
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_harness  # noqa: E402,F401  the suite's isolation: no real presence dialog
from harness_core.studio import routines  # noqa: E402

NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)
FIELDS = {"name", "title", "installed", "schedule", "last_run", "outcome", "next_run", "overdue"}
RUN_FIELDS = {"run_id", "started_at", "finished_at", "outcome", "summary"}


class FakeLaunchctl:
    def __init__(self, bootstrap_code=0, stderr=""):
        self.calls = []
        self.bootstrap_code = bootstrap_code
        self.stderr = stderr

    def __call__(self, args):
        self.calls.append(list(args))
        code = self.bootstrap_code if args[0] == "bootstrap" else 0
        return subprocess.CompletedProcess(["launchctl", *args], code, "", self.stderr)


def transcript(home, name, command, when=NOW):
    path = home / ".claude" / "projects" / "-tmp-x" / (name + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        {"type": "user", "sessionId": name, "cwd": "/tmp/x", "timestamp": stamp,
         "message": {"role": "user", "content": "commit it"}},
        {"type": "assistant", "sessionId": name, "cwd": "/tmp/x", "timestamp": stamp,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}]}},
        {"type": "user", "sessionId": name, "cwd": "/tmp/x", "timestamp": stamp,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    os.utime(str(path), (when.timestamp(), when.timestamp()))
    return path


def decision(home, when, point, answer, **extra):
    path = home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": "decision", "decision_id": "%s-%s" % (point, when.isoformat()), "point": point,
           "module": "hooks/" + point, "session_id": "s1", "deterministic_answer": answer,
           "ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "schema_version": 1}
    row.update(extra)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


class RoutinesTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(os.path.realpath(temporary.name)) / "home"
        self.home.mkdir()
        self.launchctl = FakeLaunchctl()

    def clock(self, *times):
        values = list(times)
        return lambda: values.pop(0) if len(values) > 1 else values[0]


class DefinitionTests(RoutinesTestCase):
    def test_exactly_the_two_routines_are_defined(self):
        self.assertEqual(routines.names(), ["hook-liveness", "rule-adherence"])
        with self.assertRaises(routines.RoutineError) as raised:
            routines.definition("drift-check")
        self.assertEqual(raised.exception.code, "unknown_routine")

    def test_status_shape_for_a_fresh_home(self):
        rows = routines.status(self.home, now=NOW)
        self.assertEqual([row["name"] for row in rows], ["hook-liveness", "rule-adherence"])
        for row in rows:
            self.assertEqual(set(row), FIELDS)
            self.assertFalse(row["installed"])
            self.assertIsNone(row["last_run"])
            self.assertEqual(row["outcome"], "never")
            self.assertIsNone(row["next_run"])
            self.assertFalse(row["overdue"])
        self.assertEqual(rows[1]["schedule"], {"interval_seconds": 86400, "label": "Daily"})
        self.assertEqual(rows[0]["title"], "Hook liveness")
        self.assertFalse((self.home / ".local").exists(), "reading status writes nothing")


class RunHistoryTests(RoutinesTestCase):
    def test_rule_adherence_runs_the_detectors_over_recent_transcripts(self):
        transcript(self.home, "recent", "git commit --no-verify -m wip", NOW - dt.timedelta(hours=2))
        transcript(self.home, "old", "git commit --no-verify -m old", NOW - dt.timedelta(days=30))
        record = routines.run(self.home, "rule-adherence", now=self.clock(NOW))
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(record["details"]["sessions"], 1, "a transcript past the window is skipped")
        self.assertEqual(record["details"]["hits"], {"verification/no-verify": 1})
        self.assertIn("1 finding: verification/no-verify 1", record["summary"])
        shown = routines.history(self.home, "rule-adherence")
        self.assertEqual([r["run_id"] for r in shown], [record["run_id"]])

    def test_rule_adherence_reads_only_transcripts_since_the_last_good_run(self):
        transcript(self.home, "first", "git commit --no-verify -m a", NOW - dt.timedelta(hours=3))
        routines.run(self.home, "rule-adherence", now=self.clock(NOW - dt.timedelta(hours=1)))
        later = routines.run(self.home, "rule-adherence", now=self.clock(NOW))
        self.assertEqual(later["details"]["sessions"], 0)
        self.assertEqual(later["details"]["since"], "2026-10-15T11:00:00Z")

    def test_hook_liveness_records_the_hooks_page_liveness(self):
        decision(self.home, NOW - dt.timedelta(hours=1), "grade-bash", "deny", error="boom")
        decision(self.home, NOW - dt.timedelta(hours=1), "stop-gate", "allow")
        record = routines.run(self.home, "hook-liveness", now=self.clock(NOW))
        self.assertEqual(record["outcome"], "failed", record["summary"])
        self.assertEqual(record["details"]["erroring"], ["grade-bash"])
        self.assertEqual(record["details"]["hooks"]["stop-gate"], "live")
        self.assertIn("erroring: grade-bash", record["summary"])
        healthy = tempfile.TemporaryDirectory()
        self.addCleanup(healthy.cleanup)
        clean = routines.run(Path(healthy.name), "hook-liveness", now=self.clock(NOW))
        self.assertEqual(clean["outcome"], "ok", "a silent hook alone is not a failure")

    def test_a_routine_that_raises_is_a_failed_run_on_record(self):
        broken = dict(routines.DEFINITIONS["hook-liveness"], run=mock.Mock(side_effect=OSError("x")))
        with mock.patch.dict(routines.DEFINITIONS, {"hook-liveness": broken}):
            record = routines.run(self.home, "hook-liveness", now=self.clock(NOW))
        self.assertEqual(record["outcome"], "failed")
        self.assertEqual(record["summary"], "could not run: OSError")
        self.assertEqual(routines.status_of(self.home, "hook-liveness", now=NOW)["outcome"], "failed")

    def test_history_is_newest_first_and_pruned(self):
        for minute in range(3):
            routines.run(self.home, "hook-liveness",
                         now=self.clock(NOW + dt.timedelta(minutes=minute)))
        shown = routines.history(self.home, "hook-liveness")
        self.assertEqual([r["started_at"][11:16] for r in shown], ["12:02", "12:01", "12:00"])
        with mock.patch.object(routines, "HISTORY_KEEP", 2):
            routines.run(self.home, "hook-liveness", now=self.clock(NOW + dt.timedelta(minutes=9)))
        self.assertEqual(len(list(routines.runs_dir(self.home, "hook-liveness").glob("*.json"))), 2)

    def test_a_second_run_while_one_holds_the_lock_is_refused(self):
        held = routines._try_lock(self.home, "hook-liveness")
        self.addCleanup(routines._unlock, held)
        with self.assertRaises(routines.RoutineError) as raised:
            routines.run(self.home, "hook-liveness")
        self.assertEqual(raised.exception.code, "routine_running")

    def test_a_running_record_nobody_holds_reads_as_interrupted(self):
        path = routines.runs_dir(self.home, "hook-liveness") / "20261015T110000000000Z-a.json"
        routines._write(path, {"run_id": "a", "routine": "hook-liveness", "trigger": "scheduled",
                               "started_at": "2026-10-15T11:00:00Z", "finished_at": None,
                               "outcome": "running", "summary": "", "details": {}})
        routines._write(routines._lock_path(self.home, "hook-liveness"), {})
        row = routines.status_of(self.home, "hook-liveness", now=NOW)
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(row["last_run"]["summary"], "interrupted before it finished")
        held = routines._try_lock(self.home, "hook-liveness")
        self.addCleanup(routines._unlock, held)
        self.assertEqual(routines.status_of(self.home, "hook-liveness", now=NOW)["outcome"], "running")

    def test_last_run_carries_the_contract_fields(self):
        routines.run(self.home, "hook-liveness", trigger="scheduled",
                     now=self.clock(NOW, NOW + dt.timedelta(seconds=3)))
        row = routines.status_of(self.home, "hook-liveness", now=NOW)
        self.assertEqual(set(row["last_run"]), RUN_FIELDS)
        self.assertEqual(row["last_run"]["finished_at"], "2026-10-15T12:00:03Z")
        self.assertEqual(routines.history(self.home, "hook-liveness")[0]["trigger"], "scheduled")


class ScheduleTests(RoutinesTestCase):
    def install(self, name="hook-liveness", at=NOW):
        result = routines.install(self.home, name, launchctl=self.launchctl, now=at,
                                  python="/usr/bin/python3", repo=Path("/checkout"))
        os.utime(str(routines.plist_path(self.home, name)), (at.timestamp(), at.timestamp()))
        return result

    def test_plist_runs_the_routine_on_its_interval(self):
        document = plistlib.loads(routines.render(self.home, "rule-adherence",
                                                  python="/usr/bin/python3",
                                                  repo=Path("/checkout")))
        self.assertEqual(document["Label"], "com.agent-harness.routine.rule-adherence")
        self.assertEqual(document["ProgramArguments"], [
            "/usr/bin/python3", "-I", "/checkout/bin/harness", "studio", "routine", "run",
            "rule-adherence", "--scheduled", "--json"])
        self.assertEqual(document["StartInterval"], 86400)
        self.assertIs(document["RunAtLoad"], False)
        self.assertEqual(document["EnvironmentVariables"]["HOME"], str(self.home))
        self.assertEqual(document["StandardOutPath"],
                         str(routines.log_path(self.home, "rule-adherence")))

    def test_install_writes_and_loads_the_agent_and_remove_deletes_it(self):
        domain = "gui/%d" % os.getuid()
        row = self.install()
        path = self.home / "Library" / "LaunchAgents" / "com.agent-harness.routine.hook-liveness.plist"
        self.assertTrue(path.is_file())
        self.assertEqual(plistlib.loads(path.read_bytes())["StartInterval"], 3600)
        self.assertEqual(self.launchctl.calls, [
            ["bootout", domain + "/com.agent-harness.routine.hook-liveness"],
            ["bootstrap", domain, str(path)]])
        self.assertTrue(row["installed"])
        row = routines.remove(self.home, "hook-liveness", launchctl=self.launchctl, now=NOW)
        self.assertFalse(path.exists())
        self.assertFalse(row["installed"])
        self.assertEqual(self.launchctl.calls[-1],
                         ["bootout", domain + "/com.agent-harness.routine.hook-liveness"])

    def test_a_refused_load_leaves_no_agent_file(self):
        refusing = FakeLaunchctl(bootstrap_code=5, stderr="Bootstrap failed: 5: Input/output error\n")
        with self.assertRaises(routines.RoutineError) as raised:
            routines.install(self.home, "hook-liveness", launchctl=refusing)
        self.assertEqual(raised.exception.code, "launchd_refused")
        self.assertIn("Input/output error", raised.exception.reason)
        self.assertFalse(routines.plist_path(self.home, "hook-liveness").exists())

    def test_the_real_launchctl_is_refused_for_a_home_that_is_not_the_accounts_own(self):
        with mock.patch.object(routines, "run_launchctl") as real:
            for change in (routines.install, routines.remove):
                with self.assertRaises(routines.RoutineError) as raised:
                    change(self.home, "hook-liveness")
                self.assertEqual(raised.exception.code, "launchd_refused")
            with self.assertRaises(routines.RoutineError):
                routines.preflight(self.home, "hook-liveness")
        real.assert_not_called()
        self.assertFalse(routines.agents_dir(self.home).exists())

    def test_next_run_and_overdue(self):
        self.install(at=NOW)
        row = routines.status_of(self.home, "hook-liveness", now=NOW + dt.timedelta(minutes=30))
        self.assertEqual(row["next_run"], "2026-10-15T13:00:00Z")
        self.assertFalse(row["overdue"])
        late = routines.status_of(self.home, "hook-liveness", now=NOW + dt.timedelta(hours=2, seconds=1))
        self.assertTrue(late["overdue"], "past next_run plus one interval")
        edge = routines.status_of(self.home, "hook-liveness", now=NOW + dt.timedelta(hours=2))
        self.assertFalse(edge["overdue"])
        routines.run(self.home, "hook-liveness", now=self.clock(NOW + dt.timedelta(hours=2)))
        after = routines.status_of(self.home, "hook-liveness", now=NOW + dt.timedelta(hours=2, minutes=5))
        self.assertEqual(after["next_run"], "2026-10-15T15:00:00Z", "a run moves the next one")
        self.assertFalse(after["overdue"])

    def test_a_routine_not_installed_is_never_overdue(self):
        routines.run(self.home, "hook-liveness", now=self.clock(NOW - dt.timedelta(days=3)))
        row = routines.status_of(self.home, "hook-liveness", now=NOW)
        self.assertFalse(row["overdue"])
        self.assertIsNone(row["next_run"])


class AttentionTests(RoutinesTestCase):
    def test_failed_and_overdue_routines_become_home_items(self):
        self.assertEqual(routines.attention_items(self.home, now=NOW), [])
        decision(self.home, NOW - dt.timedelta(hours=1), "grade-bash", "deny", error="boom")
        routines.run(self.home, "hook-liveness", now=self.clock(NOW))
        routines.install(self.home, "rule-adherence", launchctl=self.launchctl)
        installed = routines.plist_path(self.home, "rule-adherence")
        then = (NOW - dt.timedelta(days=3)).timestamp()
        os.utime(str(installed), (then, then))
        items = routines.attention_items(self.home, now=NOW)
        self.assertEqual([item["id"] for item in items],
                         ["routines:hook-liveness:failed", "routines:rule-adherence:overdue"])
        for item in items:
            self.assertEqual(set(item), {"id", "source", "kind", "severity", "title", "detail",
                                         "at", "action"})
            self.assertEqual(item["source"], "routines")
            self.assertEqual(item["action"], {"label": "Open Routines", "href": "/routines"})
            # The attention registry drops the whole source when either rule breaks.
            self.assertTrue(item["id"].startswith("routines:"), item["id"])
            self.assertTrue(item["action"]["href"].startswith("/"), item["action"])
        self.assertEqual([item["kind"] for item in items], ["routine-failed", "routine-overdue"])
        self.assertEqual(items[0]["severity"], "high")


class ListingTests(RoutinesTestCase):
    def test_listing_adds_description_command_and_history(self):
        routines.run(self.home, "hook-liveness", now=self.clock(NOW))
        listing = routines.listing(self.home, now=NOW)
        self.assertEqual(set(listing), {"routines", "history"})
        first = listing["routines"][0]
        self.assertEqual(set(first) - FIELDS, {"description", "command"})
        self.assertEqual(first["command"], "citizen studio routine run hook-liveness --json")
        self.assertEqual(len(listing["history"]["hook-liveness"]), 1)
        self.assertEqual(listing["history"]["rule-adherence"], [])


if __name__ == "__main__":
    unittest.main()
