# SPDX-License-Identifier: MIT
"""The transcript index: per-call tokens and cost, reset and reseed events, skills, cache (AH-S376, #1328).

Every transcript here is synthetic, written into a temporary home.
"""
import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
from harness_core.studio import transcripts  # noqa: E402
from harness_core.studio.surfaces import roles  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = base.NOW
MODEL = "claude-opus-5-5"


def at(minutes):
    return base.stamp(NOW - dt.timedelta(days=1) + dt.timedelta(minutes=minutes))


def prompt(minute, text="do the thing"):
    return {"type": "user", "timestamp": at(minute), "isSidechain": False, "cwd": "/work/proj",
            "message": {"role": "user", "content": text}}


def tool_result(minute):
    return {"type": "user", "timestamp": at(minute), "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def reply(minute, mid, input=10, read=0, write=0, output=100, model=MODEL, request=None, tools=(),
          w1h=None, sidechain=False):
    usage = {"input_tokens": input, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write, "output_tokens": output}
    if w1h is not None:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": w1h, "ephemeral_5m_input_tokens": write - w1h}
    entry = {"type": "assistant", "timestamp": at(minute), "isSidechain": sidechain,
             "message": {"id": mid, "model": model, "role": "assistant", "usage": usage,
                         "content": list(tools) or [{"type": "text", "text": "done"}]}}
    if request:
        entry["requestId"] = request
    return entry


def skill_call(ident, name):
    return {"type": "tool_use", "id": ident, "name": "Skill", "input": {"skill": name}}


def listing(minute, names, initial=True):
    return {"type": "attachment", "timestamp": at(minute), "isSidechain": False,
            "attachment": {"type": "skill_listing", "names": names, "isInitial": initial,
                           "skillCount": len(names), "content": "- listing"}}


def compact(minute, pre=500000, post=12000, trigger="auto"):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": at(minute), "isSidechain": False,
            "content": "Conversation compacted",
            "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post}}


def reseed(minute):
    return {"type": "attachment", "timestamp": at(minute), "isSidechain": False,
            "attachment": {"type": "hook_additional_context", "hookEvent": "SessionStart",
                           "hookName": "SessionStart:compact",
                           "content": ["Shared task data ...", transcripts.RESET_MARK + "; pick up.]"]}}


class Transcripts:
    def __init__(self, home):
        self.home = home
        self.project = home.root / ".claude" / "projects" / "-work-proj"
        self.project.mkdir(parents=True, exist_ok=True)

    def write(self, session_id, entries, age_days=1, raw_lines=(), subagents=None):
        path = self.project / (session_id + ".jsonl")
        lines = [json.dumps(e) for e in entries] + list(raw_lines)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for agent, rows in (subagents or {}).items():
            folder = self.project / session_id / "subagents"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ("agent-%s.jsonl" % agent)).write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.age(path, age_days)
        return path

    @staticmethod
    def age(path, days):
        when = (NOW - dt.timedelta(days=days)).timestamp()
        os.utime(str(path), (when, when))


def priced(row):
    return roles.row_cost(row, roles.price_table({}))


class TranscriptIndexTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)
        self.files = Transcripts(self.home)

    def sessions(self, days=30):
        return transcripts.sessions(self.home.root, days=days, now=NOW)

    def only(self):
        found = self.sessions()
        self.assertEqual(len(found), 1)
        return found[0]

    def test_each_call_has_its_tokens_context_and_a_row_cost_price(self):
        self.files.write("s1", [prompt(0), reply(1, "m1", input=5, read=1000, write=200, output=50, w1h=200)])
        call = self.only()["calls"][0]
        self.assertEqual((call["input"], call["cache_read"], call["cache_write"], call["output"]), (5, 1000, 200, 50))
        self.assertEqual((call["cache_write_1h"], call["cache_write_5m"]), (200, 0))
        self.assertEqual(call["context_tokens"], 1205)
        self.assertEqual(call["model"], MODEL)
        self.assertEqual(call["session_id"], "s1")
        self.assertEqual(call["ts"], at(1).replace(".000Z", "Z"))
        expected = priced({"model": MODEL, "input": 5, "cache_read": 1000, "cache_write": 200,
                           "output": 50, "cache_write_1h": 200, "cache_write_5m": 0})
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(call["cost"], expected)
        self.assertGreater(call["cost"], priced({"model": MODEL, "input": 5, "cache_read": 1000,
                                                 "cache_write": 200, "output": 50}))  # the 1h tier bites

    def test_streamed_records_of_one_response_are_one_call_at_their_largest_figures(self):
        self.files.write("s1", [prompt(0), reply(1, "m1", output=7), reply(1, "m1", output=900),
                                reply(1, "m1", output=40)])
        calls = self.only()["calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["output"], 900)

    def test_a_request_id_record_without_a_message_id_joins_the_call_that_carries_one(self):
        no_id = reply(1, None, output=5, request="req-1")
        self.files.write("s1", [prompt(0), no_id, reply(1, "m1", output=60, request="req-1")])
        calls = self.only()["calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["message_id"], calls[0]["output"]), ("m1", 60))

    def test_compaction_and_reseed_are_events_stamped_on_the_next_main_call(self):
        self.files.write("s1", [prompt(0), reply(1, "m1", read=450000), compact(2), reply(3, "m2", read=12000),
                                compact(4, trigger="manual"), reseed(5), reply(6, "m3", read=9000)])
        found = self.only()
        self.assertEqual([e["kind"] for e in found["events"]], ["compact", "compact", "reseed"])
        first = found["events"][0]
        self.assertEqual((first["trigger"], first["pre_tokens"], first["post_tokens"]), ("auto", 500000, 12000))
        self.assertEqual(found["events"][1]["trigger"], "manual")
        self.assertEqual([c["event"] for c in found["calls"]], [None, "compact", "reseed"])

    def test_the_reseed_mark_is_the_one_harness_session_writes(self):
        text = (ROOT / "policy" / "hooks" / "harness-session.py").read_text(encoding="utf-8")
        self.assertIn('RESET_LINE = ("' + transcripts.RESET_MARK, text)

    def test_a_reset_line_in_some_other_hook_is_not_a_reseed(self):
        other = reseed(1)
        other["attachment"]["hookEvent"] = "UserPromptSubmit"
        self.files.write("s1", [prompt(0), other, reply(2, "m1")])
        self.assertEqual(self.only()["events"], [])

    def test_loaded_skills_are_the_listings_and_invoked_are_skill_calls_and_slash_commands(self):
        call = reply(2, "m1", tools=[skill_call("tu1", "plan")])
        self.files.write("s1", [listing(0, ["plan", "review", "land"]), listing(1, ["handoff"], initial=False),
                                prompt(1, "<command-name>/review</command-name>"),
                                prompt(1, "<command-name>/clear</command-name>"),
                                call, call, reply(3, "m2", tools=[skill_call("tu2", "plan")])])
        found = self.only()
        self.assertEqual(found["skills_loaded"], ["handoff", "land", "plan", "review"])
        self.assertEqual(found["skills_invoked"], {"plan": 2, "review": 1})

    def test_skill_usage_counts_sessions_that_loaded_and_invoked_each_skill(self):
        self.files.write("s1", [listing(0, ["plan", "land"]), prompt(0),
                                reply(1, "m1", tools=[skill_call("a", "plan")])])
        self.files.write("s2", [listing(0, ["plan", "land"]), prompt(0), reply(1, "m2")])
        usage = transcripts.skill_usage(self.home.root, days=30, now=NOW)
        self.assertEqual(sorted(usage["plan"]["loaded"]), ["s1", "s2"])
        self.assertEqual(usage["plan"]["invoked"], ["s1"])
        self.assertEqual(usage["plan"]["calls"], 1)
        self.assertEqual(usage["land"]["invoked"], [])

    def test_turns_open_on_prompts_not_tool_results_and_carry_their_cost(self):
        self.files.write("s1", [prompt(0), reply(1, "m1"), tool_result(2), reply(3, "m2", read=200000),
                                prompt(10, "next"), reply(11, "m3")])
        found = self.only()
        self.assertEqual([t["calls"] for t in found["turns"]], [2, 1])
        self.assertEqual([c["turn"] for c in found["calls"]], [0, 0, 1])
        self.assertEqual(found["turns"][0]["max_context"], 200010)
        self.assertAlmostEqual(found["turns"][0]["cost"], found["calls"][0]["cost"] + found["calls"][1]["cost"],
                               places=6)

    def test_context_figures_count_main_thread_calls_against_the_stance_lines(self):
        self.files.write("s1", [prompt(0), reply(1, "m1", read=100000), reply(2, "m2", read=200000),
                                reply(3, "m3", read=500000)],
                         subagents={"abc": [reply(2, "sub1", read=900000, sidechain=True)]})
        found = self.only()
        self.assertEqual((transcripts.CONTEXT_WARN, transcripts.CONTEXT_LIMIT), (160000, 400000))
        self.assertEqual(found["median_context"], 200010)
        self.assertEqual(found["max_context"], 500010)
        self.assertEqual((found["over_warn"], found["over_limit"]), (2, 1))

    def test_subagent_calls_are_sidechain_rows_in_the_cost_but_out_of_the_main_series(self):
        self.files.write("s1", [prompt(0), reply(1, "m1")],
                         subagents={"abc": [reply(2, "sub1", output=1000, sidechain=True)]})
        found = self.only()
        side = [c for c in found["calls"] if c["sidechain"]]
        self.assertEqual([(c["agent_id"], c["turn"]) for c in side], [("abc", 0)])
        self.assertAlmostEqual(found["cost"], sum(c["cost"] for c in found["calls"]), places=6)
        self.assertEqual(len(transcripts.calls(self.home.root, days=30, now=NOW)), 1)
        self.assertEqual(len(transcripts.calls(self.home.root, days=30, now=NOW, sidechain=True)), 2)

    def test_an_unpriced_model_leaves_the_call_cost_none_and_the_session_incomplete(self):
        self.files.write("s1", [prompt(0), reply(1, "m1"), reply(2, "m2", model="some-unknown-model")])
        found = self.only()
        self.assertIsNone(found["calls"][1]["cost"])
        self.assertFalse(found["cost_complete"])
        self.assertAlmostEqual(found["cost"], found["calls"][0]["cost"])

    def test_one_long_context_call_is_priced_at_its_tier(self):
        self.files.write("s1", [prompt(0), reply(1, "m1", model="claude-haiku-5-5", read=150000, output=0, input=0)])
        call = self.only()["calls"][0]
        self.assertAlmostEqual(call["cost"], 150000 * 0.05 / 1000000)

    def test_synthetic_replies_are_not_calls(self):
        self.files.write("s1", [prompt(0), reply(1, "m1"), reply(2, "m2", model="<synthetic>")])
        self.assertEqual(len(self.only()["calls"]), 1)

    def test_a_malformed_line_is_counted_and_the_rest_is_indexed(self):
        self.files.write("s1", [prompt(0), reply(1, "m1")], raw_lines=["{not json", "[1, 2]"])
        found = self.only()
        self.assertEqual(found["malformed_lines"], 2)
        self.assertEqual(len(found["calls"]), 1)

    def test_title_cwd_and_span_come_from_the_transcript(self):
        self.files.write("s1", [{"type": "custom-title", "customTitle": "Fix the thing", "sessionId": "s1"},
                                prompt(0), reply(5, "m1")])
        found = self.only()
        self.assertEqual((found["title"], found["cwd"], found["project"]), ("Fix the thing", "/work/proj", "-work-proj"))
        self.assertEqual((found["started"], found["ended"]), (at(0).replace(".000Z", "Z"), at(5).replace(".000Z", "Z")))

    def test_the_window_keeps_only_transcripts_modified_inside_it(self):
        self.files.write("new", [prompt(0), reply(1, "m1")], age_days=2)
        self.files.write("old", [prompt(0), reply(1, "m2")], age_days=40)
        self.assertEqual([s["session_id"] for s in self.sessions(days=30)], ["new"])
        self.assertEqual(len(self.sessions(days=60)), 2)

    def test_session_by_id_rejects_a_path_and_answers_none_for_an_unknown_id(self):
        self.files.write("s1", [prompt(0), reply(1, "m1")])
        self.assertEqual(transcripts.session(self.home.root, "s1")["session_id"], "s1")
        self.assertIsNone(transcripts.session(self.home.root, "../-work-proj/s1"))
        self.assertIsNone(transcripts.session(self.home.root, "nope"))
        self.assertEqual(transcripts.calls(self.home.root, session_id="nope"), [])

    def test_events_lists_every_session_event_in_time_order(self):
        self.files.write("s1", [prompt(0), reply(1, "m1"), compact(5), reply(6, "m2")])
        self.files.write("s2", [prompt(0), reply(1, "m3"), compact(3), reply(4, "m4")])
        rows = transcripts.events(self.home.root, days=30, now=NOW)
        self.assertEqual([r["session_id"] for r in rows], ["s2", "s1"])

    def test_no_projects_folder_is_no_sessions(self):
        self.assertEqual(self.sessions(), [])


class TranscriptCacheTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)
        self.files = Transcripts(self.home)

    def test_an_unchanged_transcript_is_answered_from_the_cache_without_reading_it(self):
        self.files.write("s1", [prompt(0), reply(1, "m1")])
        first = transcripts.sessions(self.home.root, days=30, now=NOW)
        self.assertTrue(list(transcripts.cache_dir(self.home.root).glob("*.json")))
        with mock.patch.object(transcripts, "build", side_effect=AssertionError("re-read")), \
                mock.patch.object(transcripts, "_read", side_effect=AssertionError("re-read")):
            second = transcripts.sessions(self.home.root, days=30, now=NOW)
        self.assertEqual(first, second)

    def test_a_changed_transcript_or_subagent_file_is_read_again(self):
        path = self.files.write("s1", [prompt(0), reply(1, "m1")])
        transcripts.sessions(self.home.root, days=30, now=NOW)
        self.files.write("s1", [prompt(0), reply(1, "m1"), reply(2, "m2")])
        self.assertEqual(len(transcripts.sessions(self.home.root, days=30, now=NOW)[0]["calls"]), 2)
        folder = path.parent / "s1" / "subagents"
        folder.mkdir(parents=True)
        (folder / "agent-x.jsonl").write_text(json.dumps(reply(3, "sub", sidechain=True)) + "\n")
        found = transcripts.sessions(self.home.root, days=30, now=NOW)[0]
        self.assertEqual(len(found["calls"]), 3)

    def test_a_corrupt_cache_file_is_rebuilt(self):
        self.files.write("s1", [prompt(0), reply(1, "m1")])
        transcripts.sessions(self.home.root, days=30, now=NOW)
        for cached in transcripts.cache_dir(self.home.root).glob("*.json"):
            cached.write_text("{broken")
        self.assertEqual(len(transcripts.sessions(self.home.root, days=30, now=NOW)[0]["calls"]), 1)

    def test_the_cache_lives_under_the_studio_state_directory(self):
        self.assertEqual(transcripts.cache_dir(self.home.root),
                         self.home.state / "studio" / "transcripts")

    def test_indexing_never_writes_into_the_projects_folder(self):
        path = self.files.write("s1", [prompt(0), reply(1, "m1")])
        before = sorted(str(p) for p in path.parent.rglob("*"))
        mtime = path.stat().st_mtime_ns
        transcripts.sessions(self.home.root, days=30, now=NOW)
        self.assertEqual(sorted(str(p) for p in path.parent.rglob("*")), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
