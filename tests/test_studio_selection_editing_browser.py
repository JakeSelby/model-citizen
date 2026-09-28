# SPDX-License-Identifier: MIT
"""Rendered draft selection refusal, acknowledgement and checkpoint behavior."""
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


class SelectionEditingBrowserTests(unittest.TestCase):
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio

    def setUp(self):
        browser_support.StudioBrowserTests.setUp(self)
        self.env.pop("HARNESS_QUIET", None)
        self.env["HARNESS_WORKTREE_ROOT"] = str(Path(self.temporary.name) / "worktrees")
        self.config_path = self.home / ".config" / "agent-harness" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_bytes((browser_support.REPO / "config.example.json").read_bytes())
        self.live_before = self.config_path.read_bytes()
        self.draft = "selection-browser-" + secrets.token_hex(4)
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", self.draft, "--json"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        self.assertTrue(created.stdout, "draft create returned no JSON output")
        self.created = json.loads(created.stdout)
        self.addCleanup(self._discard_draft)

    def _discard_draft(self):
        for _ in range(100):
            discarded = subprocess.run(
                [sys.executable, str(CLI), "draft", "discard", self.draft, "--json"],
                env=self.env, capture_output=True, text=True, timeout=15,
            )
            if discarded.returncode == 0 or '"code": "busy"' not in discarded.stdout:
                break
            time.sleep(0.05)
        self.assertEqual(discarded.returncode, 0, discarded.stderr or discarded.stdout)

    def _wait(self, expression: str, message: str, attempts: int = 500):
        for _ in range(attempts):
            value = self.devtools.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        body = self.devtools.evaluate("document.body.textContent")
        self.fail(message + ": " + str(body)[-2000:])

    def _press(self, key: str, code: str, virtual_key: int, text: str = ""):
        payload = {"type": "keyDown", "key": key, "code": code,
                   "windowsVirtualKeyCode": virtual_key}
        if text:
            payload["text"] = text
        self.devtools.call("Input.dispatchKeyEvent", payload)
        self.devtools.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key, "code": code,
            "windowsVirtualKeyCode": virtual_key,
        })

    def _open(self, instrumentation: str = ""):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__selectionCalls = [];
            globalThis.__setLabelValue = (text, value) => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith(text));
              const input = label && document.getElementById(label.htmlFor);
              if (!input) throw new Error('missing input for ' + text);
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
              input.dispatchEvent(new Event('input', { bubbles: true }));
            };
            globalThis.__clickLabel = (text) => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith(text));
              const input = label && (document.getElementById(label.htmlFor) || label.closest('div').querySelector('input'));
              if (!input) throw new Error('missing checkbox for ' + text);
              input.click();
            };
        """ + instrumentation})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()
        self._wait("document.querySelector('input') !== null", "Configure did not render")
        self.devtools.evaluate("__setLabelValue('Draft name', %s)" % json.dumps(self.draft))
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Load draft' && !b.disabled)",
            "draft load did not enable",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait(
            "document.body.textContent.includes('Selection controls are ready.')",
            "draft selection editor did not load",
        )

    def test_core_refusal_writes_nothing_then_acknowledged_change_checkpoints(self):
        self._open()
        focusable = json.loads(self.devtools.evaluate("""
            [...document.querySelectorAll('.selection-switch-group')]
              .forEach(group => { group.open = true; });
            JSON.stringify([...document.querySelectorAll(
              '.selection-editor input, .selection-editor summary, .selection-editor button'
            )].filter(control => control.tabIndex >= 0 && control.getClientRects().length).map(control => {
              control.focus();
              return {focused: document.activeElement === control,
                      outline: getComputedStyle(control).outlineWidth};
            }))
        """))
        self.assertTrue(focusable)
        self.assertTrue(all(item["focused"] for item in focusable), focusable)
        self.assertTrue(all(item["outline"] != "0px" for item in focusable))

        for label_text in ("Mode", "voice"):
            self.devtools.evaluate("""
                (() => {
                  const label = [...document.querySelectorAll('label')]
                    .find(item => item.textContent.trim() === %s);
                  document.getElementById(label.htmlFor).focus();
                })()
            """ % json.dumps(label_text))
            self._press("ArrowDown", "ArrowDown", 40)
            self._wait(
                "document.activeElement.getAttribute('aria-expanded') === 'true'",
                label_text + " selector did not open from the keyboard",
            )
            self._press("Escape", "Escape", 27)
            self._wait(
                "document.activeElement.getAttribute('aria-expanded') === 'false'",
                label_text + " selector did not close from the keyboard",
            )

        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('hooks')).parentElement.open = false;"
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('hooks')).focus()"
        )
        self._press("Enter", "Enter", 13, "\r")
        self.assertTrue(self.devtools.evaluate("document.activeElement.parentElement.open"))
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith('stop-gate · core'));
              (document.getElementById(label.htmlFor) || label.closest('div').querySelector('input')).focus();
            })()
        """)
        self._press(" ", "Space", 32, " ")
        self._wait(
            "document.body.textContent.includes('core_switches_acknowledged')",
            "CLI core-hook refusal was not shown",
        )
        worktree, _ = drafts.find(browser_support.REPO, self.draft)
        self.assertEqual(drafts._read_state(worktree)["revision"], self.created["revision"])
        self.assertEqual(self.config_path.read_bytes(), self.live_before)

        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('label')].some(l => "
            "l.textContent.includes('I acknowledge that disabling a core hook'))"
        ))
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.includes('I acknowledge that disabling a core hook'));
              (document.getElementById(label.htmlFor) || label.closest('div').querySelector('input')).focus();
            })()
        """)
        self._press(" ", "Space", 32, " ")
        self._wait(
            "document.body.textContent.includes('Projected selection preview')",
            "before and after projection preview was not rendered",
        )
        for _ in range(200):
            worktree, _ = drafts.find(browser_support.REPO, self.draft)
            if drafts._read_state(worktree)["revision"] != self.created["revision"]:
                break
            time.sleep(0.05)
        else:
            self.fail("acknowledged selection was not checkpointed")
        saved = None
        for _ in range(100):
            try:
                saved = drafts.read_config(browser_support.REPO, self.draft)
                break
            except drafts.DraftError as exc:
                if exc.code != "busy":
                    raise
                time.sleep(0.05)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertNotEqual(saved["draft"]["revision"], self.created["revision"])
        self.assertEqual(saved["config"]["hooks"]["stop-gate"], "off")
        self.assertIs(saved["config"]["core_switches_acknowledged"], True)
        self.assertEqual(self.config_path.read_bytes(), self.live_before)
        self.assertIn("Nothing applied", self.devtools.evaluate("document.body.textContent"))

    def test_dependency_refusal_is_rendered_and_writes_no_checkpoint(self):
        worktree, _ = drafts.find(browser_support.REPO, self.draft)
        config_path = drafts._paths(worktree)["config"]
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.setdefault("roles", {})["designer"] = "off"
        config.setdefault("roles", {})["design-judge"] = "off"
        config.setdefault("skills", {})["design-loop"] = "off"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        self._open()
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('skills')).click()"
        )
        self.devtools.evaluate("__clickLabel('design-loop')")
        self._wait(
            "document.body.textContent.includes('depends on roles/designer')",
            "CLI dependency refusal was not shown",
        )
        self.assertEqual(drafts._read_state(worktree)["revision"], self.created["revision"])
        saved = drafts.read_config(browser_support.REPO, self.draft)
        self.assertEqual(saved["config"]["skills"]["design-loop"], "off")
        self.assertEqual(self.config_path.read_bytes(), self.live_before)

    def test_changed_edit_suppresses_stale_preview_and_saves_latest_values_once(self):
        self._open("""
            globalThis.__releaseSelectionPreview = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview') ||
                  url.includes('/api/configure/selection/save')) {
                const kind = url.endsWith('/save') ? 'save' : 'preview';
                globalThis.__selectionCalls.push({kind, body: JSON.parse(init.body || '{}')});
                if (kind === 'preview' &&
                    globalThis.__selectionCalls.filter(call => call.kind === 'preview').length === 1) {
                  await new Promise(resolve => { globalThis.__releaseSelectionPreview = resolve; });
                }
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click()"
        )
        self.devtools.evaluate("__clickLabel('cache-hygiene')")
        self._wait(
            "__selectionCalls.some(call => call.kind === 'preview')",
            "first selection preview did not start",
        )
        self.devtools.evaluate("__clickLabel('conciseness')")
        self.devtools.evaluate("__releaseSelectionPreview()")
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 1",
            "latest selection was not saved",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertEqual(saves[0]["body"]["changes"], {
            "rules.cache-hygiene": "off", "rules.conciseness": "off",
        })
        time.sleep(0.9)
        self.assertEqual(
            self.devtools.evaluate(
                "__selectionCalls.filter(call => call.kind === 'save').length"
            ),
            1,
        )

    def test_edit_during_save_uses_the_returned_revision_for_the_queued_checkpoint(self):
        self._open("""
            globalThis.__releaseFirstSelectionSave = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview') ||
                  url.includes('/api/configure/selection/save')) {
                const kind = url.endsWith('/save') ? 'save' : 'preview';
                globalThis.__selectionCalls.push({kind, body: JSON.parse(init.body || '{}')});
                if (kind === 'save' &&
                    globalThis.__selectionCalls.filter(call => call.kind === 'save').length === 1) {
                  await new Promise(resolve => { globalThis.__releaseFirstSelectionSave = resolve; });
                }
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('cache-hygiene')"
        )
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 1",
            "first selection save did not start",
        )
        self.devtools.evaluate("__clickLabel('conciseness'); __releaseFirstSelectionSave()")
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 2",
            "queued selection did not checkpoint after the first save",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertEqual(saves[0]["body"]["changes"], {"rules.cache-hygiene": "off"})
        self.assertEqual(saves[1]["body"]["changes"], {"rules.conciseness": "off"})
        self.assertNotEqual(saves[0]["body"]["base_revision"], saves[1]["body"]["base_revision"])

    def test_lost_committed_response_retries_with_same_identity_from_keyboard(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview') ||
                  url.includes('/api/configure/selection/save')) {
                const kind = url.endsWith('/save') ? 'save' : 'preview';
                const body = JSON.parse(init.body || '{}');
                globalThis.__selectionCalls.push({kind, body});
                if (kind === 'save' &&
                    globalThis.__selectionCalls.filter(call => call.kind === 'save').length === 1) {
                  const committed = await originalFetch(input, init);
                  await committed.text();
                  throw new TypeError('simulated lost committed response');
                }
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click()"
        )
        self.devtools.evaluate("__clickLabel('cache-hygiene')")
        self._wait(
            "[...document.querySelectorAll('button')].some(button => button.textContent === 'Retry save')",
            "selection failure did not expose retry",
        )
        self.assertIn("simulated lost committed response", self.devtools.evaluate("document.body.textContent"))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(button => button.textContent === 'Retry save').focus()"
        )
        self.assertNotEqual(
            self.devtools.evaluate("getComputedStyle(document.activeElement).outlineWidth"), "0px",
        )
        self._press("Enter", "Enter", 13, "\r")
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 2",
            "keyboard retry did not save the retained selection",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertEqual(saves[0]["body"]["changes"], {"rules.cache-hygiene": "off"})
        self.assertEqual(saves[1]["body"]["changes"], {"rules.cache-hygiene": "off"})
        self.assertEqual(saves[0]["body"]["base_revision"], saves[1]["body"]["base_revision"])
        self.assertEqual(saves[0]["body"]["idempotency_key"], saves[1]["body"]["idempotency_key"])
        self._wait(
            "document.body.textContent.includes('Draft checkpoint saved')",
            "successful retry did not clear the failure",
        )
        worktree, _ = drafts.find(browser_support.REPO, self.draft)
        saved = drafts.read_config(browser_support.REPO, self.draft)
        self.assertEqual(saved["config"]["rules"]["cache-hygiene"], "off")
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.created["revision"] + ".." + saved["draft"]["revision"]],
            capture_output=True, text=True, timeout=10, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "1")

    def test_stale_revision_requires_canonical_reload_instead_of_retry(self):
        self._open("""
            globalThis.__staleInjected = false;
            globalThis.__selectionPreviewPayload = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview')) {
                const response = await originalFetch(input, init);
                const body = await response.text();
                globalThis.__selectionPreviewPayload = JSON.parse(body);
                return new Response(body, {status: response.status,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/selection/save') && !globalThis.__staleInjected) {
                globalThis.__staleInjected = true;
                const body = JSON.parse(init.body || '{}');
                globalThis.__selectionCalls.push({kind: 'save', body});
                return new Response(JSON.stringify({
                  ...globalThis.__selectionPreviewPayload,
                  valid: false, error: 'draft revision changed; reload before saving',
                  error_code: 'stale-revision', saved: false, result: null,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('cache-hygiene')"
        )
        self._wait(
            "[...document.querySelectorAll('button')]"
            ".some(button => button.textContent === 'Reload draft')",
            "stale revision did not offer canonical reload",
        )
        self.assertFalse(self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".some(button => button.textContent === 'Retry save')"
        ))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(button => button.textContent === 'Reload draft').click()"
        )
        self._wait(
            "document.body.textContent.includes('Selection controls are ready.') && "
            "![...document.querySelectorAll('button')]"
            ".some(button => button.textContent === 'Reload draft')",
            "canonical draft reload did not settle",
        )
        self.assertTrue(self.devtools.evaluate(
            "(() => { const label = [...document.querySelectorAll('label')]"
            ".find(item => item.textContent.trim().startsWith('cache-hygiene'));"
            "return label.closest('div').querySelector('input').checked; })()"
        ))
        self.assertEqual(
            self.devtools.evaluate("__selectionCalls.filter(call => call.kind === 'save').length"),
            1,
        )

    def test_idempotency_conflict_retry_uses_a_new_request_identity(self):
        self._open("""
            globalThis.__conflictInjected = false;
            globalThis.__selectionPreviewPayload = null;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview')) {
                const response = await originalFetch(input, init);
                const body = await response.text();
                globalThis.__selectionPreviewPayload = JSON.parse(body);
                return new Response(body, {status: response.status,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/selection/save')) {
                const body = JSON.parse(init.body || '{}');
                globalThis.__selectionCalls.push({kind: 'save', body});
                if (!globalThis.__conflictInjected) {
                  globalThis.__conflictInjected = true;
                  return new Response(JSON.stringify({
                    ...globalThis.__selectionPreviewPayload,
                    valid: false,
                    error: 'idempotency key was already used for a different save',
                    error_code: 'idempotency-conflict', saved: false, result: null,
                  }), {status: 200, headers: {'Content-Type': 'application/json'}});
                }
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('cache-hygiene')"
        )
        self._wait(
            "[...document.querySelectorAll('button')]"
            ".some(button => button.textContent === 'Retry save')",
            "idempotency conflict did not offer a new-identity retry",
        )
        self.assertFalse(self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".some(button => button.textContent === 'Reload draft')"
        ))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(button => button.textContent === 'Retry save').click()"
        )
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 2",
            "new-identity retry did not save",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertNotEqual(saves[0]["body"]["idempotency_key"],
                            saves[1]["body"]["idempotency_key"])
        self.assertEqual(saves[0]["body"]["base_revision"],
                         saves[1]["body"]["base_revision"])
        self._wait(
            "document.body.textContent.includes('Draft checkpoint saved')",
            "new-identity retry did not reach a checkpoint",
        )

    def test_reverted_edit_is_explicitly_unchanged_and_creates_no_checkpoint(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview') ||
                  url.includes('/api/configure/selection/save')) {
                globalThis.__selectionCalls.push({
                  kind: url.endsWith('/save') ? 'save' : 'preview',
                  body: JSON.parse(init.body || '{}'),
                });
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click()"
        )
        self.devtools.evaluate("__clickLabel('cache-hygiene')")
        self._wait(
            "[...document.querySelectorAll('label')]"
            ".find(label => label.textContent.trim().startsWith('cache-hygiene'))"
            ".closest('div').querySelector('input').checked === false",
            "first switch edit did not render",
        )
        self.devtools.evaluate("__clickLabel('cache-hygiene')")
        self._wait(
            "document.body.textContent.includes('No effective selection change to checkpoint')",
            "reverted selection was not reported as unchanged",
        )
        self.assertEqual(
            self.devtools.evaluate("__selectionCalls.filter(call => call.kind === 'save').length"),
            0,
        )
        worktree, _ = drafts.find(browser_support.REPO, self.draft)
        self.assertEqual(drafts._read_state(worktree)["revision"], self.created["revision"])

    def test_mode_save_rehydrates_implicit_switches_without_redundant_checkpoint(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/preview') ||
                  url.includes('/api/configure/selection/save')) {
                globalThis.__selectionCalls.push({
                  kind: url.endsWith('/save') ? 'save' : 'preview',
                  body: JSON.parse(init.body || '{}'),
                });
              }
              return originalFetch(input, init);
            };
        """)
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim() === 'Mode');
              document.getElementById(label.htmlFor).click();
            })()
        """)
        self._wait(
            "[...document.querySelectorAll('[role=option]')]"
            ".some(option => option.textContent.trim() === 'minimal')",
            "mode choices did not open",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('[role=option]')]"
            ".find(option => option.textContent.trim() === 'minimal').click()"
        )
        self._wait(
            "__selectionCalls.filter(call => call.kind === 'save').length === 1",
            "mode selection was not checkpointed",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('workflows')).click()"
        )
        self._wait(
            "(() => { const label = [...document.querySelectorAll('label')]"
            ".find(item => item.textContent.trim().startsWith('build'));"
            "const input = label && (document.getElementById(label.htmlFor) || "
            "label.closest('div').querySelector('input')); return input && !input.checked; })()",
            "canonical mode response did not update the implicit workflow switch",
        )
        for kind in ("rules", "hooks", "skills", "workflows", "roles"):
            self.assertTrue(self.devtools.evaluate(
                "document.querySelector('[aria-label=%s]') !== null" %
                json.dumps(kind + " switch selection preview")
            ))
        self.assertIn("workflows.build", self.devtools.evaluate("document.body.textContent"))
        time.sleep(0.9)
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        saves = [call for call in calls if call["kind"] == "save"]
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0]["body"]["changes"], {"mode": "minimal"})
        saved = None
        for _ in range(100):
            try:
                saved = drafts.read_config(browser_support.REPO, self.draft)
                break
            except drafts.DraftError as exc:
                if exc.code != "busy":
                    raise
                time.sleep(0.05)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["config"]["mode"], "minimal")
        self.assertNotIn("build", saved["config"].get("workflows", {}))

    def test_draft_switch_intent_cancels_pending_selection_debounce_before_load(self):
        other = "selection-pending-other-" + secrets.token_hex(4)
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", other, "--json"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)

        def discard_other():
            for _ in range(100):
                discarded = subprocess.run(
                    [sys.executable, str(CLI), "draft", "discard", other, "--json"],
                    env=self.env, capture_output=True, text=True, timeout=15,
                )
                if discarded.returncode == 0 or '"code": "busy"' not in discarded.stdout:
                    break
                time.sleep(0.05)
            self.assertEqual(discarded.returncode, 0, discarded.stderr or discarded.stdout)

        self.addCleanup(discard_other)
        self._open("""
            globalThis.__releaseDraftSwitchRead = null;
            globalThis.__draftSwitchReadStarted = false;
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const body = JSON.parse(init.body || '{}');
              if (url.endsWith('/api/configure/read') && body.draft === %s) {
                globalThis.__draftSwitchReadStarted = true;
                await new Promise(resolve => { globalThis.__releaseDraftSwitchRead = resolve; });
              }
              if (url.includes('/api/configure/selection/')) {
                globalThis.__selectionCalls.push({
                  kind: url.endsWith('/save') ? 'save' :
                    url.endsWith('/preview') ? 'preview' : 'read', body,
                });
              }
              return originalFetch(input, init);
            };
        """ % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('cache-hygiene')"
        )
        self._wait(
            "document.body.textContent.includes('checking')",
            "selection edit did not enter its pending debounce",
        )
        self.devtools.evaluate("__setLabelValue('Draft name', %s)" % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(button => button.textContent === 'Load draft').click()"
        )
        self._wait("__draftSwitchReadStarted", "new draft read was not delayed")
        self.assertTrue(self.devtools.evaluate(
            "document.querySelector('.selection-editor') === null"
        ))
        time.sleep(1.0)
        self.assertEqual(
            self.devtools.evaluate(
                "__selectionCalls.filter(call => call.kind !== 'read').length"
            ),
            0,
        )
        self.devtools.evaluate("__releaseDraftSwitchRead()")
        self._wait(
            "__selectionCalls.some(call => call.kind === 'read' && call.body.draft === %s)" %
            json.dumps(other),
            "new draft selection did not load after release",
        )
        self._wait(
            "document.body.textContent.includes('Selection controls are ready.')",
            "new draft selection did not settle",
        )

    def test_switching_drafts_ignores_an_old_save_response(self):
        other = "selection-other-" + secrets.token_hex(4)
        created = subprocess.run(
            [sys.executable, str(CLI), "draft", "create", other, "--json"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        other_created = json.loads(created.stdout)

        def discard_other():
            for _ in range(100):
                discarded = subprocess.run(
                    [sys.executable, str(CLI), "draft", "discard", other, "--json"],
                    env=self.env, capture_output=True, text=True, timeout=15,
                )
                if discarded.returncode == 0 or '"code": "busy"' not in discarded.stdout:
                    break
                time.sleep(0.05)
            self.assertEqual(discarded.returncode, 0, discarded.stderr or discarded.stdout)

        self.addCleanup(discard_other)
        self._open("""
            globalThis.__releaseOldSelectionSave = null;
            globalThis.__oldSelectionSaveCommitted = false;
            globalThis.__raceRenderSeen = false;
            addEventListener('DOMContentLoaded', () => {
              new MutationObserver(() => {
                const editor = document.querySelector('.selection-editor');
                if (!globalThis.__raceRenderSeen && editor?.dataset.draft === %s) {
                  globalThis.__raceRenderSeen = true;
                  globalThis.__releaseOldSelectionSave();
                }
              }).observe(document.body, {
                attributes: true, attributeFilter: ['data-draft'], childList: true, subtree: true,
              });
            });
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/selection/')) {
                const kind = url.split('/').pop();
                const body = JSON.parse(init.body || '{}');
                globalThis.__selectionCalls.push({kind, body});
                if (kind === 'save' &&
                    globalThis.__selectionCalls.filter(call => call.kind === 'save').length === 1) {
                  const response = await originalFetch(input, init);
                  const responseBody = await response.text();
                  globalThis.__oldSelectionSaveCommitted = true;
                  await new Promise(resolve => { globalThis.__releaseOldSelectionSave = resolve; });
                  return new Response(responseBody, {
                    status: response.status, headers: {'Content-Type': 'application/json'},
                  });
                }
              }
              return originalFetch(input, init);
            };
        """ % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('cache-hygiene')"
        )
        self._wait("__oldSelectionSaveCommitted", "old draft save did not commit")
        self.devtools.evaluate("__setLabelValue('Draft name', %s)" % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(button => button.textContent === 'Load draft').click()"
        )
        self._wait(
            "__selectionCalls.some(call => call.kind === 'read' && call.body.draft === %s)" %
            json.dumps(other),
            "new draft selection did not load",
        )
        self._wait("__raceRenderSeen", "old save did not resolve during the new draft render")
        self._wait(
            "document.body.textContent.includes('Selection controls are ready.')",
            "new draft controls did not settle",
        )
        self.assertNotIn("Projected selection preview", self.devtools.evaluate("document.body.textContent"))
        time.sleep(0.5)
        self.assertIn("Selection controls are ready.", self.devtools.evaluate("document.body.textContent"))
        self.assertNotIn("Projected selection preview", self.devtools.evaluate("document.body.textContent"))

        self.devtools.evaluate(
            "[...document.querySelectorAll('.selection-switch-group > summary')]"
            ".find(s => s.textContent.includes('rules')).click();"
            "__clickLabel('conciseness')"
        )
        self._wait(
            "__selectionCalls.some(call => call.kind === 'save' && call.body.draft === %s)" %
            json.dumps(other),
            "new draft edit did not save",
        )
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__selectionCalls)"))
        other_save = next(call for call in calls
                          if call["kind"] == "save" and call["body"]["draft"] == other)
        self.assertEqual(other_save["body"]["base_revision"], other_created["revision"])


if __name__ == "__main__":
    unittest.main()
