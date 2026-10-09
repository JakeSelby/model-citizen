# SPDX-License-Identifier: MIT
"""Recoverable snapshots before a command `grade-bash` rates 2 or more.

Each test builds a real repository holding a committed file with working-tree changes and a
staged one, sends a Bash call through the hook or the dispatcher, then discards the work the way
the command would and restores it from the snapshot. Every test runs under a temporary HOME.

Run: python3 -m unittest discover -s tests -p test_snapshots.py
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home
from test_harness import harness

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle, snapshots  # noqa: E402

spec = importlib.util.spec_from_file_location("harness_grade_bash_snapshots_test",
                                              str(REPO / "policy" / "hooks" / "grade-bash.py"))
grader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grader)

GIT = shutil.which("git")
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t"}


@unittest.skipIf(GIT is None, "git is not installed")
class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        isolate_home(self.home)
        os.environ["HARNESS_STANCE_AUTONOMY"] = "execute"
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "tracked.txt").write_text("committed\n")
        (self.repo / "staged.txt").write_text("committed\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")
        (self.repo / "tracked.txt").write_text("work in progress\n")
        (self.repo / "staged.txt").write_text("staged work\n")
        self.git("add", "staged.txt")

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(IDENTITY)
        return subprocess.run([GIT, "-C", str(self.repo)] + list(args), env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()

    def refs(self):
        return self.git("for-each-ref", "--format=%(refname)", snapshots.PREFIX).split()

    def discard(self):
        """What `git reset --hard` does to the fixture, run here rather than through a shell."""
        self.git("reset", "-q", "--hard")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "committed\n")

    def assert_restored(self):
        self.assertEqual((self.repo / "tracked.txt").read_text(), "work in progress\n")
        self.assertEqual((self.repo / "staged.txt").read_text(), "staged work\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only").split(), ["staged.txt"])


class Library(Fixture):
    def test_take_writes_a_ref_and_changes_nothing(self):
        before = self.git("status", "--porcelain")
        ref = snapshots.take(str(self.repo / "."), "test")
        self.assertTrue(ref.startswith(snapshots.PREFIX))
        self.assertEqual(self.refs(), [ref])
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("stash", "list"), "")

    def test_restore_brings_back_the_tree_and_the_index(self):
        ref = snapshots.take(str(self.repo), "test")
        self.discard()
        ok, message = snapshots.restore(str(self.repo), "latest")
        self.assertTrue(ok, message)
        self.assert_restored()
        self.assertEqual(self.refs(), [ref])  # restoring keeps the snapshot

    def test_a_clean_tree_takes_none(self):
        self.git("stash", "-q")
        self.assertIsNone(snapshots.take(str(self.repo)))
        self.assertEqual(self.refs(), [])

    def test_outside_a_repository_takes_none(self):
        outside = self.home / "plain"
        outside.mkdir()
        self.assertIsNone(snapshots.take(str(outside)))
        self.assertIsNone(snapshots.take(str(self.home / "missing")))
        self.assertIsNone(snapshots.take(""))

    def test_prune_deletes_only_snapshots_past_the_age(self):
        now = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=datetime.timezone.utc)
        old = snapshots.take(str(self.repo), now=now - datetime.timedelta(days=20), keep_days=None)
        new = snapshots.take(str(self.repo), now=now - datetime.timedelta(days=1), keep_days=None)
        self.assertEqual(snapshots.prune(str(self.repo), 14, now=now), [old])
        self.assertEqual(self.refs(), [new])

    def test_taking_one_prunes_the_expired(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        snapshots.take(str(self.repo), now=now - datetime.timedelta(days=30), keep_days=None)
        fresh = snapshots.take(str(self.repo))
        self.assertEqual(self.refs(), [fresh])

    def test_a_planted_filter_or_fsmonitor_never_runs(self):
        marker = self.home / "ran"
        self.git("config", "core.fsmonitor", "touch " + str(marker))
        self.git("config", "filter.evil.clean", "touch " + str(marker))
        (self.repo / ".gitattributes").write_text("*.txt filter=evil\n")
        self.assertIsNotNone(snapshots.take(str(self.repo)))
        self.assertFalse(marker.exists())
        self.git("stash", "create")  # the control: an unhardened git does run the plant
        self.assertTrue(marker.exists())


class Hook(Fixture):
    def hook(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(self.repo), "permission_mode": "default", "session_id": "s"}
        sys.stdin = io.StringIO(json.dumps(payload))
        self.addCleanup(setattr, sys, "stdin", sys.__stdin__)
        with contextlib.redirect_stdout(io.StringIO()):
            grader.main()

    def dispatch(self, command):
        return lifecycle.dispatch("claude-code", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "session_id": "s", "permission_mode": "default", "cwd": str(self.repo)})

    def test_a_grade_three_command_is_snapshotted_and_restorable(self):
        self.hook("git reset --hard")
        self.assertEqual(len(self.refs()), 1)
        self.discard()
        self.assertTrue(snapshots.restore(str(self.repo))[0])
        self.assert_restored()

    def test_a_grade_two_command_is_snapshotted(self):
        self.assertEqual(grader.grade_text("git push origin main", str(self.repo))[0], 2)
        self.hook("git push origin main")
        self.assertEqual(len(self.refs()), 1)

    def test_a_confirmed_command_is_snapshotted_too(self):
        self.hook("HARNESS_CONFIRMED=1 git reset --hard")
        self.assertEqual(len(self.refs()), 1)

    def test_below_grade_two_takes_none(self):
        for command in ("git status", "ls", "touch new.txt", "python3 -c 'print(1)'"):
            self.assertLess(grader.grade_text(command, str(self.repo))[0], 2, command)
            self.hook(command)
            self.dispatch(command)
        self.assertEqual(self.refs(), [])

    def test_the_dispatcher_snapshots_before_it_asks(self):
        out = self.dispatch("git reset --hard")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(len(self.refs()), 1)
        self.discard()
        self.assertTrue(snapshots.restore(str(self.repo))[0])
        self.assert_restored()

    def test_outside_a_repository_the_hook_still_answers(self):
        self.repo = self.home / "plain"
        self.repo.mkdir()
        out = self.dispatch("git push --force origin main")
        self.assertIn(out["hookSpecificOutput"]["permissionDecision"], ("ask", "deny"))
        self.assertIsNone(grader.snapshot_before(3, str(self.repo), "rm -rf"))


class Command(Fixture):
    def run_cli(self, *argv):
        out = io.StringIO()
        os.environ.pop("HARNESS_QUIET", None)
        with contextlib.redirect_stdout(out):
            code = harness.main(["snapshot"] + list(argv) + ["-C", str(self.repo)])
        return code, out.getvalue()

    def test_list_restore_and_prune(self):
        self.assertEqual(self.run_cli("list"), (0, "no snapshots\n"))
        ref = snapshots.take(str(self.repo), "before grade 3 git reset --hard")
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn(ref[len(snapshots.PREFIX):], out)
        self.assertIn("before grade 3 git reset --hard", out)
        self.discard()
        self.assertEqual(self.run_cli("restore", ref[len(snapshots.PREFIX):])[0], 0)
        self.assert_restored()
        self.assertEqual(self.run_cli("restore", "20000101T000000.000000Z")[0], 1)
        code, out = self.run_cli("prune", "--days", "0")
        self.assertEqual((code, out.split()[1]), (0, "1"))
        self.assertEqual(self.refs(), [])


if __name__ == "__main__":
    unittest.main()
