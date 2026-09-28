# SPDX-License-Identifier: MIT
"""Rendered Activity keeps evidence safe, pageable and phone-width usable."""
import unittest
from unittest import mock

import test_studio_overview_browser as overview_support
from harness_core import decision as decision_contract


class StudioActivityBrowserTests(unittest.TestCase):
    setUp = overview_support.StudioOverviewBrowserTests.setUp
    _close_devtools = overview_support.StudioOverviewBrowserTests._close_devtools
    _stop_browser = overview_support.StudioOverviewBrowserTests._stop_browser
    _stop_studio = overview_support.StudioOverviewBrowserTests._stop_studio
    _bootstrap = overview_support.StudioOverviewBrowserTests._bootstrap
    _wait_for_shell = overview_support.StudioOverviewBrowserTests._wait_for_shell
    _wait = overview_support.StudioOverviewBrowserTests._wait
    _open = overview_support.StudioOverviewBrowserTests._open

    def test_activity_escapes_commands_pages_and_fits_phone_width(self):
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        writer = decision_contract._ledger()
        with mock.patch.object(writer, "enabled", return_value=True):
            for index in range(30):
                writer.record(
                    "grade-bash", "deny",
                    "<script>globalThis.activityInjected = true</script>" if index == 29
                    else "command-%02d" % index,
                    {"session_id": "session-activity"}, runtime="codex",
                    key="activity-%02d" % index, target=state / "decisions.jsonl",
                    now=1_780_000_000 + index)

        self._open()
        self.devtools.evaluate("location.hash = '#/activity'")
        self._wait("document.querySelectorAll('.activity-row').length === 25",
                   "Activity did not load its first bounded page")
        self.assertEqual(self.devtools.evaluate("globalThis.activityInjected"), None)
        self.assertIn("<script>globalThis.activityInjected = true</script>",
                      self.devtools.evaluate("document.querySelector('.code-view').textContent"))
        href = self.devtools.evaluate("document.querySelector('.activity-row a').getAttribute('href')")
        self.assertTrue(href.endswith("/library?path=policy/hooks/grade-bash.py"), href)

        self.devtools.evaluate(
            "[...document.querySelectorAll('button')].find(node => node.textContent.includes('Load older')).click()")
        self._wait("document.querySelectorAll('.activity-row').length === 30",
                   "Activity did not append the older page")
        self.assertEqual(self.devtools.evaluate("document.querySelectorAll('.activity-row').length"), 30)

        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 320, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.assertTrue(self.devtools.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.devtools.evaluate("document.querySelector('.activity-row a').focus()")
        self.assertNotEqual(
            self.devtools.evaluate("getComputedStyle(document.activeElement).outlineWidth"), "0px")

        self.devtools.evaluate("""
          globalThis.activityOriginalFetch = globalThis.fetch;
          globalThis.fetch = (input, init) => String(input) === '/api/activity'
            ? Promise.resolve(new Response(JSON.stringify({ error: 'forced failure' }), {
                status: 500, headers: { 'Content-Type': 'application/json' }
              }))
            : globalThis.activityOriginalFetch(input, init);
          const input = document.querySelector('[aria-label="Filter by session"]');
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(input, 'different-session');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          [...document.querySelectorAll('button')]
            .find(node => node.textContent.includes('Apply filters')).click();
        """)
        self._wait("document.body.textContent.includes('forced failure')",
                   "Activity did not report a failed filter request")
        self.assertEqual(self.devtools.evaluate("document.querySelectorAll('.activity-row').length"), 0)


if __name__ == "__main__":
    unittest.main()
