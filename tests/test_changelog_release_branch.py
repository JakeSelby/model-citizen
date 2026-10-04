"""The changelog fragment rule on a release branch: assembling fragments satisfies it without a waiver."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import changelog  # noqa: E402

spec = importlib.util.spec_from_file_location("release_notes_release_branch", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

BASE = """# Changelog

## [Unreleased]

## [0.13.1] — 2026-09-24

### Fixed

- An older fix. (#1)

## [0.13.0] — 2026-09-23

### Fixed

- The last hand-written release. (#0)
"""


def git(root, *argv):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t",
                    "-c", "commit.gpgsign=false"] + list(argv), check=True, capture_output=True)


class ReleaseBranchTests(unittest.TestCase):
    """A throwaway repository past the hand-written cut-over, whose `origin/main` is a local ref."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        (self.root / "CHANGELOG.md").write_text(BASE, encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "releasing.md").write_text("Status: 0.13.1.\n")
        fragments = self.root / changelog.DIRECTORY
        fragments.mkdir()
        (fragments / "README.md").write_text("How fragments work.\n")
        (fragments / "5.fixed.md").write_text("A fix worth announcing.\n")
        (fragments / "6.none.md").write_text("Tests only, nothing visible.\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")

    def release(self, commit=True):
        release_notes.write_changelog("0.14.0", "2026-10-01", root=self.root)
        (self.root / "docs" / "releasing.md").write_text("Status: 0.14.0.\n")
        if commit:
            git(self.root, "add", "-A")
            git(self.root, "commit", "-qm", "release")

    def test_a_release_branch_that_assembles_the_fragments_needs_no_waiver(self):
        self.release()
        self.assertEqual(changelog.findings(self.root), [])
        self.assertFalse((self.root / changelog.DIRECTORY / "6.none.md").exists(),
                         "the assembly consumes the base's waivers too")

    def test_an_uncommitted_assembly_passes_the_same_way(self):
        self.release(commit=False)
        git(self.root, "commit", "-qm", "unrelated", "--allow-empty")
        self.assertEqual(changelog.findings(self.root), [])

    def test_a_version_section_without_consumed_fragments_still_needs_one(self):
        text = BASE.replace("## [Unreleased]\n", "## [Unreleased]\n\n## [0.14.0] — 2026-10-01\n")
        (self.root / "CHANGELOG.md").write_text(text, encoding="utf-8")
        (self.root / "docs" / "releasing.md").write_text("Status: 0.14.0.\n")
        git(self.root, "commit", "-qam", "hand-written section")
        self.assertEqual(len(changelog.findings(self.root)), 1)

    def test_deleting_fragments_without_a_new_version_section_still_needs_one(self):
        (self.root / changelog.DIRECTORY / "5.fixed.md").unlink()
        (self.root / "docs" / "releasing.md").write_text("Edited.\n")
        git(self.root, "commit", "-qam", "drop a fragment")
        self.assertEqual(len(changelog.findings(self.root)), 1)

    def test_a_branch_after_the_release_merges_is_judged_again(self):
        self.release()
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "docs" / "releasing.md").write_text("Next cycle.\n")
        git(self.root, "commit", "-qam", "next")
        self.assertEqual(len(changelog.findings(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
