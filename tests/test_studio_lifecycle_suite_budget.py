# SPDX-License-Identifier: MIT
"""Every rendered Studio suite in the release qualification gets the whole-suite time budget."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO
from test_studio_lifecycle_acceptance import MODULE


class StudioLifecycleSuiteBudgetTests(unittest.TestCase):
    def test_each_browser_suite_runs_under_the_shared_whole_suite_budget(self):
        # Nineteen module editor flows ran 383 s on a loaded Mac; 300 s timed the record out.
        self.assertGreaterEqual(MODULE.BROWSER_SUITE_TIMEOUT, 900)
        timeouts = []

        def command(*args, env=None, expected=0, timeout=30):
            timeouts.append((args, timeout))
            if "--version" in args:
                return subprocess.CompletedProcess(args, 0, "Google Chrome 154.0.0.0\n", "")
            names = MODULE.BROWSER_SUITES[args[args.index("-p") + 1]]
            lines = [name + " (fixture) ... ok" for name in names]
            output = "\n".join(lines + ["Ran {} tests in 0.1s".format(len(names)), "OK"])
            return subprocess.CompletedProcess(args, 0, output, "")

        with patch.object(MODULE, "command", side_effect=command), \
                patch.object(MODULE, "supported_tuple", return_value={"stable_major": 154}):
            MODULE.browser_flow(REPO, os.sys.executable, "/fixture/chrome")
        suites = [(args, timeout) for args, timeout in timeouts if "-p" in args]
        self.assertEqual([args[args.index("-p") + 1] for args, _ in suites],
                         list(MODULE.BROWSER_SUITES))
        self.assertEqual({timeout for _, timeout in suites}, {MODULE.BROWSER_SUITE_TIMEOUT})


if __name__ == "__main__":
    unittest.main()
