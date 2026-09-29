# SPDX-License-Identifier: MIT
"""End-to-end tests for `scripts/install.sh`.

The script is exercised against a git repository built from this working tree, not from a
published remote: a test that clones GitHub would measure the network, and a test that clones
this checkout's committed history would grade the last commit rather than the change under it.
Every run gets a disposable HOME, so nothing here can reach the real one.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "install.sh"
AUTHOR = ["-c", "user.name=t", "-c", "user.email=t" + "@" + "example.invalid"]


def git(*args, **kwargs):
    subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, **kwargs)


def source_repo(root: Path) -> Path:
    """A one-commit repository on `stable` holding every tracked file as it is right now."""
    root.mkdir(parents=True)
    listing = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                             check=True, capture_output=True, text=True).stdout
    for name in filter(None, listing.split("\0")):
        src, dst = REPO / name, root / name
        if not src.exists():           # a tracked file deleted in the working tree
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        else:
            shutil.copy2(src, dst)
    git("-C", str(root), "init", "-q", "-b", "stable")
    git("-C", str(root), "add", "-A")
    git("-C", str(root), *AUTHOR, "commit", "-qm", "chore: fixture")
    return root


class InstallerScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.source = source_repo(Path(cls.shared.name) / "source")

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.checkout = self.home / "repos" / "agent-harness"
        self.addCleanup(self.tmp.cleanup)

    def env(self, **over):
        env = without_harness_vars()
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home),
                    "HARNESS_CHECKOUT": str(self.checkout),
                    "HARNESS_REPO_URL": str(self.source),
                    "HARNESS_INSTALL_NO_HOMEBREW": "1", "HARNESS_INSTALL_NO_APPS": "1"})
        env.pop("HARNESS_QUIET", None)
        env.update(over)
        return env

    def run_script(self, **over):
        return subprocess.run(["/bin/sh", str(SCRIPT)], capture_output=True, text=True,
                              cwd=str(self.tmp.name), env=self.env(**over))

    def citizen(self, *args):
        return subprocess.run([str(self.checkout / "bin" / "citizen"), *args],
                              capture_output=True, text=True, cwd=str(self.checkout),
                              env=self.env())

    def test_a_fresh_home_is_cloned_configured_and_previewed_without_being_changed(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((self.checkout / "bin" / "citizen").exists())
        branch = subprocess.run(["git", "-C", str(self.checkout), "rev-parse", "--abbrev-ref", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "stable")
        config = self.home / ".config" / "agent-harness" / "config.json"
        self.assertTrue(config.is_file())
        self.assertIn("dry run", first.stdout)
        self.assertIn(f"{self.checkout}/bin/citizen install", first.stdout)
        self.assertIn(f"{self.checkout}/bin/citizen uninstall", first.stdout)
        # The preview is a preview: no rule links, no instruction file, no runtime settings.
        claude = self.home / ".claude"
        self.assertEqual(sorted(p.name for p in claude.iterdir()) if claude.is_dir() else [], [])
        self.assertFalse((self.home / ".codex").exists())

    def test_a_second_run_updates_in_place_and_keeps_the_configuration(self):
        self.assertEqual(self.run_script().returncode, 0)
        config = self.home / ".config" / "agent-harness" / "config.json"
        stamp = config.stat().st_mtime_ns
        marker = self.checkout / ".git" / "HEAD"
        head = marker.read_text()
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("updating", second.stdout)
        self.assertNotIn("cloning", second.stdout)
        self.assertEqual(config.stat().st_mtime_ns, stamp)
        self.assertEqual(marker.read_text(), head)

    def test_the_checkout_it_leaves_behind_still_syncs_and_uninstalls(self):
        self.assertEqual(self.run_script().returncode, 0)
        synced = self.citizen("sync")
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").is_symlink())
        removed = self.citizen("uninstall")
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((self.home / ".claude" / "rules" / "harness").exists())

    def test_a_missing_requirement_names_the_step_and_fails(self):
        empty = Path(self.tmp.name) / "empty-bin"
        empty.mkdir()
        result = self.run_script(PATH=str(empty))
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirements: git is not installed", result.stderr)
        self.assertFalse(self.checkout.exists())

    def test_an_occupied_destination_is_never_clobbered(self):
        self.checkout.mkdir(parents=True)
        (self.checkout / "notes.txt").write_text("mine\n", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a git checkout", result.stderr)
        self.assertEqual((self.checkout / "notes.txt").read_text(), "mine\n")


if __name__ == "__main__":
    unittest.main()
