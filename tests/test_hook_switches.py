# SPDX-License-Identifier: MIT
"""Every lifecycle hook has an id a selection can switch off, and the core ones need an acknowledgement.

Run: python3 -m unittest discover tests
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from isolation import isolate_home
from test_harness import harness

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import catalog, lifecycle  # noqa: E402

spec = importlib.util.spec_from_file_location("harness_posture_hook_switches", str(REPO / "policy/hooks/posture.py"))
posture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(posture)

RUNTIMES = ("claude-code", "codex")
LIBRARIES = {"adherence", "decisions", "posture", "pricing", "telemetry", "rule-detectors", "otel-headers", "filter-lines"}
# Loaded for real: the resolver, the decision log, and the grader the inline Bash logic reads.
REAL = {"posture", "decisions", "grade-bash"}
FORCE_PUSH = "git push --force origin main"


def pre(tool, **inputs):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inputs, "session_id": "s"}


def post(tool, **inputs):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": inputs, "session_id": "s",
            "tool_response": {}}


# The event that reaches each module through `invoke`, and the runtimes that raise it for that module.
INVOKED = {
    "allow-plan-webfetch": (pre("WebFetch", url="https://example.com/"), RUNTIMES),
    "brief-guard": (pre("Agent", prompt="work"), RUNTIMES),
    "filter-output": (pre("Bash", command="ls"), RUNTIMES),
    "harness-session": ({"hook_event_name": "SessionStart", "session_id": "s"}, RUNTIMES),
    "neutralize-tool-output": (post("Read", file_path="/tmp/x"), RUNTIMES),
    "stage-user-files": (pre("SendUserFile", files=["/tmp/x"]), ("claude-code",)),
    "stop-gate": ({"hook_event_name": "Stop", "session_id": "s"}, RUNTIMES),
    "tier-agent-spawns": (pre("Agent", prompt="work"), ("claude-code",)),
    "usage-feed": ({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "hi"}, ("claude-code",)),
    "usage-log": ({"hook_event_name": "SessionEnd", "session_id": "s"}, RUNTIMES),
    "validate-plan-card": (post("Write", file_path="/tmp/plan.md"), RUNTIMES),
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        isolate_home(self.home)
        os.environ["HARNESS_STANCE_DELEGATION"] = "tiered"
        os.environ["HARNESS_STANCE_PLAN_CEREMONY"] = "review-card"
        os.environ["HARNESS_STANCE_AUTONOMY"] = "execute"
        self.cwd = self.home / "work"
        self.cwd.mkdir()

    def configure(self, hooks, acknowledged=True):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"hooks": hooks}
        if acknowledged:
            data[posture.CORE_ACK] = True
        path.write_text(json.dumps(data))

    def dispatch(self, runtime, event):
        """`(loaded module names, policy results before encoding)` for one event."""
        loaded, captured, real_load, real_encode = [], [], lifecycle.load, lifecycle.encode_pre

        def load(name):
            loaded.append(name)
            return real_load(name) if name in REAL else types.SimpleNamespace(main=lambda *args: None)

        def encode(runtime_, original, normalized, results):
            captured.extend(results)
            return real_encode(runtime_, original, normalized, results)

        with patch.object(lifecycle, "load", load), patch.object(lifecycle, "encode_pre", encode):
            lifecycle.dispatch(runtime, dict(event, cwd=str(self.cwd)))
        return loaded, captured


def decisions(results):
    return [r.get("hookSpecificOutput", {}).get("permissionDecision") for r in results]


class EveryIdSwitchesOff(Fixture):
    def test_each_invoked_module_is_not_loaded_while_its_id_is_off_on_both_runtimes(self):
        for hook, (event, raised) in sorted(INVOKED.items()):
            for runtime in RUNTIMES:
                with self.subTest(hook=hook, runtime=runtime):
                    self.configure({hook: "on"})
                    loaded, _ = self.dispatch(runtime, event)
                    # The control: the filter bites only if the module runs while it is on.
                    self.assertEqual(hook in loaded, runtime in raised)
                    self.configure({hook: "off"})
                    loaded, _ = self.dispatch(runtime, event)
                    self.assertNotIn(hook, loaded)

    def test_grade_bash_off_asks_nothing_and_logs_nothing_on_both_runtimes(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({})
                self.assertTrue({"ask", "deny"} & set(decisions(self.dispatch(runtime, pre("Bash", command=FORCE_PUSH))[1])))
                self.configure({"grade-bash": "off"})
                with patch.object(lifecycle, "log_bash_decision") as logged:
                    _, results = self.dispatch(runtime, pre("Bash", command=FORCE_PUSH))
                self.assertFalse({"ask", "deny"} & set(decisions(results)))
                logged.assert_not_called()
                with patch.object(lifecycle, "log_bash_outcome") as outcome:
                    self.dispatch(runtime, post("Bash", command=FORCE_PUSH))
                outcome.assert_not_called()

    def test_allow_readonly_bash_off_withdraws_the_read_only_allow_on_both_runtimes(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({})
                self.assertIn("allow", decisions(self.dispatch(runtime, pre("Bash", command="ls"))[1]))
                self.configure({"allow-readonly-bash": "off"})
                self.assertNotIn("allow", decisions(self.dispatch(runtime, pre("Bash", command="ls"))[1]))
                # Grading is its own id and still asks.
                _, results = self.dispatch(runtime, pre("Bash", command=FORCE_PUSH))
                self.assertTrue({"ask", "deny"} & set(decisions(results)))

    def test_both_bash_ids_off_never_loads_the_grader(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({"grade-bash": "off", "allow-readonly-bash": "off"})
                loaded, results = self.dispatch(runtime, pre("Bash", command=FORCE_PUSH))
                self.assertNotIn("grade-bash", loaded)
                self.assertEqual([d for d in decisions(results) if d], [])

    def test_tier_agent_spawns_off_keeps_the_constrained_role_deny_on_both_runtimes(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                # A session per runtime: a refusal is remembered, and a remembered brief is refused.
                session = "role-" + runtime
                # The control: a band worker is not a constrained role, so the deny bites on the role.
                self.configure({"tier-agent-spawns": "off"})
                self.assertNotIn("deny", decisions(self.dispatch(runtime, dict(pre(
                    "Agent", prompt="look around", subagent_type="worker-a"), session_id=session))[1]))
                loaded, results = self.dispatch(runtime, dict(
                    pre("Agent", prompt="look around", subagent_type="gatherer"), session_id=session))
                self.assertIn("deny", decisions(results))
                self.assertNotIn("tier-agent-spawns", loaded)

    def test_tier_agent_spawns_off_keeps_the_marker_deny_on_both_runtimes(self):
        spawn = pre("Agent", prompt="harness-role: reviewer\nReview the diff at /tmp/d.patch.")
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({"tier-agent-spawns": "off"})
                self.assertIn("deny", decisions(self.dispatch(runtime, spawn)[1]))

    def test_tier_agent_spawns_off_keeps_the_evasion_deny_on_both_runtimes(self):
        brief = "Find every caller of load() in the repository and list each one with its file."
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({"tier-agent-spawns": "off"})
                session = "evasion-" + runtime
                self.assertNotIn("deny", decisions(self.dispatch(runtime, dict(
                    pre("Agent", prompt=brief), session_id=session))[1]))
                self.assertIn("deny", decisions(self.dispatch(runtime, dict(
                    pre("Agent", prompt=brief, subagent_type="gatherer"), session_id=session))[1]))
                # The same brief with the role name dropped is refused as the same work.
                self.assertIn("deny", decisions(self.dispatch(runtime, dict(
                    pre("Agent", prompt=brief), session_id=session))[1]))

    def test_tier_agent_spawns_off_still_consults_the_framework_classifier(self):
        refusal = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "r"}}
        self.configure({"tier-agent-spawns": "off"})
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                with patch.object(lifecycle, "framework_deny", return_value=refusal) as framed:
                    self.assertIn("deny", decisions(self.dispatch(runtime, pre("Agent", prompt="work"))[1]))
                framed.assert_called_once()

    def test_tier_agent_spawns_off_keeps_the_workflow_launch_deny_on_both_runtimes(self):
        named = "await agent('Review the diff at /tmp/d.patch.', { label: 'named', agentType: 'reviewer' });\n"
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({"tier-agent-spawns": "off"})
                self.assertIn("deny", decisions(self.dispatch(runtime, pre("Workflow", script=named))[1]))

    def test_tier_agent_spawns_off_drops_only_the_integration_notice(self):
        notice = {"systemMessage": "notice"}
        for state, calls in (("on", 1), ("off", 0)):
            with self.subTest(state=state):
                self.configure({"tier-agent-spawns": state})
                with patch.object(lifecycle, "descriptor_notice", return_value=notice) as noticed:
                    self.dispatch("claude-code", pre("Agent", prompt="work"))
                self.assertEqual(noticed.call_count, calls)

    def test_delegation_off_still_denies_with_tier_agent_spawns_off(self):
        os.environ["HARNESS_STANCE_DELEGATION"] = "off"
        self.configure({"tier-agent-spawns": "off"})
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.assertIn("deny", decisions(self.dispatch(runtime, pre("Agent", prompt="work"))[1]))

    def test_plan_allow_tools_is_keyed_to_allow_readonly_bash(self):
        with patch.object(lifecycle, "investigating", return_value=True), \
                patch.object(lifecycle, "plan_allowed_tool", return_value=True):
            self.configure({})
            self.assertIn("allow", decisions(self.dispatch("claude-code", pre("mcp__docs__search"))[1]))
            self.configure({"allow-readonly-bash": "off"})
            self.assertNotIn("allow", decisions(self.dispatch("claude-code", pre("mcp__docs__search"))[1]))

    def test_plan_allow_tools_for_a_workflow_is_keyed_to_allow_readonly_bash(self):
        launch = pre("Workflow", script="await agent('Look around.', { agentType: 'worker-a' });\n")
        with patch.object(lifecycle, "investigating", return_value=True), \
                patch.object(lifecycle, "plan_allowed_tool", return_value=True):
            self.configure({})
            self.assertIn("allow", decisions(self.dispatch("claude-code", launch)[1]))
            self.configure({"allow-readonly-bash": "off"})
            self.assertNotIn("allow", decisions(self.dispatch("claude-code", launch)[1]))

    def test_a_core_hook_off_without_the_acknowledgement_keeps_running(self):
        self.configure({"stop-gate": "off"}, acknowledged=False)
        loaded, _ = self.dispatch("claude-code", {"hook_event_name": "Stop", "session_id": "s"})
        self.assertIn("stop-gate", loaded)

    def test_a_selection_that_will_not_resolve_leaves_every_hook_on(self):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        loaded, _ = self.dispatch("codex", {"hook_event_name": "Stop", "session_id": "s"})
        self.assertIn("stop-gate", loaded)


class CoreAcknowledgement(Fixture):
    def selection(self, strict=True, **files):
        env = {"HARNESS_HOME": str(self.home)}
        for variable, data in files.items():
            path = self.home / (variable + ".json")
            path.write_text(json.dumps(data))
            env[variable] = str(path)
        return posture.selection(env, strict=strict)

    def test_each_core_hook_off_is_refused_from_every_layer_without_the_acknowledgement(self):
        for hook in posture.CORE_HOOKS:
            for variable in ("HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG"):
                with self.subTest(hook=hook, layer=variable):
                    self.configure({}, acknowledged=False)
                    with self.assertRaisesRegex(ValueError, "core hook " + hook + " off"):
                        self.selection(**{variable: {"hooks": {hook: "off"}}})
                    resolved = self.selection(strict=False, **{variable: {"hooks": {hook: "off"}}})
                    self.assertEqual(resolved["hooks"][hook], "on")
                    self.configure({}, acknowledged=True)
                    self.assertEqual(self.selection(**{variable: {"hooks": {hook: "off"}}})["hooks"][hook], "off")

    def test_a_project_file_cannot_carry_the_acknowledgement_itself(self):
        self.configure({}, acknowledged=False)
        with self.assertRaisesRegex(ValueError, posture.CORE_ACK):
            self.selection(HARNESS_PROJECT_CONFIG={posture.CORE_ACK: True, "hooks": {"grade-bash": "off"}})

    def test_a_user_configuration_that_is_not_an_object_carries_no_acknowledgement(self):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1]")
        self.assertEqual(self.selection()["hooks"]["grade-bash"], "on")
        with self.assertRaisesRegex(ValueError, "core hook grade-bash off"):
            self.selection(HARNESS_PROJECT_CONFIG={"hooks": {"grade-bash": "off"}})
        resolved = self.selection(strict=False, HARNESS_PROJECT_CONFIG={"hooks": {"grade-bash": "off"}})
        self.assertEqual(resolved["hooks"]["grade-bash"], "on")

    def test_a_hook_that_is_not_core_needs_no_acknowledgement(self):
        self.configure({"validate-plan-card": "off"}, acknowledged=False)
        self.assertEqual(self.selection()["hooks"]["validate-plan-card"], "off")

    def test_config_set_refuses_grade_bash_off_until_acknowledged_then_a_grade_3_command_passes(self):
        with self.assertRaisesRegex(SystemExit, "core_switches_acknowledged"):
            harness.config_set("hooks.grade-bash", "off")
        self.assertFalse(harness.config_path().exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(harness.config_set("core_switches_acknowledged", "true"), 0)
            self.assertEqual(harness.config_set("hooks.grade-bash", "off"), 0)
        self.assertEqual(json.loads(harness.config_path().read_text())["hooks"]["grade-bash"], "off")
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                out = lifecycle.dispatch(runtime, dict(pre("Bash", command=FORCE_PUSH), cwd=str(self.cwd)))
                self.assertNotIn(out.get("hookSpecificOutput", {}).get("permissionDecision"), ("ask", "deny"))

    def test_config_set_refuses_withdrawing_the_acknowledgement_while_a_core_hook_is_off(self):
        with redirect_stdout(io.StringIO()):
            harness.config_set("core_switches_acknowledged", "true")
            harness.config_set("hooks.stop-gate", "off")
        before = harness.config_path().read_text()
        with self.assertRaisesRegex(SystemExit, "stop-gate"):
            harness.config_set("core_switches_acknowledged", "false")
        self.assertEqual(harness.config_path().read_text(), before)

    def test_config_set_refuses_an_unknown_hook_id_and_a_library(self):
        for unit in ("no-such-hook", "posture"):
            with self.subTest(unit=unit), self.assertRaisesRegex(SystemExit, "no unit"):
                harness.config_set("hooks." + unit, "off")


class IdMap(unittest.TestCase):
    def test_every_policy_module_is_a_hook_id_or_a_library(self):
        modules = {path.stem for path in (REPO / "policy" / "hooks").glob("*.py")}
        self.assertEqual(modules - LIBRARIES, set(catalog.HOOK_IDS))
        self.assertFalse(LIBRARIES & set(catalog.HOOK_IDS))

    def test_the_core_ids_agree_between_the_catalog_and_the_hook_resolver(self):
        self.assertEqual(tuple(catalog.CORE_HOOKS), posture.CORE_HOOKS)
        self.assertTrue(set(posture.CORE_HOOKS) <= set(catalog.HOOK_IDS))

    def test_the_catalog_emits_kind_hooks_with_every_id(self):
        items = [x for x in catalog.catalog(REPO)["primitives"] if x["kind"] == "hooks"]
        self.assertEqual([x["id"] for x in items], list(catalog.HOOK_IDS))
        self.assertEqual({x["id"] for x in items if x["core"]}, set(catalog.CORE_HOOKS))
        self.assertTrue(all(x["source"] == "policy/hooks/" + x["id"] + ".py" for x in items))

    def test_the_selection_lists_every_hook_id_and_each_declares_a_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            result = posture.selection({"HARNESS_HOME": temp}, config={}, root=REPO)
        self.assertEqual(sorted(result["hooks"]), sorted(catalog.HOOK_IDS))
        declared = json.loads((REPO / "policy" / "hooks" / "manifests.json").read_text())["hooks"]
        self.assertEqual(sorted(declared), sorted(catalog.HOOK_IDS))

    def test_both_adapters_dispatch_through_the_one_lifecycle_module(self):
        for runtime in RUNTIMES:
            text = (REPO / "adapters" / runtime / "hook.py").read_text()
            self.assertIn("from harness_core.lifecycle import main", text)



class WorkspaceEntry(Fixture):
    """The workspace block answers its own SessionStart entry, and its id switches it off."""

    def run_entry(self, runtime):
        loaded, real_load = [], lifecycle.load

        def load(name):
            loaded.append(name)
            return real_load(name) if name in REAL else types.SimpleNamespace(main=lambda *args: None)

        event = {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(self.cwd)}
        with patch.object(lifecycle, "load", load):
            lifecycle.workspace(runtime, event)
        return loaded

    def test_the_workspace_entry_runs_its_module_alone_until_switched_off_on_both_runtimes(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({"workspace-session": "on"})
                loaded = self.run_entry(runtime)
                self.assertIn("workspace-session", loaded)
                self.assertNotIn("harness-session", loaded)
                self.configure({"workspace-session": "off"})
                self.assertNotIn("workspace-session", self.run_entry(runtime))

    def test_the_plain_session_start_entry_never_loads_the_workspace_module(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.configure({})
                loaded, _ = self.dispatch(runtime, {"hook_event_name": "SessionStart", "session_id": "s"})
                self.assertIn("harness-session", loaded)
                self.assertNotIn("workspace-session", loaded)

    def test_main_routes_the_workspace_argument_and_prints_an_empty_object_for_no_answer(self):
        self.configure({"workspace-session": "off"})
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(
                {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(self.cwd)}))), \
                patch.object(lifecycle, "dispatch", side_effect=AssertionError("plain entry ran")), \
                redirect_stdout(out):
            lifecycle.main("claude-code", ["workspace"])
        self.assertEqual(out.getvalue().strip(), "{}")

    def test_bad_input_on_the_workspace_entry_prints_an_empty_answer_not_a_policy_message(self):
        for text in ("not json", "[1, 2]", ""):
            with self.subTest(text=text):
                out = io.StringIO()
                with patch.object(sys, "stdin", io.StringIO(text)), redirect_stdout(out):
                    lifecycle.main("claude-code", ["workspace"])
                self.assertEqual(out.getvalue().strip(), "{}")


if __name__ == "__main__":
    unittest.main()
