# SPDX-License-Identifier: MIT
"""Unit tests for the cache-prefix figure. Run: python3 -m unittest discover tests"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import cache_prefix  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = _load("harness_cache_prefix", REPO / "bin" / "harness")

NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def session(session_id, read, write, **extra):
    """A ledger session row carrying whatever cache fields the case is about."""
    row = {"kind": "session", "runtime": "claude-code", "session_id": session_id,
           "repo": "agent-harness", "models": ["model-a"], "turns": 10,
           "input": 100, "output": 1000, "started": NOW, "ended": NOW}
    if read is not None:
        row["cache_read"] = read
    if write is not None:
        row["cache_write"] = write
    row.update(extra)
    return row


def subagent(session_id, read, write, **extra):
    """A Claude Code subagent row, whose tokens the session row already folds in."""
    row = {"kind": "subagent", "runtime": "claude-code", "session_id": session_id,
           "agent_type": "worker-a", "turns": 4, "cache_read": read, "cache_write": write,
           "started": NOW, "ended": NOW}
    row.update(extra)
    return row


def slice_(read, write, turns):
    return {"input": 0, "output": 0, "cache_read": read, "cache_write": write, "turns": turns}


class MissRatioTests(unittest.TestCase):
    def test_a_clean_session_reports_a_low_ratio_over_its_own_totals(self):
        self.assertAlmostEqual(cache_prefix.miss_ratio(session("s-1", 99000, 1000)), 0.01)

    def test_every_token_written_rather_than_served_is_a_full_miss(self):
        self.assertEqual(cache_prefix.miss_ratio(session("s-1", 0, 5000)), 1.0)

    def test_a_row_with_no_cache_fields_is_unknown_rather_than_zero(self):
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", None, None)))

    def test_one_missing_field_is_unknown_because_the_pair_is_the_denominator(self):
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", 5000, None)))
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", None, 5000)))

    def test_two_zeroes_are_unknown_because_nothing_was_served_or_written(self):
        # Codex writes this shape: it exports no per-turn cache figures at all.
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", 0, 0)))

    def test_a_runtime_that_reports_no_writes_at_all_is_unknown(self):
        # The Codex shape: a real cached-read figure, and no cache-write figure to divide by.
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", 60000000, 0, runtime="codex")))
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", 60000000, None, runtime="codex")))

    def test_reads_against_no_writes_are_a_held_prefix_where_writes_are_reported(self):
        self.assertEqual(cache_prefix.miss_ratio(session("s-1", 60000, 0)), 0.0)

    def test_an_unreadable_field_is_unknown_rather_than_an_exception(self):
        self.assertIsNone(cache_prefix.miss_ratio(session("s-1", "lots", 10)))


class StepTests(unittest.TestCase):
    def test_a_session_that_held_its_prefix_names_no_step(self):
        row = session("s-1", 99000, 1000, days={"2026-09-18": slice_(50000, 500, 5),
                                                "2026-09-19": slice_(49000, 500, 5)})
        self.assertIsNone(cache_prefix.step(row))

    def test_one_mid_task_step_is_named_by_the_turn_the_slice_opens_on(self):
        row = session("s-1", 60000, 40000, days={"2026-09-18": slice_(50000, 1000, 6),
                                                 "2026-09-19": slice_(10000, 39000, 4)})
        found = cache_prefix.step(row)
        self.assertEqual((found["turn"], found["day"]), (7, "2026-09-19"))
        self.assertGreater(found["after"] - found["before"], cache_prefix.MISS_STEP)

    def test_drift_under_the_threshold_is_not_a_step(self):
        row = session("s-1", 90000, 10000, days={"2026-09-18": slice_(50000, 2000, 5),
                                                 "2026-09-19": slice_(40000, 8000, 5)})
        self.assertIsNone(cache_prefix.step(row))

    def test_the_sharpest_rise_wins_when_a_session_stepped_twice(self):
        row = session("s-1", 1, 1, days={"2026-09-17": slice_(9000, 1000, 3),
                                         "2026-09-18": slice_(6000, 4000, 3),
                                         "2026-09-19": slice_(1000, 9000, 3)})
        found = cache_prefix.step(row)
        self.assertEqual((found["turn"], found["day"]), (7, "2026-09-19"))

    def test_a_slice_with_no_ratio_breaks_the_chain_rather_than_being_compared_through(self):
        # The two readable slices differ by 80 points, so a surviving chain would print them as
        # adjacent days they never were.
        row = session("s-1", 1, 1, days={"2026-09-17": slice_(9000, 1000, 2),
                                         "2026-09-18": slice_(0, 0, 2),
                                         "2026-09-19": slice_(1000, 9000, 2)})
        self.assertIsNone(cache_prefix.step(row))

    def test_a_held_day_is_a_ratio_and_does_not_hide_the_step_after_it(self):
        row = session("s-1", 51000, 9000, days={"2026-09-18": slice_(50000, 0, 5),
                                                "2026-09-19": slice_(1000, 9000, 5)})
        found = cache_prefix.step(row)
        self.assertEqual((found["turn"], found["before"], found["after"]), (6, 0.0, 0.9))

    def test_a_session_with_no_day_slices_reports_its_ratio_and_no_step(self):
        row = session("s-1", 9000, 1000)
        self.assertIsNone(cache_prefix.step(row))
        self.assertAlmostEqual(cache_prefix.miss_ratio(row), 0.1)


class FoldedSubagentTests(unittest.TestCase):
    """A Claude Code session row holds its subagents' tokens; its prefix does not."""

    def test_subagent_tokens_are_subtracted_before_the_ratio_is_taken(self):
        row = session("s-1", 900000, 320000, subagents=2)
        children = [subagent("s-1", 0, 150000), subagent("s-1", 0, 150000)]
        found = cache_prefix.figure(row, children)
        self.assertEqual((found["cache_read"], found["cache_write"]), (900000, 20000))
        self.assertLess(found["ratio"], 0.03)
        # Left folded in, the same session reads as one that re-bought a quarter of its prefix.
        self.assertGreater(cache_prefix.miss_ratio(row), 0.25)

    def test_fewer_child_rows_than_the_session_spawned_is_unknown(self):
        row = session("s-1", 900000, 320000, subagents=6)
        found = cache_prefix.figure(row, [subagent("s-1", 0, 150000)])
        self.assertIsNone(found["ratio"])
        self.assertIsNone(found["cache_write"])

    def test_a_child_row_missing_a_cache_field_is_unknown(self):
        row = session("s-1", 900000, 320000, subagents=1)
        child = subagent("s-1", 0, 150000)
        del child["cache_write"]
        self.assertIsNone(cache_prefix.figure(row, [child])["ratio"])

    def test_a_subtraction_that_goes_negative_is_unknown(self):
        row = session("s-1", 900000, 20000, subagents=1)
        self.assertIsNone(cache_prefix.figure(row, [subagent("s-1", 0, 150000)])["ratio"])

    def test_a_session_that_spawned_reports_no_step_because_its_slices_fold_them_in(self):
        row = session("s-1", 60000, 40000, subagents=1,
                      days={"2026-09-18": slice_(50000, 1000, 6),
                            "2026-09-19": slice_(10000, 39000, 4)})
        self.assertIsNone(cache_prefix.step(row, [subagent("s-1", 0, 38000)]))
        self.assertTrue(cache_prefix.figure(row, [subagent("s-1", 0, 38000)])["step_blocked"])

    def test_a_codex_session_folds_nothing_in_so_nothing_is_subtracted(self):
        row = session("s-1", 900000, 320000, runtime="codex", subagents=2)
        self.assertEqual(cache_prefix.own_cache(row, []), (900000, 320000))

    def test_the_whole_ledger_is_indexed_so_a_child_outside_the_window_still_comes_out(self):
        rows = [session("s-1", 900000, 320000, subagents=1),
                subagent("s-1", 0, 300000, ended="2020-01-01T00:00:00Z")]
        found = cache_prefix.figures(rows, cutoff="2026-01-01")
        self.assertEqual([f["cache_write"] for f in found], [20000])


class FigureTests(unittest.TestCase):
    def test_mixed_models_in_one_session_report_one_ratio_and_every_model(self):
        row = session("s-1", 80000, 20000, models=["model-a", "model-b"])
        found = cache_prefix.figure(row)
        self.assertEqual(found["models"], ["model-a", "model-b"])
        self.assertAlmostEqual(found["ratio"], 0.2)

    def test_a_delegated_run_is_not_a_session_and_is_left_out(self):
        rows = [session("s-1", 9000, 1000),
                session("a-1", 0, 5000, kind="subagent"),
                session("w-1", 0, 5000, kind="worker")]
        self.assertEqual([f["session_id"] for f in cache_prefix.figures(rows)], ["s-1"])

    def test_a_row_older_than_the_window_is_dropped(self):
        old = session("s-0", 9000, 1000, ended="2020-01-01T00:00:00Z")
        self.assertEqual([f["session_id"] for f in cache_prefix.figures([old, session("s-1", 1, 1)],
                                                                       cutoff="2026-01-01")],
                         ["s-1"])

    def test_structured_summary_reuses_the_figures_and_counts_unknowns(self):
        data = cache_prefix.summary([session("s-known", 9000, 1000),
                                     session("s-unknown", None, None)])
        self.assertEqual([item["session_id"] for item in data["groups"]],
                         ["s-known", "s-unknown"])
        self.assertEqual(data["totals"], {"sessions": 2, "stepped": 0, "unknown": 1})


class ReportTests(unittest.TestCase):
    """The printed report, over a ledger written to a disposable HOME."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ.pop("HARNESS_QUIET", None)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def write(self, rows):
        path = Path(self.tmp.name) / ".local" / "state" / "agent-harness" / "usage.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def report(self, rows, **kwargs):
        self.write(rows)
        args = argparse.Namespace(days=kwargs.pop("days", 30), by=kwargs.pop("by", "prefix"),
                                  rules=kwargs.pop("rules", False),
                                  stance=kwargs.pop("stance", None), rescan=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rc = harness.cmd_usage(args)
        return buf.getvalue()

    def test_it_prints_a_ratio_per_session_and_names_the_turn_it_jumped(self):
        out = self.report([session("s-clean", 99000, 1000),
                           session("s-step", 60000, 40000,
                                   days={"2026-09-18": slice_(50000, 1000, 6),
                                         "2026-09-19": slice_(10000, 39000, 4)})])
        self.assertEqual(self.rc, 0)
        self.assertIn("1%", out)
        self.assertIn("40%", out)
        self.assertIn("turn 7 (2026-09-19): 2% -> 80%", out)
        self.assertIn("2 session(s), 1 with a mid-session step", out)

    def test_a_session_with_no_cache_fields_prints_unknown_never_zero(self):
        out = self.report([session("s-codex", 0, 0, runtime="codex"),
                           session("s-bare", None, None)])
        rows = [line.rstrip() for line in out.splitlines() if line.startswith("s-")]
        self.assertEqual(len(rows), 2)
        for row in rows:
            # The miss column is the last figure on the line, and the step column is empty.
            self.assertTrue(row.endswith("unknown  -"), msg=row)
        self.assertNotIn("0%", out)
        self.assertIn("2 reporting no cache figures", out)

    def test_it_says_that_the_figure_measures_rather_than_enforces(self):
        out = self.report([session("s-1", 9000, 1000)])
        self.assertIn("nothing here denies a prefix change", out)

    def test_an_empty_window_says_so_rather_than_printing_an_empty_table(self):
        out = self.report([])
        self.assertEqual(self.rc, 0)
        self.assertIn("no sessions recorded", out)

    def test_a_session_that_spawned_says_its_step_is_not_measurable(self):
        out = self.report([session("s-1", 900000, 320000, subagents=1,
                                   days={"2026-09-18": slice_(50000, 1000, 6),
                                         "2026-09-19": slice_(10000, 39000, 4)}),
                           subagent("s-1", 0, 300000)])
        self.assertIn("not measurable (subagents)", out)
        self.assertIn("0 with a mid-session step", out)

    def test_a_stance_says_nothing_about_a_prefix_and_the_pair_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.report([session("s-1", 9000, 1000)], stance="cost")
        self.assertEqual(self.rc, 2)
        self.assertEqual(out, "")
        self.assertIn("--rules and --stance do not apply", err.getvalue())

    def test_rules_and_prefix_are_different_questions_and_the_pair_is_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.report([session("s-1", 9000, 1000)], rules=True)
        self.assertEqual(self.rc, 2)
        self.assertEqual(out, "")
        self.assertIn("--rules and --stance do not apply", err.getvalue())


class CapabilityTests(unittest.TestCase):
    def test_the_codex_adapter_records_that_it_exports_no_cache_figures(self):
        limits = json.loads((REPO / "adapters" / "codex" / "capabilities.json")
                            .read_text(encoding="utf-8"))["limitations"]
        lines = [line for line in limits if "cache_write" in line]
        self.assertEqual(len(lines), 1, msg=limits)
        # It reports a cached-read figure; only the write side is missing, so the note must not
        # claim both fields come through as zeroes.
        self.assertIn("cache_read", lines[0])
        self.assertNotIn("zero", lines[0])

    def test_the_documentation_states_that_the_figure_does_not_enforce(self):
        text = (REPO / "docs" / "usage.md").read_text(encoding="utf-8")
        self.assertIn("--by prefix", text)
        self.assertIn("measures", text)
        # What the figure excludes, and which of several steps it keeps.
        self.assertIn("are subtracted from the", text)
        self.assertIn("sharpest rise, not the first", text)


if __name__ == "__main__":
    unittest.main()
