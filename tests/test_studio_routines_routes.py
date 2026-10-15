# SPDX-License-Identifier: MIT
"""The routines routes: shape, session, CSRF, bad input, and presence for install and remove
(AH-S383, #1335). No test reaches the real `launchctl`: the served Studio runs in a temporary
home, which `routines` refuses to load an agent for, and the in-process tests pass a fake."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from test_harness import REPO  # noqa: E402
from harness_core import presence  # noqa: E402
from harness_core.studio import headless, routines  # noqa: E402

LIST = "/api/routines"
RUN = "/api/routines/run"
INSTALL = "/api/routines/install"
REMOVE = "/api/routines/remove"


class RoutineRouteSecurityTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, path, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        status, _headers, answer = self.request("POST", path, supplied, body)
        return status, json.loads(answer)

    def test_list_needs_a_session_and_answers_each_routine(self):
        status, _headers, _body = self.request("GET", LIST)
        self.assertEqual(status, 401)
        status, _headers, body = self.request("GET", LIST, {"Cookie": self.cookie_value})
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertEqual([row["name"] for row in document["routines"]],
                         ["hook-liveness", "rule-adherence"])
        self.assertEqual(document["history"], {"hook-liveness": [], "rule-adherence": []})
        status, _headers, _body = self.request("GET", LIST + "?name=x", {"Cookie": self.cookie_value})
        self.assertEqual(status, 404, "the list takes no query")

    def test_each_post_refuses_anonymous_and_cross_site_requests(self):
        for path, payload in ((RUN, {"name": "hook-liveness"}),
                              (INSTALL, {"name": "hook-liveness", "confirm": "hook-liveness"}),
                              (REMOVE, {"name": "hook-liveness", "confirm": "hook-liveness"})):
            with self.subTest(path=path):
                body = json.dumps(payload).encode()
                status, _headers, _body = self.request("POST", path, {
                    "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
                self.assertEqual(status, 401)
                self.assertEqual(self.post(path, payload, {"X-Studio-CSRF": "wrong"})[0], 403)
                self.assertEqual(self.post(path, payload, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(list(routines.runs_dir(self.home, "hook-liveness").glob("*.json")), [])

    def test_bad_input_and_an_unknown_routine_are_400(self):
        for path in (RUN, INSTALL, REMOVE):
            extra = {} if path == RUN else {"confirm": "x"}
            for bad in ({}, dict({"name": 3}, **extra), dict({"name": "", "more": 1}, **extra)):
                with self.subTest(path=path, request=bad):
                    self.assertEqual(self.post(path, bad), (400, {"error": "invalid_request"}))
            unknown = dict({"name": "drift-check"}, **({} if path == RUN else {"confirm": "drift-check"}))
            self.assertEqual(self.post(path, unknown), (400, {"error": "unknown_routine"}))

    def test_run_records_the_run_and_the_list_shows_it(self):
        status, document = self.post(RUN, {"name": "hook-liveness"})
        self.assertEqual(status, 200, document)
        self.assertEqual(document["run"]["outcome"], "ok")
        self.assertEqual(document["run"]["trigger"], "studio")
        _status, _headers, body = self.request("GET", LIST, {"Cookie": self.cookie_value})
        listing = json.loads(body)
        self.assertEqual(listing["history"]["hook-liveness"][0]["run_id"], document["run"]["run_id"])
        self.assertEqual(listing["routines"][0]["outcome"], "ok")

    def test_install_needs_the_name_typed_and_a_home_of_this_account(self):
        for path in (INSTALL, REMOVE):
            self.assertEqual(self.post(path, {"name": "hook-liveness", "confirm": "rule-adherence"}),
                             (400, {"error": "confirmation_required"}))
            status, document = self.post(path, {"name": "hook-liveness", "confirm": "hook-liveness"})
            self.assertEqual(status, 409, document)
            self.assertEqual(document["error"], "launchd_refused")
        self.assertFalse((self.home / "Library").exists())


class FakeLaunchctl:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(["launchctl", *args], 0, "", "")


class RoutinePresenceTests(unittest.TestCase):
    """Install and remove through the route handler itself, as `headless.call_route` runs it,
    with this temporary home accepted as the account's own and a fake `launchctl`."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(os.path.realpath(temporary.name)) / "home"
        self.home.mkdir()
        self.launchctl = FakeLaunchctl()
        for patch in (mock.patch.dict(os.environ, {"HOME": str(self.home),
                                                   "HARNESS_HOME": str(self.home)}),
                      mock.patch.object(routines, "_own_home"),
                      mock.patch.object(routines, "run_launchctl", self.launchctl)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, path, request):
        return headless.call_route(REPO, self.home / ".local" / "state" / "agent-harness" / "studio",
                                   "POST", path, request)

    def test_without_a_person_confirming_nothing_is_installed(self):
        with mock.patch.object(presence, "confirm", return_value=False) as asked:
            status, document = self.call(INSTALL, {"name": "rule-adherence", "confirm": "rule-adherence"})
        self.assertEqual((status, document), (403, {"error": presence.REFUSED}))
        self.assertIn("rule adherence audit", asked.call_args[0][1])
        self.assertEqual(self.launchctl.calls, [])
        self.assertFalse(routines.plist_path(self.home, "rule-adherence").exists())

    def test_with_a_person_confirming_install_then_remove(self):
        with mock.patch.object(presence, "confirm", return_value=True), \
                mock.patch.object(presence, "record"):
            status, document = self.call(INSTALL, {"name": "rule-adherence", "confirm": "rule-adherence"})
            self.assertEqual(status, 200, document)
            self.assertTrue(document["routine"]["installed"])
            self.assertTrue(routines.plist_path(self.home, "rule-adherence").is_file())
            self.assertIsNotNone(document["routine"]["next_run"])
            status, document = self.call(REMOVE, {"name": "rule-adherence", "confirm": "rule-adherence"})
        self.assertEqual(status, 200, document)
        self.assertFalse(document["routine"]["installed"])
        self.assertFalse(routines.plist_path(self.home, "rule-adherence").exists())
        self.assertEqual([call[0] for call in self.launchctl.calls], ["bootout", "bootstrap", "bootout"])

    def test_remove_without_a_person_confirming_keeps_the_agent(self):
        routines.install(self.home, "hook-liveness", launchctl=self.launchctl)
        with mock.patch.object(presence, "confirm", return_value=False):
            status, _document = self.call(REMOVE, {"name": "hook-liveness", "confirm": "hook-liveness"})
        self.assertEqual(status, 403)
        self.assertTrue(routines.plist_path(self.home, "hook-liveness").is_file())


if __name__ == "__main__":
    unittest.main()
