"""No test may leave a ``draft/*`` branch in the shared repository."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import draft_support

ROOT = draft_support.ROOT
HOLD_LOCK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness_core.studio import drafts
with drafts._locked(Path(sys.argv[2])):
    print("locked", flush=True)
    sys.stdin.read()
"""
# A test that runs the real CLI's `draft create` works in the shared repository.
SHARED_CREATE = re.compile(r'\), "draft", "create"')


class DraftCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(os.path.realpath(temporary.name)) / "home"
        config = home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_bytes((ROOT / "config.example.json").read_bytes())
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("HARNESS_")}
        self.env.update({
            "HOME": str(home),
            "HARNESS_HOME": str(home),
            "HARNESS_WORKTREE_ROOT": str(Path(temporary.name) / "worktrees"),
        })
        self.name = "cleanup-guard-" + secrets.token_hex(4)
        created = subprocess.run(
            [sys.executable, str(draft_support.CLI), "draft", "create", self.name, "--json"],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        self.worktree = Path(json.loads(created.stdout)["path"])
        # Registered before the holder so it runs after the holder is released.
        self.addCleanup(self._force_discard)

    def _force_discard(self):
        if draft_support.draft_branch_exists(self.name):
            draft_support.discard_draft(self, self.name, self.env)

    def _hold_lock(self) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_LOCK, str(ROOT / "lib"), str(self.worktree)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self._release, holder)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        return holder

    @staticmethod
    def _release(holder: subprocess.Popen) -> None:
        if holder.poll() is None:
            holder.stdin.close()
            holder.wait(timeout=10)
        holder.stdout.close()

    def test_cleanup_stops_the_writer_before_discarding(self):
        holder = self._hold_lock()
        draft_support.discard_draft(self, self.name, self.env, stop=lambda: self._release(holder))
        self.assertFalse(draft_support.draft_branch_exists(self.name))
        self.assertFalse(draft_support.draft_worktree_registered(self.name))

    def test_cleanup_fails_the_test_when_the_branch_survives(self):
        self._hold_lock()
        with self.assertRaisesRegex(AssertionError, "busy"):
            draft_support.discard_draft(self, self.name, self.env, attempts=2)
        self.assertTrue(draft_support.draft_branch_exists(self.name))

    def test_every_shared_repository_draft_is_discarded_through_the_guard(self):
        unguarded = [
            path.name for path in sorted((ROOT / "tests").glob("test_*.py"))
            if SHARED_CREATE.search(path.read_text(encoding="utf-8"))
            and "draft_support." not in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(unguarded, [])


if __name__ == "__main__":
    unittest.main()
