# SPDX-License-Identifier: MIT
"""A Bash read whose glob or brace the shell expands is scanned, not taken for the harness's own.

An unexpanded `docs/l*` names no existing path, so the managed working directory decided the
command, while the shell read whatever the pattern matched: a link out of the checkout, or any
file outside it. A quoted pattern is not expanded and still counts as a reader's argument.

Run: python3 -m unittest discover tests
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_neutralize_own_files import SHAPED, hook


class GlobTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.checkout = root / "worktrees" / "branch"
        (self.checkout / "policy" / "hooks").mkdir(parents=True)
        (self.checkout / "bin").mkdir()
        (self.checkout / "policy" / "hooks" / "neutralize-tool-output.py").write_text("")
        (self.checkout / "bin" / "harness").write_text("")
        (self.checkout / "docs").mkdir()
        (self.checkout / "docs" / "a.md").write_text(SHAPED)
        self.other = root / "elsewhere"
        self.other.mkdir()
        (self.other / "notes.txt").write_text(SHAPED)
        (self.checkout / "docs" / "link.txt").symlink_to(self.other / "notes.txt")
        patcher = mock.patch.dict(os.environ, {"HARNESS_HOME": str(root / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def own(self, command):
        return hook.bash_reads_managed(command, str(self.checkout))

    def test_an_unquoted_glob_or_brace_is_scanned(self):
        for command in ("cat docs/l*", "cat docs/lin?.txt", "cat docs/[l]ink.txt",
                        "cat " + str(self.other) + "/*",
                        "cat docs/{a.md,../../../elsewhere/notes.txt}",
                        "grep -n x docs/*.md | head -5", "cat 'docs/'l*"):
            with self.subTest(command=command):
                self.assertFalse(self.own(command))

    def test_a_quoted_pattern_is_still_a_plain_read(self):
        for command in ("grep -n 'a.*b' docs/a.md", 'grep -n "x[0-9]?" docs/a.md',
                        "rg -n '{' docs/a.md"):
            with self.subTest(command=command):
                self.assertTrue(self.own(command))


if __name__ == "__main__":
    unittest.main()
