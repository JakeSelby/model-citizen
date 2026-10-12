# SPDX-License-Identifier: MIT
"""The compaction switch, the compaction detector and the docs for sessions that reset themselves.

`citizen sync` projects the cost variant's `context_reset_at` into Claude Code's auto-compaction,
so an automatic compaction near that point is the stance working and `cache-hygiene/compact`
leaves it out. The exemption reads what sync wrote, never the selected stance, and replay applies
the same one. Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
COST = REPO / "primitives" / "stances" / "cost"
COMPACT = "cache-hygiene/compact"
WINDOW, PERCENT = "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


posture = _load("posture_reset_docs", REPO / "policy" / "hooks" / "posture.py")
rd = _load("rule_detectors_reset_docs", REPO / "policy" / "hooks" / "rule-detectors.py")
usage_log = _load("usage_log_reset_docs", REPO / "policy" / "hooks" / "usage-log.py")
replay = _load("replay_detect_reset_docs", REPO / "scripts" / "replay_detect.py")
harness = _load("harness_reset_docs", REPO / "bin" / "harness")


def compact(trigger=None, pre_tokens=None, turn=1):
    event = {"kind": "compact", "turn": turn}
    if trigger is not None:
        event["trigger"] = trigger
    if pre_tokens is not None:
        event["pre_tokens"] = pre_tokens
    return event


AUTO_AT_400K = compact("auto", 400700)
AUTO_AT_200K = compact("auto", 200400)
MANUAL = compact("manual", 120000, turn=2)


@contextmanager
def isolated_home(pair=None, harness_wrote=True, user_window=False, override=None):
    """A throwaway home whose Claude Code settings and sync journal are as sync leaves them.

    `pair` is the window and percentage written to settings `env`; `harness_wrote` says whether
    the journal records sync writing them; `user_window` adds a user-owned `autoCompactWindow`.
    HOME, HARNESS_HOME and HARNESS_STANCE_COST are all set here, never inherited."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        live, keys = {}, {}
        if pair:
            live["env"] = {WINDOW: pair[0], PERCENT: pair[1]}
            if harness_wrote:
                for name, value in zip((WINDOW, PERCENT), pair):
                    keys[json.dumps(["env", name])] = {
                        "prior": {"present": False, "value": None},
                        "applied": {"present": True, "value": value}}
        if user_window:
            live["autoCompactWindow"] = 300000
        settings.write_text(json.dumps(live))
        journal = home / ".local" / "state" / "agent-harness" / "ownership.json"
        journal.parent.mkdir(parents=True)
        journal.write_text(json.dumps({"schema_version": 1, "files": {
            str(settings): {"kind": "json", "keys": keys}}}))
        env = {"HOME": tmp, "HARNESS_HOME": tmp}
        with patch.dict(os.environ, env):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            os.environ.pop("HARNESS_STANCE_COST", None)
            if override:
                os.environ["HARNESS_STANCE_COST"] = override
            yield home


def projected(variant):
    """The pair sync writes for a shipped variant, from sync's own function."""
    table = posture.table_for({"cost": variant}, {}, strict=True)
    values = harness.claude_context_reset_values(table)
    return (values[("env", WINDOW)], values[("env", PERCENT)]) if values else None


def hits(events, stances):
    return len(rd.run(events, stances, strict=True).get(COMPACT, []))


class CompactionSwitchTests(unittest.TestCase):
    def switches(self, variant):
        return posture.table_for({"cost": variant}, {}, strict=True)["switches"]

    def test_the_resetting_variants_compact_automatically_at_their_reset_point(self):
        for variant, reset in (("balanced", 400000), ("frugal", 200000)):
            with self.subTest(variant=variant):
                switches = self.switches(variant)
                self.assertEqual(switches["compaction"], "auto-at-reset")
                self.assertEqual(switches["context_reset_at"], reset)

    def test_max_still_allows_compaction_and_off_keeps_its_old_switch(self):
        self.assertEqual(self.switches("max")["compaction"], "compact-allowed")
        self.assertEqual(self.switches("off")["compaction"], "clear-at-task-end")
        self.assertIsNone(self.switches("off")["context_reset_at"])

    def test_the_schema_accepts_the_new_value_and_still_refuses_an_unknown_one(self):
        for kept in ("clear-only", "clear-at-task-end", "auto-at-reset", "compact-allowed"):
            with self.subTest(value=kept):
                clean, findings = posture.validate_sidecar(
                    {"schema_version": 1, "switches": {"compaction": kept}})
                self.assertEqual((clean["switches"]["compaction"], findings), (kept, []))
        clean, findings = posture.validate_sidecar(
            {"schema_version": 1, "switches": {"compaction": "auto-whenever"}})
        self.assertNotIn("switches", clean)
        self.assertEqual(findings, ["switch 'compaction' has an unusable value"])


class ProjectedResetPointTests(unittest.TestCase):
    def test_the_detector_and_sync_agree_on_the_reserve_and_the_pair(self):
        self.assertEqual(rd.AUTO_COMPACT_RESERVE, harness.AUTO_COMPACT_RESERVE)
        self.assertEqual(rd.CONTEXT_RESET_ENV, harness.CONTEXT_RESET_ENV)

    def test_the_point_is_read_back_from_what_sync_wrote(self):
        for variant, point in (("balanced", 400000), ("frugal", 200000)):
            with self.subTest(variant=variant), isolated_home(projected(variant)):
                self.assertEqual(rd.projected_reset_point(), point)

    def test_nothing_sync_did_not_write_is_a_reset_point(self):
        cases = {"no pair": dict(pair=None),
                 "user-owned pair": dict(pair=("420000", "100"), harness_wrote=False),
                 "user-owned autoCompactWindow": dict(pair=projected("balanced"), user_window=True)}
        for name, kwargs in cases.items():
            with self.subTest(case=name), isolated_home(**kwargs):
                self.assertIsNone(rd.projected_reset_point())

    def test_a_value_edited_after_sync_is_not_the_projection(self):
        with isolated_home(projected("balanced")) as home:
            settings = home / ".claude" / "settings.json"
            live = json.loads(settings.read_text())
            live["env"][WINDOW] = "500000"
            settings.write_text(json.dumps(live))
            self.assertIsNone(rd.projected_reset_point())


class CompactionDetectorTests(unittest.TestCase):
    def test_an_automatic_compaction_at_the_projected_point_is_not_a_miss(self):
        with isolated_home(projected("balanced")):
            for stances in ({"cost": "balanced"}, {}, None):
                with self.subTest(stances=stances):
                    self.assertEqual(hits([AUTO_AT_400K], stances), 0)
                    self.assertEqual(hits([AUTO_AT_400K, MANUAL], stances), 1)

    def test_configured_max_with_a_session_override_to_frugal_counts_the_compaction(self):
        # max projects no pair, and the override cannot move what Claude Code read at startup.
        with isolated_home(projected("max"), override="frugal"):
            stances = posture.resolve(None, strict=False)["stances"]
            self.assertEqual(stances["cost"], "frugal")
            self.assertEqual(hits([AUTO_AT_200K], stances), 1)

    def test_configured_balanced_with_a_session_override_to_off_still_exempts_the_reset(self):
        with isolated_home(projected("balanced"), override="off"):
            stances = posture.resolve(None, strict=False)["stances"]
            self.assertEqual(stances["cost"], "off")
            self.assertEqual(hits([AUTO_AT_400K], stances), 0)
            self.assertEqual(hits([MANUAL], stances), 1)

    def test_a_user_owned_window_leaves_every_compaction_counted(self):
        with isolated_home(projected("balanced"), user_window=True):
            self.assertEqual(hits([AUTO_AT_400K], {"cost": "balanced"}), 1)
        with isolated_home(("420000", "100"), harness_wrote=False):
            self.assertEqual(hits([AUTO_AT_400K], {"cost": "balanced"}), 1)

    def test_a_compaction_far_from_the_point_or_with_no_size_still_counts(self):
        with isolated_home(projected("balanced")):
            for event in (compact("auto", 250000), compact("auto", 470000), compact("auto"),
                          compact(None, 400000)):
                with self.subTest(event=event):
                    self.assertEqual(hits([event], {"cost": "balanced"}), 1)
            self.assertEqual(hits([compact("auto", 340000), compact("auto", 460000)],
                                  {"cost": "balanced"}), 0)

    def test_max_counts_none(self):
        with isolated_home(projected("balanced")):
            self.assertEqual(hits([AUTO_AT_400K, MANUAL], {"cost": "max"}), 0)

    def test_the_registry_keeps_the_engines_own_compaction_count(self):
        engine = {d.id: d.fn for d in rd.generic.DETECTORS}[COMPACT]
        self.assertIs(rd.DETECTORS[COMPACT].fn, engine)
        self.assertEqual(len(engine([AUTO_AT_400K, MANUAL], None)), 2)


def boundary(**extra):
    entry = {"type": "system", "subtype": "compact_boundary", "sessionId": "s-1", "cwd": "",
             "timestamp": "2026-10-12T12:00:00.000Z", "content": "Conversation compacted"}
    entry.update(extra)
    return entry


class LedgerAndReplayAgreeTests(unittest.TestCase):
    def test_the_session_scan_leaves_out_only_the_projected_reset(self):
        entries = [
            {"type": "user", "sessionId": "s-1", "cwd": "", "timestamp": "2026-10-12T11:59:00.000Z",
             "message": {"role": "user", "content": "carry on"}},
            {"type": "assistant", "sessionId": "s-1", "cwd": "",
             "timestamp": "2026-10-12T11:59:30.000Z",
             "message": {"id": "m1", "model": "model-a", "content": [{"type": "text", "text": "ok"}],
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
            boundary(compactMetadata={"trigger": "auto", "preTokens": 400700}),
            boundary(compactMetadata={"trigger": "manual", "preTokens": 120000}),
            boundary(),
        ]
        with isolated_home(projected("balanced")) as home:
            path = home / "session.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
            real = usage_log.detectors
            usage_log.detectors = lambda: rd
            try:
                record = usage_log.scan(path, "s-1", "")
            finally:
                usage_log.detectors = real
        self.assertEqual(record["stances"].get("cost"), "balanced")
        self.assertEqual(record["rules"].get(COMPACT), 2)

    def test_replay_gives_the_same_verdict_as_the_live_ledger(self):
        messages = [
            {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "a"}]}},
            {"type": "system", "subtype": "compact_boundary",
             "compact_metadata": {"trigger": "auto", "pre_tokens": 400700}},
            {"type": "system", "subtype": "compact_boundary",
             "compact_metadata": {"trigger": "auto", "pre_tokens": 250000}},
            {"type": "system", "subtype": "compact_boundary",
             "compact_metadata": {"trigger": "manual", "pre_tokens": 120000}},
        ]
        events = replay.stream_events(messages)
        replayed = replay.detect_messages(messages, rd, reset_point=400000)[COMPACT]
        self.assertEqual(len(replayed), 2)
        self.assertEqual(len(replay.detect_messages(messages, rd)[COMPACT]), 3)
        with isolated_home(projected("balanced")):
            self.assertEqual(hits(events, {"cost": "balanced"}), len(replayed))


class DocumentationTests(unittest.TestCase):
    def test_no_resetting_variant_tells_the_session_to_start_afresh(self):
        for variant in ("balanced", "frugal"):
            with self.subTest(variant=variant):
                text = (COST / (variant + ".md")).read_text()
                self.assertNotIn("fresh session", text)
                self.assertNotIn("/clear", text)
                self.assertIn("the context resets", text)
                self.assertIn("refreshes the handoff", text)

    def test_the_stances_name_their_two_points(self):
        balanced = (COST / "balanced.md").read_text().replace("\n", " ")
        frugal = (COST / "frugal.md").read_text().replace("\n", " ")
        for text, points in ((balanced, ("360,000", "400,000")), (frugal, ("180,000", "200,000"))):
            for point in points:
                self.assertIn(point, text)

    def test_the_rule_names_the_automatic_compaction_as_the_stance_working(self):
        rule = (REPO / "primitives" / "rules" / "cache-hygiene.md").read_text()
        self.assertIn("automatic compaction at the stance's reset point is the stance working", rule)

    def test_preferences_and_usage_describe_the_reset(self):
        prefs = (REPO / "docs" / "preferences.md").read_text()
        self.assertTrue("### When a session resets" in prefs)
        self.assertFalse("### When a session should hand off" in prefs)
        usage = (REPO / "docs" / "usage.md").read_text()
        for needle in ("preferences.md#when-a-session-resets", "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                       "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", ".agent-harness/handoffs/auto-",
                       "context-reset-handoff", "`auto-at-reset`"):
            with self.subTest(needle=needle):
                self.assertTrue(needle in usage, needle)
        ownership = (REPO / "docs" / "settings-ownership.md").read_text()
        self.assertTrue("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE" in ownership)


if __name__ == "__main__":
    unittest.main()
