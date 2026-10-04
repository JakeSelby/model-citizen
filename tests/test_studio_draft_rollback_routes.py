# SPDX-License-Identifier: MIT
"""The rollback preview and rollback routes refuse anonymous, cross-origin, malformed and
unconfirmed requests, name their CLI equivalents, and run the CLI for the rollback itself."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_studio_security import StudioSecurityFixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import rollback as draft_rollback  # noqa: E402
from harness_core.studio import server  # noqa: E402

PREVIEW, ROLLBACK = "/api/configure/apply/rollback/preview", "/api/configure/apply/rollback"
APPLY_ID = "a" * 32
VALID = {PREVIEW: {"apply_id": APPLY_ID}, ROLLBACK: {"apply_id": APPLY_ID, "confirm": "tuning"}}
MALFORMED = {
    PREVIEW: [{"apply_id": 7}, {"apply_id": "../../etc"}],
    ROLLBACK: [{"apply_id": APPLY_ID, "confirm": ""}, {"apply_id": None, "confirm": "tuning"},
               {"apply_id": APPLY_ID}],
}


class DraftRollbackRouteTests(StudioSecurityFixture):
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

    def test_each_route_needs_a_session_origin_csrf_and_well_typed_fields(self):
        cookie, csrf = self._session()
        origin = self.record["url"].rstrip("/")
        trusted = {"Cookie": cookie, "Origin": origin, "X-Studio-CSRF": csrf}
        for path in (PREVIEW, ROLLBACK):
            with self.subTest(path=path):
                self.assertEqual(self._post(path, VALID[path])[0], 401)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie, Origin=origin)[0], 403)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie,
                                            **{"X-Studio-CSRF": csrf})[0], 403)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie,
                                            Origin="https://example.invalid",
                                            **{"X-Studio-CSRF": csrf})[0], 403)
                for malformed in MALFORMED[path]:
                    status, body = self._post(path, malformed, **trusted)
                    self.assertEqual(status, 400, body)
                status, body = self._post(path, dict(VALID[path], extra=1), **trusted)
                self.assertEqual(status, 400, body)
                status, body = self._post(path, VALID[path], **trusted)
                self.assertEqual(status, 200, body)
                payload = json.loads(body)
                if path == PREVIEW:
                    self.assertFalse(payload["can_rollback"])
                    self.assertEqual(payload["refusals"][0]["code"], "unknown-apply")
                else:
                    self.assertEqual((payload["status"], payload["error_code"]),
                                     ("refused", "unknown-apply"))
                    self.assertFalse(payload["applied"])


class RollbackRouteContractTests(unittest.TestCase):
    def test_routes_name_their_cli_equivalents(self):
        routes = {route.path: route for route in server.ROUTES.entries}
        self.assertEqual(routes[PREVIEW].cli_command, draft_rollback.CLI_COMMANDS["preview"])
        self.assertEqual(routes[ROLLBACK].cli_command, draft_rollback.CLI_COMMANDS["rollback"])
        for path in (PREVIEW, ROLLBACK):
            self.assertEqual(routes[path].method, "POST")
            self.assertIsNone(routes[path].parity_exemption)

    def test_the_studio_rollback_runs_the_cli_with_the_draft_confirmed(self):
        completed = subprocess.CompletedProcess([], 0, stdout='noise\n{"status": "rolled-back"}\n', stderr="")
        with mock.patch.dict(os.environ, {"HARNESS_QUIET": "1"}), \
                mock.patch.object(server.subprocess, "run", return_value=completed) as run:
            payload = server._run_draft_rollback(ROOT, APPLY_ID, "tuning")
        self.assertEqual(payload, {"status": "rolled-back"})
        argv = run.call_args[0][0]
        self.assertEqual(argv[1:], [str(ROOT / "bin" / "harness"), "draft", "rollback", APPLY_ID,
                                    "--draft", "tuning", "--via-studio", "--json"])
        self.assertNotIn("HARNESS_QUIET", run.call_args[1]["env"])
        with mock.patch.object(server.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 2, stdout="", stderr="boom")):
            failed = server._run_draft_rollback(ROOT, APPLY_ID, "tuning")
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "rollback-unavailable"))
        server.APPLY_RESULT.validate(failed)

    def test_previews_and_refusals_fit_the_route_schemas(self):
        with mock.patch.dict(os.environ, {"HARNESS_HOME": "/nonexistent-home"}):
            preview = draft_rollback.preview(APPLY_ID)
        server.ROLLBACK_PREVIEW.validate(preview)


if __name__ == "__main__":
    unittest.main()
