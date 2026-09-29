# SPDX-License-Identifier: MIT
"""Prompt-cache rebuild attribution from local Claude Code transcripts."""
import argparse
import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import rebuilds  # noqa: E402


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


PRICING = load("rebuild_pricing", REPO / "policy" / "hooks" / "pricing.py")
TABLE = PRICING.shipped_prices(REPO / "policy" / "prices.json")
FIXTURES = REPO / "tests" / "fixtures" / "transcripts" / "rebuilds"
HARNESS = load("harness_rebuilds", REPO / "bin" / "harness")


def assistant(identity, stamp, read=20000, write=40000, one_hour=20000,
              model="claude-sonnet-5", version="2.0.20", sidechain=False, split=True):
    usage = {"input_tokens": 10000, "output_tokens": 100, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write}
    if split:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": one_hour}
    return {"type": "assistant", "requestId": identity, "timestamp": stamp,
            "version": version, "isSidechain": sidechain,
            "message": {"id": "msg-" + identity, "model": model, "usage": usage}}


def synthetic(identity, stamp):
    """A client-generated turn as Claude Code writes it: no request and zero usage."""
    return {"type": "assistant", "uuid": identity, "timestamp": stamp, "version": "2.0.20",
            "message": {"id": identity, "model": "<synthetic>",
                        "usage": {"input_tokens": 0, "output_tokens": 0,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}}


def write_session(root, rows, project="project", name="session.jsonl"):
    path = Path(root) / project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def causes(result, scope="all"):
    return [(row["cause"], row["breaks"]) for row in result["scopes"][scope]["causes"]]


#: Inside every fixture's 30-day window, and one past it: the fixtures are dated 2026-09-29.
INSIDE = rebuilds.timestamp("2026-09-30T00:00:00Z")
OUTSIDE = rebuilds.timestamp("2026-11-30T00:00:00Z")


def run_usage(args, projects, now):
    original = HARNESS.claude_projects_root
    HARNESS.claude_projects_root = lambda: Path(projects)
    output = io.StringIO()
    try:
        # `say` prints nothing while HARNESS_QUIET is set, and other suites set it.
        with mock.patch.dict(os.environ), mock.patch.object(HARNESS.time, "time", return_value=now), \
                contextlib.redirect_stdout(output):
            os.environ.pop("HARNESS_QUIET", None)
            code = HARNESS.cmd_usage(args)
    finally:
        HARNESS.claude_projects_root = original
    return code, output.getvalue()


def usage_args(**overrides):
    values = dict(action=None, days=30, by="rebuild", rules=False, stance=None, rescan=False,
                  conflicts=False, json=False)
    values.update(overrides)
    return argparse.Namespace(**values)


class FixtureAttributionTests(unittest.TestCase):
    EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))

    def test_each_fixture_alone_yields_exactly_its_designed_cause(self):
        for slug, label in sorted(self.EXPECTED.items()):
            with self.subTest(slug=slug), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / slug
                target.mkdir()
                shutil.copy(str(FIXTURES / slug / "session.jsonl"), str(target / "session.jsonl"))
                result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
                self.assertEqual(causes(result), [(label, 1)])

    def test_every_fixture_directory_has_an_expectation(self):
        found = sorted(path.parent.name for path in FIXTURES.glob("*/session.jsonl"))
        self.assertEqual(found, sorted(self.EXPECTED))

    def test_several_signals_resolve_in_the_designed_order(self):
        # Peel the highest-ranked signal off one at a time; each step names the next cause.
        order = ["model", "compaction", "hour", "version", "ttl", "prompt", "command", "idle"]
        expected = ["model switch, no /model", "compaction", "idle over 1h (TTL expiry)",
                    "Claude Code version changed (restart)", "idle over 5m on 5m TTL",
                    "system prompt rebuilt", "slash command /clear", "idle 5-60 min, no event",
                    "unexplained"]
        for step, label in enumerate(expected):
            on = set(order[step:])
            gap = 3700 if "hour" in on else 400 if on & {"ttl", "idle"} else 60
            rows = [assistant("a", "2026-09-29T12:00:00Z", read=20000, write=40000)]
            if "command" in on:
                rows.append({"type": "user", "timestamp": "2026-09-29T12:00:01Z",
                             "message": {"content": "<command-name>/clear</command-name>"}})
            if "prompt" in on:
                rows.append({"type": "attachment", "timestamp": "2026-09-29T12:00:02Z",
                             "attachment": {"type": "prompt_snapshot"}})
            if "compaction" in on:
                rows.append({"type": "system", "subtype": "compact_boundary",
                             "timestamp": "2026-09-29T12:00:03Z"})
            later = datetime.datetime(2026, 9, 29, 12, tzinfo=datetime.timezone.utc) + \
                datetime.timedelta(seconds=gap)
            rows.append(assistant("b", later.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  one_hour=0 if "ttl" in on else 30000,
                                  model="claude-haiku-4-5" if "model" in on else "claude-sonnet-5",
                                  version="2.0.21" if "version" in on else "2.0.20"))
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                write_session(tmp, rows)
                self.assertEqual(causes(rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)),
                                 [(label, 1)])

    def test_usage_dispatch_prints_every_cause_row_without_touching_the_ledger(self):
        original_ledger = HARNESS.usage_ledger
        HARNESS.usage_ledger = lambda path: self.fail("rebuild report read the usage ledger")
        try:
            code, text = run_usage(usage_args(), FIXTURES, INSIDE)
            late_code, late = run_usage(usage_args(), FIXTURES, OUTSIDE)
        finally:
            HARNESS.usage_ledger = original_ledger
        self.assertEqual((code, late_code), (0, 0))
        self.assertIn("Cache rebuild attribution is retrospective", text)
        self.assertIn("[all sessions] %d session(s)" % len(self.EXPECTED), text)
        self.assertIn("%d file(s) read once" % len(self.EXPECTED), text)
        rows = [line for line in text.splitlines() if line[:42].strip() in
                set(self.EXPECTED.values())]
        # Each cause once per scope table; the long scope is empty for two-call fixtures.
        self.assertEqual(sorted(line[:42].strip() for line in rows),
                         sorted(set(self.EXPECTED.values())))
        # The pinned clock is what keeps the fixtures inside the window.
        self.assertIn("[all sessions] 0 session(s)", late)

    def test_usage_rejects_rules_stance_and_rescan_for_the_rebuild_report(self):
        for overrides in ({"rules": True}, {"stance": "cost"}, {"rescan": True}):
            with self.subTest(**overrides), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(HARNESS.cmd_usage(usage_args(**overrides)), 2)
            self.assertIn("do not apply", err.getvalue())

    def test_usage_json_is_versioned_and_leaves_unpriced_causes_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            # One idle-hour break priced and one not; one /clear break on an unpriced model only.
            write_session(tmp, [assistant("a", "2026-09-29T10:00:00Z"),
                                assistant("b", "2026-09-29T11:30:00Z")], "priced")
            write_session(tmp, [assistant("c", "2026-09-29T10:00:00Z", model="unknown-model"),
                                assistant("d", "2026-09-29T11:30:00Z", model="unknown-model")],
                          "mixed")
            write_session(tmp, [assistant("e", "2026-09-29T12:00:00Z", model="unknown-model"),
                                {"type": "user", "timestamp": "2026-09-29T12:00:30Z",
                                 "message": {"content": "<command-name>/clear</command-name>"}},
                                assistant("f", "2026-09-29T12:01:00Z", model="unknown-model")],
                          "unpriced")
            code, text = run_usage(usage_args(json=True), tmp, INSIDE)
            _, table = run_usage(usage_args(), tmp, INSIDE)
        document = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(document["schema_version"], HARNESS.USAGE_JSON_VERSION)
        self.assertEqual(document["days"], 30)
        self.assertEqual((document["report"], document["by"]), ("rebuild", "rebuild"))
        self.assertEqual([group["scope"] for group in document["groups"]], ["long", "all"])
        self.assertEqual(document["files"]["files_read"], 3)
        rows = {row["cause"]: row for row in document["groups"][1]["causes"]}
        self.assertEqual(set(rows), {"idle over 1h (TTL expiry)", "slash command /clear"})
        for label, unpriced in (("idle over 1h (TTL expiry)", 1), ("slash command /clear", 1)):
            row = rows[label]
            self.assertEqual((row["breaks"], row["unpriced_breaks"]),
                             (2 if label.startswith("idle") else 1, unpriced))
            for field in ("excess_usd", "known_spend_share", "cost_per_break"):
                self.assertIsNone(row[field], (label, field))
        self.assertEqual(document["groups"][1]["unpriced_breaks"], 2)
        for line in table.splitlines():
            if line.startswith(("idle over 1h", "slash command /clear")):
                self.assertNotIn("$0.00", line)
                self.assertNotIn("0.0%", line)
                self.assertEqual(line.split()[-3:-1], ["unpriced", "unpriced"])

    def test_a_fully_priced_cause_keeps_its_dollars_and_share(self):
        rows = [assistant("a", "2026-09-29T10:00:00Z"), assistant("b", "2026-09-29T11:30:00Z")]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            row = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)["scopes"]["all"]["causes"][0]
        self.assertEqual(row["unpriced_breaks"], 0)
        self.assertAlmostEqual(row["excess_usd"], 0.122)
        self.assertAlmostEqual(row["cost_per_break"], 0.122)
        self.assertGreater(row["known_spend_share"], 0)


class PricingTests(unittest.TestCase):
    def break_row(self, **current):
        # The previous call sent 70k; the current one reads 20k and writes 40k, all rewritten.
        rows = [assistant("a", "2026-09-29T12:00:00Z"),
                assistant("b", "2026-09-29T12:01:00Z", **current)]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual(causes(result), [("unexplained", 1)])
        return result["scopes"]["all"]["causes"][0]

    def test_a_transcript_break_prices_each_write_tier_through_the_shared_authority(self):
        call, reason = rebuilds.call_from(assistant("b", "2026-09-29T12:01:00Z"), [])
        self.assertIsNone(reason)
        self.assertEqual((call["cache_write_5m"], call["cache_write_1h"]), (20000, 20000))
        # 20k x 5-minute $2.50 + 20k x 1-hour $4.00 - 40k x read $0.20.
        self.assertAlmostEqual(self.break_row()["excess_usd"], 0.122)

    def test_a_call_without_a_tier_split_is_unknown_and_priced_at_the_base_write_rate(self):
        call, reason = rebuilds.call_from(assistant("b", "2026-09-29T12:01:00Z", split=False), [])
        self.assertIsNone(reason)
        self.assertEqual((call["cache_write_5m"], call["cache_write_1h"]), (None, None))
        # 40k x base write $2.50 - 40k x read $0.20, as `tokens_cost` prices an unsplit row.
        self.assertAlmostEqual(self.break_row(split=False)["excess_usd"], 0.092)

    def test_an_idle_gap_without_a_tier_split_is_not_read_as_five_minute_ttl(self):
        rows = [assistant("a", "2026-09-29T12:00:00Z"),
                assistant("b", "2026-09-29T12:06:40Z", split=False)]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual(causes(result), [("idle 5-60 min, no event", 1)])


class SyntheticTurnTests(unittest.TestCase):
    def test_a_synthetic_turn_is_no_call_and_keeps_the_markers_before_it(self):
        rows = [assistant("a", "2026-09-29T12:00:00Z"),
                {"type": "attachment", "timestamp": "2026-09-29T12:00:10Z",
                 "attachment": {"type": "date_change"}},
                synthetic("s", "2026-09-29T12:00:20Z"),
                assistant("b", "2026-09-29T12:01:00Z")]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_session(tmp, rows)
            calls, counts = rebuilds.read_calls(path, 0)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual([call["id"] for call in calls], ["a", "b"])
        self.assertEqual(counts["synthetic_lines"], 1)
        self.assertEqual(causes(result), [("date changed", 1)])
        self.assertEqual(result["scopes"]["all"]["comparisons"], 1)
        self.assertIn("1 synthetic turn(s)", "\n".join(rebuilds.render(result)))


class TranscriptBoundaryTests(unittest.TestCase):
    EARLY = "2026-09-29T12:00:00Z"
    LATE = "2026-09-29T12:01:00Z"

    def test_shortfall_below_twenty_thousand_is_not_a_break(self):
        rows = [assistant("a", self.EARLY, read=40000, write=10000, one_hour=5000),
                assistant("b", self.LATE, read=40001, write=19999, one_hour=9999)]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual(result["scopes"]["all"]["causes"], [])
        self.assertEqual(result["scopes"]["all"]["comparisons"], 1)

    def test_repeated_request_lines_sidechains_and_old_lines_do_not_become_calls(self):
        rows = [assistant("old", "2026-09-28T12:00:00Z"),
                assistant("a", self.EARLY, read=40000, write=10000, one_hour=5000),
                assistant("a", self.EARLY),
                assistant("child", self.LATE, sidechain=True),
                assistant("b", self.LATE)]
        cutoff = rebuilds.timestamp("2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_session(tmp, rows)
            calls, counts = rebuilds.read_calls(path, cutoff)
        self.assertEqual([call["id"] for call in calls], ["a", "b"])
        self.assertEqual(counts["outside_window"], 1)
        self.assertEqual(counts["duplicate_call_lines"], 1)
        self.assertEqual(counts["sidechain_lines"], 1)

    def test_a_transcript_reachable_through_two_paths_is_read_once(self):
        rows = [assistant("a", self.EARLY), assistant("b", self.LATE)]
        with tempfile.TemporaryDirectory() as tmp:
            original = write_session(tmp, rows, "one")
            other = Path(tmp) / "two"
            other.mkdir()
            os.symlink(str(original), str(other / "same.jsonl"))
            paths, duplicates = rebuilds.transcript_paths(tmp)
        self.assertEqual((len(paths), duplicates), (1, 1))

    def test_unpriced_and_malformed_input_remain_explicit(self):
        rows = [assistant("a", self.EARLY, model="unknown-model"),
                {"type": "assistant", "timestamp": self.EARLY, "message": {"usage": {}}},
                assistant("b", self.LATE, model="unknown-model")]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        scope = result["scopes"]["all"]
        self.assertEqual((scope["unpriced_calls"], scope["unpriced_breaks"]), (2, 1))
        self.assertIsNone(scope["causes"][0]["cost_per_break"])
        self.assertEqual(result["files"]["malformed_lines"], 1)
        text = "\n".join(rebuilds.render(result))
        self.assertIn("unpriced: 2 call(s), 1 break(s)", text)
        self.assertIn("1 malformed line(s)", text)

    def test_valid_untimed_markers_are_not_malformed_and_remain_attributable(self):
        rows = [assistant("a", self.EARLY), {"type": "mode"}, assistant("b", self.LATE)]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_session(tmp, rows)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("not-json\n")
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual(result["files"]["untimed_lines"], 1)
        self.assertEqual(result["files"]["malformed_lines"], 1)
        self.assertEqual(result["scopes"]["all"]["causes"][0]["cause"],
                         "session reloaded (mode marker)")

    def test_two_hundred_in_window_calls_enter_the_long_scope(self):
        rows = [assistant(str(i), "2026-09-29T12:%02d:%02dZ" % (i // 60, i % 60),
                          read=50000, write=0, one_hour=0) for i in range(200)]
        with tempfile.TemporaryDirectory() as tmp:
            write_session(tmp, rows)
            result = rebuilds.analyse(tmp, 0, 30, TABLE, PRICING)
        self.assertEqual(result["scopes"]["long"]["sessions"], 1)
        self.assertEqual(result["scopes"]["long"]["calls"], 200)


if __name__ == "__main__":
    unittest.main()
