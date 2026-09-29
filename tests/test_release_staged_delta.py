"""Release validation includes the exact staged paths a pending commit will add."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_compatibility
from harness_core import compatibility


class ReleaseStagedDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(self.root)], check=True)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t", *args],
            text=True).strip()

    def test_working_head_includes_staged_paths_but_a_commit_target_does_not(self):
        (self.root / "docs").mkdir()
        (self.root / "VERSION").write_text("1.0.0\n")
        (self.root / "docs/release.md").write_text("prior\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "prior")
        prior = self.git("rev-parse", "HEAD")

        (self.root / "docs/release.md").write_text("committed\n")
        self.git("add", "docs/release.md")
        self.git("commit", "--quiet", "-m", "presentation")
        head = self.git("rev-parse", "HEAD")
        (self.root / "VERSION").write_text("1.0.1\n")
        self.git("add", "VERSION")

        self.assertEqual(
            compatibility.changed_files(self.root, prior, "HEAD", ["."], []),
            ["VERSION", "docs/release.md"],
        )
        self.assertEqual(
            compatibility.changed_files(self.root, prior, head, ["."], []),
            ["docs/release.md"],
        )

    def test_qualification_reuse_sees_a_staged_version_change(self):
        root, data, git = test_compatibility.QualificationReuseTests.fixture(self)
        git("reset", "--soft", "HEAD~1")

        self.assertEqual(
            compatibility.qualification_reuse(root, data)["prior_version"],
            "1.2.3",
        )

    def test_staged_revert_removes_path_from_pending_release_delta(self):
        path = self.root / "runtime.py"
        path.write_text("prior\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "prior")
        prior = self.git("rev-parse", "HEAD")
        path.write_text("changed\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "runtime change")
        path.write_text("prior\n")
        self.git("add", ".")
        self.assertEqual(compatibility.changed_files(self.root, prior, "HEAD", ["."], []), [])

    def test_staged_path_names_are_not_quoted_or_stripped(self):
        (self.root / "VERSION").write_text("1.0.0\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "prior")
        prior = self.git("rev-parse", "HEAD")
        names = [" ", "docs/é.md", "docs/two\nlines.md"]
        (self.root / "docs").mkdir()
        for name in names:
            (self.root / name).write_text("new\n")
        self.git("add", ".")
        self.assertEqual(compatibility.changed_files(self.root, prior, "HEAD", ["."], []),
                         sorted(names))

    def test_pending_reuse_refuses_catalog_not_matching_index(self):
        root, data, git = test_compatibility.QualificationReuseTests.fixture(self)
        git("reset", "--soft", "HEAD~1")
        git("restore", "--staged", "compatibility/catalog.json")
        with self.assertRaisesRegex(ValueError, "differs from the staged catalog"):
            compatibility.qualification_reuse(root, data)

    def test_pending_reuse_refuses_version_not_matching_index(self):
        root, data, git = test_compatibility.QualificationReuseTests.fixture(self)
        git("reset", "--soft", "HEAD~1")
        (root / "VERSION").write_text("1.2.99\n")
        git("add", "VERSION")
        with self.assertRaisesRegex(ValueError, "staged VERSION does not match"):
            compatibility.qualification_reuse(root, data)

    def test_immutable_reuse_target_ignores_other_staged_metadata(self):
        root, data, git = test_compatibility.QualificationReuseTests.fixture(self)
        target = git("rev-parse", "HEAD")
        (root / "VERSION").write_text("1.2.99\n")
        git("add", "VERSION")
        self.assertEqual(compatibility.qualification_reuse(root, data, target=target)["prior_version"],
                         "1.2.3")

    def test_working_head_fails_closed_when_either_path_query_fails(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="failed")
        passed = subprocess.CompletedProcess([], 0, stdout="VERSION\0", stderr="")

        with patch.object(compatibility.subprocess, "run", return_value=failed):
            self.assertIsNone(compatibility.changed_files(self.root, "prior", "HEAD", ["."], ["docs"]))
        with patch.object(compatibility.subprocess, "run", side_effect=[passed, failed]):
            self.assertIsNone(compatibility.changed_files(self.root, "prior", "HEAD", ["."], ["docs"]))


if __name__ == "__main__":
    unittest.main()
