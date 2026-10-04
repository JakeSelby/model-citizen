# SPDX-License-Identifier: MIT
"""The trends route refuses anonymous, cross-site and malformed requests (AH-S309, #992)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import server, trends  # noqa: E402

TRENDS = "/api/reports/trends"


class TrendsRouteSecurityTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", TRENDS, supplied, body)

    def test_anonymous_is_401(self):
        body = b"{}"
        status, _headers, _body = self.request("POST", TRENDS, {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)

    def test_a_wrong_csrf_token_or_a_foreign_origin_is_403(self):
        status, _headers, _body = self.post({}, {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.post({}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_any_request_field_is_400(self):
        for bad in ({"series": "replay-v2"}, {"limit": 5}):
            status, _headers, body = self.post(bad)
            self.assertEqual(status, 400, bad)
            self.assertEqual(json.loads(body), {"error": "invalid_request"})

    def test_an_empty_request_reads_the_trends(self):
        status, _headers, body = self.post({})
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertEqual(document["schema_version"], trends.SCHEMA_VERSION)
        self.assertIn("never dollars", document["ratio_note"])
        self.assertIsInstance(document["lines"], list)
        self.assertIn("proof", document)

    def test_the_route_names_its_cli_equivalent(self):
        route = {item.path: item for item in server.ROUTES.entries}[TRENDS]
        self.assertEqual((route.method, route.cli_command), ("POST", trends.CLI_COMMAND))


if __name__ == "__main__":
    unittest.main()
