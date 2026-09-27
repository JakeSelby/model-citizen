# SPDX-License-Identifier: MIT
"""Studio's launcher-only HTTP and filesystem boundary."""
from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "harness"
sys.path.insert(0, str(REPO / "lib"))

from harness_core.studio import auth  # noqa: E402
from harness_core.studio import server  # noqa: E402
from harness_core.studio import state_root  # noqa: E402
from harness_core.studio.state import Store  # noqa: E402


class StudioSecurityFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self.tmp.name)) / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home),
                        PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run(
            [sys.executable, str(CLI), "studio", "--detach", "--no-open", "--json"],
            env=self.env, capture_output=True, text=True, timeout=8)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.started = json.loads(done.stdout)
        self.record = json.loads((state_root(self.home) / "instance.json").read_text())

    def tearDown(self):
        subprocess.run([sys.executable, str(CLI), "studio", "stop", "--json"],
                       env=self.env, capture_output=True, text=True, timeout=5)
        self.tmp.cleanup()

    def request(self, method, path, headers=None, body=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=2)
        request_headers = {"Host": self.record["host"] if host is None else host}
        request_headers.update(headers or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, response.headers, payload
        connection.close()
        return result

    def issue_bootstrap(self):
        status, _headers, body = self.request(
            "POST", server.CONTROL_BOOTSTRAP,
            {"Authorization": "Bearer " + self.record["control_credential"]})
        self.assertEqual(status, 200)
        return json.loads(body)

    def bootstrap(self, origin=None, cookie=None):
        issued = self.issue_bootstrap()
        form = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Content-Length": str(len(form))}
        if origin is not None:
            headers["Origin"] = origin
        if cookie is not None:
            headers["Cookie"] = cookie
        status, response_headers, body = self.request("POST", server.BOOTSTRAP, headers, form)
        return issued, status, response_headers, body

    @staticmethod
    def cookie(headers):
        return headers["Set-Cookie"].split(";", 1)[0]


class HttpBoundaryTests(StudioSecurityFixture):
    def test_server_uses_a_unique_localhost_origin_while_binding_loopback(self):
        self.assertRegex(self.record["host"], r"^[0-9a-f]{32}\.localhost:\d+$")
        self.assertEqual(self.record["url"], "http://" + self.record["host"] + "/")
        probe = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=2)
        probe.request("GET", "/api/session", headers={"Host": self.record["host"]})
        self.assertEqual(probe.getresponse().status, 401)
        probe.close()

    def test_anonymous_api_and_static_requests_are_unauthorized_without_redirects(self):
        for path in ("/api/session", "/api/not-registered", "/"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertEqual(status, 401)
                self.assertIsNone(headers.get("Location"))
                self.assertEqual(json.loads(body), {"error": "unauthorized"})

    def test_foreign_host_is_refused_before_auth_and_leaks_no_host(self):
        status, _headers, body = self.request("GET", "/api/session", host="example.invalid")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "request_refused"})
        self.assertNotIn(b"example.invalid", body)
        self.assertNotIn(self.record["host"].encode(), body)

    def test_one_shot_bootstrap_sets_a_host_only_cookie_and_deletes_its_form(self):
        issued = self.issue_bootstrap()
        path = auth.write_launcher_form(state_root(self.home), issued["form_name"],
                                        self.record["url"].rstrip("/") + server.BOOTSTRAP,
                                        issued["token"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(issued["token"], self.record["url"])
        form = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Content-Length": str(len(form)), "Origin": "null"}
        status, response_headers, body = self.request("POST", server.BOOTSTRAP, headers, form)
        self.assertEqual(status, 200)
        self.assertIn(b'<meta http-equiv="refresh" content="0; url=/">', body)
        self.assertIsNone(response_headers.get("Location"))
        cookie = response_headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertFalse(path.exists())
        app_status, _app_headers, app_body = self.request(
            "GET", "/", {"Cookie": self.cookie(response_headers)})
        self.assertEqual(app_status, 200)
        self.assertIn(b"Model Citizen Studio", app_body)
        replay, _headers, replay_body = self.request("POST", server.BOOTSTRAP, headers, form)
        self.assertEqual(replay, 401)
        self.assertNotIn(issued["token"].encode(), replay_body)

    def test_bootstrap_refuses_cookie_foreign_origin_media_type_and_oversized_body(self):
        cases = (
            ({"Cookie": "unexpected=value"}, 403),
            ({"Origin": "https://example.invalid"}, 403),
            ({"Content-Type": "application/json"}, 415),
        )
        for additions, expected in cases:
            with self.subTest(expected=expected, additions=additions):
                issued = self.issue_bootstrap()
                body = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
                headers = {"Content-Type": "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
                headers.update(additions)
                status, _response_headers, _payload = self.request(
                    "POST", server.BOOTSTRAP, headers, body)
                self.assertEqual(status, expected)
        status, _headers, _body = self.request(
            "POST", server.BOOTSTRAP,
            {"Content-Type": "application/x-www-form-urlencoded",
             "Content-Length": str(auth.MAX_FORM_BYTES + 1)}, b"")
        self.assertEqual(status, 413)

    def test_session_csrf_origin_json_limits_and_security_headers(self):
        _issued, status, bootstrap_headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        cookie = self.cookie(bootstrap_headers)
        status, headers, body = self.request("GET", "/api/session", {"Cookie": cookie})
        self.assertEqual(status, 200)
        session = json.loads(body)
        self.assertTrue(session["authenticated"])
        for name, value in auth.SECURITY_HEADERS:
            self.assertEqual(headers[name], value)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

        body = b"{}"
        base = {"Cookie": cookie, "Content-Type": "application/json",
                "Content-Length": str(len(body))}
        for additions in ({}, {"Origin": "https://example.invalid"},
                          {"Origin": self.record["url"].rstrip("/")}):
            request_headers = dict(base)
            request_headers.update(additions)
            refused, _headers, payload = self.request(
                "POST", "/api/session", request_headers, body)
            self.assertEqual(refused, 403)
            self.assertEqual(json.loads(payload), {"error": "request_refused"})

        accepted = dict(base, Origin=self.record["url"].rstrip("/"),
                        **{"X-Studio-CSRF": session["csrf_token"]})
        ok, _headers, payload = self.request("POST", "/api/session", accepted, body)
        self.assertEqual(ok, 200)
        self.assertTrue(json.loads(payload)["authenticated"])

        wrong_media = dict(accepted, **{"Content-Type": "text/plain"})
        refused, _headers, _payload = self.request(
            "POST", "/api/session", wrong_media, body)
        self.assertEqual(refused, 415)
        oversized = dict(accepted, **{"Content-Length": str(auth.MAX_JSON_BYTES + 1)})
        refused, _headers, _payload = self.request("POST", "/api/session", oversized, b"")
        self.assertEqual(refused, 413)

    def test_control_routes_require_their_distinct_bearer_credential(self):
        refused_bearer = "Bearer " + "not-the-control-" + "credential"
        for supplied in (None, refused_bearer):
            headers = {} if supplied is None else {"Authorization": supplied}
            status, _response_headers, body = self.request(
                "POST", server.CONTROL_BOOTSTRAP, headers)
            self.assertEqual(status, 401)
            self.assertNotIn(self.record["control_credential"].encode(), body)


class SerializationAndFileBoundaryTests(unittest.TestCase):
    def test_secret_values_are_redacted_but_reference_names_are_displayable(self):
        marker = "not-a-real-sensitive-value"
        config = {"provider": {"api_key": marker,
                               "token": {"source": "keychain", "name": "studio-provider"}},
                  "ordinary": "visible"}
        public = auth.public_data(config)
        self.assertEqual(public["provider"]["api_key"], {"configured": True})
        self.assertEqual(public["provider"]["token"],
                         {"source": "keychain", "name": "studio-provider"})
        self.assertEqual(public["ordinary"], "visible")
        self.assertNotIn(marker, json.dumps(public))

    def test_camel_case_and_malformed_nested_secret_references_fail_closed(self):
        markers = {
            "accessToken": "access-value",
            "clientSecret": "client-value",
            "apiKey": "api-value",
            "passphrase": "phrase-value",
            "refresh_token": {"source": "keychain", "name": {"nested": "nested-value"}},
            "privateKey": {"source": "keychain", "name": "bad name", "value": "key-value"},
        }
        public = auth.public_data(markers)
        rendered = json.dumps(public, sort_keys=True)
        for secret in ("access-value", "client-value", "api-value", "phrase-value",
                       "nested-value", "bad name", "key-value"):
            self.assertNotIn(secret, rendered)
        for key in markers:
            self.assertEqual(public[key], {"configured": True})

    def test_known_roots_are_named_and_no_symlink_or_parent_escape_is_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(os.path.realpath(temporary))
            checkout = base / "checkout"
            config = base / "config"
            state = base / "state"
            draft = base / "draft"
            primitive = base / "primitive"
            for root in (checkout, config, state, draft, primitive):
                root.mkdir()
            (checkout / "safe.txt").write_text("safe")
            outside = base / "outside.txt"
            outside.write_text("outside-marker")
            (checkout / "escape.txt").symlink_to(outside)
            with auth.KnownRoots({"checkout": checkout, "config": config, "state": state,
                                  "draft:one": draft, "primitive:one": primitive}) as roots:
                self.assertEqual(roots.names,
                                 ("checkout", "config", "draft:one", "primitive:one", "state"))
                self.assertEqual(roots.read_bytes("checkout", "safe.txt"), b"safe")
                roots.replace_bytes("config", "settings.json", b'{}\n')
                self.assertEqual(roots.read_bytes("config", "settings.json"), b'{}\n')
                roots.delete("config", "settings.json")
                self.assertFalse((config / "settings.json").exists())
                for root, relative in (("checkout", "../outside.txt"),
                                       ("checkout", "folder//file.txt"),
                                       ("checkout", "escape.txt"),
                                       ("unknown", "safe.txt")):
                    with self.subTest(root=root, relative=relative):
                        with self.assertRaisesRegex(auth.SecurityError, "file request refused"):
                            roots.read_bytes(root, relative)
                for operation in (lambda: roots.replace_bytes("checkout", "escape.txt", b"no"),
                                  lambda: roots.delete("checkout", "escape.txt")):
                    with self.assertRaisesRegex(auth.SecurityError, "file request refused"):
                        operation()
                self.assertEqual(outside.read_text(), "outside-marker")

    def test_a_symlinked_root_is_refused_without_disclosing_its_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(os.path.realpath(temporary))
            target = base / "target"
            target.mkdir()
            link = base / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(auth.SecurityError) as caught:
                auth.KnownRoots({"checkout": link})
            self.assertEqual(str(caught.exception), "file root refused")
            self.assertNotIn(str(link), str(caught.exception))

    def test_post_open_identity_change_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(os.path.realpath(temporary))
            (root / "index.html").write_text("safe")
            with auth.KnownRoots({"static": root}) as roots, \
                    mock.patch.object(auth.os, "stat") as checked:
                checked.return_value = mock.Mock(st_mode=stat.S_IFREG | 0o600,
                                                 st_dev=-1, st_ino=-1)
                with self.assertRaisesRegex(auth.SecurityError, "file request refused"):
                    roots.read_bytes("static", "index.html")


class StaticServerBoundaryTests(unittest.TestCase):
    def test_static_index_symlink_never_escapes_the_retained_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(os.path.realpath(temporary))
            static_root = base / "static"
            state = base / "state"
            static_root.mkdir()
            outside = base / "outside.html"
            outside.write_text("<!doctype html><title>outside-marker</title>")
            (static_root / "index.html").symlink_to(outside)
            with Store(state) as store:
                instance = server.Server(("127.0.0.1", 0), static_root, "control", store)
                token, _form_name = instance.sessions.issue()
                established = instance.sessions.consume(token)
                assert established is not None
                session, _form_name = established
                thread = threading.Thread(target=instance.serve_forever, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", instance.server_address[1], timeout=2)
                    connection.request("GET", "/", headers={
                        "Host": instance.host,
                        "Cookie": auth.SESSION_COOKIE + "=" + session.cookie,
                    })
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 503)
                    self.assertNotIn(b"outside-marker", body)
                    connection.close()
                finally:
                    instance.shutdown()
                    thread.join(timeout=2)
                    instance.server_close()


if __name__ == "__main__":
    unittest.main()
