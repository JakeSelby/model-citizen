# SPDX-License-Identifier: MIT
"""Module disclosures keep the inventory compact and keyboard accessible."""
import unittest

import test_studio_overview_browser as overview_support


class StudioLibraryBrowserTests(unittest.TestCase):
    setUp = overview_support.StudioOverviewBrowserTests.setUp
    _close_devtools = overview_support.StudioOverviewBrowserTests._close_devtools
    _stop_browser = overview_support.StudioOverviewBrowserTests._stop_browser
    _stop_studio = overview_support.StudioOverviewBrowserTests._stop_studio
    _bootstrap = overview_support.StudioOverviewBrowserTests._bootstrap
    _wait_for_shell = overview_support.StudioOverviewBrowserTests._wait_for_shell
    _wait = overview_support.StudioOverviewBrowserTests._wait
    _open = overview_support.StudioOverviewBrowserTests._open

    def test_grouped_modules_expand_from_keyboard_and_fit_narrow_screens(self):
        self._open()
        self.devtools.evaluate("location.hash = '#/library'")
        self._wait("document.querySelector('.library-module-summary') !== null", "Library did not load")
        self.assertFalse(self.devtools.evaluate("[...document.querySelectorAll('.library-module')].some(item => item.open)"))
        self.assertEqual(self.devtools.evaluate("document.querySelectorAll('.library-module-root').length"), 0)
        self.assertIn("Core", self.devtools.evaluate("document.querySelector('.library-group-heading').textContent"))
        self.devtools.evaluate("document.querySelector('.library-module-summary').focus()")
        self.devtools.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
        self.devtools.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        self._wait("document.querySelector('.library-module').open", "Module did not expand from keyboard")
        self.assertNotEqual(self.devtools.evaluate("getComputedStyle(document.activeElement).outlineWidth"), "0px")
        self.assertTrue(self.devtools.evaluate("document.querySelector('.library-module-body').getBoundingClientRect().height > 0"))
        self.devtools.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
        self.devtools.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        self._wait("!document.querySelector('.library-module').open", "Module did not collapse")
        self.assertFalse(self.devtools.evaluate(
            "document.querySelector('.library-module').dataset.sourcePath.startsWith('/')"))
        self.devtools.evaluate("(() => { const sourcePath = document.querySelector('.library-module').dataset.sourcePath; location.hash = '#/library?path=' + encodeURIComponent(sourcePath) + '&line=1'; })()")
        self._wait("document.querySelector('.library-source-line.focused') !== null",
                   "Library file-line link did not focus its source")
        self.assertTrue(self.devtools.evaluate("document.querySelector('.library-module').open"))
        self.assertEqual(self.devtools.evaluate("document.activeElement.classList.contains('focused')"), True)
        self.devtools.call("Emulation.setDeviceMetricsOverride", {"width": 320, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        self.devtools.evaluate("document.querySelector('.library-module-name').textContent = 'LongModuleName'.repeat(20)")
        self.assertTrue(self.devtools.evaluate("document.documentElement.scrollWidth <= innerWidth"))


if __name__ == "__main__":
    unittest.main()
