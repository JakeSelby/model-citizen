# SPDX-License-Identifier: MIT
"""The module authoring routes refuse anonymous, cross-origin and malformed requests."""
from __future__ import annotations

import json

from test_studio_security import StudioSecurityFixture

ROUTES = ("/api/configure/authoring/read", "/api/configure/authoring/preview",
          "/api/configure/authoring/save", "/api/configure/authoring/library")
REQUEST = {"action": "add", "kind": "rules", "name": "x", "description": "d"}
SAVE = {"draft": "missing", "base_revision": "a" * 40, "idempotency_key": "key", "request": REQUEST}
# Each route's exact field set: a missing or extra field is refused like a mistyped one.
VALID = {
    "/api/configure/authoring/read": {"draft": "missing"},
    "/api/configure/authoring/preview": {"draft": "missing", "request": REQUEST},
    "/api/configure/authoring/save": SAVE,
    "/api/configure/authoring/library": {"draft": "missing"},
}
MALFORMED = {
    "/api/configure/authoring/read": {"draft": 7},
    "/api/configure/authoring/preview": {"draft": "missing", "request": "not an object"},
    "/api/configure/authoring/save": dict(SAVE, base_revision=None),
    "/api/configure/authoring/library": {"draft": ["missing"]},
}


class AuthoringRouteBoundaryTests(StudioSecurityFixture):
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

    def test_each_route_needs_a_session_origin_and_csrf_and_refuses_bad_types(self):
        cookie, csrf = self._session()
        origin = self.record["url"].rstrip("/")
        for path in ROUTES:
            with self.subTest(path=path):
                anonymous, _body = self._post(path, VALID[path])
                self.assertEqual(anonymous, 401)
                no_csrf, _body = self._post(path, VALID[path], Cookie=cookie, Origin=origin)
                self.assertEqual(no_csrf, 403)
                no_origin, _body = self._post(path, VALID[path], Cookie=cookie, **{"X-Studio-CSRF": csrf})
                self.assertEqual(no_origin, 403)
                foreign, _body = self._post(path, VALID[path], Cookie=cookie,
                                            Origin="https://example.invalid",
                                            **{"X-Studio-CSRF": csrf})
                self.assertEqual(foreign, 403)
                malformed, body = self._post(path, MALFORMED[path], Cookie=cookie, Origin=origin,
                                             **{"X-Studio-CSRF": csrf})
                self.assertEqual(malformed, 400, body)
                accepted, body = self._post(path, VALID[path], Cookie=cookie, Origin=origin,
                                            **{"X-Studio-CSRF": csrf})
                # An unknown draft is a domain refusal, not a transport one.
                self.assertIn(accepted, (200, 409), body)
