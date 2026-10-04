# SPDX-License-Identifier: MIT
"""Regression tests for #1201: a session's own claim in another worktree warns, never refuses.

Parallel builders of one orchestrating session each work in their own worktree, so the same path
edited in two of them meets only at the merge. Another live session's claim keeps warn-then-deny,
and a claim whose worktree is gone or whose branch has landed is swept.

Run: python3 -m unittest discover -s tests -p 'test_intent*'
"""
import json
import os
import unittest

import test_intents
from test_intents import git, intents, lifecycle


class SiblingWorktreeTests(test_intents.Base):
    """Session S holds a claim in worktree A; edits land in worktree B."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PID"] = str(os.getpid())

    def claim(self, worktree, *paths, session="S"):
        return intents.claim(list(paths), cwd=str(worktree), session=session, pid=os.getpid(),
                             env=self.env)

    def edit(self, worktree, session="S", agent_id=None, cwd=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": session,
                   "cwd": str(cwd or worktree),
                   "tool_input": {"file_path": str(worktree / "shared.py"),
                                  "old_string": "x", "new_string": "y"}}
        if agent_id:
            payload["agent_id"] = agent_id
        return lifecycle.dispatch("claude-code", payload)

    def test_the_sessions_own_claim_in_another_worktree_warns_every_time(self):
        self.claim(self.a, "shared.py")
        for agent in (None, "builder-b"):
            for _ in range(2):
                answer = self.edit(self.b, agent_id=agent)["hookSpecificOutput"]
                self.assertNotIn("permissionDecision", answer)
                self.assertIn("this session's own, in another worktree",
                              answer["additionalContext"])
        rows = self.rows()
        self.assertEqual([r["deterministic_answer"] for r in rows], ["warn"] * 4)
        self.assertEqual({r["same_session"] for r in rows}, {True})
        self.assertFalse(intents.hits_dir(self.env).exists())

    def test_the_deny_variant_does_not_change_the_sibling_rule(self):
        self.config(json.dumps({"coordination": {"repeat_overlap": "deny"}}))
        self.claim(self.a, "shared.py")
        for _ in range(3):
            self.assertNotIn("permissionDecision", self.edit(self.b)["hookSpecificOutput"])

    def test_another_live_sessions_claim_is_still_warned_then_denied(self):
        os.environ["CLAUDE_PID"] = str(os.getppid())
        self.claim(self.a, "shared.py", session="O")
        first = self.edit(self.b)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", first)
        self.assertNotIn("this session's own", first["additionalContext"])
        second = self.edit(self.b)["hookSpecificOutput"]
        self.assertEqual(second["permissionDecision"], "deny")
        rows = self.rows()
        self.assertEqual([r["deterministic_answer"] for r in rows], ["warn", "deny"])
        self.assertEqual({r["same_session"] for r in rows}, {False})

    def test_another_sessions_claim_wins_over_a_siblings_on_the_same_path(self):
        self.claim(self.a, "shared.py")
        self.claim(self.main, "shared.py", session="O")
        self.edit(self.b)
        second = self.edit(self.b)["hookSpecificOutput"]
        self.assertEqual(second["permissionDecision"], "deny")
        self.assertEqual({r["claimed_by"] for r in self.rows()}, {"O"})

    def test_an_edit_into_the_claims_own_worktree_keeps_the_deny_rule(self):
        # The same file on disk: the session writes from A into B, where it claimed the path.
        self.claim(self.b, "shared.py")
        self.edit(self.b, cwd=self.a)
        second = self.edit(self.b, cwd=self.a)["hookSpecificOutput"]
        self.assertEqual(second["permissionDecision"], "deny")
        self.assertEqual({r["same_session"] for r in self.rows()}, {False})

    def test_check_before_commit_only_warns_on_a_siblings_claim(self):
        self.claim(self.a, "shared.py")
        answer, found = intents.check([str(self.b / "shared.py")], cwd=str(self.b), session="S",
                                      pid=os.getpid(), env=self.env)
        self.assertEqual((answer, len(found)), ("warn", 1))
        self.claim(self.main, "shared.py", session="O")
        answer, found = intents.check([str(self.b / "shared.py")], cwd=str(self.b), session="S",
                                      pid=os.getpid(), env=self.env)
        self.assertEqual((answer, len(found)), ("deny", 2))

    def test_the_check_command_labels_a_siblings_claim_as_a_warning(self):
        self.claim(self.a, "shared.py")
        (self.b / "shared.py").write_text("x = 2\n")
        run = test_intents.CommandTests.run_cli
        code, text = run(self, "intent", "check", "--session", "S")
        self.assertEqual(code, 0, text)
        self.assertIn("warn: `shared.py`", text)
        self.assertIn("this session's own claim in another worktree", text)


class LandedClaimTests(test_intents.Base):
    """A claim ends when its branch lands, even though its worktree and runtime live on."""

    def claim(self, worktree):
        return intents.claim(["shared.py"], cwd=str(worktree), session="S", pid=os.getpid(),
                             env=self.env)

    def held(self):
        return [c["worktree"] for c in intents.claims(self.env)]

    def test_a_live_claim_on_an_existing_branch_is_kept(self):
        self.claim(self.a)
        self.assertEqual(intents.sweep(self.env), [])
        self.assertEqual(self.held(), [str(self.a)])

    def test_a_deleted_branch_is_swept(self):
        self.claim(self.a)
        git(self.a, "checkout", "-q", "--detach")
        git(self.main, "branch", "-q", "-D", "a")
        self.assertEqual(intents.sweep(self.env), [intents.slot("S", str(self.a))])
        self.assertEqual(self.held(), [])

    def test_a_removed_worktree_is_swept(self):
        self.claim(self.a)
        git(self.main, "worktree", "remove", str(self.a))
        self.assertEqual(intents.sweep(self.env), [intents.slot("S", str(self.a))])

    def test_a_branch_whose_upstream_is_gone_is_swept(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "-q", "--bare", str(remote))
        git(self.a, "remote", "add", "origin", str(remote))
        git(self.a, "push", "-q", "-u", "origin", "a")
        self.claim(self.a)
        self.assertEqual(self.held(), [str(self.a)])
        # The merged remote branch is deleted and the deletion fetched.
        git(remote, "branch", "-q", "-D", "a")
        git(self.a, "fetch", "-q", "--prune", "origin")
        self.assertEqual(self.held(), [])

    def test_an_unborn_or_detached_branch_never_reads_as_landed(self):
        other = self.root / "unborn"
        other.mkdir()
        git(other, "init", "-q", "-b", "fresh")
        self.assertFalse(intents.landed(str(other), "fresh"))
        self.assertFalse(intents.landed(str(self.a), "HEAD"))
        self.assertFalse(intents.landed(str(self.root / "missing"), "a"))


if __name__ == "__main__":
    unittest.main()
