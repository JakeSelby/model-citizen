# SPDX-License-Identifier: MIT
"""Rendered operational Overview behavior in the production Studio bundle."""
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
        self._wait("document.querySelector('.system-summary') !== null", "Overview did not load")

    def test_overview_is_read_only_keyboard_reachable_and_reflows_at_phone_width(self):
        self._open()
        text = self.devtools.evaluate("document.querySelector('main').textContent")
        self.assertIn("Installed system", text)
        self.assertIn("Doctor checks", text)
        self.assertIn("Projection drift", text)
        self.assertIn("Recent runs", text)
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

        self.devtools.evaluate(
            "[...document.querySelectorAll('a')].find(node => node.textContent.includes('Review sync before applying')).click()"
        )
        self._wait("document.querySelector('#governed-sync') !== null", "Governed sync review did not open")
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
