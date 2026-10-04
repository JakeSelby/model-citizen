# SPDX-License-Identifier: MIT
"""The draft leak check counts only its own run's drafts, never a sibling suite's."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import draft_support

TESTS = Path(__file__).resolve().parent
PREFIXES = ("selection-parity-", "cleanup-guard-")
# A child test process that names a draft through draft_support and leaves its branch behind.
LEAK = """
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import draft_support
name = draft_support.draft_name("selection-parity-")
subprocess.run(["git", "-C", sys.argv[2], "branch", "draft/" + name], check=True)
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


class RunTokenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "-c", "user.name=t", "-c", "user.email=t",
             "commit", "-q", "--allow-empty", "-m", "base")
        self.token = draft_support.new_run_token()

    def _branch(self, name: str) -> None:
        _git(self.repo, "branch", "draft/" + name)

    def test_a_foreign_draft_appearing_mid_run_is_ignored(self):
        self.assertEqual(draft_support.leaked_drafts(PREFIXES, self.token, self.repo), [])
        foreign = draft_support.draft_name("selection-parity-", draft_support.new_run_token())
        self._branch(foreign)
        self._branch(draft_support.draft_name("cleanup-guard-", draft_support.new_run_token()))
        self.assertIn(foreign, draft_support.draft_branches(self.repo))
        self.assertEqual(draft_support.leaked_drafts(PREFIXES, self.token, self.repo), [])

    def test_a_draft_leaked_by_this_run_is_still_reported(self):
        own = draft_support.draft_name("selection-parity-", self.token)
        self._branch(draft_support.draft_name("selection-parity-", draft_support.new_run_token()))
        self._branch(own)
        self.assertEqual(draft_support.leaked_drafts(PREFIXES, self.token, self.repo), [own])

    def test_a_child_process_leak_is_stamped_with_the_inherited_token(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        env[draft_support.RUN_TOKEN_ENV] = self.token
        subprocess.run([sys.executable, "-c", LEAK, str(TESTS), str(self.repo)],
                       env=env, check=True, capture_output=True, text=True, timeout=30)
        leaked = draft_support.leaked_drafts(PREFIXES, self.token, self.repo)
        self.assertEqual(len(leaked), 1)
        self.assertTrue(leaked[0].startswith("selection-parity-" + self.token + "-"))

    def test_the_default_token_is_this_process_run_token(self):
        own = draft_support.draft_name("cleanup-guard-")
        self._branch(own)
        self.assertEqual(draft_support.leaked_drafts(PREFIXES, repo=self.repo), [own])

    def _child_token(self, value: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, **{draft_support.RUN_TOKEN_ENV: value})
        return subprocess.run(
            [sys.executable, "-c", "import draft_support; print(draft_support.RUN_TOKEN)"],
            cwd=TESTS, env=env, capture_output=True, text=True, timeout=30,
        )

    def test_a_token_minted_by_the_parent_is_inherited(self):
        shown = self._child_token(self.token)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.strip(), self.token)

    def test_a_fixed_or_foreign_inherited_token_is_refused(self):
        for value in ("bad/token", "run1234abcd", "", "p1r0123abcd", "p%dr%s" % (1, "0" * 8)):
            with self.subTest(value=value):
                shown = self._child_token(value)
                self.assertNotEqual(shown.returncode, 0, shown.stdout)
                self.assertRegex(shown.stderr, "run token|minted by process")

    def test_an_explicit_token_is_validated(self):
        for value in ("", "bad", "run1234abcd", "p12rXYZ"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    draft_support.draft_name("x-", value)
                with self.assertRaises(ValueError):
                    draft_support.leaked_drafts(PREFIXES, value, self.repo)

    def test_tokens_carry_this_process_id_and_differ_per_call(self):
        self.assertRegex(self.token, r"^p%dr[0-9a-f]{8}$" % os.getpid())
        self.assertNotEqual(self.token, draft_support.new_run_token())

    def test_names_are_recorded_when_a_log_is_set(self):
        log = self.repo.parent / "names"
        with mock.patch.dict(os.environ, {draft_support.NAME_LOG_ENV: str(log)}):
            first = draft_support.draft_name("x-")
            second = draft_support.draft_name("y-", self.token)
        self.assertEqual(log.read_text(encoding="utf-8").split(), [first, second])


if __name__ == "__main__":
    unittest.main()
