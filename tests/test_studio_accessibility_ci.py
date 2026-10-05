# SPDX-License-Identifier: MIT
"""The rendered accessibility checks run in CI with Chrome required, not skipped when it is missing."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import studio_e2e_support as e2e_support
import test_studio_accessibility_browser as accessibility

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "studio-e2e.yml"


class AccessibilityInCiTests(unittest.TestCase):
    def test_the_studio_e2e_job_runs_the_accessibility_module_opted_in(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = text[text.index("- name: Accessibility rule set and keyboard walk"):]
        self.assertIn("run: python3 -m unittest -v test_studio_accessibility_browser", step)
        self.assertIn('STUDIO_E2E: "1"', step)
        self.assertIn("google-chrome --version", text[:text.index("- name: Accessibility")])

    def test_opted_in_a_missing_chrome_fails_instead_of_skipping(self):
        case = accessibility.StudioAccessibilityBrowserTests("test_tab_reaches_every_route_control_with_visible_focus_and_no_trap")
        with mock.patch.dict(os.environ, {e2e_support.OPT_IN_ENV: "1"}), \
                mock.patch.object(accessibility.browser_support, "_chrome", return_value=None):
            with self.assertRaises(AssertionError):
                case.setUp()

    def test_not_opted_in_a_missing_chrome_skips(self):
        case = accessibility.StudioAccessibilityBrowserTests("test_tab_reaches_every_route_control_with_visible_focus_and_no_trap")
        environment = {key: value for key, value in os.environ.items() if key != e2e_support.OPT_IN_ENV}
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(accessibility.browser_support, "_chrome", return_value=None):
            with self.assertRaises(unittest.SkipTest):
                case.setUp()


if __name__ == "__main__":
    unittest.main()
