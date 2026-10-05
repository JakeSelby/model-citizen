# SPDX-License-Identifier: MIT
"""Hosted-browser module editor lint, keyboard, and narrow-layout coverage."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import draft_support
import test_studio_browser as browser_support
from harness_core.studio import drafts


class ModuleEditorBrowserTests(unittest.TestCase):
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio

    def setUp(self):
        browser_support.StudioBrowserTests.setUp(self)
        self.env.pop("HARNESS_QUIET", None)
        # In-process draft commits read this process's environment, and a hosted runner has no
        # Git identity of its own; lend them the fixture's.
        identity = mock.patch.dict(
            "os.environ", {key: value for key, value in self.env.items() if key.startswith("GIT_")})
        identity.start()
        self.addCleanup(identity.stop)
        self.env["HARNESS_WORKTREE_ROOT"] = str(Path(self.temporary.name) / "worktrees")
        config_path = self.home / ".config" / "agent-harness" / "config.json"
        config_path.parent.mkdir(parents=True)
        config = json.loads((browser_support.REPO / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = [str(browser_support.REPO / "developer-primitives")]
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.draft = draft_support.draft_name("module-browser-")
        created = subprocess.run(
            [sys.executable, str(browser_support.CLI), "draft", "create", self.draft, "--json"],
            env=self.env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        draft_support.register_draft_cleanup(self, self.draft, self.env, stop=self._stop_studio)
        self.created = json.loads(created.stdout)
        saved = drafts.checkpoint(
            browser_support.REPO, self.draft, self.created["revision"], "browser-module",
            files={
                "developer-primitives/rules/browser-rule.md": b"# Browser rule\n\nClean.\n",
                "developer-primitives/rules/second-rule.md": b"# Second rule\n\nSecond clean.\n",
                "developer-primitives/rules/unsafe $(rule).md": b"# Unsafe identity\n\nClean.\n",
            },
            check_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        self.revision = saved["revision"]

    def _wait(self, expression, message, attempts=1000):
        for _ in range(attempts):
            if self.devtools.evaluate(expression):
                return
            time.sleep(0.05)
        calls = self.devtools.evaluate(
            "JSON.stringify(globalThis.__moduleCalls || [])"
        )
        self.fail(message + " calls=" + calls + ": "
              + self.devtools.evaluate("document.body.textContent")[-2000:])

    def _open(self, instrumentation=""):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__moduleCalls = [];
            globalThis.__setDraft = value => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith('Draft name'));
              const input = document.getElementById(label.htmlFor);
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                .set.call(input, value);
              input.dispatchEvent(new Event('input', {bubbles: true}));
            };
        """ + instrumentation})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()
        self._wait("document.querySelector('input') !== null", "Configure did not render")
        self.devtools.evaluate("__setDraft(%s)" % json.dumps(self.draft))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait("document.body.textContent.includes('Choose a draft-owned rule')",
                   "module list did not load")

    def _select(self, name):
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim() === 'Draft-owned module');
              document.getElementById(label.htmlFor).click();
            })()
        """)
        self._wait(
            "[...document.querySelectorAll('[role=option]')].some(o => o.textContent.includes(%s))"
            % json.dumps(name), "module option did not open",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('[role=option]')].find(o => o.textContent.includes(%s)).click()"
            % json.dumps(name)
        )

    def _choose(self, name):
        self._select(name)
        self._wait(
            "document.querySelector('.cm-content')?.getAttribute('aria-label')?.includes(%s)"
            % json.dumps(name), "CodeMirror did not load the selected module",
        )

    def _replace(self, text):
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "a", "code": "KeyA", "modifiers": 4,
                "windowsVirtualKeyCode": 65,
            })
        self.devtools.call("Input.insertText", {"text": text})
        self._wait(
            "[...document.querySelectorAll('button')].some(b => "
            "b.textContent === 'Save checkpoint' && !b.disabled)",
            "clean preview did not enable save",
        )

    def test_secret_line_refuses_keyboard_save_and_reflows_at_320px(self):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 320, "height": 900, "deviceScaleFactor": 1, "mobile": False,
        })
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()
        self._wait("document.querySelector('input') !== null", "Configure did not render")
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith('Draft name'));
              const input = document.getElementById(label.htmlFor);
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                .set.call(input, %s);
              input.dispatchEvent(new Event('input', {bubbles: true}));
            })()
        """ % json.dumps(self.draft))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait("document.body.textContent.includes('Choose a draft-owned rule')",
                   "module list did not load")
        self.devtools.evaluate("""
            (() => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim() === 'Draft-owned module');
              document.getElementById(label.htmlFor).click();
            })()
        """)
        self._wait("[...document.querySelectorAll('[role=option]')].some(o => o.textContent.includes('browser-rule'))",
                   "module option did not open")
        self.devtools.evaluate(
            "[...document.querySelectorAll('[role=option]')].find(o => o.textContent.includes('browser-rule')).click()"
        )
        self._wait("document.querySelector('.cm-content') !== null", "CodeMirror did not load")
        secret = "AKIA" + "A" * 16
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 4,
            "windowsVirtualKeyCode": 65,
        })
        self.devtools.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 4,
            "windowsVirtualKeyCode": 65,
        })
        self.devtools.call("Input.insertText", {"text": "# Browser rule\n" + secret + "\n"})
        self._wait("document.querySelector('.cm-module-diagnostic-line') !== null",
                   "secret finding did not mark its line")
        self._wait(
            "document.activeElement?.textContent?.includes('Nothing was saved')",
            "refusal summary did not receive focus",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent.includes('Line 2:')).focus()"
        )
        for kind in ("keyDown", "keyUp"):
            event = {
                "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
            }
            if kind == "keyDown":
                event["text"] = "\r"
            self.devtools.call("Input.dispatchKeyEvent", event)
        self._wait(
            "document.activeElement?.classList?.contains('cm-content') && "
            "window.getSelection()?.anchorNode?.parentElement?.closest('.cm-line')"
            "?.textContent?.includes(%s)" % json.dumps(secret),
            "keyboard diagnostic activation did not select the source line",
        )
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Save checkpoint').disabled"
        ))
        self.devtools.call("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "s", "code": "KeyS", "modifiers": 4,
            "windowsVirtualKeyCode": 83,
        })
        self.devtools.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "s", "code": "KeyS", "modifiers": 4,
            "windowsVirtualKeyCode": 83,
        })
        time.sleep(0.3)
        self.assertIn(secret, self.devtools.evaluate("document.querySelector('.cm-content').textContent"))
        self.assertLessEqual(self.devtools.evaluate("document.documentElement.scrollWidth"), 320)
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        self.assertEqual(state["revision"], self.revision)
        self.assertNotIn(secret, (worktree / "developer-primitives" / "rules" / "browser-rule.md")
                         .read_text(encoding="utf-8"))

    def test_parent_revision_drift_retains_buffer_and_refuses_without_a_save_request(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const body = init.body ? JSON.parse(init.body) : {};
              if (url.includes('/api/configure/module/save')) {
                globalThis.__moduleCalls.push({kind: 'save', body});
              }
              const response = await originalFetch(input, init);
              if (url.includes('/api/configure/selection/save')) globalThis.__selectionSaved = true;
              return response;
            };
        """)
        self._choose("browser-rule")
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
        self._wait("globalThis.__selectionSaved === true",
                   "a second checkpoint did not advance the parent revision")
        buffer = "# Browser rule\n\nRevision drift buffer.\n"
        self._replace(buffer)
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait(
            "document.body.textContent.includes('draft revision changed; reload before saving')",
            "revision drift did not surface the stale conflict",
        )
        self.assertEqual(self.devtools.evaluate(
            "__moduleCalls.filter(call => call.kind === 'save').length"
        ), 0)
        self.assertIn("Revision drift buffer", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Reload canonical draft').click()"
        )
        self._wait("document.body.textContent.includes('Canonical draft module reloaded')",
                   "external advance did not reload canonically")
        self._replace("# Browser rule\n\nSaved after external advance.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "parent revision was not published by reload for the next real save")

    def test_typing_during_real_save_retains_newer_text_and_accepts_the_next_save(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.__saveGate = new Promise(resolve => { globalThis.__releaseSave = resolve; });
            let held = false;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save') && !held) {
                held = true;
                const response = await originalFetch(input, init);
                const payload = await response.json();
                globalThis.__saveCommitted = true;
                await globalThis.__saveGate;
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nCommitted first text.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("globalThis.__saveCommitted === true", "real checkpoint did not commit")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.insertText", {"text": "Newer unsaved text.\n"})
        self.devtools.evaluate("globalThis.__releaseSave()")
        self._wait(
            "document.body.textContent.includes('newer unsaved text is retained')",
            "committed response did not retain the newer buffer",
        )
        self.assertIn("Newer unsaved text", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self._wait(
            "[...document.querySelectorAll('button')].some(b => "
            "b.textContent === 'Save checkpoint' && !b.disabled)",
            "newer text did not receive a preview against the committed revision",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "newer text did not save on the published revision")
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.revision + ".." + state["revision"]],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "2")

    def test_typing_during_an_in_flight_save_keeps_save_disabled_and_sends_one_request(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.__saveGate = new Promise(resolve => { globalThis.__releaseSave = resolve; });
            globalThis.__saves = 0;
            globalThis.__typed = false;
            globalThis.__previewsAfterTyping = 0;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save')) {
                globalThis.__saves += 1;
                const response = await originalFetch(input, init);
                const payload = await response.json();
                globalThis.__saveCommitted = true;
                await globalThis.__saveGate;
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              const response = await originalFetch(input, init);
              if (url.includes('/api/configure/module/preview') && globalThis.__typed) {
                globalThis.__previewsAfterTyping += 1;
              }
              return response;
            };
        """)
        save_button = ("[...document.querySelectorAll('button')]"
                       ".find(b => b.textContent.includes('Save checkpoint'))")
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nIn-flight first text.\n")
        self.devtools.evaluate(save_button + ".click()")
        self._wait("globalThis.__saveCommitted === true", "first save did not commit")
        self.devtools.evaluate("globalThis.__typed = true")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.insertText", {"text": "Typed while saving.\n"})
        self._wait("globalThis.__previewsAfterTyping >= 1", "typed text was not previewed")
        self._wait("document.body.textContent.includes('Preview is clean')",
                   "clean preview of the typed text did not render")
        self.assertTrue(self.devtools.evaluate(save_button + ".disabled"))
        self.devtools.evaluate(save_button + ".click()")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "s", "code": "KeyS", "modifiers": 4,
                "windowsVirtualKeyCode": 83,
            })
        time.sleep(0.5)
        self.assertEqual(self.devtools.evaluate("globalThis.__saves"), 1)
        self.devtools.evaluate("globalThis.__releaseSave()")
        self._wait("document.body.textContent.includes('newer unsaved text is retained')",
                   "settled save did not retain the typed text")
        self._wait(save_button + " && !" + save_button + ".disabled",
                   "save did not re-enable after the first request settled")
        self.assertEqual(self.devtools.evaluate("globalThis.__saves"), 1)
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.revision + ".." + state["revision"]],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "1")

    def test_rendered_command_quotes_an_unsafe_valid_module_identity(self):
        self._open()
        self._choose("unsafe $(rule)")
        command = self.devtools.evaluate(
            "[...document.querySelectorAll('pre[aria-label=\\\"Module save\\\"]')]"
            ".map(node => node.textContent).find(Boolean)"
        )
        self.assertIn("$(rule)", command)
        self.assertIn("'", command)
        self.assertNotIn(" $(rule) ", command)

    def test_keyboard_save_creates_one_checkpoint_and_renders_exact_runtime_budgets(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const response = await originalFetch(input, init);
              if (url.includes('/api/configure/module/preview')) {
                const payload = await response.json();
                globalThis.__lastPreview = payload;
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/module/save')) {
                globalThis.__moduleCalls.push({kind: 'save'});
              }
              return response;
            };
        """)
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nKeyboard checkpoint.\n")
        budgets = json.loads(self.devtools.evaluate("JSON.stringify(__lastPreview.budgets)"))
        self.assertEqual({row["runtime"] for row in budgets}, {"claude-code", "codex"})
        body = self.devtools.evaluate("document.body.textContent")
        for budget in budgets:
            self.assertIn(
                f'{budget["tokens"]} tokens ({budget["token_delta"]:+d}) / {budget["token_cap"]}',
                body,
            )
            self.assertIn(
                f'{budget["lines"]} lines ({budget["line_delta"]:+d}) / {budget["line_cap"]}',
                body,
            )
            self.assertIn("over cap" if budget["over_cap"] else "within cap", body)
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "s", "code": "KeyS", "modifiers": 4,
                "windowsVirtualKeyCode": 83,
            })
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "keyboard save did not settle")
        self.assertEqual(self.devtools.evaluate(
            "__moduleCalls.filter(call => call.kind === 'save').length"
        ), 1)
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.revision + ".." + state["revision"]],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "1")

    def test_lost_save_retries_canonically_and_creates_one_checkpoint(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save')) {
                const body = JSON.parse(init.body || '{}');
                globalThis.__moduleCalls.push(body);
                if (globalThis.__moduleCalls.length === 1) {
                  const committed = await originalFetch(input, init);
                  await committed.text();
                  throw new TypeError('simulated lost module response');
                }
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        buffer = "# Browser rule\n\nSaved once.\n"
        self._replace(buffer)
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry save')",
            "lost response did not preserve a retry action",
        )
        self.assertIn("Saved once", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Retry save').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "canonical retry did not settle")
        calls = json.loads(self.devtools.evaluate("JSON.stringify(__moduleCalls)"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.revision + ".." + state["revision"]],
            capture_output=True, text=True, timeout=10, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "1")

    def test_replay_settling_after_newer_parent_revision_reloads_canonical_content(self):
        newer = "f" * 40
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            let moduleSaves = 0;
            let selectionPlan = null;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const body = init.body ? JSON.parse(init.body) : {};
              if (url.includes('/api/configure/module/save')) {
                moduleSaves += 1;
                const response = await originalFetch(input, init);
                const payload = await response.json();
                if (moduleSaves === 1) throw new TypeError('lost first save response');
                globalThis.__reconcileRead = true;
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/selection/preview')) {
                const response = await originalFetch(input, init);
                selectionPlan = await response.json();
                return new Response(JSON.stringify(selectionPlan), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/selection/save')) {
                globalThis.__selectionSaved = true;
                return new Response(JSON.stringify({...selectionPlan, saved: true,
                  result: {revision: %s, replayed: false}}), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/module/read') && body.module
                  && globalThis.__reconcileRead) {
                const response = await originalFetch(input, init);
                const payload = await response.json();
                payload.content = '# Browser rule\\n\\nNewer canonical content.\\n';
                payload.source_digest = 'newer-digest';
                payload.draft.revision = %s;
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """ % (json.dumps(newer), json.dumps(newer)))
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nReplay candidate.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry save')",
            "lost response did not expose retry",
        )
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
        self._wait("globalThis.__selectionSaved === true",
                   "newer parent revision was not accepted")
        time.sleep(0.1)
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Retry save').click()"
        )
        self._wait(
            "document.body.textContent.includes('Saved response reconciled with the newer canonical checkpoint')",
            "replayed response did not reconcile",
        )
        self.assertIn("Newer canonical content", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertIn(newer[:12], self.devtools.evaluate("document.body.textContent"))

    def test_projection_truncation_notice_is_rendered(self):
        self._open()
        self._choose("browser-rule")
        text = "# Browser rule\n\n" + ("x" * 13000) + "\n"
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "a", "code": "KeyA", "modifiers": 4,
                "windowsVirtualKeyCode": 65,
            })
        self.devtools.call("Input.insertText", {"text": text})
        self._wait("document.body.textContent.includes('preview truncated')",
                   "projection truncation was not disclosed")

    def test_source_conflict_preserves_the_buffer_and_writes_nothing(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save')) {
                const body = JSON.parse(init.body || '{}');
                globalThis.__moduleCalls.push({kind: 'save', body});
                return new Response(JSON.stringify({
                  valid: false, error: 'draft module changed; reload before saving',
                  error_code: 'stale-source', base_revision: body.base_revision,
                  source_digest: body.source_digest, content_digest: '', unchanged: false,
                  module: null, diagnostics: [], budgets: [], projections: [],
                  nothing_applied: true, saved: false, result: null, saved_lint: [],
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        buffer = "# Browser rule\n\nSource drift buffer.\n"
        self._replace(buffer)
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait(
            "document.body.textContent.includes('draft module changed; reload before saving')",
            "source drift did not surface the stale conflict",
        )
        self.assertIn("Source drift buffer", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".some(b => b.textContent === 'Reload canonical draft')"
        ))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Reload canonical draft').click()"
        )
        self._wait(
            "document.body.textContent.includes('Canonical draft module reloaded')",
            "explicit reload did not finish",
        )
        self.assertIn("Clean", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertNotIn("Source drift buffer", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertIn(self.revision[:12], self.devtools.evaluate("document.body.textContent"))
        self.assertIn("browser-rule", self.devtools.evaluate(
            "document.querySelector('.cm-content').getAttribute('aria-label')"
        ))
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        self.assertEqual(state["revision"], self.revision)
        self.assertNotIn("Source drift buffer", (worktree / "developer-primitives" / "rules"
                                                  / "browser-rule.md").read_text(encoding="utf-8"))

    def test_structured_preview_stale_source_owns_reload_and_retains_the_buffer(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/preview')) {
                return new Response(JSON.stringify({
                  valid: false, error: 'draft module changed; reload before saving',
                  error_code: 'stale-source', base_revision: '', source_digest: '',
                  content_digest: '', unchanged: false, module: null, diagnostics: [], budgets: [],
                  projections: [], nothing_applied: true,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.insertText", {"text": " retained"})
        self._wait(
            "document.body.textContent.includes('draft module changed; reload before saving')",
            "structured preview error was discarded",
        )
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".some(b => b.textContent === 'Reload canonical draft')"
        ))
        self.assertIn("retained", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))

    def test_transient_preview_retries_and_reverting_to_saved_text_clears_refusal(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            let previews = 0;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/preview')) {
                previews += 1; globalThis.__previewCount = previews;
                if (previews === 1) return new Response(JSON.stringify({
                  valid: false, error: 'preview worker unavailable', error_code: 'preview-unavailable',
                  base_revision: '', source_digest: '', content_digest: '', unchanged: false,
                  module: null, diagnostics: [], budgets: [], projections: [], nothing_applied: true,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
                if (globalThis.__staleNext) {
                  globalThis.__staleNext = false;
                  return new Response(JSON.stringify({
                    valid: false, error: 'draft module changed; reload before saving',
                    error_code: 'stale-source', base_revision: '', source_digest: '',
                    content_digest: '', unchanged: false, module: null, diagnostics: [],
                    budgets: [], projections: [], nothing_applied: true,
                  }), {status: 200, headers: {'Content-Type': 'application/json'}});
                }
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nAutomatically retried.\n")
        self.assertGreaterEqual(self.devtools.evaluate("globalThis.__previewCount"), 2)
        self.devtools.evaluate("globalThis.__staleNext = true")
        self.devtools.call("Input.insertText", {"text": " stale"})
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Reload canonical draft')",
            "stale preview did not expose reload",
        )
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "a", "code": "KeyA", "modifiers": 4,
                "windowsVirtualKeyCode": 65,
            })
        self.devtools.call("Input.insertText", {"text": "# Browser rule\n\nClean.\n"})
        self._wait(
            "![...document.querySelectorAll('button')].some(b => b.textContent === 'Reload canonical draft')",
            "reverting exactly to saved text did not clear obsolete recovery controls",
        )
        self.assertFalse(self.devtools.evaluate(
            "document.body.textContent.includes('draft module changed; reload before saving')"
        ))

    def test_reload_invalidates_an_in_flight_preview_for_unchanged_text(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/preview') && globalThis.__staleNext) {
                globalThis.__staleNext = false;
                return new Response(JSON.stringify({
                  valid: false, error: 'draft module changed; reload before saving',
                  error_code: 'stale-source', base_revision: '', source_digest: '',
                  content_digest: '', unchanged: false, module: null, diagnostics: [],
                  budgets: [], projections: [], nothing_applied: true,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/module/preview') && globalThis.__holdNext) {
                globalThis.__holdNext = false;
                const response = await originalFetch(input, init);
                const payload = await response.json();
                payload.projections[0].text = 'Obsolete held preview marker.';
                globalThis.__previewHeld = true;
                await new Promise(resolve => { globalThis.__releaseHeldPreview = resolve; });
                return new Response(JSON.stringify(payload), {status: 200,
                  headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self.devtools.evaluate("globalThis.__staleNext = true")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.insertText", {"text": " stale"})
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Reload canonical draft')",
            "stale preview did not expose reload",
        )
        self.devtools.evaluate("globalThis.__holdNext = true")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        self.devtools.call("Input.insertText", {"text": " held"})
        self._wait("globalThis.__previewHeld === true", "replacement preview was not held")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Reload canonical draft').click()"
        )
        self._wait("document.body.textContent.includes('Canonical draft module reloaded')",
                   "canonical reload did not settle")
        self.devtools.evaluate("globalThis.__releaseHeldPreview()")
        time.sleep(0.3)
        self.assertNotIn("Obsolete held preview marker", self.devtools.evaluate(
            "document.body.textContent"
        ))
        self.assertIn("Clean", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))

    def test_non_stale_save_refusal_keeps_clean_preview_retryable(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            let refused = false;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save') && !refused) {
                refused = true;
                const body = JSON.parse(init.body || '{}');
                return new Response(JSON.stringify({
                  valid: false, error: 'preview worker timed out', error_code: 'preview-timeout',
                  base_revision: body.base_revision, source_digest: body.source_digest,
                  content_digest: '', unchanged: false, module: null, diagnostics: [], budgets: [],
                  projections: [], nothing_applied: true, saved: false, result: null, saved_lint: [],
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nRetryable refusal.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry save')",
            "non-stale refusal did not expose retry",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Retry save').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "retry did not reuse the clean preview")

    def test_save_time_lint_refusal_does_not_offer_a_no_op_retry(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/save')) {
                const body = JSON.parse(init.body || '{}');
                return new Response(JSON.stringify({
                  valid: false, error: 'Fix the module findings before saving.',
                  error_code: 'lint-refused', base_revision: body.base_revision,
                  source_digest: body.source_digest, content_digest: '', unchanged: false,
                  module: null, diagnostics: [{message: 'late lint finding', line: null,
                    severity: 'error'}], budgets: [], projections: [], nothing_applied: true,
                  saved: false, result: null, saved_lint: [],
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nLate lint refusal.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("document.body.textContent.includes('late lint finding')",
                   "save-time lint refusal was not rendered")
        self.assertFalse(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Retry save')"
        ))

    def test_real_checkpoint_then_module_switch_publishes_revision_for_the_next_save(self):
        self._open()
        self._choose("browser-rule")
        self._replace("# Browser rule\n\nFirst real checkpoint.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "first real checkpoint did not settle")
        self._select("second-rule")
        self._wait(
            "document.querySelector('.cm-content')?.getAttribute('aria-label')?.includes('second-rule')",
            "second module did not load after the real checkpoint",
        )
        self._replace("# Second rule\n\nSecond real checkpoint.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("document.body.textContent.includes('Draft checkpoint saved')",
                   "the switched module retained a stale parent revision")
        worktree, state = drafts.find(browser_support.REPO, self.draft)
        commits = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--count",
             self.revision + ".." + state["revision"]],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(commits.stdout.strip(), "2")

    def test_late_preview_and_save_responses_cannot_update_a_switched_module(self):
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            let previewHeld = false;
            let saveHeld = false;
            globalThis.__previewGate = new Promise(resolve => { globalThis.__releasePreview = resolve; });
            globalThis.__saveGate = new Promise(resolve => { globalThis.__releaseSave = resolve; });
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const body = init.body ? JSON.parse(init.body) : {};
              if (url.includes('/api/configure/module/read')) {
                globalThis.__moduleCalls.push({kind: 'read', body});
              }
              if (url.includes('/api/configure/module/preview') && !previewHeld) {
                previewHeld = true;
                globalThis.__moduleCalls.push({kind: 'preview', body});
                await globalThis.__previewGate;
                return new Response(JSON.stringify({
                  valid: true, error: '', error_code: '', base_revision: 'old',
                  source_digest: 'old', content_digest: 'old', unchanged: false,
                  module: null, diagnostics: [], budgets: [],
                  projections: [{runtime: 'codex', path: 'old',
                    text: 'Late preview marker.', truncated: false}],
                  nothing_applied: true,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              if (url.includes('/api/configure/module/save') && !saveHeld) {
                saveHeld = true;
                globalThis.__moduleCalls.push({kind: 'save', body});
                await globalThis.__saveGate;
                return new Response(JSON.stringify({
                  valid: true, error: '', error_code: '', base_revision: body.base_revision,
                  source_digest: body.source_digest, content_digest: 'late-save', unchanged: false,
                  module: null, diagnostics: [], budgets: [], projections: [],
                  nothing_applied: true, saved: true,
                  result: {revision: 'f'.repeat(40)}, saved_lint: [],
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "a", "code": "KeyA", "modifiers": 4,
                "windowsVirtualKeyCode": 65,
            })
        self.devtools.call("Input.insertText", {"text": "# Browser rule\n\nLate preview marker.\n"})
        self._wait("__moduleCalls.some(call => call.kind === 'preview')", "preview was not held")
        self._select("second-rule")
        self.devtools.evaluate("__releasePreview()")
        self._wait(
            "document.querySelector('.cm-content')?.getAttribute('aria-label')?.includes('second-rule')",
            "second module did not load after the stale preview settled",
        )
        self.assertIn("Second clean", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertNotIn("Late preview marker", self.devtools.evaluate("document.body.textContent"))

        self._replace("# Second rule\n\nLate save response.\n")
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Save checkpoint').click()"
        )
        self._wait("__moduleCalls.some(call => call.kind === 'save')", "save was not held")
        self._select("browser-rule")
        self.devtools.evaluate("__releaseSave()")
        self._wait(
            "document.querySelector('.cm-content')?.getAttribute('aria-label')?.includes('browser-rule')",
            "first module did not load after the stale save settled",
        )
        self.assertIn("Clean", self.devtools.evaluate("document.querySelector('.cm-content').textContent"))
        self.assertNotIn("Draft checkpoint saved", self.devtools.evaluate("document.body.textContent"))

    def test_delayed_inventory_and_keyed_reads_cannot_replace_newer_editor_ownership(self):
        other = draft_support.draft_name("module-browser-owner-")
        created = subprocess.run(
            [sys.executable, str(browser_support.CLI), "draft", "create", other, "--json"],
            env=self.env, capture_output=True, text=True, timeout=20, check=True,
        )
        draft_support.register_draft_cleanup(self, other, self.env, stop=self._stop_studio)
        initial = json.loads(created.stdout)
        drafts.checkpoint(
            browser_support.REPO, other, initial["revision"], "ownership-modules",
            files={
                "developer-primitives/rules/owner-one.md": b"# Owner one\n\nFirst.\n",
                "developer-primitives/rules/owner-two.md": b"# Owner two\n\nSecond.\n",
            },
            check_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__moduleCalls = [];
            globalThis.__inventoryGate = new Promise(resolve => { __releaseInventory = resolve; });
            globalThis.__keyedGate = new Promise(resolve => { __releaseKeyed = resolve; });
            globalThis.__setDraft = value => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith('Draft name'));
              const input = document.getElementById(label.htmlFor);
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                .set.call(input, value);
              input.dispatchEvent(new Event('input', {bubbles: true}));
            };
            const originalFetch = globalThis.fetch.bind(globalThis);
            let heldInventory = false;
            let heldKeyed = false;
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              const body = init.body ? JSON.parse(init.body) : {};
              const response = await originalFetch(input, init);
              if (url.includes('/api/configure/module/read') && !body.module && !heldInventory) {
                heldInventory = true; __inventoryHeld = true; await __inventoryGate;
              } else if (url.includes('/api/configure/module/read')
                  && body.module?.includes('owner-one') && !heldKeyed) {
                heldKeyed = true; __keyedHeld = true; await __keyedGate;
              }
              return response;
            };
        """})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()
        self._wait("document.querySelector('input') !== null", "Configure did not render")
        self.devtools.evaluate("__setDraft(%s)" % json.dumps(self.draft))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait("globalThis.__inventoryHeld === true", "initial inventory was not held")
        self.devtools.evaluate("__setDraft(%s)" % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Load draft').click()"
        )
        self._wait("document.body.textContent.includes('Choose a draft-owned rule')",
                   "newer inventory did not load")
        self.devtools.evaluate("__releaseInventory()")
        time.sleep(0.2)
        self._select("owner-one")
        self._wait("globalThis.__keyedHeld === true", "initial keyed read was not held")
        self._select("owner-two")
        self._wait(
            "document.querySelector('.cm-content')?.getAttribute('aria-label')?.includes('owner-two')",
            "newer keyed read did not load",
        )
        self.devtools.evaluate("__releaseKeyed()")
        time.sleep(0.2)
        self.assertIn("Second", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertIn("owner-two", self.devtools.evaluate(
            "document.querySelector('.cm-content').getAttribute('aria-label')"
        ))

    def test_late_preview_cannot_update_a_switched_draft(self):
        other = draft_support.draft_name("module-browser-other-")
        created = subprocess.run(
            [sys.executable, str(browser_support.CLI), "draft", "create", other, "--json"],
            env=self.env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        draft_support.register_draft_cleanup(self, other, self.env, stop=self._stop_studio)
        other_created = json.loads(created.stdout)
        drafts.checkpoint(
            browser_support.REPO, other, other_created["revision"], "other-module",
            files={"developer-primitives/rules/browser-rule.md": b"# Browser rule\n\nOther draft.\n"},
            check_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        self._open("""
            const originalFetch = globalThis.fetch.bind(globalThis);
            let held = false;
            globalThis.__draftGate = new Promise(resolve => { globalThis.__releaseDraft = resolve; });
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/module/preview') && !held) {
                held = true;
                globalThis.__moduleCalls.push({kind: 'preview'});
                await globalThis.__draftGate;
                return new Response(JSON.stringify({
                  valid: true, error: '', error_code: '', base_revision: 'old',
                  source_digest: 'old', content_digest: 'old', unchanged: false,
                  module: null, diagnostics: [], budgets: [],
                  projections: [{runtime: 'codex', path: 'old',
                    text: 'Late draft marker.', truncated: false}],
                  nothing_applied: true,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              return originalFetch(input, init);
            };
        """)
        self._choose("browser-rule")
        self.devtools.evaluate("document.querySelector('.cm-content').focus()")
        for kind in ("keyDown", "keyUp"):
            self.devtools.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "a", "code": "KeyA", "modifiers": 4,
                "windowsVirtualKeyCode": 65,
            })
        self.devtools.call("Input.insertText", {"text": "# Browser rule\n\nLate draft marker.\n"})
        self._wait("__moduleCalls.some(call => call.kind === 'preview')", "preview was not held")
        self.devtools.evaluate("__setDraft(%s)" % json.dumps(other))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')]"
            ".find(b => b.textContent === 'Load draft').click()"
        )
        self.devtools.evaluate("__releaseDraft()")
        self._wait("document.body.textContent.includes(%s)" % json.dumps(
            "citizen draft settings read " + other
        ),
                   "second draft module list did not load")
        self._choose("browser-rule")
        self.assertIn("Other draft", self.devtools.evaluate(
            "document.querySelector('.cm-content').textContent"
        ))
        self.assertNotIn("Late draft marker", self.devtools.evaluate("document.body.textContent"))


if __name__ == "__main__":
    unittest.main()
