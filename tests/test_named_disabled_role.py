# SPDX-License-Identifier: MIT
"""Named native spawns honor the effective role switch even when a stale definition remains."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle


class NamedDisabledRoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def write(self, name, roles):
        path = self.home / (name + ".json")
        path.write_text(json.dumps({"roles": roles}), encoding="utf-8")
        return str(path)

    def user(self, roles):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"roles": roles}), encoding="utf-8")

    def spawn(self, runtime, role="builder", user=None, project=None, session=None, invoke=None):
        if user is not None:
            self.user(user)
        env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        if project is not None:
            env["HARNESS_PROJECT_CONFIG"] = self.write("project", project)
        if session is not None:
            env["HARNESS_SESSION_CONFIG"] = self.write("session", session)
        if runtime == "codex":
            event = {"hook_event_name": "PreToolUse", "tool_name": "spawn_agent",
                     "tool_input": {"message": "work", "agent_type": role}}
        else:
            event = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                     "tool_input": {"prompt": "work", "subagent_type": role}}
        replacement = (lambda name, event: {}) if invoke is None else invoke
        with patch.dict(os.environ, env, clear=True), patch.object(lifecycle, "invoke", replacement):
            return lifecycle.dispatch(runtime, event)

    def test_project_and_session_precedence_names_the_layer_that_switched_the_role_off(self):
        cases = (
            ({"builder": "off"}, None, None, "user"),
            ({"builder": "off"}, {"builder": "on"}, None, None),
            ({"builder": "on"}, {"builder": "off"}, None, "project"),
            ({"builder": "on"}, {"builder": "off"}, {"builder": "on"}, None),
            ({"builder": "on"}, {"builder": "on"}, {"builder": "off"}, "session"),
        )
        for runtime in ("claude-code", "codex"):
            for user, project, session, source in cases:
                with self.subTest(runtime=runtime, source=source):
                    result = self.spawn(runtime, user=user, project=project, session=session)
                    output = result.get("hookSpecificOutput", {})
                    if source is None:
                        self.assertNotEqual(output.get("permissionDecision"), "deny")
                    else:
                        self.assertEqual(output["permissionDecision"], "deny")
                        self.assertIn("switched off by the %s selection" % source,
                                      output["permissionDecisionReason"])

    def test_an_enabled_named_role_and_both_native_envelopes_remain_allowed(self):
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime):
                self.assertEqual(self.spawn(runtime, user={"builder": "on"}), {})

    def test_a_disabled_named_role_is_refused_before_routing_or_rewrites(self):
        called = []

        def invoke(name, event):
            called.append(name)
            return {}

        result = self.spawn("claude-code", project={"builder": "off"}, invoke=invoke)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(called, [])

    def test_an_unnamed_spawn_still_reaches_routing(self):
        called = []

        def invoke(name, event):
            called.append(name)
            return {}

        for runtime in ("claude-code", "codex"):
            called.clear()
            self.spawn(runtime, role=None, project={"builder": "off"}, invoke=invoke)
            self.assertIn("brief-guard", called)
            if runtime == "claude-code":
                self.assertIn("tier-agent-spawns", called)

    def test_native_and_unknown_types_are_not_harness_roles(self):
        for runtime in ("claude-code", "codex"):
            for role in ("general-purpose", "explorer", "absent-role"):
                with self.subTest(runtime=runtime, role=role):
                    self.assertEqual(self.spawn(runtime, role=role, project={role: "off"}), {})

    def test_a_custom_harness_role_is_still_enforced(self):
        root = self.home / "custom"
        (root / "roles").mkdir(parents=True)
        (root / "roles" / "custom-worker.md").write_text("---\nname: custom-worker\n---\nWork.\n")
        self.user({"custom-worker": "off"})
        path = self.home / ".config" / "agent-harness" / "config.json"
        config = json.loads(path.read_text())
        config["primitive_roots"] = [str(root)]
        path.write_text(json.dumps(config))
        result = self.spawn("codex", role="custom-worker")
        self.assertIn("switched off", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_broken_custom_role_cannot_erase_effective_role_or_delegation_switches(self):
        root = self.home / "custom"
        (root / "roles").mkdir(parents=True)
        (root / "roles" / "broken.md").write_bytes(b"\xff")
        self.user({"builder": "off"})
        path = self.home / ".config" / "agent-harness" / "config.json"
        config = json.loads(path.read_text())
        config["primitive_roots"] = [str(root)]
        path.write_text(json.dumps(config))
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime):
                result = self.spawn(runtime)
                self.assertIn("switched off by the user",
                              result["hookSpecificOutput"]["permissionDecisionReason"])
        config["stances"] = {"delegation": "off"}
        path.write_text(json.dumps(config))
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime, delegation="off"):
                result = self.spawn(runtime)
                self.assertIn("Delegation is off",
                              result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_disabled_constrained_role_reports_its_selection_first(self):
        result = self.spawn("codex", role="gatherer", project={"gatherer": "off"})
        self.assertIn("switched off by the project", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_delegation_off_precedes_a_disabled_named_role(self):
        for runtime in ("claude-code", "codex"):
            with patch.dict(os.environ, {"HARNESS_STANCE_DELEGATION": "off"}):
                selection = {"stances": {"delegation": "off"}, "roles": {"builder": "off"},
                             "role_names": {"builder"}}
                with patch.object(lifecycle, "effective_selection", return_value=selection):
                    result = self.spawn(runtime)
                self.assertIn("Delegation is off", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_one_dispatch_keeps_its_selection_and_cleans_up_on_error(self):
        before = (list(lifecycle._SELECTIONS), list(lifecycle._SWITCHES))
        outer = {"stances": {"delegation": "off"}, "roles": {}, "role_names": {"builder"}}
        def dispatch(runtime, payload):
            self.assertIs(lifecycle.effective_selection(), outer)
            with patch.object(lifecycle, "_dispatch", side_effect=ValueError("nested")):
                with self.assertRaisesRegex(ValueError, "nested"):
                    lifecycle.dispatch(runtime, payload)
            self.assertIs(lifecycle.effective_selection(), outer)
            raise ValueError("outer")
        posture = type("Posture", (), {"_user_config": staticmethod(lambda *a: {}),
                                      "selection": staticmethod(lambda **k: outer),
                                      "role_catalog": staticmethod(lambda cfg: ({"builder"}, set()))})()
        with patch.object(lifecycle, "load", return_value=posture), \
                patch.object(lifecycle, "_dispatch", side_effect=dispatch):
            with self.assertRaisesRegex(ValueError, "outer"):
                lifecycle.dispatch("codex", {})
        self.assertEqual((lifecycle._SELECTIONS, lifecycle._SWITCHES), before)

    def test_a_constrained_role_keeps_its_existing_refusal(self):
        for runtime in ("claude-code", "codex"):
            result = self.spawn(runtime, role="gatherer")
            reason = result["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn(lifecycle.CONFINEMENT_SENTENCE, reason)


if __name__ == "__main__":
    unittest.main()
