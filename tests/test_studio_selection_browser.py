# SPDX-License-Identifier: MIT
"""Rendered effective-selection provenance and budget behavior."""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import test_studio_browser as browser_support


class SelectionBrowserTests(unittest.TestCase):
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio

    def setUp(self):
        browser_support.StudioBrowserTests.setUp(self)
        self.config = self.home / ".config" / "agent-harness" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes((browser_support.REPO / "config.example.json").read_bytes())
        self.project = Path(self.temporary.name).resolve() / "project-selection.json"
        self.project.write_text(json.dumps({"stances": {"testing": "off"}}), encoding="utf-8")

    def _wait(self, expression: str, message: str, attempts: int = 400):
        for _ in range(attempts):
            value = self.devtools.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        self.fail(message)

    def _open_configure(self):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            globalThis.__setLabelValue = (text, value) => {
              const label = [...document.querySelectorAll('label')]
                .find(item => item.textContent.trim().startsWith(text));
              const input = label && document.getElementById(label.htmlFor);
              if (!input) throw new Error('missing input for ' + text);
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
              input.dispatchEvent(new Event('input', { bubbles: true }));
            };
        """})
        self.devtools.call("Page.navigate", {"url": self.started["url"] + "#/configure"})
        self.devtools.call("Page.bringToFront")
        self._wait_for_shell()
        self._wait(
            "document.body.textContent.includes('values resolved through the policy kernel')",
            "the launcher selection did not resolve",
        )

    def test_project_override_discloses_both_files_and_runtime_budgets(self):
        self._open_configure()
        body = self.devtools.evaluate("document.body.textContent")
        self.assertIn("Claude Code", body)
        self.assertIn("Codex", body)
        self.assertIn("always-loaded token budget", self.devtools.evaluate(
            "[...document.querySelectorAll('[role=progressbar]')].map(n => n.getAttribute('aria-label')).join(' ')"
        ))

        self.devtools.evaluate("__setLabelValue('Project selection file', %s)" % json.dumps(str(self.project)))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(b => b.textContent === 'Resolve selection').click()"
        )
        self._wait(
            "document.body.textContent.includes(%s)" % json.dumps(str(self.project)),
            "project selection provenance did not render",
        )
        self.devtools.evaluate(
            "[...document.querySelectorAll('summary')].find(s => s.textContent.includes('stances.testing')).click()"
        )
        self._wait(
            "document.body.textContent.includes('Overridden values')",
            "overridden provenance did not expand",
        )
        body = self.devtools.evaluate("document.body.textContent")
        self.assertIn(str(self.project), body)
        self.assertIn(str(self.config.resolve()), body)
        self.assertIn("required", body)
        self.assertIn("off", body)

if __name__ == "__main__":
    unittest.main()
