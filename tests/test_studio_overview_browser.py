# SPDX-License-Identifier: MIT
"""The installed-system summary on Baseline, and the open-and-wait helpers other Studio browser
suites borrow."""
import json
import time
import unittest

import test_studio_browser as browser_support


class StudioOverviewBrowserTests(unittest.TestCase):
    setUp = browser_support.StudioBrowserTests.setUp
    _close_devtools = browser_support.StudioBrowserTests._close_devtools
    _stop_browser = browser_support.StudioBrowserTests._stop_browser
    _stop_studio = browser_support.StudioBrowserTests._stop_studio
    _bootstrap = browser_support.StudioBrowserTests._bootstrap
    _wait_for_shell = browser_support.StudioBrowserTests._wait_for_shell

    def _wait(self, expression, message, attempts=1200):
        for _ in range(attempts):
            value = self.devtools.evaluate(expression)
            if value:
                return value
            time.sleep(0.05)
        self.fail(message)

    def _open(self):
        name, value = self.cookie.split("=", 1)
        self.devtools.call("Network.enable")
        self.devtools.call("Page.enable")
        self.devtools.call("Network.setCookie", {
            "name": name, "value": value, "url": self.started["url"],
            "httpOnly": True, "sameSite": "Strict",
        })
        self.devtools.call("Page.navigate", {"url": self.started["url"]})
        self._wait_for_shell()
        self._wait("location.hash === '#/home'", "Studio did not open on Home")

    def _go(self, route, ready, message):
        self.devtools.evaluate("location.hash = %s" % json.dumps(route))
        self._wait(ready, message)

    def test_installed_summary_is_read_only_keyboard_reachable_and_reflows_at_phone_width(self):
        """The Hub's installed-system summary now lives on Baseline, its run list on Evals and its
        sync review on Changes; each keeps the checks it had on the Hub."""
        self._open()
        self._go("#/baseline", "document.querySelector('.system-summary') !== null",
                 "Baseline did not show the installed summary")
        text = self.devtools.evaluate("document.querySelector('main').textContent")
        self.assertIn("Installed system", text)
        self.assertIn("Doctor checks", text)
        self.assertIn("Projection drift", text)
        self.assertIn("Updates are never installed automatically", text)
        self.assertFalse(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].some(node => /update|sync/i.test(node.textContent))"
        ))
        self.devtools.evaluate(
            "globalThis.__overviewBody = null; fetch('/api/overview').then(r => r.text()).then(text => { globalThis.__overviewBody = text; })"
        )
        self._wait("globalThis.__overviewBody !== null", "Overview API did not answer")
        response = json.loads(self.devtools.evaluate("globalThis.__overviewBody"))
        self.assertEqual(response["commands"]["doctor"], "citizen doctor")
        self.assertEqual(response["commands"]["diff"], "citizen diff")
        self.assertEqual(response["commands"]["catalog"], "citizen catalog")
        self.assertFalse(self.devtools.evaluate("[...document.querySelectorAll('details')].some(item => item.open)"))
        disclosure = self.devtools.evaluate("document.querySelector('main details') !== null")
        if disclosure:
            self.devtools.evaluate("document.querySelector('main details').querySelector('summary').focus()")
            self.devtools.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
            self.devtools.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
            self._wait("document.querySelector('main details').open", "Details did not expand from keyboard")
            self.assertNotEqual(self.devtools.evaluate("getComputedStyle(document.activeElement).outlineWidth"), "0px")
        # Sections are divided by a single hairline, never boxed as cards.
        self.assertEqual(self.devtools.evaluate("getComputedStyle(document.querySelector('.system-summary')).borderTopStyle"), "solid")
        self.assertEqual(self.devtools.evaluate("getComputedStyle(document.querySelector('.system-summary')).borderLeftStyle"), "none")

        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 320, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.assertTrue(self.devtools.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.devtools.evaluate("document.querySelector('main a').focus()")
        focus = self.devtools.evaluate(
            "({tag:document.activeElement.tagName,width:getComputedStyle(document.activeElement).outlineWidth})"
        )
        self.assertEqual(focus["tag"], "A")
        self.assertNotEqual(focus["width"], "0px")
        self.devtools.call("Emulation.clearDeviceMetricsOverride")

        # The Hub's recent runs are the run history on Evals' Experiments tab.
        self._go("#/evals", "document.querySelector('main')?.textContent.includes('Run history')",
                 "Evals did not show the run history")
        self.assertIn("Run history", self.devtools.evaluate("document.querySelector('main').textContent"))

        # The Hub's "Review sync before applying" link is Home's drift item, /changes#governed-sync.
        self._go("#/changes#governed-sync", "document.querySelector('#governed-sync') !== null",
                 "Governed sync review did not open")
        sync_text = self.devtools.evaluate("document.querySelector('#governed-sync').textContent")
        self.assertIn("citizen sync --dry-run", sync_text)
        self.assertNotIn("Apply reviewed sync", sync_text)
        self.devtools.evaluate("document.querySelector('#governed-sync input[type=checkbox]').click()")
        self._wait(
            "document.querySelector('#governed-sync').textContent.includes('Apply reviewed sync')",
            "Reviewed sync command was not revealed",
        )
        sync_text = self.devtools.evaluate("document.querySelector('#governed-sync').textContent")
        self.assertIn("citizen sync", sync_text)
        self.assertIn("Copying does not run sync", sync_text)

if __name__ == "__main__":
    unittest.main()
