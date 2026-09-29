# SPDX-License-Identifier: MIT
"""Provisioning cannot inherit Git selectors that redirect work to another repository."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_native_acceptance_cases import MODULE_FOR


class ProvisionGitEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.provision = MODULE_FOR("qualification_provision")

    def git(self, repo, *args):
        return subprocess.check_output(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t", *args],
            text=True,
        ).strip()

    def test_an_inherited_git_dir_cannot_redirect_clone_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            decoy = root / "decoy"
            out = root / "round"
            subprocess.run(["git", "init", "--quiet", "-b", "main", str(source)], check=True)
            (source / "file.txt").write_text("first\n")
            self.git(source, "add", ".")
            self.git(source, "commit", "--quiet", "-m", "first")
            requested = self.git(source, "rev-parse", "HEAD")
            (source / "file.txt").write_text("second\n")
            self.git(source, "commit", "--quiet", "-am", "second")
            source_head = self.git(source, "rev-parse", "HEAD")
            subprocess.run(["git", "clone", "--quiet", str(source), str(decoy)], check=True)
            decoy_head = self.git(decoy, "rev-parse", "HEAD")
            out.mkdir()

            with mock.patch.object(self.provision, "ROOT", source), \
                    mock.patch.dict(os.environ, {"GIT_DIR": str(decoy / ".git")}):
                target = self.provision.clone(out, requested)

            self.assertEqual(source_head, decoy_head)
            self.assertEqual(self.git(decoy, "rev-parse", "HEAD"), decoy_head)
            self.assertEqual(self.git(target, "rev-parse", "HEAD"), requested)

    def test_run_scrubs_git_selectors_and_keeps_the_intended_environment(self):
        script = ("import json, os; print(json.dumps({"
                  "'git_dir': os.environ.get('GIT_DIR'), "
                  "'work_tree': os.environ.get('GIT_WORK_TREE'), "
                  "'sentinel': os.environ.get('QUALIFICATION_SENTINEL')}))")
        inherited = {"GIT_DIR": "/decoy/.git", "GIT_WORK_TREE": "/decoy",
                     "QUALIFICATION_SENTINEL": "kept"}
        with mock.patch.dict(os.environ, inherited):
            result = self.provision.run([sys.executable, "-c", script])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"git_dir": None, "work_tree": None, "sentinel": "kept"})

    def test_git_reported_local_variables_extend_the_fallback_set(self):
        reported = subprocess.CompletedProcess([], 0, "GIT_REPORTED_SELECTOR\n", "")
        with mock.patch.object(self.provision.subprocess, "run", return_value=reported):
            names = self.provision.repository_local_variables()

        self.assertIn("GIT_REPORTED_SELECTOR", names)
        self.assertIn("GIT_DIR", names)


if __name__ == "__main__":
    unittest.main()
