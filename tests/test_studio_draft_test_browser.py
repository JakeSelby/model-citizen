# SPDX-License-Identifier: MIT
"""The rendered Configure page mounts "Test this draft" for a loaded draft (AH-S308, #991)."""
from __future__ import annotations

import unittest

import test_studio_browser as browser_support
import test_studio_configure_browser as configure_browser


class DraftTestBrowserTests(unittest.TestCase):
    """Borrows the Configure browser harness, not its tests: a real Studio, Chrome and draft."""

    setUp = configure_browser.ConfigureBrowserTests.setUp
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio
    _wait = configure_browser.ConfigureBrowserTests._wait
    _open_configure = configure_browser.ConfigureBrowserTests._open_configure
    _load_draft = configure_browser.ConfigureBrowserTests._load_draft

    def test_a_loaded_draft_shows_the_test_panel_and_its_verdicts_from_the_route(self):
        self._open_configure("""
            globalThis.__verdictCalls = [];
            const originalFetch = globalThis.fetch.bind(globalThis);
            globalThis.fetch = async (input, init = {}) => {
              const url = typeof input === 'string' ? input : input.url;
              if (url.includes('/api/configure/test/verdicts')) {
                globalThis.__verdictCalls.push(JSON.parse(init.body || '{}'));
              }
              return originalFetch(input, init);
            };
        """)
        self.assertFalse(self.devtools.evaluate(
            "[...document.querySelectorAll('h2')].some(h => h.textContent === 'Test this draft')"))
        self._load_draft(self.draft)
        self._wait("[...document.querySelectorAll('h2')].some(h => h.textContent === 'Test this draft')",
                   "the draft test panel did not mount")
        self._wait("document.body.textContent.includes('No tests of this draft yet.')",
                   "the panel did not show the verdicts route's answer")
        self.assertEqual(self.devtools.evaluate("__verdictCalls[0].draft"), self.draft)
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].some(b => b.textContent === 'Check power and spend')"))


if __name__ == "__main__":
    unittest.main()
