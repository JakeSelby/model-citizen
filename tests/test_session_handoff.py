# SPDX-License-Identifier: MIT
"""The hard fresh-session threshold: a Stop past it is blocked once with the hand-off instruction.

On a 1M-token window a soft line at the context size where the per-call cost curve leaves its floor
was not enough on its own (#1197), so each cost variant names a soft and a hard threshold and a
cost curve. These tests hold what makes the block safe to ship: the thresholds per variant, the
soft line naming its cost multiple, one block per crossing and the release after it, a headless
run left alone unless enabled, and an adherence row for every block.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_session_nudge import NUDGE, load_posture, response
from test_usage_feed import Fixture, append, load_feed

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle  # noqa: E402

INTERACTIVE = {"CLAUDE_CODE_ENTRYPOINT": "cli"}


def load_adherence():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence_handoff", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Handoff(Fixture):
    def hard(self, at=300000, extends="balanced"):
        self.variant("handed", {"schema_version": 1, "extends": extends,
                                "switches": {"session_handoff_at": at}})

    def stop_answer(self, **extra):
        env = dict(INTERACTIVE, **extra)
        raw = self.fire({"hook_event_name": "Stop", "session_id": self.SESSION,
                         "transcript_path": str(self.transcript), "stop_hook_active": False},
                        **env)
        return json.loads(raw) if raw.strip() else None

    def grow(self, mid, context):
        append(self.transcript, [response(mid, 400, context)])

    def emitted(self):
        target = self.home / ".local" / "state" / "agent-harness" / "adherence.jsonl"
        if not target.exists():
            return []
        rows = [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
        return [row for row in rows if row.get("kind") == "emitted"]


class ShippedThresholds(Handoff):
    def test_each_variant_carries_its_soft_and_hard_threshold(self):
        # The soft threshold is where the curve leaves its floor; the hard one is the posture's.
        module = load_feed()
        for name, soft, hard in (("balanced", [160000], 400000), ("frugal", [160000], 200000),
                                 ("max", [], None)):
            self.variant(name)
            table = module.settings(self.env())[0]
            self.assertEqual(module.session_nudges(table), soft, msg=name)
            self.assertEqual(module.handoff_at(table), hard, msg=name)
        # `off` inherits balanced's sizes but turns the feed off, and the block with it.
        self.variant("off")
        self.assertEqual(module.settings(self.env())[1], "off")

    def test_the_cost_curve_names_the_measured_multiples(self):
        module = load_feed()
        self.variant("frugal")
        table = module.settings(self.env())[0]
        self.assertIsNone(module.cost_multiple(table, 120000))
        self.assertEqual(module.cost_multiple(table, 160000), (1.3, 160000))
        self.assertEqual(module.cost_multiple(table, 250000), (1.7, 160000))
        self.assertEqual(module.cost_multiple(table, 900000), (3.3, 160000))


class SoftLine(Handoff):
    def test_the_soft_line_names_the_threshold_and_the_cost_multiple(self):
        self.grow("m1", 172000)
        said = [line for line in self.submit() if NUDGE in line]
        self.assertEqual(said, ["usage-feed: session context 172,000 tokens, past the "
                                "fresh-session threshold of 160,000, where a call costs about "
                                "1.3× one under 160,000 — finish the task, write the handoff, "
                                "start a fresh session"])


class StopBlock(Handoff):
    def test_a_context_under_the_hard_threshold_never_blocks(self):
        self.hard()
        self.grow("m1", 299999)
        self.assertIsNone(self.stop_answer())
        self.assertEqual(self.emitted(), [])

    def test_past_the_hard_threshold_the_stop_is_blocked_once_with_the_handoff(self):
        self.hard()
        self.grow("m1", 310000)
        answer = self.stop_answer()
        self.assertEqual(answer["decision"], "block")
        self.assertIn("past the hard fresh-session threshold of 300,000", answer["reason"])
        self.assertIn("1.7× one under 160,000", answer["reason"])
        self.assertIn("write the handoff", answer["reason"])
        self.assertIn("start a fresh session", answer["reason"])

    def test_the_next_stop_is_released_whatever_the_turn_did(self):
        # Never a trap: the block is spent, so the next stop ends the turn even when the context
        # grew and no handoff was written.
        self.hard()
        self.grow("m1", 310000)
        self.assertEqual(self.stop_answer()["decision"], "block")
        self.assertIsNone(self.stop_answer())
        self.grow("m2", 340000)
        self.assertIsNone(self.stop_answer())
        self.assertEqual(self.state()["handed_off"], 300000)

    def test_a_context_that_fell_back_under_the_threshold_rearms_it(self):
        self.hard()
        self.grow("m1", 310000)
        self.assertEqual(self.stop_answer()["decision"], "block")
        self.grow("m2", 90000)      # a compaction
        self.assertIsNone(self.stop_answer())
        self.assertIsNone(self.state()["handed_off"])
        self.grow("m3", 305000)
        self.assertEqual(self.stop_answer()["decision"], "block")

    def test_the_stop_read_leaves_the_turn_line_to_the_next_prompt(self):
        self.hard()
        self.grow("m1", 310000)
        self.stop_answer()
        lines = self.submit()
        self.assertIn("last turn 400 output tokens", lines[0])

    def test_variants_with_no_hard_threshold_never_block(self):
        for name in ("max", "off"):
            self.variant(name)
            self.grow("m-" + name, 900000)
            self.assertIsNone(self.stop_answer(), msg=name)

    def test_a_subagent_stop_is_never_blocked(self):
        self.hard()
        self.grow("m1", 310000)
        raw = self.fire({"hook_event_name": "Stop", "session_id": self.SESSION,
                         "agent_id": "a1", "transcript_path": str(self.transcript)}, **INTERACTIVE)
        self.assertEqual(raw.strip(), "")


class Headless(Handoff):
    def test_a_headless_run_is_not_blocked(self):
        # A replay trial is `claude -p`; blocking its last stop would add a turn to the measure.
        self.hard()
        self.grow("m1", 310000)
        self.assertIsNone(self.stop_answer(CLAUDE_CODE_ENTRYPOINT="sdk-cli"))
        self.assertIsNone(self.state())

    def test_a_test_can_enable_it_headless(self):
        self.hard()
        self.grow("m1", 310000)
        answer = self.stop_answer(CLAUDE_CODE_ENTRYPOINT="sdk-cli", HARNESS_HANDOFF_BLOCK="on")
        self.assertEqual(answer["decision"], "block")

    def test_the_switch_can_turn_it_off_interactively(self):
        self.hard()
        self.grow("m1", 310000)
        self.assertIsNone(self.stop_answer(HARNESS_HANDOFF_BLOCK="off"))


class Logged(Handoff):
    def test_each_block_records_one_adherence_emission(self):
        self.hard()
        self.submit()
        self.grow("m1", 310000)
        self.stop_answer()
        self.stop_answer()
        rows = self.emitted()
        self.assertEqual([(r["recommendation"], r["module"], r["session_id"], r["turn"])
                          for r in rows],
                         [("fresh-session-handoff", "hooks/usage-feed", self.SESSION, 1)])

    def test_a_session_end_inside_the_window_reads_as_followed(self):
        adherence = load_adherence()
        row = {"kind": "emitted", "adherence_id": "h1", "recommendation": "fresh-session-handoff",
               "module": "hooks/usage-feed", "session_id": "s-1", "turn": 1,
               "ts": adherence.now_ts(1790000000.0), "profile_fingerprint": "p",
               "schema_version": 1}
        def observed(*events):
            return [{"event": event, "session_id": "s-1", "runtime": "claude-code", "ts": "t"}
                    for event in events]
        prompt = "UserPromptSubmit"
        self.assertEqual(adherence.respond(row, observed(prompt, "Stop", "Stop", "SessionEnd")),
                         ("followed", "SessionEnd", 0))
        # The window is two prompts: the third after the block arrives first, so not followed.
        self.assertEqual(adherence.respond(row, observed(prompt, "Stop", "Stop", prompt, prompt,
                                                         prompt, "SessionEnd")),
                         ("not_followed", prompt, 2))


class Validation(unittest.TestCase):
    def test_a_hard_threshold_is_a_whole_positive_size_or_null(self):
        posture = load_posture()
        for bad in (0, -1, 1.5, True, "400000", [400000]):
            clean, findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"session_handoff_at": bad}})
            self.assertTrue(any("session_handoff_at" in f for f in findings), bad)
            self.assertNotIn("session_handoff_at", clean.get("switches", {}))
        for good in (None, 400000):
            clean, findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"session_handoff_at": good}})
            self.assertEqual(findings, [])
            self.assertEqual(clean["switches"]["session_handoff_at"], good)

    def test_a_cost_curve_is_ascending_size_and_positive_multiple_pairs(self):
        posture = load_posture()
        for bad in ([[160000]], [[200000, 1.7], [160000, 1.3]], [[160000, 0]], [[160000, "x"]],
                    [[0, 1.3]], {"160000": 1.3}, [160000, 1.3]):
            clean, findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"context_cost_curve": bad}})
            self.assertTrue(any("context_cost_curve" in f for f in findings), bad)
            self.assertNotIn("context_cost_curve", clean.get("switches", {}))
        curve = [[160000, 1.3], [400000, 3.3]]
        clean, findings = posture.validate_sidecar(
            {"schema_version": 1, "switches": {"context_cost_curve": curve}})
        self.assertEqual((findings, clean["switches"]["context_cost_curve"]), ([], curve))


class Composition(unittest.TestCase):
    """The lifecycle asks the gate first and spends the one-shot block only on a stop it lets go."""

    STOP = {"hook_event_name": "Stop", "session_id": "s-1", "transcript_path": "/x"}
    GATE = {"decision": "block", "reason": "gate red"}
    HANDOFF = {"decision": "block", "reason": "hand off"}

    def answer(self, runtime, gate, handoff):
        asked = []

        def invoke(name, event):
            asked.append(name)
            return {"stop-gate": gate, "usage-feed": handoff}[name]
        with patch.object(lifecycle, "invoke", invoke):
            return lifecycle._dispatch(runtime, dict(self.STOP)), asked

    def test_a_red_gate_answers_alone(self):
        self.assertEqual(self.answer("claude-code", self.GATE, self.HANDOFF),
                         (self.GATE, ["stop-gate"]))

    def test_a_released_gate_lets_the_handoff_block(self):
        self.assertEqual(self.answer("claude-code", {}, self.HANDOFF),
                         (self.HANDOFF, ["stop-gate", "usage-feed"]))

    def test_no_handoff_leaves_the_gate_answer(self):
        self.assertEqual(self.answer("claude-code", {}, {}), ({}, ["stop-gate", "usage-feed"]))

    def test_codex_never_asks_the_feed(self):
        self.assertEqual(self.answer("codex", {}, self.HANDOFF), ({}, ["stop-gate"]))


if __name__ == "__main__":
    unittest.main()
