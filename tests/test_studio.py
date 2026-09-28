# SPDX-License-Identifier: MIT
"""Studio server state, lifecycle and public CLI acceptance tests."""
from __future__ import annotations

import json
import http.client
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "harness"
sys.path.insert(0, str(REPO / "lib"))

from harness_core import workers  # noqa: E402
from harness_core.studio import current, state_root  # noqa: E402
from harness_core.studio import lifecycle as studio_lifecycle  # noqa: E402
from harness_core.studio import server as studio_server  # noqa: E402
from harness_core.studio.state import (PROTOCOL_VERSION, SCHEMA_VERSION, StateError,  # noqa: E402
                                       Store)


class StudioFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self.tmp.name)) / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home),
                        PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        subprocess.run([sys.executable, str(CLI), "studio", "stop", "--json"],
                       env=self.env, capture_output=True, text=True, timeout=5)
        self.tmp.cleanup()

    def cli(self, *args, timeout=20):
        return subprocess.run([sys.executable, str(CLI), "studio"] + list(args), env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def start(self, *extra):
        done = self.cli("--detach", "--no-open", "--json", *extra)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.splitlines()), 1)
        return json.loads(done.stdout)

    def session_cookie(self, started):
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        connection = http.client.HTTPConnection("127.0.0.1", started["port"], timeout=2)
        connection.request("POST", studio_server.CONTROL_BOOTSTRAP, headers={
            "Host": record["host"],
            "Authorization": "Bearer " + record["control_credential"],
        })
        issued_response = connection.getresponse()
        self.assertEqual(issued_response.status, 200)
        issued = json.loads(issued_response.read())
        connection.close()
        body = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
        connection = http.client.HTTPConnection("127.0.0.1", started["port"], timeout=2)
        connection.request("POST", studio_server.BOOTSTRAP, body=body, headers={
            "Host": record["host"], "Origin": "null",
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
        })
        bootstrap = connection.getresponse()
        self.assertEqual(bootstrap.status, 200)
        cookie = bootstrap.headers["Set-Cookie"].split(";", 1)[0]
        bootstrap.read()
        connection.close()
        return cookie


class StateTests(StudioFixture):
    def test_state_is_mode_0600_and_versioned(self):
        result = self.start()
        path = state_root(self.home) / "instance.json"
        record = json.loads(path.read_text())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)
        self.assertEqual(record["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(record["pid"], result["pid"])
        self.assertTrue(record["pid_start"])
        self.assertTrue(record["control_credential"])

    def test_store_refuses_symlinked_state_component(self):
        target = self.home / "target"
        target.mkdir()
        link = self.home / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(StateError):
            with Store(link / "studio"):
                pass

    def test_owned_state_directory_and_lock_permissions_are_repaired(self):
        root = state_root(self.home)
        root.mkdir(parents=True)
        root.chmod(0o777)
        lock_path = root / "instance.lock"
        lock_path.write_text("")
        lock_path.chmod(0o666)
        with Store(root) as store:
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            lock = store.acquire()
            assert lock is not None
            try:
                self.assertEqual(stat.S_IMODE(os.fstat(lock).st_mode), 0o600)
            finally:
                os.close(lock)

    def test_store_refuses_an_unsafe_lock_inode(self):
        root = state_root(self.home)
        root.mkdir(parents=True)
        (root / "instance.lock").mkdir()
        with Store(root) as store:
            with self.assertRaisesRegex(StateError, "safe regular file"):
                store.acquire()

    def test_store_refuses_a_state_file_with_unsafe_permissions(self):
        root = state_root(self.home)
        with Store(root) as store:
            path = root / "instance.json"
            path.write_text(json.dumps({"schema_version": SCHEMA_VERSION}))
            path.chmod(0o666)
            with self.assertRaisesRegex(StateError, "mode-0600"):
                store.read()

    def test_ui_preferences_persist_safely_across_store_reopens(self):
        root = state_root(self.home)
        with Store(root) as store:
            self.assertEqual(store.read_ui_preferences(), {"color_scheme": "auto"})
            store.write_ui_preferences({"color_scheme": "dark"})
        self.assertEqual(stat.S_IMODE((root / "ui-preferences.json").stat().st_mode), 0o600)
        with Store(root) as store:
            self.assertEqual(store.read_ui_preferences(), {"color_scheme": "dark"})
            with self.assertRaisesRegex(StateError, "preferences are invalid"):
                store.write_ui_preferences({"color_scheme": "sepia"})

    def test_incompatible_state_is_refused_without_signalling_pid(self):
        root = state_root(self.home)
        with Store(root) as store:
            path = root / "instance.json"
            path.write_text(json.dumps({"schema_version": 99}))
            path.chmod(0o600)
        done = self.cli("status", "--json")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unsupported Studio state schema", done.stderr)

    def test_pid_start_identity_mismatch_is_stale_and_never_signalled(self):
        root = state_root(self.home)
        record = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
                  "pid": os.getpid(), "pid_start": "not-this-process", "port": 49152,
                  "host": "0" * 32 + ".localhost:49152",
                  "url": "http://" + "0" * 32 + ".localhost:49152/",
                  "control_credential": "credential",
                  "instance_epoch": "epoch"}
        self.assertNotEqual(workers.process_start(os.getpid()), record["pid_start"])
        with Store(root) as store:
            store.write(record)
        self.assertIsNone(current(root))
        self.assertFalse((root / "instance.json").exists())
        os.kill(os.getpid(), 0)


class LifecycleTests(StudioFixture):
    def test_static_page_without_the_nonce_marker_fails_closed(self):
        handler = mock.Mock()
        handler.server.static_files.read_bytes.return_value = b"<!doctype html><title>Studio</title>"
        route = studio_server.ROUTES.resolve("GET", "/")
        assert route is not None

        studio_server._static(handler, route)

        handler._send.assert_called_once_with(
            503, b"Studio bundle is unavailable\n", "text/plain; charset=utf-8")

    def test_static_control_and_health_routes_are_registered_with_owned_schemas(self):
        routes = {(route.method, route.path): route for route in studio_server.ROUTES.entries}
        for key in (("GET", "/"), ("HEAD", "/"), ("GET", "/index.html"),
                    ("GET", studio_server.STATIC_ASSET_ROUTES["css"]),
                    ("HEAD", studio_server.STATIC_ASSET_ROUTES["css"]),
                    ("GET", studio_server.STATIC_ASSET_ROUTES["js"]),
                    ("HEAD", studio_server.STATIC_ASSET_ROUTES["js"]),
                    ("POST", studio_server.BOOTSTRAP), ("GET", "/api/session"),
                    ("GET", studio_server.CONTROL_HEALTH),
                    ("POST", studio_server.CONTROL_BOOTSTRAP),
                    ("POST", studio_server.CONTROL_STOP)):
            self.assertIn(key, routes)
        self.assertEqual(routes[("GET", "/")].parity_exemption, "static")
        self.assertEqual(routes[("GET", studio_server.CONTROL_HEALTH)].parity_exemption,
                         "authenticated-health")
        self.assertEqual(routes[("POST", studio_server.CONTROL_STOP)].parity_exemption,
                         "transport")

    def test_duplicate_method_and_path_registration_is_refused(self):
        route = studio_server.ROUTES.resolve("GET", "/")
        assert route is not None
        with self.assertRaisesRegex(ValueError, "duplicate Studio route"):
            studio_server.RouteRegistry((route, route))

    def test_unapproved_parity_exemption_is_refused(self):
        route = studio_server.ROUTES.resolve("GET", "/")
        assert route is not None
        invalid = studio_server.Route(route.method, "/unapproved", route.media_type,
                                      route.response_schema, route.handler, "domain-shortcut")
        with self.assertRaisesRegex(ValueError, "unsupported Studio parity exemption"):
            studio_server.RouteRegistry((invalid,))

    def test_registered_media_types_and_response_schemas_match_served_payloads(self):
        started = self.start()
        static_route = studio_server.ROUTES.resolve("GET", "/")
        health_route = studio_server.ROUTES.resolve("GET", studio_server.CONTROL_HEALTH)
        assert static_route is not None and health_route is not None
        cookie = self.session_cookie(started)
        static_request = urllib.request.Request(started["url"], headers={"Cookie": cookie})
        with urllib.request.urlopen(static_request, timeout=2) as response:
            body = response.read()
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertEqual(response.headers["Content-Type"], static_route.media_type)
            static_route.response_schema.validate(body)
        asset_path = re.search(rb'["\']\./(assets/[^"\']+)["\']', body)
        assert asset_path is not None
        relative = asset_path.group(1).decode("ascii")
        asset_route = studio_server.ROUTES.resolve("GET", "/" + relative)
        assert asset_route is not None
        asset_request = urllib.request.Request(started["url"] + relative,
                                               headers={"Cookie": cookie})
        with urllib.request.urlopen(asset_request, timeout=2) as response:
            asset_body = response.read()
            self.assertEqual(response.headers["Content-Type"], asset_route.media_type)
            self.assertEqual(response.headers["Cache-Control"],
                             "public, max-age=31536000, immutable")
            asset_route.response_schema.validate(asset_body)
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        request = urllib.request.Request(started["url"] + studio_server.CONTROL_HEALTH,
                                         headers={"Authorization": "Bearer " +
                                                  record["control_credential"]})
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
            self.assertEqual(response.headers["Content-Type"], health_route.media_type)
            health_route.response_schema.validate(payload)

    def test_unknown_routes_remain_refused(self):
        started = self.start()
        cookie = self.session_cookie(started)
        request = urllib.request.Request(started["url"] + "not-registered",
                                         headers={"Cookie": cookie})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(json.loads(caught.exception.read()), {"error": "not_found"})
        caught.exception.close()

    def test_unsupported_methods_use_the_registry_error_contract(self):
        started = self.start()
        cookie = self.session_cookie(started)
        request = urllib.request.Request(started["url"], method="PATCH",
                                         headers={"Cookie": cookie})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        response = caught.exception
        body = response.read()
        payload = json.loads(body)
        self.assertEqual(response.code, 405)
        self.assertEqual(response.headers["Content-Type"], studio_server.ROUTES.error_media_type)
        studio_server.ROUTES.error_schema.validate(payload)
        self.assertEqual(payload, {"error": "method_not_allowed"})
        self.assertNotIn(b"Error code", body)
        self.assertNotIn(b"<html", body.lower())
        response.close()

    def test_detach_reuse_status_static_page_and_stop(self):
        started = self.start()
        self.assertRegex(started["url"], r"^http://[0-9a-f]{32}\.localhost:\d+/$")
        self.assertFalse(started["reused"])
        self.assertFalse(started["port_fallback"])
        self.assertNotIn("control_credential", started)
        cookie = self.session_cookie(started)
        request = urllib.request.Request(started["url"], headers={"Cookie": cookie})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("Model Citizen Studio", response.read().decode())
        request = urllib.request.Request(started["url"], method="HEAD",
                                         headers={"Cookie": cookie})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")

        reused = self.cli("--no-open", "--json")
        self.assertEqual(reused.returncode, 0, reused.stderr)
        self.assertEqual(json.loads(reused.stdout)["pid"], started["pid"])
        self.assertTrue(json.loads(reused.stdout)["reused"])

        status_done = self.cli("status", "--json")
        self.assertEqual(status_done.returncode, 0, status_done.stderr)
        status_row = json.loads(status_done.stdout)
        self.assertEqual(status_row["pid"], started["pid"])
        self.assertTrue(status_row["running"])

        stopped = self.cli("stop", "--json")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertTrue(json.loads(stopped.stdout)["stopped"])
        self.assertFalse((state_root(self.home) / "instance.json").exists())
        absent = self.cli("status", "--json")
        self.assertEqual(absent.returncode, 1)
        self.assertEqual(json.loads(absent.stdout), {"running": False})

    def test_concurrent_detached_launches_reuse_the_single_winner(self):
        command = [sys.executable, str(CLI), "studio", "--detach", "--no-open", "--json"]
        first = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        first_out, first_err = first.communicate(timeout=20)
        second_out, second_err = second.communicate(timeout=20)
        self.assertEqual(first.returncode, 0, first_err)
        self.assertEqual(second.returncode, 0, second_err)
        rows = [json.loads(first_out), json.loads(second_out)]
        self.assertEqual(rows[0]["pid"], rows[1]["pid"])
        self.assertEqual(sorted(row["reused"] for row in rows), [False, True])

    def test_concurrent_launch_with_a_different_port_never_calls_reuse_a_fallback(self):
        probes = []
        for _ in range(2):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
        requested = [probe.getsockname()[1] for probe in probes]
        for probe in probes:
            probe.close()
        processes = [subprocess.Popen(
            [sys.executable, str(CLI), "studio", "--detach", "--no-open", "--json",
             "--port", str(port)], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for port in requested]
        completed = [process.communicate(timeout=20) for process in processes]
        for process, (_stdout, stderr) in zip(processes, completed):
            self.assertEqual(process.returncode, 0, stderr)
        rows = [json.loads(stdout) for stdout, _stderr in completed]
        self.assertEqual(rows[0]["pid"], rows[1]["pid"])
        reused = next(row for row in rows if row["reused"])
        self.assertFalse(reused["port_fallback"])
        self.assertNotIn("requested_port", reused)
        self.assertRegex(reused["url"], r"^http://[0-9a-f]{32}\.localhost:\d+/$")

    def test_detached_loser_attributes_no_fallback_to_a_different_port_winner(self):
        winner = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
                  "pid": 222, "pid_start": "winner", "port": 49153,
                  "url": "http://127.0.0.1:49153/", "control_credential": "credential",
                  "instance_epoch": "epoch"}
        child = mock.Mock(pid=111)
        child.poll.return_value = 1
        with mock.patch.object(studio_lifecycle.subprocess, "Popen", return_value=child), \
                mock.patch.object(studio_lifecycle, "current", return_value=winner):
            result = studio_lifecycle.launch_detached(["citizen"], state_root(self.home), 49152)
        self.assertTrue(result["reused"])
        self.assertFalse(result["port_fallback"])
        self.assertNotIn("requested_port", result)
        self.assertEqual(result["port"], winner["port"])

    def test_browser_open_success_failure_and_json_output(self):
        report = self.home / "browser-url"
        browser = self.home / "browser"
        browser.write_text("#!/bin/sh\nprintf '%s' \"$1\" > \"$STUDIO_BROWSER_REPORT\"\n")
        browser.chmod(0o700)
        opened_env = dict(self.env, BROWSER=str(browser), STUDIO_BROWSER_REPORT=str(report))
        opened = subprocess.run([sys.executable, str(CLI), "studio", "--detach", "--json"],
                                env=opened_env, capture_output=True, text=True, timeout=20)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(len(opened.stdout.splitlines()), 1)
        opened_row = json.loads(opened.stdout)
        deadline = time.monotonic() + 2
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(report.read_text().startswith("file://"))
        self.assertNotEqual(report.read_text(), opened_row["url"])

        failed_browser = self.home / "failed-browser"
        failed_browser.write_text("#!/bin/sh\nexit 1\n")
        failed_browser.chmod(0o700)
        failed_env = dict(self.env, BROWSER=str(failed_browser))
        failed = subprocess.run([sys.executable, str(CLI), "studio", "--json"], env=failed_env,
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertEqual(len(failed.stdout.splitlines()), 1)
        self.assertEqual(json.loads(failed.stdout)["pid"], opened_row["pid"])

    def test_control_route_requires_the_private_credential(self):
        started = self.start()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(started["url"] + "__studio/control/health", timeout=2)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

    def test_an_occupied_requested_port_falls_back_and_reports_both_ports(self):
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        self.addCleanup(occupied.close)
        requested = occupied.getsockname()[1]
        started = self.start("--port", str(requested))
        self.assertTrue(started["port_fallback"])
        self.assertEqual(started["requested_port"], requested)
        self.assertNotEqual(started["port"], requested)
        self.assertRegex(started["url"], r"^http://[0-9a-f]{32}\.localhost:\d+/$")

    def test_no_flag_can_select_a_non_loopback_bind(self):
        help_text = self.cli("--help")
        self.assertEqual(help_text.returncode, 0)
        self.assertNotIn("--host", help_text.stdout)
        self.assertNotIn("--bind", help_text.stdout)
        self.assertRegex(self.start()["url"], r"^http://[0-9a-f]{32}\.localhost:\d+/$")

    def test_bad_ports_are_refused(self):
        for port in ("-1", "65536"):
            with self.subTest(port=port):
                done = self.cli("--detach", "--no-open", "--port", port)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("between 0 and 65535", done.stderr)


class SignalTests(StudioFixture):
    def foreground(self):
        process = subprocess.Popen([sys.executable, str(CLI), "studio", "--no-open", "--json"],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        assert process.stdout is not None
        row = json.loads(process.stdout.readline())
        return process, row

    def child_pids(self, parent):
        done = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
                              timeout=2)
        return [int(line.split()[0]) for line in done.stdout.splitlines()
                if len(line.split()) == 2 and line.split()[1] == str(parent)]

    def test_sigint_and_sigterm_exit_with_open_connections_and_no_children(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                process, row = self.foreground()
                connection = socket.create_connection(("127.0.0.1", row["port"]), timeout=2)
                connection.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n")
                self.assertEqual(self.child_pids(process.pid), [])
                started = time.monotonic()
                process.send_signal(signum)
                process.wait(timeout=2)
                elapsed = time.monotonic() - started
                connection.close()
                assert process.stdout is not None and process.stderr is not None
                process.stdout.close()
                process.stderr.close()
                self.assertLess(elapsed, 2)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(self.child_pids(process.pid), [])
                self.assertFalse((state_root(self.home) / "instance.json").exists())

    def test_signal_during_state_publication_or_ready_callback_exits_cleanly(self):
        root = state_root(self.home)
        script = """
import os
import signal
import sys
from pathlib import Path
sys.path.insert(0, {library!r})
from harness_core.studio import server
from harness_core.studio.state import Store

def interrupt(_store, _record):
    os.kill(os.getpid(), signal.SIGTERM)

phase = sys.argv[1]
if phase == "state":
    Store.write = interrupt
def ready(_result):
    if phase == "ready":
        os.kill(os.getpid(), signal.SIGINT)
with Store(Path({root!r})) as store:
    server.run(Path({static!r}), store, 0, ready)
print("clean")
""".format(library=str(REPO / "lib"), root=str(root),
           static=str(REPO / "studio" / "dist"))
        for phase in ("state", "ready"):
            with self.subTest(phase=phase):
                done = subprocess.run([sys.executable, "-c", script, phase], env=self.env,
                                      capture_output=True, text=True, timeout=5)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "clean\n")
                self.assertEqual(done.stderr, "")
                self.assertFalse((root / "instance.json").exists())

    def test_signal_immediately_after_handler_installation_exits_cleanly(self):
        root = state_root(self.home)
        script = """
import os
import signal
import sys
from pathlib import Path
sys.path.insert(0, {library!r})
from harness_core.studio import server
from harness_core.studio.state import Store

real_signal = signal.signal
install_count = 0
def interrupt_after_install(signum, handler):
    global install_count
    result = real_signal(signum, handler)
    install_count += 1
    if install_count == 2:
        os.kill(os.getpid(), signal.SIGTERM)
    return result

signal.signal = interrupt_after_install
with Store(Path({root!r})) as store:
    server.run(Path({static!r}), store, 0)
print("clean")
""".format(library=str(REPO / "lib"), root=str(root),
           static=str(REPO / "studio" / "dist"))
        done = subprocess.run([sys.executable, "-c", script], env=self.env, capture_output=True,
                              text=True, timeout=5)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "clean\n")
        self.assertEqual(done.stderr, "")
        self.assertFalse((root / "instance.json").exists())


if __name__ == "__main__":
    unittest.main()
