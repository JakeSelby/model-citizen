# SPDX-License-Identifier: MIT
"""Every route that discards uncommitted work grades 3, as `git checkout -- <path>` does.

A refusal of one route is only worth what the next route costs: an agent refused `git checkout
--` that then runs `git restore`, `git reset` or `: > file` loses the same work. Each route here
runs against a real repository holding a clean file, a file with working-tree changes, a staged
file and an untracked one, so the routes that ask `git status` are graded on what it says.

Run: python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from test_grade_bash import grader

library = grader.library
GIT = shutil.which("git")
# A `GIT_DIR` or `GIT_INDEX_FILE` a hook sets would point the fixture's git at the outer
# repository, so the fixture strips them as the grader does.
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

# (command, the verb the reason names). Each discards work in the fixture repository.
DISCARDS = [
    ("git checkout -- dirty.py", "git checkout --"),
    ("git checkout .", "git checkout"),
    ("git checkout dirty.py", "git checkout"),
    ("git checkout HEAD~1 dirty.py", "git checkout"),
    ("git checkout '*.py'", "git checkout"),
    ("git checkout --theirs dirty.py", "git checkout"),
    ("git checkout -p dirty.py", "git checkout"),
    ("git checkout -f main", "git checkout -f"),
    ("git restore dirty.py", "git restore"),
    ("git restore --staged --worktree dirty.py", "git restore"),
    ("git reset --hard", "git reset --hard"),
    ("git reset --merge", "git reset --merge"),
    ("git stash drop", "git stash drop"),
    ("git stash clear", "git stash clear"),
    ("git clean -f", "git clean -f"),
    ("git clean -fdx", "git clean -f"),
    ("git switch -f main", "git switch --discard-changes"),
    ("git switch --discard-changes main", "git switch --discard-changes"),
    ("git read-tree HEAD", "git read-tree"),
    ("git read-tree -m -u HEAD", "git read-tree"),
    ("git checkout-index -f -a", "git checkout-index -f"),
    ("git rm -f dirty.py", "git rm -f"),
    ("git worktree remove --force ../other", "git worktree remove --force"),
    ("git show HEAD:clean.py > clean.py", "git show >"),
    ("git cat-file -p HEAD:dirty.py > dirty.py", "git cat-file >"),
    ("rm dirty.py", "rm"),
    ("rm -f dirty.py", "rm -f"),
    ("rm -rf sub", "rm -rf"),
    # An untracked file is work no git command restores, as `git clean -f new.txt` grades.
    ("rm new.txt", "rm"),
    ("> dirty.py", "empty write to"),
    (": > dirty.py", "empty write to"),
    ("true > dirty.py", "empty write to"),
    ("cat /dev/null > dirty.py", "empty write to"),
    ("printf '' > dirty.py", "empty write to"),
    ("echo -n > dirty.py", "empty write to"),
    ("cp /dev/null dirty.py", "cp /dev/null"),
    ("truncate -s 0 dirty.py", "truncate"),
]

# Routes that lose nothing here: a branch switch, a reset that keeps the index or refuses,
# a dry run, and a delete or an empty write of a file whose content git still holds.
KEEPS = [
    "git checkout main",
    "git checkout -b topic main",
    "git switch main",
    "git stash",
    "git stash pop",
    "git reset --soft HEAD~1",
    "git reset --keep HEAD~1",
    # An index-only reset or restore: the working tree keeps every change.
    "git reset",
    "git reset HEAD staged.py",
    "git reset --mixed HEAD~1",
    "git restore --staged staged.py",
    "git restore -S staged.py",
    "git clean -n",
    "git read-tree -n HEAD",
    "git checkout-index -a",
    "git rm clean.py",
    "git rm --cached staged.py",
    "git worktree remove ../other",
    "rm clean.py",
    "rm staged.py",
    "> clean.py",
    "> new.txt",
    "cat /dev/null >> dirty.py",
    "cp /dev/null new.txt",
    "truncate -s 0 new.txt",
    "git show HEAD:dirty.py > /tmp/dirty-before.py",
]


@unittest.skipUnless(GIT, "git is not installed")
class DiscardRoutes(unittest.TestCase):
    def setUp(self):
        library._DIRTY.clear()
        self.addCleanup(library._DIRTY.clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.realpath(tmp.name)

        def git(*args):
            subprocess.run([GIT, "-C", self.repo] + list(args), check=True, env=GIT_ENV,
                           capture_output=True, stdin=subprocess.DEVNULL)

        git("init", "-q", "-b", "main")
        git("config", "user.email", "test" + "@" + "example.invalid")
        git("config", "user.name", "test")
        os.mkdir(os.path.join(self.repo, "sub"))
        for name in ("clean.py", "dirty.py", "staged.py", "sub/inner.py"):
            self.write(name, "one\n")
        git("add", ".")
        git("commit", "-qm", "init")
        self.write("dirty.py", "two\n")
        self.write("sub/inner.py", "two\n")
        self.write("staged.py", "two\n")
        git("add", "staged.py")
        self.write("new.txt", "untracked\n")

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as stream:
            stream.write(text)

    def grade(self, command):
        return grader.grade_text(command, self.repo)

    def test_every_discard_route_grades_three_and_names_its_verb(self):
        for command, verb in DISCARDS:
            with self.subTest(command=command):
                grade, named, _target, family = self.grade(command)
                self.assertEqual((grade, named, family), (3, verb, "git-discard"))

    def test_a_route_that_keeps_the_work_is_not_graded_as_a_discard(self):
        for command in KEEPS:
            with self.subTest(command=command):
                self.assertLess(self.grade(command)[0], 3)

    def test_the_reason_names_the_file_whose_work_is_lost(self):
        grade, verb, target, family = self.grade("rm -rf sub")
        self.assertEqual(target, "sub/inner.py")
        self.assertIn("discards local work", grader.reason(grade, verb, target, family, "execute"))

    def test_git_c_reads_the_working_tree_it_names(self):
        self.assertEqual(grader.grade_text("git -C %s checkout dirty.py" % self.repo, "/")[0], 3)

    def test_a_status_check_that_does_not_answer_in_time_fails_closed(self):
        timeout = subprocess.TimeoutExpired(["git"], library.GIT_STATUS_SECONDS)
        with mock.patch.object(library.subprocess, "run", side_effect=timeout):
            grade, verb, target, _family = self.grade("rm clean.py")
        self.assertEqual((grade, verb), (3, "rm"))
        self.assertIn(library.UNCHECKED, target)

    def test_each_command_line_asks_git_status_afresh(self):
        self.assertEqual(self.grade("rm clean.py")[0], 1)
        self.write("clean.py", "changed\n")
        self.assertEqual(self.grade("rm clean.py")[0], 3)
        for _ in range(library.GIT_STATUS_CALLS + 1):
            self.assertEqual(self.grade(": > new.txt")[0], 1)

    def test_outside_a_repository_nothing_is_asked_and_nothing_is_a_discard(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(grader.grade_text("rm notes.txt", plain)[0], 1)
            self.assertEqual(grader.grade_text(": > notes.txt", plain)[0], 1)


if __name__ == "__main__":
    unittest.main()
