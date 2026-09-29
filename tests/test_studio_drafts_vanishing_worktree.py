# SPDX-License-Identifier: MIT
"""Draft lookup survives an unrelated worktree vanishing while the list is being read."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_harness  # noqa: F401  loads the repository library path
from harness_core.studio import drafts

REGISTERED_WORKTREES = drafts._registered_worktrees


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class VanishingWorktreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test" + "@" + "example.invalid")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "initial")
        self.draft = self.root / "draft"
        git(self.repo, "worktree", "add", "-q", "-b", "draft", str(self.draft))
        state_dir = Path(subprocess.run(
            ["git", "-C", str(self.draft), "rev-parse", "--absolute-git-dir"],
            check=True, capture_output=True, text=True).stdout.strip()) / drafts.STATE_DIR
        state_dir.mkdir()
        head = subprocess.run(["git", "-C", str(self.draft), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        (state_dir / "state.json").write_text(json.dumps({
            "schema_version": drafts.SCHEMA_VERSION, "name": "kept", "draft_id": "kept-id",
            "branch": "draft", "base_ref": "main", "base_revision": head, "revision": head,
            "created_at": "2026-09-29T00:00:00+00:00",
        }), encoding="utf-8")
        self.other = self.root / "other"
        git(self.repo, "worktree", "add", "-q", "-b", "other", str(self.other))

    def listing_then_removing_other(self):
        """List every worktree, then delete the unrelated one before it is probed."""
        listed = list(REGISTERED_WORKTREES(self.repo))
        self.assertIn(self.other, listed)
        shutil.rmtree(str(self.other))
        return iter([self.other] + [path for path in listed if path != self.other])

    def test_find_skips_a_worktree_removed_after_listing(self):
        with mock.patch.object(drafts, "_registered_worktrees",
                               lambda repo: self.listing_then_removing_other()):
            worktree, state = drafts.find(self.repo, "kept")
        self.assertEqual(worktree, self.draft)
        self.assertEqual(state["draft_id"], "kept-id")

    def test_list_skips_a_worktree_removed_after_listing(self):
        with mock.patch.object(drafts, "_registered_worktrees",
                               lambda repo: self.listing_then_removing_other()), \
                mock.patch.object(drafts, "describe",
                                  lambda repo, worktree: {"name": "kept", "draft_id": "kept-id"}):
            listed = drafts.list_drafts(self.repo)
        self.assertEqual([item["draft_id"] for item in listed], ["kept-id"])

    def give_other_a_state_then_remove_it_after_the_probe(self):
        """Other is a draft too; its checkout vanishes after `_state_path` says it has state."""
        admin = Path(subprocess.run(
            ["git", "-C", str(self.other), "rev-parse", "--absolute-git-dir"],
            check=True, capture_output=True, text=True).stdout.strip()) / drafts.STATE_DIR
        admin.mkdir()
        (admin / "state.json").write_text(json.dumps({
            "schema_version": drafts.SCHEMA_VERSION, "name": "gone", "draft_id": "gone-id",
        }), encoding="utf-8")
        real = drafts._state_path

        def probe_then_remove(worktree):
            path = real(worktree)
            if worktree == self.other:
                shutil.rmtree(str(self.other))
            return path

        return (mock.patch.object(drafts, "_registered_worktrees",
                                  lambda repo: iter([self.other, self.draft])),
                mock.patch.object(drafts, "_state_path", probe_then_remove))

    def test_find_skips_a_draft_whose_checkout_vanishes_before_its_state_is_read(self):
        listing, probe = self.give_other_a_state_then_remove_it_after_the_probe()
        with listing, probe:
            worktree, state = drafts.find(self.repo, "kept")
        self.assertEqual((worktree, state["draft_id"]), (self.draft, "kept-id"))

    def test_list_skips_a_draft_whose_checkout_vanishes_before_it_is_described(self):
        listing, probe = self.give_other_a_state_then_remove_it_after_the_probe()
        with listing, probe:
            listed = drafts.list_drafts(self.repo)
        self.assertEqual([(item["draft_id"], item["path"]) for item in listed],
                         [("kept-id", str(self.draft))])

    def test_a_present_checkout_that_git_refuses_still_fails(self):
        (self.other / ".git").write_text("gitdir: " + str(self.root / "missing") + "\n",
                                         encoding="utf-8")
        with mock.patch.object(drafts, "_registered_worktrees",
                               lambda repo: iter([self.other, self.draft])):
            with self.assertRaises(drafts.DraftError) as caught:
                drafts.find(self.repo, "kept")
        self.assertEqual(caught.exception.code, "git-failed")


if __name__ == "__main__":
    unittest.main()
