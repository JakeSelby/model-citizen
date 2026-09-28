# SPDX-License-Identifier: MIT
"""Rendered Configure autosave behavior in a real browser."""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path

import test_studio_browser as browser_support
from harness_core.studio import drafts

CLI = browser_support.CLI


class ConfigureBrowserTests(unittest.TestCase):
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio

    def setUp(self):
        browser_support.StudioBrowserTests.setUp(self)
        self.env["HARNESS_WORKTREE_ROOT"] = str(Path(self.temporary.name) / "worktrees")
        config_path = self.home / ".config" / "agent-harness" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes((browser_support.REPO / "config.example.json").read_bytes())
        self.draft = "configure-browser-" + secrets.token_hex(4)
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", self.draft, "--json"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        self.addCleanup(self._discard_draft)

    def _discard_draft(self):
        subprocess.run(
            [sys.executable, str(CLI), "draft", "discard", self.draft, "--json"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def _wait(self, expression: str, message: str, attempts: int = 400):
        for _ in range(attempts):
            value = self.devtools.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        self.fail(message)

    def _open_configure(self, instrumentation: str) -> None:
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__settingsCalls = [];
            globalThis.__setLabelValue = (text, value) => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith(text));
              const input = label && document.getElementById(label.htmlFor);
              if (!input) throw new Error('missing input for ' + text);
              const prototype = input instanceof HTMLTextAreaElement
                ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
              Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
              input.dispatchEvent(new Event('input', { bubbles: true }));
            };
        """ + instrumentation})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self._wait_for_shell()
        self._wait("document.querySelector('input') !== null", "Configure did not render")

    def _load_draft(self, draft: str) -> None:
        self.devtools.evaluate("__setLabelValue('Draft name', %s)" % json.dumps(draft))
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Load draft' && !b.disabled)",
            "draft load did not enable",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait(
            "document.body.textContent.includes('Draft configuration is ready.')",
            "draft configuration did not load",
        )

    def test_configure_debounces_and_retains_an_edit_during_inflight_save(self):
        self._open_configure("""
            globalThis.__releaseFirstSave = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/preview') || url.includes('/api/configure/save')) {
                const kind = url.endsWith('/save') ? 'save' : 'preview';
                globalThis.__settingsCalls.push({
                  kind, at: performance.now(), body: JSON.parse(init.body || '{}'),
                });
                if (kind === 'save' && globalThis.__settingsCalls.filter(c => c.kind === 'save').length === 1) {
                  await new Promise(resolve => { globalThis.__releaseFirstSave = resolve; });
                }
              }
              return originalFetch(input, init);
            };
        """)
        self._load_draft(self.draft)

        self.devtools.evaluate(
            "globalThis.__firstEdit = performance.now(); __setLabelValue('Name', 'First edit')"
        )
        self._wait(
            "__settingsCalls.some(call => call.kind === 'save')",
            "first debounced save did not start",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__settingsCalls)"))
        first_preview = next(call for call in calls if call["kind"] == "preview")
        first_edit = self.devtools.evaluate("__firstEdit")
        self.assertGreaterEqual(first_preview["at"] - first_edit, 700)
        self.assertEqual(first_preview["body"]["changes"], {"identity.name": "First edit"})

        self.devtools.evaluate("__setLabelValue('Role', 'Queued during save')")
        time.sleep(0.9)
        self.assertEqual(
            self.devtools.evaluate("__settingsCalls.filter(call => call.kind === 'preview').length"),
            1,
        )
        self.devtools.evaluate("__releaseFirstSave()")
        self._wait(
            "__settingsCalls.filter(call => call.kind === 'save').length === 2",
            "queued edit did not start a second save",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__settingsCalls)"))
        previews = [call for call in calls if call["kind"] == "preview"]
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertEqual(len(previews), 2)
        self.assertEqual(saves[0]["body"]["changes"], {"identity.name": "First edit"})
        self.assertEqual(previews[1]["body"]["changes"], {"identity.role": "Queued during save"})
        self.assertEqual(saves[1]["body"]["changes"], {"identity.role": "Queued during save"})

    def test_failed_save_keeps_loaded_identity_and_allows_explicit_retry(self):
        self._open_configure("""
            globalThis.__releaseFailedSave = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/preview') || url.includes('/api/configure/save')) {
                const kind = url.endsWith('/save') ? 'save' : 'preview';
                const body = JSON.parse(init.body || '{}');
                globalThis.__settingsCalls.push({ kind, at: performance.now(), body });
                if (kind === 'save' && globalThis.__settingsCalls.filter(c => c.kind === 'save').length === 1) {
                  await new Promise(resolve => { globalThis.__releaseFailedSave = resolve; });
                  return new Response(JSON.stringify({
                    valid: false,
                    errors: [{ path: 'draft', message: 'temporary save failure' }],
                    warnings: [], changed: Object.keys(body.changes), preview: {},
                    base_revision: body.base_revision, saved: false, result: null,
                  }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
              }
              return originalFetch(input, init);
            };
        """)
        self._load_draft("  " + self.draft + "  ")
        self.devtools.evaluate("__setLabelValue('Draft name', 'another-draft')")
        self.devtools.evaluate("__setLabelValue('Name', 'Retained after failure')")
        self._wait(
            "__settingsCalls.some(call => call.kind === 'save')",
            "failed save did not start",
        )
        self.devtools.evaluate("__setLabelValue('Role', 'Queued before failure')")
        self.devtools.evaluate("__releaseFailedSave()")
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry save')",
            "failed autosave did not expose retry",
        )
        self.assertIn("2 unsaved fields", self.devtools.evaluate("document.body.textContent"))
        time.sleep(0.9)
        self.assertEqual(
            self.devtools.evaluate("__settingsCalls.filter(call => call.kind === 'save').length"),
            1,
        )
        first_calls = json.loads(self.devtools.evaluate("JSON.stringify(__settingsCalls)"))
        self.assertTrue(all(call["body"]["draft"] == self.draft for call in first_calls))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Retry save').click()"
        )
        self._wait(
            "__settingsCalls.filter(call => call.kind === 'save').length === 2",
            "explicit retry did not save retained changes",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__settingsCalls)"))
        retry_preview = [call for call in calls if call["kind"] == "preview"][-1]
        retry_save = [call for call in calls if call["kind"] == "save"][-1]
        expected = {
            "identity.name": "Retained after failure",
            "identity.role": "Queued before failure",
        }
        self.assertEqual(retry_preview["body"]["changes"], expected)
        self.assertEqual(retry_save["body"]["changes"], expected)
        self.assertEqual(retry_save["body"]["draft"], self.draft)
        self._wait(
            "document.body.textContent.includes('0 unsaved fields')",
            "successful retry did not clear pending changes",
        )

    def test_redacted_malformed_reference_has_a_working_clear_action(self):
        worktree, _state = drafts.find(browser_support.REPO, self.draft)
        config_path = drafts._paths(worktree)["config"]
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("telemetry", {})["headers_env"] = False
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        self._open_configure("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/preview') || url.includes('/api/configure/save')) {
                globalThis.__settingsCalls.push({
                  kind: url.endsWith('/save') ? 'save' : 'preview',
                  body: JSON.parse(init.body || '{}'),
                });
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate("__setLabelValue('Draft name', %s)" % json.dumps(self.draft))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Clear malformed reference')",
            "malformed reference did not render a Clear action",
        )
        self.assertIn(
            "Malformed stored reference",
            self.devtools.evaluate(
                "[...document.querySelectorAll('input')].find(i => i.placeholder === 'Malformed stored reference').placeholder"
            ),
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Clear malformed reference').click()"
        )
        self._wait(
            "__settingsCalls.some(call => call.kind === 'save')",
            "Clear action did not checkpoint the repair",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__settingsCalls)"))
        self.assertEqual(calls[-1]["kind"], "save")
        self.assertEqual(calls[-1]["body"]["changes"], {
            "telemetry.headers_env": {"source": "environment", "name": ""},
        })
        self._wait(
            "![...document.querySelectorAll('button')].some(b => b.textContent === 'Clear malformed reference')",
            "Clear action remained after the malformed reference was removed",
        )


if __name__ == "__main__":
    unittest.main()
