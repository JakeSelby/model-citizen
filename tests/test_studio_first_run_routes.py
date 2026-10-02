# SPDX-License-Identifier: MIT
"""The first-run routes refuse anonymous, cross-origin and malformed requests, and starting the run
creates its draft through the CLI once, however often it is asked."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from test_studio_security import StudioSecurityFixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import drafts  # noqa: E402

import draft_support  # noqa: E402

ROUTES = ("/api/first-run", "/api/first-run/start")


class FirstRunRouteTests(StudioSecurityFixture):
    def _session(self):
        _issued, status, bootstrap_headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        cookie = self.cookie(bootstrap_headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": cookie})
        self.assertEqual(status, 200)
        return cookie, json.loads(body)["csrf_token"]

    def _post(self, path, payload, **headers):
        body = json.dumps(payload).encode("utf-8")
        base = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        status, _headers, response = self.request("POST", path, dict(base, **headers), body)
        return status, response

    def _environment(self):
        return {key: value for key, value in self.env.items() if key != "HARNESS_QUIET"}

    def test_each_route_needs_a_session_origin_csrf_and_a_valid_draft_name(self):
        cookie, csrf = self._session()
        origin = self.record["url"].rstrip("/")
        trusted = {"Cookie": cookie, "Origin": origin, "X-Studio-CSRF": csrf}
        valid = {"draft": "first-run-missing"}
        for path in ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, valid)[0], 401)
                self.assertEqual(self._post(path, valid, Cookie=cookie, Origin=origin)[0], 403)
                self.assertEqual(self._post(path, valid, Cookie=cookie, **{"X-Studio-CSRF": csrf})[0], 403)
                self.assertEqual(self._post(path, valid, Cookie=cookie, Origin="https://example.invalid",
                                            **{"X-Studio-CSRF": csrf})[0], 403)
                for malformed in ({"draft": 7}, {"draft": "../escape"}, {"draft": ""}, {},
                                  dict(valid, extra=1)):
                    status, body = self._post(path, malformed, **trusted)
                    self.assertEqual(status, 400, (malformed, body))
                    self.assertEqual(json.loads(body)["error"], "invalid_request")
        status, body = self._post("/api/first-run", valid, **trusted)
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertEqual((payload["state"], payload["draft_name"]), ("not-started", "first-run-missing"))
        self.assertTrue(payload["nothing_live_changed"])
        self.assertEqual([step["id"] for step in payload["steps"]],
                         ["health", "draft", "identity", "preferences", "check", "apply", "done"])

    def test_start_creates_the_draft_through_the_cli_and_a_second_start_resumes_it(self):
        cookie, csrf = self._session()
        trusted = {"Cookie": cookie, "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": csrf}
        name = "first-run-" + uuid.uuid4().hex[:10]
        draft_support.register_draft_cleanup(self, name, self._environment(), missing_ok=True)

        status, body = self._post("/api/first-run/start", {"draft": name}, **trusted)
        self.assertEqual(status, 200, body)
        started = json.loads(body)
        self.assertEqual(started["state"], "in-progress")
        self.assertEqual(started["draft"]["name"], name)
        self.assertTrue(started["nothing_live_changed"])
        self.assertIn("citizen draft create %s --json" % name, started["commands"]["agent"])

        status, body = self._post("/api/first-run/start", {"draft": name}, **trusted)
        self.assertEqual(status, 200, body)
        resumed = json.loads(body)
        self.assertEqual(resumed["draft"]["revision"], started["draft"]["revision"])
        self.assertEqual(sum(1 for item in drafts.list_drafts(ROOT) if item["name"] == name), 1)
