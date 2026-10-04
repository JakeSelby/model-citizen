# SPDX-License-Identifier: MIT
"""The real module editor fixture seeds its draft on a host with no Git identity at all."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO
import test_studio_browser

CASE = ("test_studio_module_editor_browser.ModuleEditorBrowserTests."
        "test_projection_truncation_notice_is_rendered")


class HostedRunnerIdentityTests(unittest.TestCase):
    def test_module_editor_fixture_commits_without_any_host_git_identity(self):
        if test_studio_browser._chrome() is None:
            self.skipTest("Chrome or Chromium is required for rendered Studio checks")
        # A hosted Linux runner: no global or system Git config, and no identity guessed from
        # the host name. Only an identity the fixture itself supplies can make the commit.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "EMAIL"))
               and key != "HARNESS_QUIET"}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.useConfigOnly",
                   GIT_CONFIG_VALUE_0="true", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-m", "unittest", CASE], cwd=str(REPO / "tests"),
                                env=env, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        self.assertNotIn("Author identity unknown", output)
        self.assertEqual(result.returncode, 0, output[-3000:])


if __name__ == "__main__":
    unittest.main()
