# SPDX-License-Identifier: MIT
"""A refused draft command says why on stderr, even with HARNESS_QUIET set.

The test suite sets HARNESS_QUIET, which silences everything the CLI prints to stdout. A discard
refused because a Studio save still held the draft's writer lock therefore exited 1 with nothing
on either stream, and the cleanup's retry, which looks for the `busy` code, could not see it.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness_draft_refusal_test", str(ROOT / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness_draft_refusal_test", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

from harness_core.studio import drafts  # noqa: E402


class DraftRefusalReasonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(os.path.realpath(self.temporary.name))
        self.repo = root / "repos" / "project"
        self.repo.mkdir(parents=True)
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Test"),
                     ("config", "user.email", "test" + "@" + "example.invalid")):
            self.git(*args)
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        home = root / "home"
        config = home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"identity":{"name":"A Name"}}\n', encoding="utf-8")
        environment = mock.patch.dict(os.environ, {
            "HARNESS_HOME": str(home), "HARNESS_WORKTREE_ROOT": str(root / "worktrees"),
            "HARNESS_QUIET": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        repository = mock.patch.object(harness, "REPO", self.repo)
        repository.start()
        self.addCleanup(repository.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True)

    def run_cli(self, *argv):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = harness.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_busy_discard_under_quiet_names_the_busy_code_on_stderr(self):
        self.assertEqual(self.run_cli("draft", "create", "held", "--json")[0], 0)
        worktree, _state = drafts.find(self.repo, "held")
        with drafts._held(worktree):
            code, stdout, stderr = self.run_cli("draft", "discard", "held", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("draft discard: busy: ", stderr)

        code, _stdout, stderr = self.run_cli("draft", "discard", "held", "--json")
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(worktree.exists())

    def test_a_text_mode_refusal_says_why_on_stderr(self):
        code, stdout, stderr = self.run_cli("draft", "discard", "missing")
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(stderr, "draft discard: not-found: draft does not exist: missing\n")

    def test_json_mode_keeps_its_error_object_on_stdout_when_not_quiet(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("HARNESS_QUIET")
            code, stdout, stderr = self.run_cli("draft", "discard", "missing", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["error"]["code"], "not-found")
        self.assertIn("draft discard: not-found: ", stderr)

    def test_a_first_run_refusal_under_quiet_says_why_on_stderr(self):
        for argv in (("draft", "first-run", "bad name"), ("draft", "first-run", "bad name", "--json")):
            code, stdout, stderr = self.run_cli(*argv)
            self.assertEqual((code, stdout), (1, ""), argv)
            self.assertIn("draft first-run: invalid-name: ", stderr)

    def test_preflight_refusals_under_quiet_say_why_on_stderr(self):
        cases = {
            "apply": ("draft", "apply", "missing", "--revision", "0" * 40, "--json"),
            "rollback": ("draft", "rollback", "no-such-apply", "--json"),
            "recover": ("draft", "recover", "--json"),
        }
        for action, argv in cases.items():
            with self.subTest(action=action), \
                    mock.patch.object(harness, "person_present",
                                      side_effect=AssertionError("asked before refusing")):
                code, stdout, stderr = self.run_cli(*argv)
                self.assertEqual((code, stdout), (1, ""))
                self.assertRegex(stderr, r"^draft %s: [a-z-]+: \S" % action)


if __name__ == "__main__":
    unittest.main()
