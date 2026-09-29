"""Run history remains usable at both supported phone widths."""
import time
import subprocess
import sys
import unittest

import test_studio_overview_browser as overview_support


class StudioRunHistoryBrowserTests(unittest.TestCase):
    setUp = overview_support.StudioOverviewBrowserTests.setUp
    _close_devtools = overview_support.StudioOverviewBrowserTests._close_devtools
    _stop_browser = overview_support.StudioOverviewBrowserTests._stop_browser
    _stop_studio = overview_support.StudioOverviewBrowserTests._stop_studio
    _bootstrap = overview_support.StudioOverviewBrowserTests._bootstrap
    _wait_for_shell = overview_support.StudioOverviewBrowserTests._wait_for_shell
    _wait = overview_support.StudioOverviewBrowserTests._wait
    _open = overview_support.StudioOverviewBrowserTests._open

    def test_history_filters_render_without_page_overflow_at_phone_widths(self):
        indexed = subprocess.run(
            [sys.executable, str(overview_support.browser_support.CLI),
             "runs", "reindex", "--json"], env=self.env, capture_output=True,
            text=True, timeout=45)
        self.assertEqual(indexed.returncode, 0, indexed.stderr)
        self._open()
        self.devtools.evaluate("location.hash = '#/experiments'")
        self._wait("document.querySelector('.run-history') !== null",
                   "Run history did not render")
        text = self.devtools.evaluate("document.querySelector('.run-history').textContent")
        self.assertIn("Run history", text)
        self.assertIn("Minimum cost", text)
        self.devtools.evaluate("document.querySelector('.run-history input').focus()")
        before = self.devtools.evaluate(
            "performance.getEntriesByType('resource').filter(item => item.name.endsWith('/api/runs/history')).length")
        refreshed = subprocess.run(
            [sys.executable, str(overview_support.browser_support.CLI),
             "runs", "reindex", "--json"], env=self.env, capture_output=True,
            text=True, timeout=45)
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        started = time.monotonic()
        self._wait(
            "performance.getEntriesByType('resource').filter(item => item.name.endsWith('/api/runs/history')).length > %d" % before,
            "Runs live event did not refresh history", attempts=60)
        self.assertLess(time.monotonic() - started, 3.1)
        self.assertTrue(self.devtools.evaluate(
            "document.activeElement === document.querySelector('.run-history input')"))
        for width in (320, 390):
            with self.subTest(width=width):
                self.devtools.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 844, "deviceScaleFactor": 1, "mobile": True,
                })
                self.assertTrue(self.devtools.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"))

        self.devtools.evaluate("""
          const input = document.querySelector('.run-history input');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(input, 'native-acceptance');
          input.dispatchEvent(new Event('input', {bubbles:true}));
        """)
        self._wait("document.querySelector('.run-history tbody a') !== null",
                   "Seeded native run did not render")
        self.devtools.evaluate("document.querySelector('.run-history tbody a').click()")
        self._wait("document.body.textContent.includes('Run detail') && document.body.textContent.includes('native-acceptance')",
                   "Run detail route did not render")
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(node => node.textContent === 'Rerun').disabled"))
        case_button = self.devtools.evaluate(
            "[...document.querySelectorAll('button')].some(node => node.textContent.includes('.'))")
        if case_button:
            self.devtools.evaluate(
                "[...document.querySelectorAll('button')].find(node => node.textContent.includes('.')).click()")
            self._wait("document.body.textContent.includes('Case history:')",
                       "Case history action did not render")
        self.assertTrue(self.devtools.evaluate(
            "[...document.querySelectorAll('button')].some(node => node.textContent.includes('Declared artifact') && !node.disabled)"))
        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(node => node.textContent.includes('Declared artifact') && !node.disabled).click()")
        self._wait("document.querySelector('.run-log') !== null || document.body.textContent.includes('Evidence unavailable')",
                   "Declared evidence action did not finish")
        self.assertTrue(self.devtools.evaluate("document.querySelector('.run-log') !== null"),
                        self.devtools.evaluate("document.body.textContent"))


if __name__ == "__main__":
    unittest.main()
