# SPDX-License-Identifier: MIT
"""The per-run cache-miss ratio the replay records beside its normalised cost, on recorded CLI
output only: no test here launches an agent. Run: python3 -m unittest discover tests"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
import isolation
from test_harness import REPO
from test_cost_bench import Launch, TASK, options  # the runner's own fakes, not a second set

sys.path.insert(0, str(REPO / "lib"))
from harness_core import cache_prefix  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = load("cost_bench")


def result(cost=0.5, error=False):
    return {"type": "result", "subtype": "error_during_execution" if error else "success",
            "is_error": error, "num_turns": 3, "total_cost_usd": cost,
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40}}


def turn(thread=None, read=0, write=0, model="claude-test-20260101"):
    """An assistant message carrying the per-turn usage block the ratio is summed over."""
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"model": model,
                        "usage": {"cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write}}}


def silent_turn(thread=None):
    """A turn whose usage block reports tokens and no cache fields at all."""
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"model": "claude-test-20260101",
                        "usage": {"input_tokens": 12, "output_tokens": 34}}}


def row(arm, cost=1.0, miss=0.1, error=False, **extra):
    out = {"arm": arm, "rep": 1, "passed": not error, "error": error, "cost_usd": cost,
           "cost_normalised_usd": cost, "cache_miss_ratio": miss, "date": "2026-01-01",
           "harness_version": "9.9.9", "harness_sha": "a" * 40, "tag": "candidate",
           "model": "claude-test", "cli_version": "1.0", "bucket": "", "predicted_ratio": None,
           "task": "demo", "change_note": ""}
    out.update(extra)
    return out


class PerRunRatioTests(unittest.TestCase):
    def test_the_ratio_is_the_write_share_of_every_turn_the_run_opened(self):
        stream = [turn(None, read=9000, write=1000), turn("toolu_1", read=0, write=10000),
                  result()]
        parsed = BENCH.parse_result(json.dumps(stream))
        self.assertEqual(parsed["cache_miss_ratio"], 0.55)  # 11000 written of 20000 served

    def test_it_is_the_figure_the_ledger_reports_for_a_session(self):
        """One definition, imported rather than restated: a replay row and `usage --by prefix`
        that disagreed about the same arithmetic would be worse than either alone."""
        stream = [turn(None, read=7000, write=3000), result()]
        parsed = BENCH.parse_result(json.dumps(stream))
        ledger = cache_prefix.miss_ratio({"cache_read": 7000, "cache_write": 3000}, "")
        self.assertEqual(parsed["cache_miss_ratio"], round(ledger, 4))

    def test_output_with_no_per_turn_cache_figures_is_unknown_and_never_zero(self):
        self.assertIsNone(BENCH.parse_result(json.dumps([result()]))["cache_miss_ratio"])

    def test_turns_reporting_neither_reads_nor_writes_are_unknown_too(self):
        stream = [turn(None, read=0, write=0), result()]
        self.assertIsNone(BENCH.parse_result(json.dumps(stream))["cache_miss_ratio"])

    def test_one_turn_reporting_no_cache_fields_makes_the_whole_run_unknown(self):
        """Counted as two zeroes it would dilute the run's ratio towards a held prefix, which is
        the one reading the ledger's rule forbids: unknown is never averaged with known."""
        stream = [turn(None, read=0, write=9000), silent_turn(), result()]
        self.assertIsNone(BENCH.parse_result(json.dumps(stream))["cache_miss_ratio"])

    def test_a_turn_missing_one_of_the_two_cache_fields_is_unknown_too(self):
        partial = {"type": "assistant", "parent_tool_use_id": None,
                   "message": {"model": "claude-test-20260101",
                               "usage": {"cache_read_input_tokens": 500}}}
        stream = [turn(None, read=1000, write=1000), partial, result()]
        self.assertIsNone(BENCH.parse_result(json.dumps(stream))["cache_miss_ratio"])

    def test_a_run_that_served_its_whole_prefix_reports_zero_not_unknown(self):
        stream = [turn(None, read=5000, write=0), result()]
        self.assertEqual(BENCH.parse_result(json.dumps(stream))["cache_miss_ratio"], 0.0)

    def test_the_ratio_sits_beside_the_normalised_cost_on_every_scored_row(self):
        """Both are derived from the same stream and neither replaces the other: the normalised
        cost says what a cold prefix would have cost, the ratio says how cold it actually was."""
        stream = json.dumps([turn(None, read=9000, write=1000), result()])
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([stream] * 2))
        self.assertEqual([r["arm"] for r in rows], list(BENCH.ARMS))
        for scored in rows:
            self.assertEqual(scored["cache_miss_ratio"], 0.1)
            self.assertIsNotNone(scored["cost_normalised_usd"])

    def test_an_errored_row_reports_unknown_rather_than_a_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch(["garbage"] * 2))
        self.assertEqual([r["error"] for r in rows], [True, True])
        self.assertIsNone(rows[0]["cache_miss_ratio"])

    def test_a_run_that_errored_after_spending_cache_still_reports_unknown(self):
        """The turns are real and the tool counts are kept as diagnostics, but an aborted run's
        prefix is not the prefix it would have held, so the figure is not its spend."""
        stream = json.dumps([turn(None, read=9000, write=1000), result(error=True)])
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([stream] * 2))
        self.assertEqual([r["error"] for r in rows], [True, True])
        for errored in rows:
            self.assertIsNone(errored["cache_miss_ratio"])
            self.assertIsNotNone(errored["first_call_cache_write"])


class BackfillTests(unittest.TestCase):
    """`backfill_rows` fingerprints the inherited profile, so every case here runs under a
    temporary home rather than globbing the developer's own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_environ = dict(os.environ)
        isolation.isolate_home(self.home)
        self.raw = self.home / "raw"
        self.raw.mkdir()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_environ)
        self.tmp.cleanup()

    def kept(self, name, stream):
        (self.raw / name).write_text(stream, encoding="utf-8")

    def test_a_row_written_before_the_field_existed_gets_it_from_the_raw_output(self):
        self.kept("demo-harness-1.json", json.dumps([turn(None, read=9000, write=1000), result()]))
        rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "harness", "rep": 1}],
                                            self.raw, home=self.home)
        self.assertEqual(missing, [])
        self.assertEqual(rows[0]["cache_miss_ratio"], 0.1)

    def test_a_row_with_no_raw_output_stays_unknown_rather_than_borrowing_one(self):
        rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "bare", "rep": 1}],
                                            self.raw, home=self.home)
        self.assertEqual(len(missing), 1)
        self.assertIsNone(rows[0]["cache_miss_ratio"])

    def test_a_missing_raw_file_preserves_the_live_evidence(self):
        """Missing raw output cannot disprove diagnostic evidence the original run recorded."""
        live = {"task": "demo", "arm": "bare", "rep": 1, "cache_miss_ratio": 0.42,
                "spawns": 3, "tool_counts": {"Bash": 2}}
        rows, missing = BENCH.backfill_rows([live], self.raw, home=self.home)
        self.assertEqual(len(missing), 1)
        self.assertEqual(rows[0]["cache_miss_ratio"], 0.42)
        self.assertEqual(rows[0]["spawns"], 3)
        self.assertEqual(rows[0]["tool_counts"], {"Bash": 2})

    def test_an_errored_row_is_not_given_a_ratio_the_runner_would_have_refused(self):
        self.kept("demo-bare-1.json", json.dumps([turn(None, read=9000, write=1000),
                                                  result(error=True)]))
        errored = {"task": "demo", "arm": "bare", "rep": 1, "error": True}
        rows, missing = BENCH.backfill_rows([errored], self.raw, home=self.home)
        self.assertEqual(missing, [])
        self.assertIsNone(rows[0]["cache_miss_ratio"])
        self.assertEqual(rows[0]["first_call_cache_write"], 1000)


class HistoryTests(unittest.TestCase):
    def test_each_arm_carries_the_mean_of_the_runs_that_reported_a_figure(self):
        rows = [row("bare", miss=0.2), row("bare", miss=0.4), row("harness", miss=0.1)]
        self.assertEqual(BENCH.cache_miss(rows), {"bare": 0.3, "harness": 0.1})

    def test_an_arm_with_no_figure_is_none_and_an_errored_run_is_left_out(self):
        rows = [row("bare", miss=None), row("bare", miss=0.9, error=True),
                row("harness", miss=0.2), row("harness", miss=None)]
        self.assertEqual(BENCH.cache_miss(rows), {"bare": None, "harness": 0.2})

    def test_the_history_row_carries_it_without_changing_the_upsert_key(self):
        rows = [row("bare", cost=1.0, miss=0.5), row("harness", cost=0.5, miss=0.25)]
        made = BENCH.history_row(rows, "s1")
        self.assertEqual(made["cache_miss"], {"bare": 0.5, "harness": 0.25})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            BENCH.upsert_history(path, made)
            kept = BENCH.upsert_history(path, BENCH.history_row(rows, "s1"))
        self.assertEqual(len(kept), 1)

    def test_the_rendered_table_shows_both_arms_and_an_older_row_still_renders(self):
        rows = [row("bare", cost=1.0, miss=0.5), row("harness", cost=0.5, miss=0.25)]
        made = BENCH.history_row(rows, "s1")
        older = dict(made)
        older.pop("cache_miss")
        older["date"] = "2025-12-01"
        text = BENCH.render_history([older, made])
        self.assertIn("| Cache miss, bare | Cache miss, harness |", text)
        self.assertIn("| 0.500 | 0.250 |", text)
        self.assertIn("| n/a | n/a |", text)
        header, rule = text.splitlines()[4], text.splitlines()[5]
        self.assertEqual(header.count("|"), rule.count("|"))
        data = [line for line in text.splitlines()
                if line.startswith("| 2") and line.split("|")[1].strip()[:2] == "20"]
        self.assertEqual(len(data), 2)  # the legacy row is width-checked too, not skipped
        for line in data:
            self.assertEqual(line.count("|"), header.count("|"))


if __name__ == "__main__":
    unittest.main()
