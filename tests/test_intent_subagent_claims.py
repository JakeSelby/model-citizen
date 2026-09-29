# SPDX-License-Identifier: MIT
"""Regression tests for #993: a subagent's edit to a path its own session claimed.

A Claude Code subagent's hook payload carries its parent's `cwd`, the orchestrator's worktree, and
an `agent_id`. The builder claims and edits inside its own worktree, so the claim's worktree must
be read from the target there, while other sessions and sibling builders still overlap.

Run: python3 -m unittest discover -s tests -p 'test_intent*'
"""
import os
import unittest

import test_intents
from test_intents import intents, lifecycle


class SubagentClaimTests(test_intents.Base):
    """Session S spawned builders; its pid is the runtime's, shared by every builder."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PID"] = str(os.getpid())

    def claim(self, worktree, *paths, session="S"):
        return intents.claim(list(paths), cwd=str(worktree), session=session, pid=os.getpid(),
                             env=self.env)

    def edit(self, worktree, agent_id, name="shared.py"):
        # The payload's `cwd` is the parent's worktree, never the builder's.
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "S",
                   "cwd": str(self.main), "agent_id": agent_id, "agent_type": "builder",
                   "tool_input": {"file_path": str(worktree / name),
                                  "old_string": "x", "new_string": "y"}}
        return lifecycle.dispatch("claude-code", payload)

    def test_a_builder_edits_its_own_claim_twice_without_an_overlap(self):
        self.claim(self.b, "shared.py")
        self.assertEqual(self.edit(self.b, "builder-b"), {})
        self.assertEqual(self.edit(self.b, "builder-b"), {})
        self.assertEqual(self.rows(), [])

    def test_the_same_edit_without_agent_id_keeps_the_cwd_rule(self):
        self.claim(self.b, "shared.py")
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "S",
                   "cwd": str(self.main), "tool_input": {"file_path": str(self.b / "shared.py"),
                                                         "old_string": "x", "new_string": "y"}}
        first = lifecycle.dispatch("claude-code", payload)
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])

    def test_another_live_sessions_claim_is_warned_then_denied(self):
        os.environ["CLAUDE_PID"] = str(os.getppid())
        self.claim(self.b, "shared.py", session="O")
        first = self.edit(self.b, "builder-b")
        self.assertNotIn("permissionDecision", first["hookSpecificOutput"])
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        second = self.edit(self.b, "builder-b")
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")
        rows = self.rows()
        self.assertEqual([r["deterministic_answer"] for r in rows], ["warn", "deny"])
        self.assertEqual({r["claimed_by"] for r in rows}, {"O"})

    def test_a_sibling_in_its_own_worktree_still_overlaps(self):
        self.claim(self.a, "shared.py")
        self.claim(self.b, "other.py")
        self.assertEqual(self.edit(self.a, "builder-a"), {})
        first = self.edit(self.b, "builder-b")
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        # The overlap counts under the builder's worktree, never under its parent's.
        hits = intents.hits_dir(self.env)
        self.assertTrue((hits / intents.slot("S", str(self.b))).exists())
        self.assertFalse((hits / intents.slot("S", str(self.main))).exists())
        second = self.edit(self.b, "builder-b")
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual([r["claim"] for r in self.rows()], ["shared.py", "shared.py"])

    def test_given_up_a_sibling_writing_into_anothers_worktree_by_absolute_path(self):
        # Documented in the module docstring: the target's worktree reads as the editor's.
        self.claim(self.a, "shared.py")
        self.assertEqual(self.edit(self.a, "builder-b"), {})


if __name__ == "__main__":
    unittest.main()
