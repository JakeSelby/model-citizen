"""Native envelopes compose shared policy without weakening permission decisions."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import harness
from harness_core import lifecycle


class LifecycleTests(unittest.TestCase):
    def test_deny_wins_and_discards_rewrite(self):
        original = {"tool_name": "exec_command", "tool_input": {"cmd": "git push"}}
        result = lifecycle.encode_pre("codex", original, lifecycle.normalize(original), [
            {"hookSpecificOutput": {"permissionDecision": "allow", "updatedInput": {"command": "echo ok"}}},
            {"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "confirm"}}])
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", result["hookSpecificOutput"])

    def test_independent_rewrites_survive_composition(self):
        original = {"tool_name": "Agent", "tool_input": {"prompt": "work", "model": "old"}}
        result = lifecycle.encode_pre("claude-code", original, lifecycle.normalize(original), [
            {"hookSpecificOutput": {"updatedInput": {"prompt": "work", "model": "new"}}},
            {"hookSpecificOutput": {"updatedInput": {"prompt": "bounded work", "model": "old"}}}])
        self.assertEqual(result["hookSpecificOutput"]["updatedInput"], {"prompt": "bounded work", "model": "new"})

    def test_a_policys_notice_survives_composition_on_claude_code_only(self):
        original = {"tool_name": "Agent", "tool_input": {"prompt": "work"}}
        results = [{"hookSpecificOutput": {"updatedInput": {"prompt": "work", "model": "new"}}, "systemMessage": "tiered"},
                   {"systemMessage": "off the ladder"}, {}]
        encoded = lifecycle.encode_pre("claude-code", original, lifecycle.normalize(original), results)
        self.assertEqual(encoded["systemMessage"], "tiered\noff the ladder")
        self.assertEqual(encoded["hookSpecificOutput"]["updatedInput"]["model"], "new")
        # A notice with no rewrite still reaches the user, and Codex's envelope is left as it was.
        self.assertEqual(lifecycle.encode_pre("claude-code", original, lifecycle.normalize(original), results[1:]),
                         {"systemMessage": "off the ladder"})
        self.assertNotIn("systemMessage", lifecycle.encode_pre("codex", original, lifecycle.normalize(original), results))

    def test_a_top_tier_request_for_a_named_agent_is_rewritten_through_the_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / ".claude" / "agents"
            agents.mkdir(parents=True)
            (agents / "builder.md").write_text("---\nname: builder\nmodel: opus\n---\n")
            with patch.dict(os.environ, {"HOME": tmp, "HARNESS_STANCE_DELEGATION": "tiered"}):
                out = lifecycle.dispatch("claude-code", {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                    "tool_input": {"prompt": "build", "subagent_type": "builder", "model": "fable"}})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["model"], "opus")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["subagent_type"], "builder")
        self.assertIn("role that declares it", out["systemMessage"])

    def test_codex_rewrite_does_not_manufacture_shell_permission(self):
        original = {"tool_name": "exec_command", "tool_input": {"cmd": "make"}}
        self.assertEqual(lifecycle.encode_pre("codex", original, lifecycle.normalize(original), [
            {"hookSpecificOutput": {"updatedInput": {"command": "make | filter"}}}]), {})

    def test_multi_file_patch_enumerates_all_paths(self):
        event = lifecycle.normalize({"cwd": "/repo", "tool_name": "apply_patch", "tool_input": {
            "command": "*** Update File: a.md\n*** Move to: b.md\n*** Add File: c.md\n"}})
        self.assertEqual(lifecycle.patch_paths(event), ["/repo/a.md", "/repo/b.md", "/repo/c.md"])

    def test_native_patch_text_result_validates_each_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans = root / ".agent-harness/plans"
            plans.mkdir(parents=True)
            for name in ("first.md", "second.md"):
                (plans / name).write_text("Invalid plan fixture")
            event = {"hook_event_name": "PostToolUse", "cwd": directory,
                     "tool_name": "apply_patch", "tool_input": {"command":
                         "*** Begin Patch\n*** Add File: .agent-harness/plans/first.md\n"
                         "+Invalid plan fixture\n*** Add File: .agent-harness/plans/second.md\n"
                         "+Invalid plan fixture\n*** End Patch"},
                     "tool_response": "Exit code: 0\nSuccess. Updated the following files."}
            with patch.object(lifecycle, "selected", return_value="review-card"):
                result = lifecycle.dispatch("codex", event)
            context = result["hookSpecificOutput"]["additionalContext"]
            for name in ("first.md", "second.md"):
                self.assertIn("The plan file " + name + " does not meet", context)

    def test_native_write_object_result_still_validates_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".agent-harness/plans/claude.md"
            path.parent.mkdir(parents=True)
            path.write_text("Invalid plan fixture")
            event = {"hook_event_name": "PostToolUse", "tool_name": "Write",
                     "tool_input": {"file_path": str(path)},
                     "tool_response": {"filePath": str(path), "type": "create"}}
            with patch.object(lifecycle, "selected", return_value="review-card"):
                result = lifecycle.dispatch("claude-code", event)
            self.assertIn("The plan file claude.md does not meet",
                          result["hookSpecificOutput"]["additionalContext"])

    def test_delegation_off_denies_both_native_envelopes(self):
        with patch.object(lifecycle, "selected", return_value="off"):
            for runtime, name, inputs in (("codex", "spawn_agent", {"message": "work"}),
                                           ("claude-code", "Agent", {"prompt": "work"})):
                result = lifecycle.dispatch(runtime, {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inputs})
                self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_registration_has_one_coordinator_per_event(self):
        for runtime, events in (("codex", 5), ("claude-code", 8)):
            hooks = lifecycle.registration(Path("/fixture with spaces"), runtime)["hooks"]
            self.assertEqual(len(hooks), events)
            for event, entries in hooks.items():
                # SessionStart carries a second entry, the workspace block, with its own output cap.
                self.assertEqual(len(entries), 2 if event == "SessionStart" else 1, event)
                self.assertTrue(all(len(entry["hooks"]) == 1 for entry in entries), event)
            self.assertIn("harness:" + lifecycle.WORKSPACE_MARKER, hooks["SessionStart"][1]["hooks"][0]["command"])

    def test_codex_transcript_cumulative_usage_and_unknown_metrics(self):
        module = lifecycle.load("usage-log")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            rows = [{"type": "session_meta", "payload": {"id": "fixture", "cli_version": "fixture-v1"}},
                    {"type": "turn_context", "payload": {"model": "fixture-model"}}]
            for total in (100, 150):
                rows.append({"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "total_token_usage": {"input_tokens": total, "cached_input_tokens": 50, "output_tokens": 20}}}})
            rows += [{"type": "response_item", "payload": {"type": "function_call", "call_id": "one",
                      "name": "exec_command", "arguments": json.dumps({"cmd": "git status"})}}]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = module.scan(path)
            self.assertEqual(result["input"], 100)
            self.assertEqual(result["output"], 20)
            self.assertIsNone(result["cache_write"])
            self.assertEqual(result["runtime"], "codex")
            self.assertNotIn("rules_error", result)
            path.write_text(json.dumps(rows[0]) + "\n")
            self.assertIsNone(module.scan(path)["input"])

    def test_usage_lock_contention_refuses_overwrite(self):
        module = lifecycle.load("usage-log")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            path.write_text("existing\n")
            path.with_name("usage.jsonl.lock").touch()
            with patch.object(module.time, "sleep"), self.assertRaisesRegex(RuntimeError, "lock unavailable"):
                module.upsert({"session_id": "fixture"}, path)
            self.assertEqual(path.read_text(), "existing\n")
