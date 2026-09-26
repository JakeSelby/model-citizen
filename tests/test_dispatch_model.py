# SPDX-License-Identifier: MIT
"""The single-coordinator dispatch model: what sync registers, and what it will not read.

Registration is generated from `lifecycle.registration`, one command per lifecycle event, so the
committed settings template carries no hooks block at all. An entry written there by hand is
discarded unread, which is a silent failure worth a test rather than a comment.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import shlex
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import lifecycle  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness", loader))
loader.exec_module(harness)

TEMPLATE_PATH = REPO / "claude" / "settings.template.json"
TEMPLATE = json.loads(TEMPLATE_PATH.read_text())
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())

# Each hook id the ownership manifest claims, and the policy module the coordinator reaches it by.
# Ownership still names the policies, because what a user gets is still those decisions; only the
# registration in front of them collapsed to one entry per event.
POLICIES = {
    "readonly-bash": "allow-readonly-bash.py",
    "grade-bash": "grade-bash.py",
    "filter-output": "filter-output.py",
    "plan-webfetch": "allow-plan-webfetch.py",
    "tier-spawns": "tier-agent-spawns.py",
    "stage-files": "stage-user-files.py",
    "intent-overlap": "intent-overlap.py",
    "brief-guard": "brief-guard.py",
    "plan-card": "validate-plan-card.py",
    "neutralize": "neutralize-tool-output.py",
    "session": "harness-session.py",
    "stop-gate": "stop-gate.py",
    "usage-log": "usage-log.py",
}


class TemplateTests(unittest.TestCase):
    def test_the_committed_template_registers_no_hooks(self):
        self.assertNotIn("hooks", TEMPLATE)

    def test_no_retired_per_hook_entry_survives_anywhere_in_the_file(self):
        text = TEMPLATE_PATH.read_text()
        for hid, module in POLICIES.items():
            self.assertNotIn("# harness:" + hid, text, msg=hid)
            self.assertNotIn(module, text, msg=module)

    def test_a_sync_writes_the_registration_the_coordinator_emits(self):
        merged = harness.merge_claude_settings({}, harness.runtime_template(),
                                               json.loads((REPO / "config.example.json").read_text()))
        self.assertEqual(merged["hooks"], lifecycle.registration(REPO, "claude-code")["hooks"])

    def test_the_template_is_otherwise_carried_through_unchanged(self):
        template = harness.runtime_template()
        for key, value in TEMPLATE.items():
            self.assertEqual(template[key], value, msg=key)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.hooks = harness.runtime_template()["hooks"]

    def test_one_command_per_event_and_nothing_else(self):
        self.assertEqual(sorted(self.hooks), sorted(lifecycle.EVENTS["claude-code"]))
        for event, entries in self.hooks.items():
            # SessionStart's second entry is the workspace block, with its own output cap.
            self.assertEqual(len(entries), 2 if event == "SessionStart" else 1, msg=event)
            self.assertEqual(len(entries[0]["hooks"]), 1, msg=event)
            self.assertIn("# harness:runtime-" + event.lower(),
                          entries[0]["hooks"][0]["command"], msg=event)

    def test_the_workspace_entry_is_the_same_adapter_with_its_own_argument_and_marker(self):
        first, second = (entry["hooks"][0] for entry in self.hooks["SessionStart"])
        self.assertEqual(second["command"], first["command"].split(" # ", 1)[0]
                         + " workspace # harness:runtime-sessionstart-workspace")
        self.assertEqual(second["timeout"], 10)

    def test_every_registered_command_names_a_script_that_exists(self):
        for event, entries in self.hooks.items():
            command = entries[0]["hooks"][0]["command"]
            parts = [p for p in shlex.split(command.split(" # ", 1)[0]) if not p.startswith("-")]
            script = Path(parts[-1])
            self.assertTrue(script.is_file(), msg=event + ": " + str(script))

    def test_every_owned_hook_id_still_has_its_policy_on_disk(self):
        self.assertEqual(sorted(POLICIES), sorted(OWNERSHIP["claude"]["hook_ids"]))
        for hid, module in POLICIES.items():
            self.assertTrue((REPO / "policy" / "hooks" / module).is_file(), msg=hid)
            self.assertIn(OWNERSHIP["claude"]["hook_ids"][hid]["event"], self.hooks, msg=hid)

    def test_each_event_gets_the_budget_its_slowest_policy_needs(self):
        # The stop gate runs a repository's whole test suite; SessionEnd is a native 1.5s budget.
        timeouts = dict((event, entries[0]["hooks"][0]["timeout"])
                        for event, entries in self.hooks.items())
        self.assertEqual(timeouts.pop("Stop"), 300)
        self.assertEqual(timeouts.pop("SessionEnd"), 2)
        self.assertEqual(sorted(set(timeouts.values())), [10])

    def test_hook_health_finds_every_registered_command(self):
        count, problems = harness.hook_health(harness.runtime_template())
        self.assertEqual(count, sum(len(entries) for entries in self.hooks.values()))
        self.assertEqual(problems, [])


class RoutingTests(unittest.TestCase):
    """Which policies a tool name reaches. Registration no longer carries a matcher, so the
    routing the matchers used to express is only true if dispatch does it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        config = self.home / ".config" / "agent-harness"
        config.mkdir(parents=True)
        (config / "config.json").write_text(json.dumps({"stances": {"delegation": "tiered"}}))
        self.transcript = self.home / "session.jsonl"
        self.transcript.write_text(json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5"}}) + "\n")

    def _dispatch(self, event):
        clean = dict((k, v) for k, v in os.environ.items() if not k.startswith("HARNESS_"))
        clean["HOME"] = str(self.home)
        with unittest.mock.patch.dict(os.environ, clean, clear=True):
            return lifecycle.dispatch("claude-code", event)

    def test_a_plan_mode_web_fetch_reaches_the_plan_webfetch_policy(self):
        result = self._dispatch({"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                                 "permission_mode": "plan",
                                 "tool_input": {"url": "https://docs.example.com/x"}})
        output = result["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertIn("allow-plan-webfetch", output["permissionDecisionReason"])

    def test_a_file_sent_from_outside_the_session_is_staged_inside_it(self):
        session = self.home / "repo"
        session.mkdir()
        report = self.home / "report.md"
        report.write_text("# findings\n")
        inputs = {"files": [str(report)], "caption": "the report", "status": "normal"}
        result = self._dispatch({"hook_event_name": "PreToolUse", "tool_name": "SendUserFile",
                                 "cwd": str(session), "permission_mode": "default",
                                 "tool_input": inputs})
        output = result["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", output)
        staged = Path(output["updatedInput"]["files"][0])
        self.assertTrue(staged.resolve().is_relative_to(session.resolve()), msg=staged)
        self.assertEqual(staged.read_text(), "# findings\n")
        self.assertEqual(output["updatedInput"]["caption"], "the report")
        self.assertIn("stage-user-files", result["systemMessage"])

    def test_a_bare_spawn_reaches_both_the_tier_ladder_and_the_brief_guard(self):
        result = self._dispatch({"hook_event_name": "PreToolUse", "tool_name": "Agent",
                                 "transcript_path": str(self.transcript),
                                 "tool_input": {"prompt": "Find the callers.", "description": "d"}})
        updated = result["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["model"], "sonnet")           # tier-agent-spawns
        self.assertIn("400 words", updated["prompt"])          # brief-guard
        self.assertIn("brief-guard", result["systemMessage"])


class StanceTests(unittest.TestCase):
    """A stance that once decided whether an entry was written now decides inside dispatch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan = Path(self.tmp.name) / ".agent-harness" / "plans" / "no-card.md"
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text("# A plan with no Review Card\n\nJust prose.\n", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _context(self, variant):
        event = {"hook_event_name": "PostToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": str(self.plan)},
                 "tool_response": {"filePath": str(self.plan)}}
        with unittest.mock.patch.dict(os.environ, {"HARNESS_STANCE_PLAN_CEREMONY": variant}):
            result = lifecycle.dispatch("claude-code", event)
        return result.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_the_plan_card_is_checked_under_review_card_and_not_under_light(self):
        self.assertIn("Review Card", self._context("review-card"))
        self.assertEqual(self._context("light"), "")


if __name__ == "__main__":
    unittest.main()
