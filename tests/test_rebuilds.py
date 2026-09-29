# SPDX-License-Identifier: MIT
"""Prompt-cache rebuild attribution from local Claude Code transcripts."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
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
              model="claude-sonnet-5", version="2.0.20", sidechain=False):
    return {"type": "assistant", "requestId": identity, "timestamp": stamp,
            "version": version, "isSidechain": sidechain,
            "message": {"id": "msg-" + identity, "model": model,
                        "usage": {"input_tokens": 10000, "output_tokens": 100,
                                  "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write,
                                  "cache_creation": {"ephemeral_1h_input_tokens": one_hour}}}}


def write_session(root, rows, project="project", name="session.jsonl"):
    path = Path(root) / project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


class FixtureAttributionTests(unittest.TestCase):
    def test_every_designed_cause_has_a_transcript_and_is_attributed(self):
        expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        result = rebuilds.analyse(FIXTURES, 0, 30, TABLE, PRICING)
        found = {row["cause"]: row["breaks"] for row in result["scopes"]["all"]["causes"]}
        self.assertEqual(found, {cause: 1 for cause in expected.values()})
        self.assertEqual(result["files"]["files_read"], len(expected))
        for slug in expected:
            self.assertTrue((FIXTURES / slug / "session.jsonl").is_file(), slug)

    def test_usage_dispatch_reads_transcripts_without_touching_the_ledger(self):
        original_root = HARNESS.claude_projects_root
        original_ledger = HARNESS.usage_ledger
        HARNESS.claude_projects_root = lambda: FIXTURES
        HARNESS.usage_ledger = lambda path: self.fail("rebuild report read the usage ledger")
        args = argparse.Namespace(action=None, days=30, by="rebuild", rules=False, stance=None,
                                  rescan=True, conflicts=False)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = HARNESS.cmd_usage(args)
        finally:
            HARNESS.claude_projects_root = original_root
            HARNESS.usage_ledger = original_ledger
        self.assertEqual(code, 0)
        self.assertIn("Cache rebuild attribution is retrospective", output.getvalue())
        self.assertIn("20 file(s) read once", output.getvalue())

    def test_usage_rejects_rules_and_stance_for_the_rebuild_report(self):
        for rules, stance in ((True, None), (False, "cost")):
            args = argparse.Namespace(action=None, days=30, by="rebuild", rules=rules,
                                      stance=stance, rescan=False, conflicts=False)
            with self.subTest(rules=rules, stance=stance), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(HARNESS.cmd_usage(args), 2)

    def test_break_cost_prices_each_write_tier_through_the_shared_authority(self):
        call = {"model": "claude-sonnet-5", "cache_write": 40000,
                "cache_write_5m": 20000, "cache_write_1h": 20000}
        # 20k x 5-minute $2.50 + 20k x 1-hour $4.00 - 40k x read $0.20.
        self.assertAlmostEqual(rebuilds._break_cost(call, 40000, TABLE, PRICING), 0.122)


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
