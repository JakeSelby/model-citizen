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
        # The hit counts under the `cwd` worktree, the parent's, not the target's.
        hits = intents.hits_dir(self.env)
        self.assertTrue((hits / intents.slot("S", str(self.main))).exists())
        self.assertFalse((hits / intents.slot("S", str(self.b))).exists())
        second = lifecycle.dispatch("claude-code", payload)
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")

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

    def test_a_sibling_in_its_own_worktree_still_overlaps_but_only_warns(self):
        self.claim(self.a, "shared.py")
        self.claim(self.b, "other.py")
        self.assertEqual(self.edit(self.a, "builder-a"), {})
        first = self.edit(self.b, "builder-b")
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        # The sibling's claim is never counted toward a denial (#1201).
        self.assertFalse(intents.hits_dir(self.env).exists())
        second = self.edit(self.b, "builder-b")
        self.assertNotIn("permissionDecision", second["hookSpecificOutput"])
        self.assertEqual([r["claim"] for r in self.rows()], ["shared.py", "shared.py"])
        self.assertEqual({r["same_session"] for r in self.rows()}, {True})

    def test_given_up_a_sibling_writing_into_anothers_worktree_by_absolute_path(self):
        # Documented in the module docstring: the target's worktree reads as the editor's.
        self.claim(self.a, "shared.py")
        self.assertEqual(self.edit(self.a, "builder-b"), {})

    def test_given_up_a_subagent_writing_into_the_parents_claim_in_its_worktree(self):
        # Parent and subagents share the session id, so the orchestrator's own claim passes too.
        self.claim(self.main, "shared.py")
        self.assertEqual(self.edit(self.main, "builder-b"), {})
        self.assertEqual(self.edit(self.main, "builder-b"), {})
        self.assertEqual(self.rows(), [])

    def test_another_session_id_in_the_same_process_is_not_the_subagents_own(self):
        # After `/clear` the runtime pid is unchanged but the session id is new: the pid alone
        # does not make a claim a subagent's own when its worktree comes from the target.
        self.claim(self.b, "shared.py", session="before-clear")
        first = self.edit(self.b, "builder-b")
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        second = self.edit(self.b, "builder-b")
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual({r["claimed_by"] for r in self.rows()}, {"before-clear"})

    def test_the_same_process_rule_is_unchanged_for_a_main_thread_edit(self):
        self.claim(self.b, "shared.py", session="before-clear")
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "S",
                   "cwd": str(self.b), "tool_input": {"file_path": str(self.b / "shared.py"),
                                                      "old_string": "x", "new_string": "y"}}
        self.assertEqual(lifecycle.dispatch("claude-code", payload), {})


class RuntimeScopeTests(test_intents.Base):
    """The `agent_id` rule is Claude Code's; a Codex payload keeps the `cwd` rule."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_PID"] = str(os.getpid())
        intents.claim(["shared.py"], cwd=str(self.b), session="S", pid=os.getpid(), env=self.env)

    def payload(self, tool):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "S",
                "cwd": str(self.main), "agent_id": "builder-b",
                "tool_input": {"file_path": str(self.b / "shared.py"),
                               "old_string": "x", "new_string": "y"}}

    def test_claude_code_takes_the_subagent_rule(self):
        os.environ["HARNESS_RUNTIME"] = "claude-code"
        self.assertEqual(lifecycle.dispatch("claude-code", self.payload("Edit")), {})
        self.assertEqual(lifecycle.dispatch("claude-code", self.payload("Edit")), {})

    def test_codex_keeps_the_cwd_rule_even_with_agent_id(self):
        os.environ["HARNESS_RUNTIME"] = "codex"
        # Codex carries no agent context, so the warning shows only as the logged row.
        self.assertNotIn("hookSpecificOutput", lifecycle.dispatch("codex", self.payload("edit_file")))
        self.assertEqual([r["deterministic_answer"] for r in self.rows()], ["warn"])
        hits = intents.hits_dir(self.env)
        self.assertTrue((hits / intents.slot("S", str(self.main))).exists())
        second = lifecycle.dispatch("codex", self.payload("edit_file"))
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
