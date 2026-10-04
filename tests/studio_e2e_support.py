# SPDX-License-Identifier: MIT
"""A Studio served from a fixture checkout to a fixture home, driven in headless Chrome.

The end-to-end flows and the performance budgets share it. Three things keep a run sealed:

- Each test class serves a full-history clone of this checkout's commit (uncommitted edits are
  not in it), so its drafts, including the first-run guide's fixed ``first-run`` draft, live in
  the clone's refs and never in the shared repository a sibling suite or the developer works in.
- ``claude`` and ``codex`` on the Studio's PATH are stubs that let the offline probes through
  and record and fail any other call, so :meth:`StudioE2E.assert_no_model_calls` proves no flow
  reached a model client.
- :meth:`StudioE2E.assert_no_network` reads the DevTools ``Network`` events of every document
  the tab loaded, WebSockets included, and refuses any request that left the Studio's loopback
  origin. It sees the browser only: the Studio's own subprocesses (lint, the hook matrix, git)
  are not watched for network use.

These classes are opt-in: they skip unless ``STUDIO_E2E=1``, which only the ``studio-e2e``
workflow sets, so the plain ``discover -s tests`` runs in ``ci.yml`` and ``release.yml`` neither
repeat the flows nor gate on wall-clock budgets. With the flag set, a missing Chrome fails the
test instead of skipping it, so a runner image without Chrome cannot pass the job.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import isolation  # noqa: F401 -- isolated suite home and quiet git maintenance
import draft_support
import test_studio_browser as browser_support
from test_replay_pack import make_pack

REPO = browser_support.REPO
OPT_IN_ENV = "STUDIO_E2E"
# Requests that never leave the browser.
LOCAL_SCHEMES = ("data:", "blob:")
MODEL_CLIENTS = ("claude", "codex")

sys.path.insert(0, str(REPO / "lib"))
from harness_core.studio import state_root  # noqa: E402

# Injected before every page: label- and text-addressed controls, as a user finds them.
PAGE_HELPERS = """
globalThis.__labelled = (text) => {
  const label = [...document.querySelectorAll('label')]
    .find(item => item.textContent.trim().startsWith(text));
  const input = label && (document.getElementById(label.htmlFor)
    || label.closest('div').querySelector('input, textarea'));
  if (!input) throw new Error('missing control for ' + text);
  return input;
};
globalThis.__setLabelValue = (text, value) => {
  const input = __labelled(text);
  const prototype = input instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
};
globalThis.__button = (text) => [...document.querySelectorAll('button')]
  .find(item => item.textContent.trim() === text);
globalThis.__buttonReady = (text) => {
  const button = __button(text);
  return Boolean(button && !button.disabled && button.getAttribute('aria-disabled') !== 'true'
    && !button.dataset.loading);
};
globalThis.__click = (text) => {
  const button = __button(text);
  if (!button) throw new Error('missing button ' + text);
  button.click();
  return true;
};
globalThis.__has = (text) => document.body.textContent.includes(text);
"""


def opted_in() -> bool:
    return os.environ.get(OPT_IN_ENV) == "1"


def chrome_or_fail(test: unittest.TestCase) -> str:
    chrome = browser_support._chrome()
    if chrome is None:
        test.fail("%s=1 and no Chrome or Chromium was found" % OPT_IN_ENV)
    assert chrome is not None
    return chrome


class RecordingDevTools(browser_support.DevTools):
    """The browser tests' DevTools client, keeping every event it reads instead of dropping it."""

    def __init__(self, url: str):
        super().__init__(url)
        self.events: List[dict] = []

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
            if "method" in response:
                self.events.append(response)

    def requested_urls(self) -> List[str]:
        """Every URL a document or WebSocket of this tab asked for, read up to now."""
        self.call("Runtime.evaluate", {"expression": "0"})  # drains events queued before it
        urls = []
        for event in self.events:
            if event["method"] == "Network.requestWillBeSent":
                urls.append(event["params"]["request"]["url"])
            elif event["method"] == "Network.webSocketCreated":
                urls.append(event["params"]["url"])
        return urls


def clone_checkout(destination: Path) -> Path:
    """A clone of this checkout's commit, with its history: lint reads released commits."""
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    for argv in (["git", "clone", "-q", "--no-checkout", str(REPO), str(destination)],
                 ["git", "-C", str(destination), "checkout", "-q", "--detach", head]):
        subprocess.run(argv, check=True, capture_output=True, text=True,
                       env=isolation.quiet_git_maintenance(dict(os.environ)))
    return destination


class FixtureCheckout:
    """A full-history clone of this checkout's commit, an evaluator pack beside it and a stub
    bin directory, shared by one test class."""

    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-e2e-")
        base = Path(os.path.realpath(self.temporary.name))
        self.root = clone_checkout(base / "checkout")
        self.cli = self.root / "bin" / "harness"
        # This repository lists no replay tasks of its own; a draft test plans over the tasks of
        # an evaluator pack found beside the checkout, as a user's pack would be.
        self.pack = make_pack(base / "fixture-pack")
        self.stubs = base / "stubs"
        self.stubs.mkdir()
        self.calls = base / "model-calls.log"
        for client in MODEL_CLIENTS:
            stub = self.stubs / client
            # Offline probes start no model turn: a version probe (doctor, the overview) is
            # answered, Codex's config probes (`codex_client`) go unanswered, and anything
            # else is recorded as a model call.
            stub.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  --version) echo '%s 0.0.0-fixture'; exit 0 ;;\n"
                "  'app-server generate-json-schema'*|'app-server --listen off'*) exit 1 ;;\n"
                "esac\n"
                "echo \"%s $*\" >> '%s'\nexit 97\n" % (client, client, self.calls),
                encoding="utf-8")
            stub.chmod(0o755)

    def cleanup(self):
        self.temporary.cleanup()


def write_library(root: Path, count: int) -> Path:
    """A personal primitive root of ``count`` small rule modules, for the library budget."""
    rules = root / "rules"
    rules.mkdir(parents=True)
    for index in range(count):
        (rules / ("fixture-rule-%03d.md" % index)).write_text(
            "# Fixture rule %03d\n\n- **Keep fixture rule %03d short.** It exists to size the "
            "library.\n" % (index, index), encoding="utf-8")
    return root


class StudioE2E(unittest.TestCase):
    """One fixture home, one detached Studio from the class's fixture checkout, one Chrome.

    A subclass may override :meth:`prepare_home` to seed the home before the Studio starts.
    """

    checkout: FixtureCheckout

    @classmethod
    def setUpClass(cls):
        if not opted_in():
            raise unittest.SkipTest("set %s=1 to run the Studio end-to-end checks" % OPT_IN_ENV)
        super().setUpClass()
        cls.checkout = FixtureCheckout()

    @classmethod
    def tearDownClass(cls):
        cls.checkout.cleanup()
        super().tearDownClass()

    # Borrowed from the browser harness: bootstrap, shell wait, browser and socket teardown.
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser

    def prepare_home(self) -> None:
        """Seed ``self.home`` before the Studio starts; the default seeds nothing."""

    def setUp(self):
        chrome = chrome_or_fail(self)
        if self.checkout.calls.exists():
            self.checkout.calls.unlink()
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-e2e-home-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(os.path.realpath(self.temporary.name))
        self.home = base / "home"
        self.config_path = self.home / ".config" / "agent-harness" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        config = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        self.config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        # HARNESS_QUIET goes too: the flows read the CLI's --json output.
        env = isolation.without_harness_vars(dict(os.environ))
        env.update({
            "HOME": str(self.home), "HARNESS_HOME": str(self.home),
            "HARNESS_WORKTREE_ROOT": str(base / "worktrees"),
            "PATH": str(self.checkout.stubs) + os.pathsep + env.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_AUTHOR_NAME": "Studio E2E", "GIT_COMMITTER_NAME": "Studio E2E",
            "GIT_AUTHOR_EMAIL": browser_support.GIT_EMAIL,
            "GIT_COMMITTER_EMAIL": browser_support.GIT_EMAIL,
        })
        isolation.quiet_git_maintenance(env)
        self.env = env
        self.prepare_home()
        self.addCleanup(self._stop_studio)
        launched = self.cli("studio", "--detach", "--no-open", "--json", timeout=60)
        self.assertEqual(launched.returncode, 0, launched.stderr or launched.stdout)
        self.started = json.loads(launched.stdout)
        record = json.loads((state_root(self.home) / "instance.json").read_text())
        self.record = record
        self.cookie = self._bootstrap(record)
        self.profile = base / "chrome"
        # --use-mock-keychain: the isolated home has no login keychain, and macOS Chrome stalls
        # on keychain access without it (see test_studio_browser).
        self.browser = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--use-mock-keychain",
             "--no-first-run", "--disable-background-networking",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
             "--disable-backgrounding-occluded-windows",
             "--remote-debugging-port=0", "--user-data-dir=" + str(self.profile), "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop_browser)
        self.devtools = RecordingDevTools(self._target())
        self.addCleanup(self._close_devtools)
        # Recording starts before the first navigation, so every document of the tab is seen.
        self.devtools.call("Network.enable")

    def _target(self) -> str:
        active = self.profile / "DevToolsActivePort"
        lines: List[str] = []
        for _ in range(600):
            try:
                lines = active.read_text().splitlines()
                if lines and int(lines[0]) > 0:
                    break
            except (OSError, ValueError):
                lines = []
            time.sleep(0.05)
        self.assertTrue(lines, "Chrome did not open its DevTools endpoint")
        request = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?about:blank" % int(lines[0]), method="PUT")
        for _ in range(40):
            try:
                with urllib.request.urlopen(request, timeout=0.5) as response:
                    return json.load(response)["webSocketDebuggerUrl"]
            except (OSError, urllib.error.URLError, ValueError):
                time.sleep(0.1)
        self.fail("Chrome did not accept a DevTools target")

    def _stop_studio(self):
        self.cli("studio", "stop", "--json", timeout=15)

    def cli(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.checkout.cli), *args],
                              cwd=str(self.checkout.root), env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def cli_json(self, *args: str, timeout: int = 120) -> Dict[str, Any]:
        done = self.cli(*args, "--json", timeout=timeout)
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        return json.loads(done.stdout)

    def create_draft(self, prefix: str) -> str:
        """A draft in the fixture checkout, discarded (and checked gone) after the test."""
        name = draft_support.draft_name(prefix)
        self.cli_json("draft", "create", name, timeout=60)
        self.discard_after(name)
        return name

    def discard_after(self, name: str) -> None:
        self.addCleanup(draft_support.discard_draft, self, name, self.env, self._stop_studio,
                        repo=self.checkout.root, missing_ok=True, cli=self.checkout.cli)

    # Browser driving.
    def open(self, route: str = "") -> None:
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": PAGE_HELPERS})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + route})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()

    def wait(self, expression: str, message: str, seconds: float = 30.0,
             section: Optional[str] = None) -> Any:
        """Poll ``expression`` until truthy; on timeout fail with the text of the ``section``
        whose heading starts with that string, or the end of the page."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.devtools.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        shown = ("[...document.querySelectorAll('h2')].find(h => h.textContent.startsWith(%s))"
                 "?.closest('.mantine-Paper-root, .mantine-Stack-root')?.innerText"
                 % json.dumps(section)) if section else "''"
        text = self.devtools.evaluate(shown) or self.devtools.evaluate(
            "document.body ? document.body.innerText : ''")
        self.fail("%s; %s: %s" % (message, section or "page ends", str(text)[-2500:]))

    def js(self, expression: str) -> Any:
        return self.devtools.evaluate(expression)

    def click(self, text: str, seconds: float = 30.0) -> None:
        self.wait("__buttonReady(%s)" % json.dumps(text), "button %r never became ready" % text,
                  seconds)
        self.js("__click(%s)" % json.dumps(text))

    def options(self, label: str) -> List[str]:
        """Open the Mantine combobox labelled ``label`` and list its own options."""
        # A click toggles the dropdown, so only a closed one is clicked open.
        self.js("(input => input.getAttribute('aria-expanded') === 'true' || input.click())"
                "(__labelled(%s))" % json.dumps(label))
        listbox = ("document.getElementById(__labelled(%s).getAttribute('aria-controls'))"
                   % json.dumps(label))
        self.wait("(%s)?.querySelector('[role=option]') != null" % listbox,
                  "%s listed no options" % label)
        return self.js("[...%s.querySelectorAll('[role=option]')].map(item => item.textContent.trim())"
                       % listbox)

    def choose_option(self, label: str, option: str) -> None:
        """Pick ``option`` in the Mantine combobox labelled ``label``, as a pointer user does."""
        self.assertIn(option, self.options(label))
        self.js("[...document.getElementById(__labelled(%s).getAttribute('aria-controls'))"
                ".querySelectorAll('[role=option]')].find(item => item.textContent.trim() === %s)"
                ".click()" % (json.dumps(label), json.dumps(option)))

    # Seals.
    def assert_no_network(self) -> None:
        origin = self.started["url"].rstrip("/")
        local = (origin + "/", "ws" + origin[len("http"):] + "/") + LOCAL_SCHEMES
        urls = self.devtools.requested_urls()
        self.assertTrue(any(url.startswith(origin + "/") for url in urls),
                        "no request was recorded at all, so the seal saw nothing")
        foreign = sorted({url for url in urls if not url.startswith(local)})
        self.assertEqual(foreign, [], "the tab requested outside the Studio's loopback origin")

    def assert_no_model_calls(self) -> None:
        calls = (self.checkout.calls.read_text(encoding="utf-8")
                 if self.checkout.calls.exists() else "")
        self.assertEqual(calls, "", "a flow invoked a model client")

    def api(self, method: str, path: str, body: Optional[dict] = None):
        """One authenticated API call with the session's cookie, CSRF token and origin."""
        connection = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=30)
        try:
            headers = {"Host": self.record["host"], "Cookie": self.cookie}
            if method == "POST":
                if not hasattr(self, "_csrf"):
                    connection.request("GET", "/api/session", headers=headers)
                    self._csrf = json.loads(connection.getresponse().read())["csrf_token"]
                payload = json.dumps(body or {}).encode("utf-8")
                headers.update({"Origin": self.started["url"].rstrip("/"),
                                "X-Studio-CSRF": self._csrf,
                                "Content-Type": "application/json",
                                "Content-Length": str(len(payload))})
                connection.request(method, path, body=payload, headers=headers)
            else:
                connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()
