# SPDX-License-Identifier: MIT
"""A workflow script's `agent()` calls pick their own model and effort, past band routing, so the
launch reads each literal `model` and `effort` against the active cost variant. As AD-14 allows, only
a `frontier` model is refused; a value above the variant's ceiling is let through with an
`over-ceiling` row, and one the read cannot judge with an `unresolved` row.
Run: python3 -m unittest discover tests
"""
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from harness_core import lifecycle

real_load = lifecycle.load

WITHIN = ("await parallel([\n"
          "  agent('Summarise it.', { label: 'a', model: 'sonnet', effort: 'low' }),\n"
          "  agent('Plan it.', { label: 'b', model: 'claude-opus-4-1', effort: \"high\" }),\n"
          "]);\n")
FRONTIER = "await agent('Design it.', { model: 'fable' });\n"
FRONTIER_ESCAPED = 'await agent("Design it.", {"mod\\u0065l": "claude-fable-1"});\n'
FRONTIER_ASSIGNED = "const opts = {};\nopts.model = 'fable';\nawait agent('Design it.', opts);\n"
XHIGH = "await agent('Think hard.', { model: 'opus', effort: 'xhigh' });\n"
MAX = "await agent('Think harder.', { effort: `max` });\n"
COMPUTED = "await agent('Pick one.', { model: pickModel(), effort: level });\n"
SHORTHAND = "const effort = choose();\nawait agent('Pick one.', { label: 'x', effort });\n"
TEMPLATE = "const tier = 'op';\nawait agent('Pick one.', { model: `${tier}us` });\n"
UNKNOWN_MODEL = "await agent('Use another.', { model: 'gpt-9' });\n"
UNKNOWN_EFFORT = "await agent('Try it.', { effort: 'extreme' });\n"
BRACKET_ASSIGNED = "const o = {};\no['model'] = 'fable';\nawait agent('Design it.', o);\n"
BRACKET_COMPUTED = "await agent('Design it.', {['model']: 'fable'});\n"
BRACKET_JOINED = "await agent('Design it.', {[`mod` + 'el']: 'fable'});\n"
BLOCK_COMMENTED = "await agent('Design it.', { model: 'fable' /* top */ });\n"
LINE_COMMENTED = "await agent('Design it.', {\n  label: 'd',\n  model: 'fable' // best\n});\n"
PARENTHESISED = "await agent('Design it.', { model: ('fable') });\n"
AS_CONST = "await agent('Design it.', { model: 'fable' as const });\n"
ESCAPED_WITHIN = "await agent('Summarise it.', { model: 'sonn\\u0065t', effort: 'l\\x6fw' });\n"
COMMENTED_OUT = ("// never set model: 'fable'\n"
                 "/* effort: 'max' */\n"
                 "await agent('Summarise it.', { model: 'sonnet' });\n")
TYPED = ("type Options = { model: 'fable'; effort: 'max' }\n"
         "interface Wide {\n  model: 'fable'\n}\n"
         "await agent('Summarise it.', { model: 'haiku' });\n")
PROSE = ("// Pick the model and effort per task; effort === 'max' is never right here.\n"
         "if (effort === 'max') throw new Error('no');\n"
         "await agent('Explain which model is best and why effort matters.', { label: 'p' });\n")


def decision(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision")


def reason(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


class WorkflowOptionReadTests(unittest.TestCase):
    def test_literals_are_named_and_expressions_are_unresolved(self):
        named, unresolved = lifecycle.workflow_options(WITHIN)
        self.assertEqual(named, [("model", "sonnet"), ("effort", "low"),
                                 ("model", "claude-opus-4-1"), ("effort", "high")])
        self.assertEqual(unresolved, [])
        named, unresolved = lifecycle.workflow_options(COMPUTED)
        self.assertEqual(named, [])
        self.assertEqual([option for option, _ in unresolved], ["model", "effort"])

    def test_escaped_keys_assignments_and_shorthand_are_read(self):
        self.assertEqual(lifecycle.workflow_options(FRONTIER_ESCAPED)[0], [("model", "claude-fable-1")])
        self.assertEqual(lifecycle.workflow_options(FRONTIER_ASSIGNED)[0], [("model", "fable")])
        self.assertIn(("effort", "effort (shorthand)"), lifecycle.workflow_options(SHORTHAND)[1])

    def test_every_spelling_of_a_model_key_is_read(self):
        for script in (BRACKET_ASSIGNED, BRACKET_COMPUTED, BRACKET_JOINED):
            with self.subTest(script=script[:40]):
                self.assertEqual(lifecycle.workflow_options(script), ([("model", "fable")], []))

    def test_a_literal_is_whole_past_comments_parentheses_and_as_const(self):
        for script in (BLOCK_COMMENTED, LINE_COMMENTED, PARENTHESISED, AS_CONST):
            with self.subTest(script=script[:50]):
                self.assertEqual(lifecycle.workflow_options(script), ([("model", "fable")], []))

    def test_an_escaped_value_is_read_once_as_its_decoded_form(self):
        self.assertEqual(lifecycle.workflow_options(ESCAPED_WITHIN),
                         ([("model", "sonnet"), ("effort", "low")], []))

    def test_comments_and_typescript_types_say_nothing(self):
        self.assertEqual(lifecycle.workflow_options(COMMENTED_OUT), ([("model", "sonnet")], []))
        self.assertEqual(lifecycle.workflow_options(TYPED), ([("model", "haiku")], []))

    def test_a_regular_expression_or_template_substitution_keeps_the_read_in_step(self):
        script = "const q = /'/g;\nawait agent(`Use ${ {model: 'fable'}.model }`, { effort: 'low' });\n"
        self.assertEqual(lifecycle.workflow_options(script),
                         ([("model", "fable"), ("effort", "low")], []))

    def test_prose_and_comparisons_say_nothing(self):
        self.assertEqual(lifecycle.workflow_options(PROSE), ([], []))
        self.assertEqual(lifecycle.workflow_options(None), ([], []))


class WorkflowCeilingTests(unittest.TestCase):
    def fake_posture(self, rows):
        return types.SimpleNamespace(
            DEFAULT_STANCES={"cost": "balanced", "delegation": "tiered"},
            selected=lambda name, fallback=None: {"cost": "custom", "delegation": "tiered"}.get(name, fallback),
            tier_models=lambda runtime="claude-code": {"frontier": "fable", "strong": "opus",
                                                       "standard": "sonnet", "light": "haiku"},
            _user_config=lambda env, strict: {},
            table_for=lambda stances, config, strict=True: {"rows": rows})

    def test_the_ceiling_is_the_strongest_row_the_variant_grants(self):
        fake = self.fake_posture({"A": {"class": "light", "effort": "low"},
                                  "B": {"class": "standard", "effort": "medium"},
                                  "fixed": {"class": None, "effort": None}})
        with patch.object(lifecycle, "load", return_value=fake), \
                patch.object(lifecycle, "_SELECTIONS", []):
            variant, strongest, highest, models, applies = lifecycle.workflow_ceiling("claude-code")
            self.assertEqual((variant, strongest, highest, applies), ("custom", "standard", "medium", True))
            refusal, over, unresolved = lifecycle.workflow_limits("claude-code", WITHIN)
            self.assertIsNone(refusal, "a class above a custom variant's strongest is only logged")
            self.assertEqual(over, ["model `claude-opus-4-1`", "effort `high`"])
            self.assertEqual(unresolved, [])
            refusal, _, _ = lifecycle.workflow_limits("claude-code", FRONTIER)
        self.assertIn("names model `fable` in agent(), the `frontier` class", refusal)
        self.assertIn("name `sonnet` or a lighter model", refusal)

    def test_an_unreadable_cost_table_keeps_the_frontier_refusal(self):
        fake = self.fake_posture({})

        def broken(stances, config, strict=True):
            raise ValueError("unreadable cost table")
        fake.table_for = broken
        with patch.object(lifecycle, "load", return_value=fake), \
                patch.object(lifecycle, "_SELECTIONS", []):
            _variant, strongest, highest, models, _applies = lifecycle.workflow_ceiling("claude-code")
            self.assertEqual((strongest, highest), lifecycle.WORKFLOW_CEILING)
            self.assertEqual(models["frontier"], "fable")
            refusal, _, _ = lifecycle.workflow_limits("claude-code", FRONTIER)
        self.assertIn("names model `fable` in agent(), the `frontier` class", refusal)

    def test_a_custom_variants_over_strongest_class_reads_over_ceiling_at_launch(self):
        fake = self.fake_posture({"A": {"class": "light", "effort": "high"},
                                  "B": {"class": "standard", "effort": "medium"}})
        script = "await agent('Plan it.', { model: 'claude-opus-4-1', effort: 'low' });\n"
        with tempfile.TemporaryDirectory() as home, \
                patch.dict(os.environ, {"HOME": home, "PATH": os.environ["PATH"]}, clear=True), \
                patch.object(lifecycle, "load", side_effect=lambda name: fake if name == "posture"
                             else real_load(name)), \
                patch.object(lifecycle, "_SELECTIONS", [{"stances": {"cost": "custom",
                                                                     "delegation": "tiered"}}]):
            module = lifecycle.decisions()
            with patch.object(module, "_CONFIG", [{}]):
                result = lifecycle.dispatch("claude-code", {
                    "hook_event_name": "PreToolUse", "tool_name": "Workflow",
                    "session_id": "wf-custom", "cwd": home, "tool_input": {"script": script}})
            log = Path(home) / ".local" / "state" / "agent-harness" / "decisions.jsonl"
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertNotEqual(decision(result), "deny", "a custom variant's over-strongest class is logged")
        self.assertEqual([row["deterministic_answer"] for row in rows
                          if row.get("point") == lifecycle.WORKFLOW_POINT], ["over-ceiling"])

    def test_a_frontier_row_never_lifts_the_ceiling(self):
        fake = self.fake_posture({"C": {"class": "frontier", "effort": "high"}})
        with patch.object(lifecycle, "load", return_value=fake), \
                patch.object(lifecycle, "_SELECTIONS", []):
            self.assertEqual(lifecycle.workflow_ceiling("claude-code")[1:3], ("strong", "high"))

    def test_an_unreadable_table_holds_the_stances_own_ceiling(self):
        def broken(name):
            raise OSError("no posture")
        with patch.object(lifecycle, "load", side_effect=broken), \
                patch.object(lifecycle, "selected", lambda name, fallback: fallback):
            self.assertEqual(lifecycle.workflow_ceiling("claude-code"),
                             ("balanced", "strong", "high", {}, True))
            refusal, over, unresolved = lifecycle.workflow_limits("claude-code", MAX)
        self.assertEqual((refusal, over, unresolved), (None, ["effort `max`"], []))


class WorkflowAgentOptionLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HOME": str(self.base), "PATH": os.environ["PATH"],
                                "HARNESS_STANCE_DELEGATION": "tiered",
                                "HARNESS_STANCE_COST": "balanced"}, clear=True).start()
        module = lifecycle.decisions()
        self.assertIsNotNone(module)
        # The config is cached per process; another test's config must not switch logging off.
        patch.object(module, "_CONFIG", [{}]).start()
        self.log = self.base / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def launch(self, script):
        return lifecycle.dispatch("claude-code", {
            "hook_event_name": "PreToolUse", "tool_name": "Workflow", "session_id": "wf-options",
            "cwd": str(self.base), "tool_input": {"script": script}})

    def answers(self):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [row["deterministic_answer"] for row in rows if row.get("point") == lifecycle.WORKFLOW_POINT]

    def test_values_within_the_ceiling_are_allowed(self):
        for script in (WITHIN, PROSE, ESCAPED_WITHIN, COMMENTED_OUT, TYPED):
            with self.subTest(script=script[:30]):
                self.assertNotEqual(decision(self.launch(script)), "deny")
        self.assertEqual(self.answers(), ["allow"] * 5)

    def test_a_frontier_model_is_refused(self):
        spellings = (FRONTIER, FRONTIER_ESCAPED, FRONTIER_ASSIGNED, BRACKET_ASSIGNED, BRACKET_COMPUTED,
                     BRACKET_JOINED, BLOCK_COMMENTED, LINE_COMMENTED, PARENTHESISED, AS_CONST)
        for script in spellings:
            with self.subTest(script=script[:40]):
                result = self.launch(script)
                self.assertEqual(decision(result), "deny")
                self.assertIn("the `frontier` class, which no spawn may request", reason(result))
                self.assertIn("past every spawn guard", reason(result))
                self.assertIn("name `opus` or a lighter model", reason(result))
        self.assertEqual(self.answers(), ["deny"] * len(spellings))

    def test_an_effort_above_the_ceiling_is_logged_not_refused(self):
        for script, effort in ((XHIGH, "xhigh"), (MAX, "max")):
            with self.subTest(effort=effort):
                self.assertNotEqual(decision(self.launch(script)), "deny")
        self.assertEqual(self.answers(), ["over-ceiling", "over-ceiling"])

    def test_over_ceiling_wins_over_unresolved_and_frontier_over_both(self):
        self.assertNotEqual(decision(self.launch(COMPUTED + MAX)), "deny")
        self.assertEqual(decision(self.launch(MAX + COMPUTED + FRONTIER)), "deny")
        self.assertEqual(self.answers(), ["over-ceiling", "deny"])

    def test_what_cannot_be_judged_is_logged_as_unresolved_not_refused(self):
        for script in (COMPUTED, SHORTHAND, TEMPLATE, UNKNOWN_MODEL, UNKNOWN_EFFORT):
            with self.subTest(script=script[:40]):
                self.assertNotEqual(decision(self.launch(script)), "deny")
        self.assertEqual(self.answers(), ["unresolved"] * 5, "one row per launch")

    def test_delegation_off_refuses_before_any_option_is_read(self):
        os.environ["HARNESS_STANCE_DELEGATION"] = "off"
        for script in (WITHIN, COMPUTED):
            with self.subTest(script=script[:30]):
                result = self.launch(script)
                self.assertEqual(decision(result), "deny")
                self.assertIn("Delegation is off", reason(result))
        self.assertEqual(self.answers(), ["deny", "deny"])

    def test_outside_tiered_delegation_a_model_is_not_judged_but_effort_is(self):
        os.environ["HARNESS_STANCE_DELEGATION"] = "session-model"
        self.assertNotEqual(decision(self.launch(FRONTIER)), "deny")
        self.assertNotEqual(decision(self.launch(XHIGH)), "deny")
        self.assertEqual(self.answers(), ["allow", "over-ceiling"])


if __name__ == "__main__":
    unittest.main()
