# SPDX-License-Identifier: MIT
"""The module editor browser fixture commits with its own Git identity on identity-less hosts."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import test_studio_module_editor_browser as module_editor

IDENTITY = {"GIT_AUTHOR_NAME": "Studio Qualification",
            "GIT_AUTHOR_EMAIL": "fixture" + "@" + "example.invalid",
            "GIT_COMMITTER_NAME": "Studio Qualification",
            "GIT_COMMITTER_EMAIL": "fixture" + "@" + "example.invalid"}


class ModuleEditorFixtureIdentityTests(unittest.TestCase):
    def test_in_process_seed_checkpoint_sees_the_fixture_identity_then_releases_it(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        outer = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

        def browser_setup(case):
            case.temporary = temporary
            case.home = Path(temporary.name) / "home"
            case.home.mkdir()
            case.env = dict(outer, HOME=str(case.home), **IDENTITY)

        observed = {}

        def checkpoint(*_args, **_kwargs):
            observed.update({key: os.environ.get(key) for key in IDENTITY})
            return {"revision": "seeded"}

        created = SimpleNamespace(returncode=0, stderr="",
                                  stdout=json.dumps({"revision": "base"}))
        case = module_editor.ModuleEditorBrowserTests("test_projection_truncation_notice_is_rendered")
        with mock.patch.dict("os.environ", outer, clear=True), \
                mock.patch.object(module_editor.browser_support.StudioBrowserTests, "setUp",
                                  browser_setup), \
                mock.patch.object(module_editor.subprocess, "run", return_value=created), \
                mock.patch.object(module_editor.draft_support, "register_draft_cleanup"), \
                mock.patch.object(module_editor.drafts, "checkpoint", side_effect=checkpoint):
            case.setUp()
            self.assertEqual(observed, IDENTITY)
            self.assertEqual(case.revision, "seeded")
            case.doCleanups()
            self.assertFalse(any(key in os.environ for key in IDENTITY))


if __name__ == "__main__":
    unittest.main()
