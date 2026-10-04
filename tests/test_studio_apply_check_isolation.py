# SPDX-License-Identifier: MIT
"""A sibling run's temporary check checkout never disturbs this run's apply checks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import drafts  # noqa: E402

import test_studio_draft_apply as apply_tests  # noqa: E402


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


class ForeignCheckCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.config["primitive_roots"] = []
        self.revision = _git("rev-parse", "HEAD").stdout.strip()

    def _plant_foreign(self) -> Path:
        """What another worktree's suite leaves registered while its own checks run."""
        temporary = tempfile.mkdtemp(prefix="studio-apply-check-")
        self.addCleanup(shutil.rmtree, temporary, True)
        foreign = Path(os.path.realpath(temporary)) / "checkout"
        added = _git("worktree", "add", "--detach", "--quiet", str(foreign), self.revision)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.addCleanup(_git, "worktree", "prune")
        self.addCleanup(_git, "worktree", "remove", "--force", str(foreign))
        return foreign

    def test_a_foreign_check_checkout_planted_mid_run_is_ignored(self):
        planted = []
        original = draft_apply._checks

        def checks_with_a_sibling(checkout, mapped):
            planted.append(self._plant_foreign())
            return original(checkout, mapped)

        with mock.patch.object(draft_apply, "_checks", checks_with_a_sibling), \
                apply_tests.own_check_checkouts() as made:
            checks = draft_apply.checks_for(ROOT, ROOT, self.config, self.revision)
        self.assertIn(checks["status"], ("passed", "failed"))
        foreign = planted[0].resolve()
        # The sibling's checkout is visible to a whole-repository listing...
        self.assertIn(foreign, apply_tests.registered_worktrees())
        # ...but the test's own-checkout check counts only what this run made, and passes.
        self.assertEqual(len(made), 1, made)
        self.assertNotEqual(made[0], foreign)
        self.assertNotIn(made[0], apply_tests.registered_worktrees())
        self.assertFalse(made[0].exists())
        # The production path removed only its own checkout and left the sibling's alone.
        self.assertTrue(foreign.is_dir())

    def test_draft_listing_skips_a_foreign_check_checkout(self):
        foreign = self._plant_foreign().resolve()
        listed = drafts.list_drafts(ROOT)
        self.assertNotIn(str(foreign), json.dumps(listed))
        with self.assertRaises(drafts.DraftError):
            drafts.find(ROOT, "no-such-draft-" + foreign.parent.name)


if __name__ == "__main__":
    unittest.main()
