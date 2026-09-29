# SPDX-License-Identifier: MIT
"""How the Studio's loopback boundary treats the request shapes of phone-access transports.

A byte tunnel such as an SSH local forward delivers the browser's own Host and Origin headers
unchanged; a reverse proxy such as Tailscale Serve delivers the public name the phone typed.
The decision these tests pin is recorded in docs/spikes/2026-09-29-studio-phone-access.md.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import unittest
import urllib.parse

from test_studio_security import StudioSecurityFixture, server

PROXY_HOST = "studio-mac.example-tailnet.ts.net"


class ByteTunnel:
    """A loopback listener that relays bytes to the Studio port untouched, as ssh -L does."""

    def __init__(self, target_port):
        self.target_port = target_port
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()

    def _accept(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=15)
            except OSError:
                client.close()
                continue
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()

    @staticmethod
    def _pipe(source, sink):
        try:
            while True:
                chunk = source.recv(65536)
                if not chunk:
                    break
                sink.sendall(chunk)
        except OSError:
            pass
        finally:
            for end in (source, sink):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                end.close()

    def close(self):
        # close() alone does not wake a thread blocked in accept() on macOS.
        try:
            self.listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.listener.close()
        self.acceptor.join(timeout=5)


class PhoneTransportFixture(StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        self.tunnel = ByteTunnel(self.started["port"])
        self.addCleanup(self.tunnel.close)
        self.origin = "http://" + self.record["host"]

    def via(self, port, method, path, headers, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.headers, response.read()
        connection.close()
        return result

    def form(self, token):
        body = urllib.parse.urlencode({"token": token}).encode("ascii")
        return body, {"Content-Type": "application/x-www-form-urlencoded",
                      "Content-Length": str(len(body))}

    def tunnel_session(self, host):
        issued = self.issue_bootstrap()
        body, headers = self.form(issued["token"])
        headers["Host"] = host
        status, response_headers, _ = self.via(self.tunnel.port, "POST", server.BOOTSTRAP,
                                               headers, body)
        return status, response_headers


class ByteTunnelTests(PhoneTransportFixture):
    def test_tunnel_keeping_the_studio_port_passes_every_check_and_applies_a_change(self):
        status, headers = self.tunnel_session(self.record["host"])
        self.assertEqual(status, 200)
        cookie = self.cookie(headers)
        base = {"Host": self.record["host"], "Cookie": cookie}
        status, _, body = self.via(self.tunnel.port, "GET", "/api/session", base)
        self.assertEqual(status, 200)
        csrf = json.loads(body)["csrf_token"]
        before = json.loads(self.via(self.tunnel.port, "GET", "/api/ui/preferences", base)[2])
        self.assertNotEqual(before["color_scheme"], "dark")
        change = json.dumps({"color_scheme": "dark"}).encode()
        write = dict(base, **{"Content-Type": "application/json",
                              "Content-Length": str(len(change))})
        self.assertEqual(self.via(self.tunnel.port, "POST", "/api/ui/preferences",
                                  dict(write, Origin=self.origin,
                                       **{"X-Studio-CSRF": csrf}), change)[0], 200)
        after = json.loads(self.via(self.tunnel.port, "GET", "/api/ui/preferences", base)[2])
        self.assertEqual(after["color_scheme"], "dark")
        self.assertEqual(self.via(self.tunnel.port, "POST", "/api/ui/preferences",
                                  dict(write, Origin=self.origin), change)[0], 403)
        self.assertEqual(self.via(self.tunnel.port, "POST", "/api/ui/preferences",
                                  dict(write, Origin="https://" + PROXY_HOST,
                                       **{"X-Studio-CSRF": csrf}), change)[0], 403)

    def test_tunnel_on_another_local_port_is_refused_before_bootstrap(self):
        host = self.record["host"].rsplit(":", 1)[0] + ":%d" % self.tunnel.port
        status, headers = self.tunnel_session(host)
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Set-Cookie"))


class ReverseProxyTests(PhoneTransportFixture):
    def proxy_headers(self, host):
        return {"Host": host, "X-Forwarded-Host": PROXY_HOST, "X-Forwarded-Proto": "https",
                "Tailscale-User-Login": "phone-user",
                "Origin": "https://" + PROXY_HOST}

    def test_serve_shaped_requests_are_refused_and_leave_the_token_unspent(self):
        _, headers = self.tunnel_session(self.record["host"])
        cookie = self.cookie(headers)
        for host in (PROXY_HOST, "127.0.0.1:%d" % self.started["port"]):
            with self.subTest(host=host):
                forwarded = dict(self.proxy_headers(host), Cookie=cookie)
                self.assertEqual(self.via(self.started["port"], "GET", "/api/session",
                                          forwarded)[0], 403)
        issued = self.issue_bootstrap()
        body, form_headers = self.form(issued["token"])
        refused = dict(self.proxy_headers(PROXY_HOST), **form_headers)
        del refused["Origin"]
        self.assertEqual(self.via(self.started["port"], "POST", server.BOOTSTRAP,
                                  refused, body)[0], 403)
        form_headers["Host"] = self.record["host"]
        self.assertEqual(self.via(self.started["port"], "POST", server.BOOTSTRAP,
                                  form_headers, body)[0], 200)


class ListenerScopeTests(PhoneTransportFixture):
    def test_studio_port_is_unreachable_on_a_routable_address(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
        except OSError:
            address = None
        finally:
            probe.close()
        if not address or address.startswith("127."):
            self.skipTest("no routable IPv4 address on this machine")
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection((address, self.started["port"]), timeout=2).close()
        status, _, _ = self.via(self.started["port"], "GET", "/api/session",
                                {"Host": self.record["host"]})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
