# SPDX-License-Identifier: MIT
"""The session caps: live subagents against the cost variant's fan-out cap, web searches against the
research rule's, a warning past 80% of either and a denial at it. Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from harness_core import lifecycle

HOOK = REPO / "policy" / "hooks" / "session-caps.py"
SESSION = "caps-session"


def load():
    spec = importlib.util.spec_from_file_location("harness_session_caps", str(HOOK))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPS = load()


def decision(result):
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(result):
    return (result.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


def context(result):
    return (result.get("hookSpecificOutput") or {}).get("additionalContext", "")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HOME": str(self.home), "PATH": os.environ["PATH"],
                                "HARNESS_STANCE_DELEGATION": "tiered",
                                "HARNESS_STANCE_COST": "balanced"}, clear=True).start()
        self.journal = CAPS.journal_path(SESSION, os.environ)
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def dispatch(self, tool, runtime="claude-code", **fields):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": SESSION,
                   "cwd": str(self.home), "tool_input": fields.pop("tool_input", {})}
        payload.update(fields)
        return lifecycle.dispatch(runtime, payload)

    def spawn(self, **fields):
        return self.dispatch("Agent", tool_input={"prompt": "Find the callers of load().",
                                                  "subagent_type": "worker-a"}, **fields)

    def search(self, **fields):
        return self.dispatch("WebSearch", tool_input={"query": "claude code hooks"}, **fields)

    def event(self, kind, agent_id):
        return lifecycle.dispatch("claude-code", {"hook_event_name": kind, "session_id": SESSION,
                                                  "agent_id": agent_id, "agent_type": "worker-a"})

    def started(self, count, prefix="a"):
        for index in range(count):
            self.spawn()
            self.event("SubagentStart", "%s%d" % (prefix, index))

    def searched(self, count):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as handle:
            for _ in range(count):
                handle.write(json.dumps({"t": "search", "at": time.time()}) + "\n")

    def rows(self):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("point") == CAPS.POINT]

    def config(self, data):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


class CountingTests(unittest.TestCase):
    def test_live_is_started_minus_stopped(self):
        now = time.time()
        counts = CAPS.tally([{"t": "start", "id": "a", "at": now}, {"t": "start", "id": "b", "at": now},
                             {"t": "stop", "id": "a", "at": now}], now)
        self.assertEqual(counts["live"], 1)

    def test_a_reservation_counts_until_its_start_consumes_it(self):
        now = time.time()
        entries = [{"t": "spawn", "at": now - 1}, {"t": "spawn", "at": now - 1}]
        self.assertEqual(CAPS.tally(entries, now)["live"], 2)
        entries.append({"t": "start", "id": "a", "at": now})
        counts = CAPS.tally(entries, now)
        self.assertEqual((counts["live"], counts["running"], counts["reserved"]), (2, 1, 1))

    def test_an_unstarted_reservation_and_a_stopless_start_decay(self):
        now = time.time()
        entries = [{"t": "spawn", "at": now - CAPS.RESERVE_TTL - 1},
                   {"t": "start", "id": "old", "at": now - CAPS.RUNNING_TTL - 1}]
        self.assertEqual(CAPS.tally([entries[0]], now - CAPS.RUNNING_TTL)["live"], 1)
        self.assertEqual(CAPS.tally([entries[1]], now - CAPS.RUNNING_TTL)["live"], 1)
        self.assertEqual(CAPS.tally([entries[0]], now)["live"], 0)
        self.assertEqual(CAPS.tally([entries[1]], now)["live"], 0)

    def test_a_stop_with_no_start_takes_nothing_away(self):
        now = time.time()
        counts = CAPS.tally([{"t": "start", "id": "a", "at": now}, {"t": "stop", "id": "x", "at": now}], now)
        self.assertEqual(counts["live"], 1)

    def test_searches_are_counted(self):
        self.assertEqual(CAPS.tally([{"t": "search", "at": 1}] * 3)["searches"], 3)


class CapSourceTests(Fixture):
    def test_the_fan_out_cap_follows_the_cost_variant(self):
        for variant, cap in (("balanced", 6), ("frugal", 3), ("max", None), ("off", 6)):
            with self.subTest(variant=variant):
                os.environ["HARNESS_STANCE_COST"] = variant
                self.assertEqual(CAPS.fanout_cap(os.environ), cap)

    def test_the_search_cap_is_read_from_the_shipped_research_rule(self):
        self.assertEqual(CAPS.search_cap(os.environ), 200)

    def test_the_rule_sentence_is_parsed_and_a_rewording_reads_none(self):
        self.assertEqual(CAPS.rule_cap("Web search is capped per session — 1,500 calls on X"), 1500)
        self.assertIsNone(CAPS.rule_cap("Search sparingly."))

    def test_switching_the_research_rule_off_lifts_the_search_cap(self):
        self.config({"rules": {CAPS.RULE: "off"}})
        self.assertIsNone(CAPS.search_cap(os.environ))


class FanOutTests(Fixture):
    def test_a_spawn_at_the_cap_is_denied_naming_cap_and_count(self):
        self.started(6)
        result = self.spawn()
        self.assertEqual(decision(result), "deny")
        self.assertIn("6 subagents are live", reason(result))
        self.assertIn("cap is 6", reason(result))
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "deny")
        self.assertEqual((self.rows()[-1]["cap"], self.rows()[-1]["limit"], self.rows()[-1]["count"]),
                         ("fan-out", 6, 6))

    def test_spawns_in_one_message_count_before_they_start(self):
        for _ in range(6):
            self.assertNotEqual(decision(self.spawn()), "deny")
        self.assertEqual(decision(self.spawn()), "deny")

    def test_a_finished_subagent_releases_its_slot(self):
        self.started(6)
        self.assertEqual(decision(self.spawn()), "deny")
        self.event("SubagentStop", "a0")
        self.assertNotEqual(decision(self.spawn()), "deny")

    def test_past_eighty_percent_warns_and_below_it_does_not(self):
        self.started(3)
        quiet = self.spawn()
        self.assertEqual(context(quiet).count("session-caps"), 0)
        self.event("SubagentStart", "b0")
        warned = self.spawn()
        self.assertNotEqual(decision(warned), "deny")
        self.assertIn("5 of the cost variant's 6", context(warned))
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "warn")

    def test_frugal_caps_at_three(self):
        os.environ["HARNESS_STANCE_COST"] = "frugal"
        self.started(2)
        third = self.spawn()
        self.assertIn("3 of the cost variant's 3", context(third))
        self.event("SubagentStart", "c0")
        self.assertEqual(decision(self.spawn()), "deny")

    def test_max_has_no_cap(self):
        os.environ["HARNESS_STANCE_COST"] = "max"
        self.started(12)
        result = self.spawn()
        self.assertNotEqual(decision(result), "deny")
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "allow")

    def test_a_stop_after_a_blocked_stop_leaves_the_slot_released(self):
        self.started(6)
        self.event("SubagentStop", "a0")
        lifecycle.dispatch("claude-code", {"hook_event_name": "SubagentStop", "session_id": SESSION,
                                           "agent_id": "a0", "agent_type": "worker-a",
                                           "stop_hook_active": True})
        self.assertEqual(CAPS.tally(CAPS.records(self.journal))["live"], 5)
        self.assertNotEqual(decision(self.spawn()), "deny")

    def test_a_spawn_another_policy_denies_takes_no_slot(self):
        os.environ["HARNESS_STANCE_DELEGATION"] = "off"
        self.assertEqual(decision(self.spawn()), "deny")
        self.assertEqual(CAPS.tally(CAPS.records(self.journal))["live"], 0)
        self.assertEqual(self.rows(), [])

    def test_a_workflow_launch_at_the_cap_is_denied_and_reserves_nothing(self):
        script = "await agent('Look at it.', { label: 'a' });\n"
        self.assertNotEqual(decision(self.dispatch("Workflow", tool_input={"script": script})), "deny")
        self.assertEqual(CAPS.tally(CAPS.records(self.journal))["live"], 0)
        self.started(6)
        result = self.dispatch("Workflow", tool_input={"script": script})
        self.assertEqual(decision(result), "deny")
        self.assertIn("agent() calls are spawns too", reason(result))

    def test_codex_is_not_held_to_a_count_it_cannot_lower(self):
        for _ in range(8):
            self.assertNotEqual(decision(self.spawn(runtime="codex")), "deny")
        self.assertEqual(self.rows(), [])


class SearchTests(Fixture):
    def test_the_cap_allows_two_hundred_then_denies(self):
        self.searched(199)
        self.assertNotEqual(decision(self.search()), "deny")
        result = self.search()
        self.assertEqual(decision(result), "deny")
        self.assertIn("200 searches", reason(result))
        self.assertIn("caps a session at 200", reason(result))
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "deny")
        self.assertEqual(self.rows()[-1]["cap"], "web-search")

    def test_a_subagents_search_counts_against_its_parent_session(self):
        self.searched(199)
        self.assertNotEqual(decision(self.search(agent_id="sub-1")), "deny")
        self.assertEqual(decision(self.search()), "deny")
        self.assertTrue(self.rows()[-2]["subagent"])

    def test_past_eighty_percent_warns_once(self):
        self.searched(159)
        self.assertEqual(context(self.search()), "")
        warned = self.search()
        self.assertIn("161 of the research rule's 200", context(warned))
        self.assertEqual(context(self.search()), "")
        self.assertEqual([r["deterministic_answer"] for r in self.rows()], ["allow", "warn", "allow"])

    def test_no_cap_while_the_rule_is_off(self):
        self.config({"rules": {CAPS.RULE: "off"}})
        self.searched(400)
        self.assertNotEqual(decision(self.search()), "deny")


class HeadlessTests(Fixture):
    def test_a_headless_run_is_never_denied_and_logs_would_deny(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        self.started(6)
        spawned = self.spawn()
        self.assertNotEqual(decision(spawned), "deny")
        self.assertIn("headless", context(spawned))
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "would-deny")
        self.searched(200)
        searched = self.search()
        self.assertNotEqual(decision(searched), "deny")
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "would-deny")

    def test_a_let_through_spawn_reserves_its_slot(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        self.started(5)
        self.spawn()
        self.assertEqual(context(self.spawn()).count("let through"), 1)
        self.event("SubagentStart", "late")
        counts = CAPS.tally(CAPS.records(self.journal))
        self.assertEqual((counts["live"], counts["running"], counts["reserved"]), (7, 6, 1))

    def test_a_test_can_switch_enforcement_on(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        os.environ[CAPS.HEADLESS_VARIABLE] = "enforce"
        self.started(6)
        self.assertEqual(decision(self.spawn()), "deny")
        self.searched(200)
        self.assertEqual(decision(self.search()), "deny")

    def test_an_sdk_session_is_not_headless(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-ts"
        self.started(6)
        self.assertEqual(decision(self.spawn()), "deny")


if __name__ == "__main__":
    unittest.main()
