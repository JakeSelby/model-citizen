# SPDX-License-Identifier: MIT
"""The free-suite launcher stays usable at desktop and phone widths."""
import unittest

import test_studio_overview_browser as overview_support


class StudioExperimentsBrowserTests(unittest.TestCase):
    setUp = overview_support.StudioOverviewBrowserTests.setUp
    _close_devtools = overview_support.StudioOverviewBrowserTests._close_devtools
    _stop_browser = overview_support.StudioOverviewBrowserTests._stop_browser
    _stop_studio = overview_support.StudioOverviewBrowserTests._stop_studio
    _bootstrap = overview_support.StudioOverviewBrowserTests._bootstrap
    _wait_for_shell = overview_support.StudioOverviewBrowserTests._wait_for_shell
    _wait = overview_support.StudioOverviewBrowserTests._wait
    _open = overview_support.StudioOverviewBrowserTests._open

    def test_free_suite_launcher_shows_command_and_fits_phone_width(self):
        self._open()
        self.devtools.evaluate("location.hash = '#/experiments'")
        self._wait("document.querySelector('.experiment-launch') !== null",
                   "Experiments catalog did not load")
        text = self.devtools.evaluate("document.querySelector('.experiment-launch').textContent")
        self.assertIn("No model usage", text)
        self.assertIn("citizen runs start unit-tests", text)
        self.assertIn("Test scope", text)

        self.devtools.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.assertTrue(self.devtools.evaluate(
            "document.documentElement.scrollWidth <= innerWidth"))
        self.assertGreaterEqual(self.devtools.evaluate(
            "document.querySelector('.experiment-launch').getBoundingClientRect().width"), 300)


if __name__ == "__main__":
    unittest.main()
