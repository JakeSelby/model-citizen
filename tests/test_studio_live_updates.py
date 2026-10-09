# SPDX-License-Identifier: MIT
"""Deterministic coverage for Studio change watching and SSE delivery."""
from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_studio import StudioFixture

from harness_core.studio import server as studio_server
from harness_core.studio import auth
from harness_core.studio import live_updates
from harness_core.studio.live_updates import (MAX_WATCHED_PATHS, OVERFLOW_PATH, POLL_SECONDS,
                                               EventBroker, LiveWatcher, OverflowAggregate,
                                               WatchScanner, encode, event_id)
from harness_core.studio import state_root
from harness_core.studio.state import Store


class BrokerTests(unittest.TestCase):
    def test_snapshot_replay_order_and_explicit_gaps(self):
        broker = EventBroker("epoch", replay_limit=2)
        snapshot = broker.events_after(None)
        self.assertEqual(snapshot[0]["kind"], "snapshot")
        self.assertEqual(event_id(snapshot[0]), "epoch:0")

        first = broker.publish(("selection",), ("/config.json",))
        second = broker.publish(("library",), ("/rules/one.md",))
        self.assertEqual([item["sequence"] for item in broker.events_after("epoch:0")], [1, 2])
        self.assertEqual(broker.events_after(event_id(second)), [])

        broker.publish(("library",), ("/rules/two.md",))
        self.assertEqual(broker.events_after("epoch:0")[0]["kind"], "gap")
        self.assertEqual(broker.events_after("other:3")[0]["kind"], "gap")
        self.assertEqual(broker.events_after("epoch:99")[0]["kind"], "gap")
        self.assertIn(b"event: change", encode(first))

    def test_close_releases_waiters(self):
        broker = EventBroker("epoch")
        broker.close()
        started = time.monotonic()
        self.assertEqual(broker.wait("epoch:0", 10), [])
        self.assertLess(time.monotonic() - started, 0.1)


CPU_SAMPLES = 5


def least_cpu_seconds(work, samples: int = CPU_SAMPLES) -> float:
    """The least CPU time this thread spent on ``work`` over several runs.

    Thread time leaves out every other thread in the test process, and the least of several
    runs leaves out machine load: a contended run is scheduled onto a slower core or a lower
    clock and spends more CPU on the same work, never less. A real regression, more work per
    poll, raises every run, the least included, so the budget it is judged against still
    measures the scanner.
    """
    spent = []
    for _ in range(samples):
        started = time.thread_time()
        work()
        spent.append(time.thread_time() - started)
    return min(spent)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.primitives = self.root / "extra-primitives"
        (self.primitives / "rules").mkdir(parents=True)
        self.module = self.primitives / "rules" / "local.md"
        self.module.write_text("first\n", encoding="utf-8")
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"primitive_roots": [str(self.primitives)]}), encoding="utf-8")
        state = self.root / "state"
        state.mkdir()
        self.state = state.resolve()
        self.scanner = WatchScanner(
            Path(__file__).resolve().parent.parent, self.state,
            {"HOME": str(self.home), "HARNESS_HOME": str(self.home)},
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_config_modules_ledgers_and_runs_are_scoped(self):
        snapshot = self.scanner.scan()
        module = str(self.module.absolute())
        config = str((self.home / ".config" / "agent-harness" / "config.json").absolute())
        ledger = str((self.home / ".local" / "state" / "agent-harness" /
                      "observation.jsonl").absolute())
        run = str((self.state / "runs").resolve())
        self.assertEqual(snapshot[module][1], ("library", "overview"))
        self.assertEqual(snapshot[config][1],
                         ("configure", "library-index", "overview", "selection"))
        self.assertEqual(snapshot[ledger][1], ("activity", "overview", "reports"))
        self.assertEqual(snapshot[run][1], ("overview", "reports", "runs"))
        self.assertNotIn(str(self.home.resolve()), snapshot)

    def test_one_module_change_emits_only_the_views_that_show_its_effect(self):
        before = self.scanner.scan()
        self.module.write_text("second and larger\n", encoding="utf-8")
        after = self.scanner.scan()
        topics, paths = LiveWatcher.changes(before, after)
        self.assertEqual(topics, ["library", "overview"])
        self.assertIn(str(self.module.absolute()), paths)

    def test_contained_symlink_tracks_target_content_and_outside_targets_are_rejected(self):
        target = self.primitives / "rules" / "target.txt"
        target.write_text("first", encoding="utf-8")
        linked = self.primitives / "rules" / "linked.md"
        linked.symlink_to(target)
        outside = self.root / "outside.txt"
        outside.write_text("first", encoding="utf-8")
        unsafe = self.primitives / "rules" / "unsafe.md"
        unsafe.symlink_to(outside)
        before = self.scanner.scan()
        target_stat = target.stat()
        self.assertIsNotNone(before[str(linked.absolute())][0])
        self.assertIsNone(before[str(unsafe.absolute())][0])

        target.write_text("other", encoding="utf-8")
        os.utime(target, ns=(target_stat.st_atime_ns, target_stat.st_mtime_ns))
        outside.write_text("other", encoding="utf-8")
        after = self.scanner.scan()
        topics, paths = LiveWatcher.changes(before, after)
        self.assertIn(str(linked.absolute()), paths)
        self.assertNotIn(str(unsafe.absolute()), paths)
        self.assertIn("library", topics)

    def test_config_symlinks_track_contained_targets_and_reject_outside_targets(self):
        config = self.home / ".config" / "agent-harness" / "config.json"
        target = config.parent / "config-target.json"
        replacement = config.parent / "config-replacement.json"
        target.write_text('{"stances":{"voice":"plain"}}', encoding="utf-8")
        replacement.write_text('{"stances":{"voice":"concise"}}', encoding="utf-8")
        config.unlink()
        config.symlink_to(target.name)
        before = self.scanner.scan()
        self.assertIsNotNone(before[str(config.absolute())][0])

        target.write_text('{"stances":{"voice":"direct"}}', encoding="utf-8")
        after_edit = self.scanner.scan()
        topics, paths = LiveWatcher.changes(before, after_edit)
        self.assertIn(str(config.absolute()), paths)
        self.assertTrue({"configure", "selection"}.issubset(topics))

        config.unlink()
        config.symlink_to(replacement.name)
        after_replacement = self.scanner.scan()
        topics, paths = LiveWatcher.changes(after_edit, after_replacement)
        self.assertIn(str(config.absolute()), paths)
        self.assertTrue({"configure", "selection"}.issubset(topics))

        outside_root = self.root / "outside-primitives"
        (outside_root / "rules").mkdir(parents=True)
        escaped_module = outside_root / "rules" / "escaped.md"
        escaped_module.write_text("outside\n", encoding="utf-8")
        outside = self.root / "outside-config.json"
        outside.write_text(json.dumps({"primitive_roots": [str(outside_root)]}), encoding="utf-8")
        config.unlink()
        config.symlink_to(outside)
        unsafe = self.scanner.scan()
        self.assertIsNone(unsafe[str(config.absolute())][0])
        self.assertNotIn(str(escaped_module.absolute()), unsafe)
        outside.write_text(json.dumps({"primitive_roots": [str(outside_root)], "mode": "focus"}),
                           encoding="utf-8")
        changed = self.scanner.scan()
        _topics, paths = LiveWatcher.changes(unsafe, changed)
        self.assertNotIn(str(config.absolute()), paths)

    def test_project_and_session_config_symlinks_track_contained_targets(self):
        for variable in ("HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG"):
            with self.subTest(variable=variable):
                path = self.root / (variable.lower() + ".json")
                target = self.root / (variable.lower() + "-target.json")
                target.write_text('{"stances":{"voice":"plain"}}', encoding="utf-8")
                path.symlink_to(target.name)
                self.scanner.environ[variable] = str(path)
                before = self.scanner.scan()
                target.write_text('{"stances":{"voice":"direct"}}', encoding="utf-8")
                after = self.scanner.scan()
                topics, paths = LiveWatcher.changes(before, after)
                self.assertIn(str(path.absolute()), paths)
                self.assertTrue({"configure", "selection"}.issubset(topics))
                del self.scanner.environ[variable]
                path.unlink()
                target.unlink()

    def test_more_than_watch_capacity_forces_topic_wide_invalidation(self):
        created = []
        for index in range(MAX_WATCHED_PATHS + 1):
            path = self.primitives / "rules" / ("overflow-%d.md" % index)
            path.write_text("module\n", encoding="utf-8")
            created.append(path)
        before = self.scanner.scan()
        self.assertIn(OVERFLOW_PATH, before)
        self.assertEqual(len(before), MAX_WATCHED_PATHS + 1)
        dropped = next(path for path in created if str(path.absolute()) not in before)

        broker = EventBroker("epoch")
        watcher = LiveWatcher(self.scanner, broker)
        idle = before

        def five_polls():
            nonlocal idle
            for _ in range(5):
                idle = watcher.poll(idle)

        consumed = least_cpu_seconds(five_polls)
        self.assertLess(consumed, POLL_SECONDS * 5 * 0.05,
                        "overflow aggregation exceeded 5% of one core while idle")
        self.assertEqual(watcher.scan_count, 5 * CPU_SAMPLES)
        self.assertEqual(broker.sequence, 0)

        dropped.write_text("changed\n", encoding="utf-8")
        watcher.poll(idle)
        self.assertEqual(broker.sequence, 1)
        event = broker.events_after("epoch:0")[0]
        self.assertIn("library", event["topics"])
        self.assertEqual(event["paths"], [])

    def test_capacity_boundary_records_overflow_instead_of_dropping_silently(self):
        snapshot = {}
        overflow = OverflowAggregate()
        with mock.patch.object(live_updates, "MAX_WATCHED_PATHS", 2):
            for index in range(3):
                live_updates._add(snapshot, self.root / ("missing-%d" % index),
                                  ("library",), overflow)
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(overflow.count, 1)
        self.assertEqual(overflow.topics, {"library"})

    def test_one_watcher_serves_twenty_cursors_without_more_scans(self):
        class Scanner:
            def __init__(self):
                self.calls = 0

            def scan(self):
                self.calls += 1
                return {"/module.md": ((0, self.calls, self.calls), ("library",))}

        scanner = Scanner()
        broker = EventBroker("epoch")
        watcher = LiveWatcher(scanner, broker)
        previous = scanner.scan()
        watcher.poll(previous)
        for _ in range(20):
            events = broker.events_after("epoch:0")
            self.assertEqual(len(events), 1)
        self.assertEqual(scanner.calls, 2)
        self.assertEqual(watcher.scan_count, 1)

    def test_five_hundred_modules_stay_below_two_percent_of_one_core(self):
        for index in range(1, 500):
            (self.primitives / "rules" / ("local-%d.md" % index)).write_text(
                "module\n", encoding="utf-8")
        self.scanner.scan()

        def ten_scans():
            for _ in range(10):
                self.scanner.scan()

        consumed = least_cpu_seconds(ten_scans)
        self.assertLess(consumed, POLL_SECONDS * 10 * 0.02,
                        "500-module polling exceeded 2% of one core")

    def test_least_cpu_seconds_judges_the_least_run_of_this_thread(self):
        readings = iter([1.0, 1.5, 2.0, 2.1, 3.0, 3.9])
        with mock.patch.object(time, "thread_time", side_effect=lambda: next(readings)):
            self.assertAlmostEqual(least_cpu_seconds(lambda: None, samples=3), 0.1)

    def test_a_regression_that_adds_work_to_every_poll_still_fails_the_budget(self):
        for index in range(1, 500):
            (self.primitives / "rules" / ("local-%d.md" % index)).write_text(
                "module\n", encoding="utf-8")
        original = live_updates._fingerprint

        def slower(path, allowed_root=None, info=None):
            deadline = time.thread_time() + 0.0001
            while time.thread_time() < deadline:
                pass
            return original(path, allowed_root, info)

        with mock.patch.object(live_updates, "_fingerprint", slower):
            consumed = least_cpu_seconds(lambda: [self.scanner.scan() for _ in range(10)],
                                         samples=2)
        self.assertGreater(consumed, POLL_SECONDS * 10 * 0.02)

    def test_nested_modules_record_each_file_and_its_parent_once(self):
        skill = self.primitives / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: demo\n---\n", encoding="utf-8")
        snapshot = self.scanner.scan()
        self.assertEqual(snapshot[str(skill.absolute())][1], ("library", "overview"))
        self.assertEqual(snapshot[str(skill.parent.absolute())][1],
                         ("library-index", "overview", "selection"))
        self.assertEqual(snapshot[str(skill.absolute())][0][0], stat.S_IFREG)

    def test_twenty_live_streams_and_five_hundred_modules_stay_below_two_percent(self):
        for index in range(1, 500):
            (self.primitives / "rules" / ("local-%d.md" % index)).write_text(
                "module\n", encoding="utf-8")
        original = {name: os.environ.get(name) for name in ("HOME", "HARNESS_HOME")}
        os.environ.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        server = thread = None
        streams = []
        try:
            with Store(self.state) as store:
                server, _fallback = studio_server.bind(
                    Path(__file__).resolve().parent.parent / "studio" / "dist",
                    "performance-credential", store, 0)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
                thread.start()
                token, _form = server.sessions.issue()
                consumed = server.sessions.consume(token)
                assert consumed is not None
                session, _form = consumed
                cookie = "%s=%s" % (auth.SESSION_COOKIE, session.cookie)
                for _ in range(20):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_address[1], timeout=2)
                    connection.request("GET", "/api/live", headers={
                        "Host": server.host, "Cookie": cookie,
                    })
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    for _line in range(5):
                        response.fp.readline()
                    streams.append((connection, response))

                watched = server.live_watcher.scanner.scan()
                module_prefix = str((self.primitives / "rules").absolute()) + os.sep
                self.assertEqual(sum(path.startswith(module_prefix) and path.endswith(".md")
                                     for path in watched), 500)
                self.assertEqual(len(streams), 20)
                scans_before = server.live_watcher.scan_count
                cpu_before, wall_before = time.process_time(), time.monotonic()
                time.sleep(5.0)
                cpu_used = time.process_time() - cpu_before
                wall_used = time.monotonic() - wall_before
                scans = server.live_watcher.scan_count - scans_before
                self.assertGreaterEqual(scans, 4, "watcher did not sustain its one-second cadence")
                self.assertLess(cpu_used / wall_used, 0.02,
                                "20 live streams with 500 modules exceeded 2% of one core")
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
            for connection, response in streams:
                response.close()
                connection.close()
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_watcher_coalesces_a_poll_and_stops_cleanly(self):
        class Scanner:
            def __init__(self):
                self.calls = 0

            def scan(self):
                self.calls += 1
                return {"/config": ((0, self.calls, 0), ("selection",)),
                        "/module": ((0, self.calls, 0), ("library",))}

        broker = EventBroker("epoch")
        watcher = LiveWatcher(Scanner(), broker, interval=0.01)
        watcher.start()
        deadline = time.monotonic() + 1
        while broker.sequence < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        watcher.close()
        self.assertGreaterEqual(watcher.scan_count, 2)
        self.assertTrue(broker.closed)
        self.assertEqual(watcher._thread, None)


class LiveEndpointTests(StudioFixture):
    def test_live_route_requires_session_and_streams_versioned_snapshot(self):
        started = self.start()
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        connection = http.client.HTTPConnection("127.0.0.1", started["port"], timeout=2)
        connection.request("GET", "/api/live", headers={"Host": record["host"]})
        refused = connection.getresponse()
        self.assertEqual(refused.status, 401)
        refused.read()
        connection.close()

        cookie = self.session_cookie(started)
        connection = http.client.HTTPConnection("127.0.0.1", started["port"], timeout=2)
        connection.request("GET", "/api/live", headers={"Host": record["host"], "Cookie": cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        lines = [response.fp.readline().decode("utf-8").rstrip("\n") for _ in range(5)]
        self.assertEqual(lines[0], "retry: 1000")
        self.assertEqual(lines[1], "id: %s:0" % record["instance_epoch"])
        self.assertEqual(lines[2], "event: snapshot")
        payload = json.loads(lines[3].removeprefix("data: "))
        self.assertEqual(payload["instance_epoch"], record["instance_epoch"])
        self.assertEqual(payload["kind"], "snapshot")
        connection.close()

    def test_cli_stance_change_arrives_without_reload_within_two_seconds(self):
        started = self.start()
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        cookie = self.session_cookie(started)
        connection = http.client.HTTPConnection("127.0.0.1", started["port"], timeout=3)
        connection.request("GET", "/api/live", headers={"Host": record["host"], "Cookie": cookie})
        response = connection.getresponse()
        for _ in range(5):
            response.fp.readline()

        began = time.monotonic()
        changed = subprocess.run(
            [str(Path(__file__).resolve().parent.parent / "bin" / "harness"),
             "config", "set", "stances.voice", "answer-card"],
            env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        lines = [response.fp.readline().decode("utf-8").rstrip("\n") for _ in range(5)]
        self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual(lines[2], "event: change")
        payload = json.loads(lines[3].removeprefix("data: "))
        self.assertIn("selection", payload["topics"])
        connection.close()

    def test_live_route_is_registered_as_authenticated_sse(self):
        route = studio_server.ROUTES.resolve("GET", "/api/live")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.parity_exemption, "sse")
        self.assertEqual(route.response_schema.kind, "live-sse-stream")


if __name__ == "__main__":
    unittest.main()
