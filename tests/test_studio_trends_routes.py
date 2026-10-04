# SPDX-License-Identifier: MIT
"""The trends route refuses anonymous, cross-site and malformed requests (AH-S309, #992)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import evaluation, run_store, server, trends  # noqa: E402

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


class FailingStoreHandlerTests(unittest.TestCase):
    """The handler itself, in process: a run store that fails answers 200 with the proof set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repository = Path(self.tmp.name).resolve()
        (self.repository / "product.json").write_text(json.dumps({"evidence_cards": [
            {"field": "/headline", "text": "x", "bundle": "proof", "card": "ratio"}]}))

    def call(self, history):
        sent = {}

        class Mutations:
            @staticmethod
            def call(action):
                return action()

        class Supervisor:
            pass
        supervisor = Supervisor()
        supervisor.history = history
        handler = mock.Mock()
        handler.request_json = {}
        handler.server.mutations = Mutations()
        handler.server.run_supervisor = supervisor
        handler.server.repo_root = self.repository
        handler._json.side_effect = lambda status, payload: sent.update(status=status, payload=payload)
        verified = {"ok": True, "bundle_id": "b", "errors": [], "unknown": [], "checks": {},
                    "cards": [{"id": "ratio", "claim": "c", "estimand": "intention-to-treat",
                               "figure": {"value": 1}, "interval": {"value": [0, 2]},
                               "verify_status": True}]}
        route = {item.path: item for item in server.ROUTES.entries}[TRENDS]
        with mock.patch.object(evaluation, "verify_bundle", return_value=verified) as verify:
            server._trends(handler, route)
        verify.assert_called_once_with(self.repository / "proof")
        return sent

    def test_a_failing_run_store_leaves_the_proof_set_and_answers_200(self):
        store = mock.Mock()
        store.history.side_effect = run_store.RunStoreError("run index read failed")
        sent = self.call(store)
        self.assertEqual(sent["status"], 200)
        document = sent["payload"]
        self.assertEqual(document["sections"]["history"]["status"], "unavailable")
        self.assertIn("run index read failed", document["sections"]["static"]["reason"])
        self.assertEqual(document["lines"], [])
        self.assertEqual(document["proof"]["claims"][0]["status"], "verified")
        self.assertEqual(document["proof"]["bundles"][0]["bundle_id"], "b")

    def test_a_coding_error_in_the_read_is_not_reported_as_an_unreadable_index(self):
        with self.assertRaises(AttributeError):
            self.call(object())


if __name__ == "__main__":
    unittest.main()
