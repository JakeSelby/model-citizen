# SPDX-License-Identifier: MIT
"""The rule-health routes refuse anonymous, cross-site and malformed requests (AH-S310, #994)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402

HEALTH = "/api/rules/health"
TRY_WITHOUT = "/api/rules/try-without"


class RuleHealthRouteSecurityTests(studio_security.StudioSecurityFixture):
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
        return self.request("POST", path, supplied, body)

    def refuses_anonymous_and_cross_site(self, path, payload):
        body = json.dumps(payload).encode()
        status, _headers, _body = self.request("POST", path, {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)
        status, _headers, _body = self.post(path, payload, {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.post(path, payload, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_health_needs_a_session_csrf_and_origin_then_an_empty_request(self):
        self.refuses_anonymous_and_cross_site(HEALTH, {})
        status, _headers, body = self.post(HEALTH, {"days": 7})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})
        status, _headers, body = self.post(HEALTH, {})
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertEqual(document["schema_version"], 1)
        self.assertIn(document["status"], ("ready", "partial"))
        # The corpus script's exit 1 on a failing detector is a result, so precision always reads.
        self.assertEqual(document["sources"]["precision"]["status"], "ready", document["sources"])
        self.assertTrue(document["working_directory"])
        self.assertEqual(document["summary"]["rules"], len(document["rows"]))
        self.assertTrue(document["rows"])
        for row in document["rows"]:
            self.assertIn(row["state"], ("measured", "dark", "unmeasured"))

    def test_try_without_needs_a_session_csrf_and_origin_then_a_rule_name(self):
        self.refuses_anonymous_and_cross_site(TRY_WITHOUT, {"rule": "conciseness"})
        for bad in ({}, {"rule": "conciseness", "draft": "x"}, {"rule": "../secrets"}, {"rule": 3}):
            status, _headers, body = self.post(TRY_WITHOUT, bad)
            self.assertEqual(status, 400, bad)
            self.assertEqual(json.loads(body), {"error": "invalid_request"})

    def test_try_without_refuses_a_rule_that_is_not_switched_on_and_makes_no_draft(self):
        status, _headers, body = self.post(TRY_WITHOUT, {"rule": "no-such-rule"})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "rule-not-switchable"})


if __name__ == "__main__":
    unittest.main()
