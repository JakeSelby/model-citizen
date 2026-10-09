# SPDX-License-Identifier: MIT
"""The review and apply routes refuse anonymous, cross-origin, malformed and unconfirmed requests,
and a confirmed Studio apply runs the CLI and lands in Activity."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from test_studio_security import CLI, StudioSecurityFixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import module_authoring  # noqa: E402

import draft_support  # noqa: E402
import presence_support  # noqa: E402

ROUTES = ("/api/configure/apply/review", "/api/configure/apply", "/api/configure/apply/recover")
VALID = {
    "/api/configure/apply/review": {"draft": "missing"},
    "/api/configure/apply": {"draft": "missing", "revision": "a" * 40, "confirm": "missing"},
    "/api/configure/apply/recover": {"action": "restore", "confirm": "missing"},
}
MALFORMED = {
    "/api/configure/apply/review": {"draft": 7},
    "/api/configure/apply": {"draft": "missing", "revision": None, "confirm": "missing"},
    "/api/configure/apply/recover": {"action": "rewind", "confirm": "missing"},
}


class DraftApplyRouteTests(StudioSecurityFixture):
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
        presence_support.serve_as_present(self)  # so a well-typed request reaches its lookup
        cookie, csrf = self._session()
        origin = self.record["url"].rstrip("/")
        trusted = {"Cookie": cookie, "Origin": origin, "X-Studio-CSRF": csrf}
        for path in ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self._post(path, VALID[path])[0], 401)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie, Origin=origin)[0], 403)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie,
                                            **{"X-Studio-CSRF": csrf})[0], 403)
                self.assertEqual(self._post(path, VALID[path], Cookie=cookie,
                                            Origin="https://example.invalid",
                                            **{"X-Studio-CSRF": csrf})[0], 403)
                status, body = self._post(path, MALFORMED[path], **trusted)
                self.assertEqual(status, 400, body)
                status, body = self._post(path, dict(VALID[path], extra=1), **trusted)
                self.assertEqual(status, 400, body)
                status, body = self._post(path, VALID[path], **trusted)
                self.assertEqual(status, 200, body)
                payload = json.loads(body)
                if path.endswith("/review"):
                    self.assertFalse(payload["can_apply"])
                    self.assertEqual(payload["refusals"][0]["code"], "not-found")
                elif path.endswith("/recover"):
                    self.assertEqual((payload["status"], payload["error_code"]),
                                     ("refused", "nothing-to-recover"))
                else:
                    self.assertEqual((payload["status"], payload["error_code"]), ("refused", "not-found"))
        status, body = self._post("/api/configure/apply",
                                  dict(VALID["/api/configure/apply"], confirm="something-else"), **trusted)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "confirmation_required")

    def test_a_confirmed_studio_apply_runs_the_cli_and_activity_names_the_studio(self):
        presence_support.serve_as_present(self)  # the person who confirmed at the Mac
        cookie, csrf = self._session()
        trusted = {"Cookie": cookie, "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": csrf}
        config_path = draft_apply.config_file(self.home)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        env = {key: value for key, value in self.env.items()
               if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        env.update(HOME=str(self.home), HARNESS_HOME=str(self.home),
                   HARNESS_WORKTREE_ROOT=str(self.home.parent / "worktrees"))
        name = draft_support.draft_name("apply-route-")
        created = subprocess.run([sys.executable, str(CLI), "draft", "create", name, "--json"],
                                 cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        draft_support.register_draft_cleanup(self, name, env)
        with mock.patch.dict(os.environ, env, clear=True):
            added = module_authoring.save(ROOT, name, json.loads(created.stdout)["revision"], "add", {
                "action": "add", "kind": "rules", "name": "greeting",
                "description": "Greets first.", "create_root": True})
        self.assertTrue(added["saved"], added)

        status, body = self._post("/api/configure/apply/review", {"draft": name}, **trusted)
        self.assertEqual(status, 200, body)
        review = json.loads(body)
        self.assertTrue(review["can_apply"], review["refusals"])
        status, body = self._post("/api/configure/apply", {
            "draft": name, "revision": review["draft"]["revision"], "confirm": name}, **trusted)
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertEqual(result["status"], "applied", result)
        self.assertTrue((draft_apply.destination(self.home) / "rules" / "greeting.md").is_file())

        status, body = self._post("/api/activity", {"limit": 5}, **trusted)
        self.assertEqual(status, 200, body)
        entry = next(item for item in json.loads(body)["entries"] if item["kind"] == "apply")
        self.assertEqual((entry["title"], entry["draft"], entry["actor"], entry["outcome"]),
                         ("Draft applied", name, "Studio", "completed"))
