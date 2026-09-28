# SPDX-License-Identifier: MIT
"""Rendered Studio shell behavior in a real browser."""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "harness"
GIT_EMAIL = "studio-qualification" + "@" + "example.invalid"
sys.path.insert(0, str(REPO / "lib"))

from harness_core.studio import auth, state_root  # noqa: E402


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        if len(channels) != 3:
            raise ValueError("expected an opaque rgb color, got " + color)
        linear = [value / 12.92 if value <= 0.04045
                  else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _chrome() -> Optional[str]:
    for candidate in (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


class DevTools:
    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (parsed.path, parsed.hostname, parsed.port, key)
        )
        self.socket.sendall(request.encode("ascii"))
        response = self._until(b"\r\n\r\n")
        if not response.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("Chrome refused the DevTools websocket")
        self.next_id = 1

    def close(self):
        self.socket.close()

    def _until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            part = self.socket.recv(4096)
            if not part:
                raise RuntimeError("Chrome closed the DevTools websocket")
            data += part
        return data

    def _receive(self) -> dict:
        first = self._exact(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._exact(8))[0]
        payload = self._exact(length)
        if opcode == 9:
            self._send(payload, opcode=10)
            return self._receive()
        if opcode != 1:
            return self._receive()
        return json.loads(payload)

    def _exact(self, length: int) -> bytes:
        data = b""
        while len(data) < length:
            part = self.socket.recv(length - len(data))
            if not part:
                raise RuntimeError("Chrome closed the DevTools websocket")
            data += part
        return data

    def _send(self, payload: bytes, opcode: int = 1) -> None:
        mask = secrets.token_bytes(4)
        length = len(payload)
        header = bytes((0x80 | opcode, 0x80 | length)) if length < 126 else (
            bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        )
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(header + mask + masked)

    def call(self, method: str, params=None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        payload = json.dumps({"id": request_id, "method": method, "params": params or {}})
        self._send(payload.encode("utf-8"))
        while True:
            response = self._receive()
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["result"]

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")


class StudioBrowserTests(unittest.TestCase):
    def setUp(self):
        chrome = _chrome()
        if chrome is None:
            self.skipTest("Chrome or Chromium is required for rendered Studio checks")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(os.path.realpath(self.temporary.name)) / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home),
                        PYTHONDONTWRITEBYTECODE="1",
                        GIT_AUTHOR_NAME="Studio Qualification",
                        GIT_AUTHOR_EMAIL=GIT_EMAIL,
                        GIT_COMMITTER_NAME="Studio Qualification",
                        GIT_COMMITTER_EMAIL=GIT_EMAIL)
        self.addCleanup(self._stop_studio)
        launched = subprocess.run(
            [sys.executable, str(CLI), "studio", "--detach", "--no-open", "--json"],
            env=self.env, capture_output=True, text=True, timeout=45)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.started = json.loads(launched.stdout)
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        self.cookie = self._bootstrap(record)
        self.profile = Path(self.temporary.name) / "chrome"
        self.browser = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
             "--disable-backgrounding-occluded-windows",
             "--remote-debugging-port=0", "--user-data-dir=" + str(self.profile), "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop_browser)
        active = self.profile / "DevToolsActivePort"
        active_lines = []
        for _ in range(400):
            try:
                active_lines = active.read_text().splitlines()
                if active_lines and int(active_lines[0]) > 0:
                    break
            except (OSError, ValueError):
                active_lines = []
            time.sleep(0.05)
        self.assertTrue(active_lines, "Chrome did not open its DevTools endpoint")
        port = int(active_lines[0])
        request = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?about:blank" % port, method="PUT")
        target = None
        for _ in range(20):
            try:
                with urllib.request.urlopen(request, timeout=0.5) as response:
                    target = json.load(response)
                break
            except (OSError, urllib.error.URLError, ValueError):
                time.sleep(0.1)
        self.assertIsNotNone(target, "Chrome did not accept a DevTools target")
        assert target is not None
        self.devtools = DevTools(target["webSocketDebuggerUrl"])
        self.addCleanup(self._close_devtools)

    def _close_devtools(self):
        if hasattr(self, "devtools"):
            try:
                self.devtools.close()
            except OSError:
                pass

    def _stop_browser(self):
        if hasattr(self, "browser"):
            if self.browser.poll() is None:
                self.browser.terminate()
                try:
                    self.browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.browser.kill()
                    self.browser.wait()

    def _stop_studio(self):
        if hasattr(self, "env"):
            subprocess.run([sys.executable, str(CLI), "studio", "stop", "--json"],
                           env=self.env, capture_output=True, text=True, timeout=5)

    def _bootstrap(self, record: dict) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=2)
        connection.request("POST", "/__studio/control/bootstrap", headers={
            "Host": record["host"],
            "Authorization": "Bearer " + record["control_credential"],
        })
        issued = json.loads(connection.getresponse().read())
        form = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
        connection.request("POST", "/__studio/bootstrap", body=form, headers={
            "Host": record["host"],
            "Origin": "null",
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(form)),
        })
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        connection.close()
        return cookie

    def _wait_for_shell(self):
        for _ in range(100):
            if self.devtools.evaluate("document.querySelector('.studio-frame') !== null"):
                return
            time.sleep(0.05)
        state = self.devtools.evaluate(
            "JSON.stringify({href:location.href,body:document.body?.innerHTML,title:document.title})"
        )
        self.fail("Studio shell did not render: " + str(state))

    def test_shell_routes_focuses_and_styles_under_the_production_csp(self):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": (
            "globalThis.__studioCspViolations=[];"
            "document.addEventListener('securitypolicyviolation',event=>"
            "globalThis.__studioCspViolations.push(event.violatedDirective));"
        )})
        self.devtools.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "dark"},
        ]})
        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/not-a-route"})
        self._wait_for_shell()
        for _ in range(100):
            if self.devtools.evaluate("location.hash === '#/'"):
                break
            time.sleep(0.05)

        before = self.devtools.evaluate("location.hash")
        self.devtools.evaluate("document.querySelector('.skip-link').click()")
        result = self.devtools.evaluate("JSON.stringify({"
            "hash:location.hash,title:document.title,"
            "heading:document.querySelector('h1')?.textContent,"
            "active:document.querySelector('.nav-link[aria-current=page]')?.textContent,"
            "navLabel:document.querySelector('nav')?.getAttribute('aria-label'),"
            "focused:document.activeElement?.id,"
            "scheme:document.documentElement.dataset.mantineColorScheme,"
            "canvas:getComputedStyle(document.body).backgroundColor,"
            "buttonForeground:getComputedStyle(document.querySelector('.page-heading .mantine-Button-root')).color,"
            "buttonBackground:getComputedStyle(document.querySelector('.page-heading .mantine-Button-root')).backgroundColor,"
            "direction:getComputedStyle(document.querySelector('.header-inner')).flexDirection,"
            "stylePadding:getComputedStyle(document.querySelector('.overview-card')).paddingTop,"
            "styleAttribute:document.querySelector('.overview-card').getAttribute('style'),"
            "nonce:document.querySelector('style[data-mantine-styles]')?.nonce,"
            "declaredNonce:document.querySelector('meta[name=studio-style-nonce]')?.content,"
            "violations:globalThis.__studioCspViolations ?? ['listener-missing']"
            "})")
        rendered = json.loads(result)
        self.assertEqual(before, "#/")
        self.assertEqual(rendered["hash"], "#/")
        self.assertEqual(rendered["title"], "Hub · Model Citizen Studio")
        self.assertEqual(rendered["heading"], "Your harness at a glance.")
        self.assertEqual(rendered["active"], "Hub")
        self.assertEqual(rendered["navLabel"], "Studio")
        self.assertEqual(rendered["focused"], "main-content")
        self.assertEqual(rendered["scheme"], "dark")
        self.assertEqual(rendered["canvas"], "rgb(16, 27, 30)")
        self.assertGreaterEqual(
            _contrast_ratio(rendered["buttonForeground"], rendered["buttonBackground"]), 4.5)
        self.assertEqual(rendered["direction"], "column")
        self.assertEqual(rendered["stylePadding"], "32px")
        self.assertIn("padding-block:", rendered["styleAttribute"])
        self.assertEqual(rendered["nonce"], rendered["declaredNonce"])
        self.assertEqual(rendered["violations"], [])

        self.devtools.call("Emulation.setEmulatedMedia", {"features": [
            {"name": "prefers-color-scheme", "value": "light"},
        ]})
        for _ in range(100):
            scheme = self.devtools.evaluate(
                "document.documentElement.dataset.mantineColorScheme"
            )
            if scheme == "light":
                break
            time.sleep(0.05)
        light = json.loads(self.devtools.evaluate("JSON.stringify({"
            "canvas:getComputedStyle(document.body).backgroundColor,"
            "foreground:getComputedStyle(document.querySelector('.page-heading .mantine-Button-root')).color,"
            "background:getComputedStyle(document.querySelector('.page-heading .mantine-Button-root')).backgroundColor"
            "})"))
        self.assertEqual(scheme, "light")
        self.assertEqual(light["canvas"], "rgb(245, 248, 248)")
        self.assertGreaterEqual(_contrast_ratio(light["foreground"], light["background"]), 4.5)

        self.devtools.evaluate("location.hash = '#/reports'")
        for _ in range(100):
            active = self.devtools.evaluate(
                "document.querySelector('.nav-link[aria-current=page]')?.textContent"
            )
            if active == "Reports":
                break
            time.sleep(0.05)
        self.assertEqual(active, "Reports")
        self.assertEqual(self.devtools.evaluate("document.querySelector('h1')?.textContent"),
                         "Reports")


if __name__ == "__main__":
    unittest.main()
