# SPDX-License-Identifier: MIT
"""Unit tests for the price table: `policy/prices.json` and the dollars `harness usage` reports.

Run: python3 -m unittest discover tests
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = _load("harness", REPO / "bin" / "harness")
usage_log = _load("usage_log", REPO / "claude" / "hooks" / "usage-log.py")

PRICES = json.loads((REPO / "policy" / "prices.json").read_text(encoding="utf-8"))
STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# A rate table small enough to reason about: every figure here is invented for the test and
# none of it is read from a provider.
TABLE = {
    "test-model": {"input": 10.0, "output": 50.0, "cache_read": 0.25,
                   "cache_write": 12.5, "cache_write_5m": 12.5, "cache_write_1h": 20.0,
                   "as_of": "2026-09-21", "source": "https://example.invalid/pricing"},
    "test-model-mini": {"input": 1.0, "output": 5.0, "cache_read": 0.1,
                        "cache_write": 1.25, "as_of": "2026-09-21",
                        "source": "https://example.invalid/pricing"},
}


def row(**extra):
    base = {"kind": "session", "runtime": "claude-code", "session_id": "s-1", "ended": STAMP,
            "models": ["test-model"], "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    base.update(extra)
    return base


@contextlib.contextmanager
def loud():
    prior = os.environ.pop("HARNESS_QUIET", None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        if prior is not None:
            os.environ["HARNESS_QUIET"] = prior


class ShippedTableTests(unittest.TestCase):
    """The file is the claim. Every entry has to carry the evidence for its own numbers."""

    def test_every_entry_carries_four_rates_a_date_and_a_source(self):
        for name, entry in PRICES["models"].items():
            with self.subTest(model=name):
                self.assertIsNotNone(harness.usable_rate(entry), "incomplete rates")
                self.assertRegex(entry["as_of"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(entry["source"].startswith("https://"), entry["source"])

    def test_no_entry_is_dated_in_the_future(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        for name, entry in PRICES["models"].items():
            with self.subTest(model=name):
                self.assertLessEqual(entry["as_of"], today)

    def test_every_key_is_already_normalised(self):
        # A key that normalisation would rewrite could never be matched, and the table would
        # look complete while the model it names stayed unpriced.
        for name in PRICES["models"]:
            with self.subTest(model=name):
                self.assertEqual(harness.normalise_model(name), name)

    def test_the_file_states_how_each_runtime_maps_onto_the_four_columns(self):
        text = "\n".join(PRICES["_mapping"])
        self.assertIn("cached_input_tokens", text)
        self.assertIn("reasoning_output_tokens", text)
        self.assertIn("cache_write_1h", text)


class ResolutionTests(unittest.TestCase):
    def test_a_dated_id_resolves_to_its_family(self):
        rate = harness.price_for(TABLE, "test-model-20260921")
        self.assertEqual(rate["input"], 10.0)

    def test_a_dated_variant_reaches_its_own_entry_not_a_shorter_one(self):
        # Its date comes off and what is left is a listed id in its own right. Nothing here
        # is a prefix match: `test-model-mini` prices because the table names it.
        self.assertEqual(harness.price_for(TABLE, "test-model-mini-20260921")["input"], 1.0)

    def test_a_bedrock_id_and_a_long_context_suffix_reach_the_same_entry(self):
        for name in ("anthropic.test-model-20260921-v1:0", "us.anthropic.test-model",
                     "test-model[1m]", "Test-Model", "test-model@20260921",
                     "test-model-2026-09-21"):
            with self.subTest(model=name):
                self.assertEqual(harness.price_for(TABLE, name)["input"], 10.0)

    def test_an_unlisted_variant_of_a_listed_family_is_unpriced(self):
        # A word after the family name is a different model at a different price. Charging it
        # at its family's rate would present a known-low figure as a known one.
        for name in ("test-model-pro", "test-model-mini-pro", "test-model-thinking"):
            with self.subTest(model=name):
                self.assertIsNone(harness.price_for(TABLE, name))

    def test_an_unknown_model_has_no_rate(self):
        self.assertIsNone(harness.price_for(TABLE, "some-other-model"))
        self.assertIsNone(harness.price_for(TABLE, ""))

    def test_an_entry_missing_a_rate_is_no_rate_at_all(self):
        partial = {"test-model": {"input": 10.0, "output": 50.0, "cache_read": 0.25}}
        self.assertIsNone(harness.price_for(partial, "test-model"))

    def test_the_shipped_families_resolve_from_the_ids_a_ledger_holds(self):
        table = harness.load_prices({})
        for name, expected in (("claude-fable-5-1", 0.25), ("claude-fable-5", 1.0),
                               ("claude-opus-5[1m]", 0.5),
                               ("anthropic.claude-haiku-4-5-20251001-v1:0", 0.1),
                               ("gpt-5.6-sol", 0.4), ("gpt-5.5", 0.5)):
            with self.subTest(model=name):
                self.assertEqual(harness.price_for(table, name)["cache_read"], expected)

    def test_a_premium_variant_of_a_shipped_family_is_unpriced_not_under_billed(self):
        # The regression this file exists to hold: `gpt-5.5-pro` is $30/$180 where `gpt-5.5`
        # is $5/$30, so a prefix rule would report a sixth of the real bill as if it were the
        # bill. Unpriced until the provider's page is read and an entry is added here.
        table = harness.load_prices({})
        self.assertEqual(table["gpt-5.5"]["input"], 5.0)
        self.assertIsNone(harness.price_for(table, "gpt-5.5-pro"))
        self.assertIsNone(harness.price_for(table, "claude-opus-5-pro"))

    def test_a_variant_prices_once_it_is_named_in_an_override(self):
        table = harness.load_prices({"prices": {"gpt-5.5-pro": {
            "input": 30.0, "output": 180.0, "cache_read": 3.0, "cache_write": 0.0}}})
        self.assertEqual(harness.price_for(table, "gpt-5.5-pro")["output"], 180.0)


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "prices.json"
        self.path.write_text(json.dumps({"schema_version": 1, "models": TABLE}), encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_an_override_replaces_one_rate_and_keeps_the_rest_of_the_entry(self):
        table = harness.load_prices({"prices": {"test-model": {"output": 99.0}}}, self.path)
        self.assertEqual(table["test-model"]["output"], 99.0)
        self.assertEqual(table["test-model"]["input"], 10.0)
        self.assertEqual(table["test-model"]["source"], "https://example.invalid/pricing")

    def test_an_override_can_add_a_model_the_file_does_not_list(self):
        rates = {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.0}
        table = harness.load_prices({"prices": {"Local-Model": rates}}, self.path)
        self.assertEqual(harness.price_for(table, "local-model-v2")["output"], 2.0)

    def test_a_malformed_override_is_ignored_rather_than_crashing_the_report(self):
        table = harness.load_prices({"prices": {"test-model": "free, obviously"}}, self.path)
        self.assertEqual(table["test-model"]["input"], 10.0)

    def test_a_missing_price_file_leaves_an_empty_table_not_an_error(self):
        self.assertEqual(harness.load_prices({}, Path(self.tmp.name) / "nope.json"), {})


class RowCostTests(unittest.TestCase):
    def test_each_column_is_charged_at_its_own_rate(self):
        cost = harness.row_cost(
            row(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000),
            TABLE)
        self.assertAlmostEqual(cost, 10.0 + 50.0 + 0.25 + 12.5)

    def test_a_reported_cache_tier_split_is_charged_tier_by_tier(self):
        cost = harness.row_cost(row(cache_write=1_000_000, cache_write_5m=400_000,
                                    cache_write_1h=600_000), TABLE)
        self.assertAlmostEqual(cost, 0.4 * 12.5 + 0.6 * 20.0)

    def test_a_row_with_no_split_is_charged_at_the_five_minute_rate(self):
        self.assertAlmostEqual(harness.row_cost(row(cache_write=1_000_000), TABLE), 12.5)

    def test_a_split_that_does_not_add_up_falls_back_to_the_single_rate(self):
        # Both tiers zero on a row that wrote a million tokens is a runtime that reported no
        # split, not a million free writes.
        cost = harness.row_cost(row(cache_write=1_000_000, cache_write_5m=0, cache_write_1h=0),
                                TABLE)
        self.assertAlmostEqual(cost, 12.5)

    def test_an_unknown_model_is_unpriced_and_never_zero(self):
        self.assertIsNone(harness.row_cost(row(models=["mystery-model"], output=10), TABLE))

    def test_a_partial_row_is_unpriced(self):
        self.assertIsNone(harness.row_cost(row(output=10, partial=True), TABLE))

    def test_a_row_missing_a_token_count_is_unpriced(self):
        self.assertIsNone(harness.row_cost(row(cache_write=None), TABLE))

    def test_a_session_that_ran_two_models_cannot_be_split_so_it_is_unpriced(self):
        self.assertIsNone(harness.row_cost(
            row(models=["test-model", "test-model-mini"], output=10), TABLE))

    def test_a_subagents_tokens_are_priced_at_its_own_model_and_taken_off_the_parents(self):
        parent = row(output=1_000_000, cache_read=1_000_000)
        child = {"kind": "subagent", "runtime": "claude-code", "session_id": "s-1",
                 "ended": STAMP, "model": "test-model-mini",
                 "input": 0, "output": 400_000, "cache_read": 1_000_000, "cache_write": 0}
        cost = harness.row_cost(parent, TABLE, [child])
        # 600k output at the parent's $50, nothing left of the cache reads, then the child's
        # 400k output at $5 and its million cache reads at $0.10.
        self.assertAlmostEqual(cost, 0.6 * 50.0 + 0.4 * 5.0 + 1.0 * 0.1)

    def test_an_unpriceable_child_leaves_the_whole_session_unpriced(self):
        parent = row(output=1_000_000)
        child = {"kind": "subagent", "runtime": "claude-code", "session_id": "s-1",
                 "model": "mystery-model", "input": 0, "output": 10, "cache_read": 0,
                 "cache_write": 0}
        self.assertIsNone(harness.row_cost(parent, TABLE, [child]))

    def test_a_session_whose_totals_predate_subagent_capture_is_priced_alone(self):
        # The child's tokens are not inside this parent's totals, so subtracting them would
        # bill a negative session; the row is priced exactly as its tokens are reported.
        parent = row(output=100)
        child = {"kind": "subagent", "runtime": "claude-code", "session_id": "s-1",
                 "model": "test-model-mini", "input": 0, "output": 1_000_000,
                 "cache_read": 0, "cache_write": 0}
        self.assertAlmostEqual(harness.row_cost(parent, TABLE, [child]), 100 * 50.0 / 1e6)

    def test_a_session_on_the_same_model_as_its_subagent_is_still_priced(self):
        parent = row(output=1_000_000)
        child = {"kind": "subagent", "runtime": "claude-code", "session_id": "s-1",
                 "model": "test-model", "input": 0, "output": 400_000, "cache_read": 0,
                 "cache_write": 0}
        self.assertAlmostEqual(harness.row_cost(parent, TABLE, [child]), 50.0)


# The same rates with cache writes free, as OpenAI prices them: a Codex rollout reports no
# cache-write count at all, so the column only resolves against a rate of zero.
CODEX_TABLE = dict((name, dict(entry, cache_write=0.0))
                   for name, entry in TABLE.items())


def transcript(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


class BreakdownTests(unittest.TestCase):
    """The largest sessions are the ones that switched models, so they are the ones to price."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home

    def claude_row(self):
        """A two-model Claude Code session: a million output tokens on each of two models."""
        def assistant(mid, model, output):
            return {"type": "assistant", "sessionId": "s-1", "cwd": "",
                    "timestamp": STAMP.replace("Z", ".000Z"),
                    "message": {"id": mid, "model": model, "content": [],
                                "usage": {"input_tokens": 1_000_000, "output_tokens": output,
                                          "cache_read_input_tokens": 0,
                                          "cache_creation_input_tokens": 0}}}
        path = transcript(self.home / "s-1.jsonl", [
            {"type": "user", "sessionId": "s-1", "timestamp": STAMP.replace("Z", ".000Z")},
            assistant("m1", "test-model", 1_000_000),
            assistant("m2", "test-model-mini", 1_000_000),
        ])
        return usage_log.scan(path, "s-1", "")

    def test_a_two_model_claude_session_is_priced_model_by_model(self):
        record = self.claude_row()
        self.assertEqual(record["models"], ["test-model", "test-model-mini"])
        self.assertEqual(sorted(record["by_model"]), ["test-model", "test-model-mini"])
        # A million input and a million output on each: $10 + $50, then $1 + $5.
        self.assertAlmostEqual(harness.row_cost(record, TABLE), 66.0)

    def test_without_the_map_the_same_session_stays_unpriced(self):
        record = self.claude_row()
        record.pop("by_model")
        self.assertIsNone(harness.row_cost(record, TABLE))

    def test_a_map_that_does_not_add_up_is_never_written(self):
        per_message = {"m1": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0,
                              "model": "test-model", "day": "2026-09-21"}}
        self.assertTrue(usage_log.models_agree(usage_log.by_model(per_message),
                                               usage_log.summed(per_message)))
        self.assertFalse(usage_log.models_agree(usage_log.by_model(per_message),
                                                {"input": 99, "output": 2,
                                                 "cache_read": 0, "cache_write": 0}))

    def test_each_part_records_its_largest_prompt_cache_included(self):
        per_message = {"m1": {"input": 10, "output": 2, "cache_read": 90000, "cache_write": 5,
                              "model": "test-model", "day": ""},
                       "m2": {"input": 20, "output": 2, "cache_read": 1000, "cache_write": 0,
                              "model": "test-model", "day": ""},
                       "m3": {"input": 7, "output": 2, "cache_read": 0, "cache_write": 0,
                              "model": "test-model-mini", "day": ""}}
        for slot in per_message.values():
            slot["prompt_known"] = True
        parts = usage_log.by_model(per_message)
        self.assertEqual(parts["test-model"]["max_prompt"], 90015)
        self.assertEqual(parts["test-model-mini"]["max_prompt"], 7)
        self.assertTrue(usage_log.models_agree(parts, usage_log.summed(per_message)))

    def test_a_record_omitting_a_prompt_field_leaves_the_largest_prompt_unknown(self):
        per_message = {}
        full = {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 0}
        usage_log.record_usage(per_message, "m1", full, model="test-model")
        usage_log.record_usage(per_message, "m2", {"input_tokens": 10, "output_tokens": 2,
                                                   "cache_read_input_tokens": 99000}, model="test-model")
        usage_log.record_usage(per_message, "m3", full, model="test-model-mini")
        parts = usage_log.by_model(per_message)
        self.assertNotIn("max_prompt", parts["test-model"])
        self.assertEqual(parts["test-model-mini"]["max_prompt"], 510)

    def test_a_record_naming_no_model_drops_the_whole_map(self):
        per_message = {"m1": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0,
                              "model": "test-model", "day": ""},
                       "m2": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0,
                              "model": "", "day": ""}}
        self.assertEqual(usage_log.by_model(per_message), {})

    def test_a_disagreeing_map_on_a_row_leaves_it_unpriced(self):
        # Nothing writes one, but a hand-edited or half-written ledger must not be priced from
        # a map that does not describe the row it sits on.
        record = self.claude_row()
        record["by_model"]["test-model"]["output"] = 0
        self.assertNotAlmostEqual(harness.row_cost(record, TABLE), 66.0)
        record["by_model"]["mystery-model"] = {"input": 0, "output": 0, "cache_read": 0,
                                               "cache_write": 0}
        self.assertIsNone(harness.row_cost(record, TABLE))

    def test_a_synthetic_part_that_spent_nothing_does_not_unprice_the_row(self):
        record = self.claude_row()
        record["models"].append("<synthetic>")
        record["by_model"]["<synthetic>"] = {"input": 0, "output": 0, "cache_read": 0,
                                             "cache_write": 0}
        self.assertAlmostEqual(harness.row_cost(record, TABLE), 66.0)

    def test_a_synthetic_part_carrying_tokens_leaves_the_row_unpriced(self):
        record = self.claude_row()
        record["by_model"]["<synthetic>"] = {"input": 5, "output": 0, "cache_read": 0,
                                             "cache_write": 0}
        self.assertIsNone(harness.row_cost(record, TABLE))

    def codex_row(self):
        """A two-model Codex thread: cumulative snapshots either side of a model change."""
        def line(kind, payload):
            return {"timestamp": STAMP, "type": kind, "payload": payload}

        def snapshot(input_tokens, cached, output):
            return line("event_msg", {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": input_tokens, "cached_input_tokens": cached,
                "output_tokens": output, "total_tokens": input_tokens + output}}})

        path = transcript(self.home / "rollout.jsonl", [
            line("session_meta", {"id": "c-1", "cwd": "", "source": "cli"}),
            line("turn_context", {"model": "test-model", "effort": "medium"}),
            snapshot(1_000_000, 0, 1_000_000),
            line("turn_context", {"model": "test-model-mini", "effort": "medium"}),
            snapshot(3_000_000, 1_000_000, 2_000_000),
        ])
        return usage_log.scan_codex(path, "c-1", "")

    def test_a_two_model_codex_thread_is_priced_model_by_model(self):
        record = self.codex_row()
        self.assertEqual(record["models"], ["test-model", "test-model-mini"])
        # First model: 1M billed input, no cache reads, 1M output. Second: 2M more input of
        # which 1M was cached, so 1M billed and 1M at the cache rate, and 1M more output.
        # No `cache_write` on the parts, because the rollout names none on the row either.
        self.assertEqual(record["by_model"]["test-model"],
                         {"input": 1_000_000, "output": 1_000_000, "cache_read": 0})
        self.assertEqual(record["by_model"]["test-model-mini"],
                         {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000})
        self.assertAlmostEqual(harness.row_cost(record, CODEX_TABLE),
                               (10.0 + 50.0) + (1.0 + 5.0 + 0.1))

    def test_a_codex_part_is_unpriced_when_its_missing_column_is_billed(self):
        # The rollout names no cache-write count. Against OpenAI's zero rate that cannot change
        # the bill, but against a rate that charges for one the part is genuinely unknown.
        self.assertIsNone(harness.row_cost(self.codex_row(), TABLE))

    def test_the_codex_breakdown_sums_to_the_rows_own_totals(self):
        record = self.codex_row()
        for field in ("input", "output", "cache_read"):
            self.assertEqual(sum(p[field] for p in record["by_model"].values()), record[field])


class CodexMappingTests(unittest.TestCase):
    """Codex counts cached input inside `input_tokens` and reasoning inside `output_tokens`."""

    def test_the_cached_part_is_charged_once_at_the_cache_rate(self):
        record = {}
        usage_log.codex_totals(record, {"total_tokens": 1_200_000, "input_tokens": 1_000_000,
                                        "cached_input_tokens": 900_000,
                                        "output_tokens": 200_000,
                                        "reasoning_output_tokens": 150_000,
                                        "cache_write_input_tokens": 0})
        self.assertEqual((record["input"], record["cache_read"]), (100_000, 900_000))
        cost = harness.row_cost(dict(record, kind="session", runtime="codex", ended=STAMP,
                                     models=["test-model"]), TABLE)
        # 100k billed input, 900k cache reads, and the 200k output that already contains the
        # reasoning tokens — charged once, never as 350k.
        self.assertAlmostEqual(cost, 0.1 * 10.0 + 0.9 * 0.25 + 0.2 * 50.0)

    def test_a_rollout_naming_no_cache_write_is_still_priced(self):
        # Codex reports no cache-write figure at all and OpenAI bills none, so an unreported
        # count against a zero rate is read as zero rather than leaving the session unpriced.
        free_writes = dict(TABLE["test-model"], cache_write=0.0)
        free_writes.pop("cache_write_5m"), free_writes.pop("cache_write_1h")
        cost = harness.row_cost(row(runtime="codex", input=1_000_000, output=0, cache_read=0,
                                    cache_write=None), {"test-model": free_writes})
        self.assertAlmostEqual(cost, 10.0)

    def test_a_missing_count_against_a_real_rate_is_still_unpriced(self):
        self.assertIsNone(harness.row_cost(row(input=1_000_000, cache_write=None), TABLE))

    def test_a_codex_row_carrying_only_a_total_is_unpriced(self):
        record = {}
        usage_log.codex_totals(record, {"total_tokens": 500_000})
        self.assertTrue(record["partial"])
        self.assertIsNone(harness.row_cost(dict(record, models=["test-model"]), TABLE))


class CacheTierCaptureTests(unittest.TestCase):
    """The hook records the tier split Claude Code reports, additively."""

    def test_the_split_is_recorded_beside_the_total(self):
        per_message = {}
        usage_log.record_usage(per_message, "m1", {
            "input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 100,
            "cache_creation": {"ephemeral_5m_input_tokens": 40,
                               "ephemeral_1h_input_tokens": 60}}, "2026-09-21")
        totals = usage_log.summed(per_message)
        self.assertEqual(totals["cache_write"], 100)
        self.assertEqual((totals["cache_write_5m"], totals["cache_write_1h"]), (40, 60))

    def test_a_usage_object_with_no_split_carries_neither_key(self):
        per_message = {}
        usage_log.record_usage(per_message, "m1", {
            "input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 100}, "2026-09-21")
        totals = usage_log.summed(per_message)
        self.assertNotIn("cache_write_5m", totals)
        self.assertEqual(totals["cache_write"], 100)


# Token counts read on 2026-09-21 from a real Claude Code session and its one subagent — a
# `scan_all` of the transcript reported exactly these figures. The dollar figure is the one the
# CLI reported for that session, `total_cost_usd = 0.60097775`. Reproducing it from
# `policy/prices.json` is what proves the table, the tier split and the subagent join are all
# right at once; the measured deviation against the live transcript was 0.000%, well inside the
# 2% the issue asks for, and this fixture reproduces it to the cent and beyond.
ORACLE_SESSION = {
    "kind": "session", "runtime": "claude-code", "session_id": "oracle", "ended": STAMP,
    "models": ["claude-fable-5-1"], "input": 8, "output": 500,
    "cache_read": 88624, "cache_write": 35589, "cache_write_5m": 11453, "cache_write_1h": 24136,
}
ORACLE_SUBAGENT = {
    "kind": "subagent", "runtime": "claude-code", "session_id": "oracle", "ended": STAMP,
    "agent_type": "Explore", "model": "claude-opus-5", "tool_calls": 1,
    "input": 4, "output": 230, "cache_read": 20842, "cache_write": 11453,
    "cache_write_5m": 11453, "cache_write_1h": 0,
}
ORACLE_USD = 0.60097775


class OracleTests(unittest.TestCase):
    def test_the_shipped_table_reproduces_the_runtimes_own_figure(self):
        table = harness.load_prices({})
        cost = harness.row_cost(ORACLE_SESSION, table, [ORACLE_SUBAGENT])
        self.assertIsNotNone(cost)
        self.assertLess(abs(cost - ORACLE_USD) / ORACLE_USD, 0.02)
        self.assertAlmostEqual(cost, ORACLE_USD, places=8)

    def test_pricing_the_session_alone_would_miss_by_more_than_the_tolerance(self):
        # Charging the subagent's tokens at the parent's rate is the mistake this join exists
        # to prevent; it is recorded here so a regression cannot pass quietly.
        table = harness.load_prices({})
        naive = harness.row_cost(ORACLE_SESSION, table)
        self.assertGreater(abs(naive - ORACLE_USD) / ORACLE_USD, 0.02)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore)
        self.path = self.home / ".local" / "state" / "agent-harness" / "usage.jsonl"
        self.path.parent.mkdir(parents=True)

    def _restore(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home

    def write(self, *rows):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def report(self, **kwargs):
        args = argparse.Namespace(days=kwargs.pop("days", 30), by=kwargs.pop("by", "day"),
                                  rules=False, rescan=False, stance=kwargs.pop("stance", None))
        with loud() as out:
            self.assertEqual(harness.cmd_usage(args), 0)
        return out.getvalue()

    def test_every_token_grouping_carries_a_usd_column(self):
        self.write(ORACLE_SESSION, ORACLE_SUBAGENT)
        for by in ("day", "repo", "model"):
            with self.subTest(by=by):
                text = self.report(by=by)
                self.assertIn("usd", text.splitlines()[0])
                self.assertIn("0.60", text)

    def test_a_stance_grouping_carries_it_too(self):
        session = dict(ORACLE_SESSION, stances={"cost": "balanced"}, stances_source="session")
        self.write(session, ORACLE_SUBAGENT)
        text = self.report(by="stance", stance="cost")
        self.assertIn("cost=balanced", text)
        self.assertIn("0.60", text)

    def test_an_unpriced_run_is_counted_in_the_footer_and_adds_no_dollars(self):
        self.write(dict(ORACLE_SESSION, session_id="x", models=["mystery-model"]))
        text = self.report()
        self.assertIn("unpriced: 1 run(s)", text)
        self.assertRegex(text, r"TOTAL.*\s0\.00\s*\n")

    def test_an_unlisted_variant_is_counted_in_the_footer_rather_than_under_billed(self):
        self.write(dict(ORACLE_SESSION, session_id="x", models=["gpt-5.5-pro"]))
        text = self.report()
        self.assertIn("unpriced: 1 run(s)", text)
        self.assertRegex(text, r"TOTAL.*\s0\.00\s*\n")

    def test_a_priced_window_says_so_rather_than_staying_silent(self):
        self.write(ORACLE_SESSION, ORACLE_SUBAGENT)
        self.assertIn("unpriced: none", self.report())

    def test_a_multi_day_session_spreads_its_cost_over_its_slices(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
        half = {"input": 4, "output": 250, "cache_read": 44312, "cache_write": 17794, "turns": 1}
        rest = {"input": 4, "output": 250, "cache_read": 44312, "cache_write": 17795, "turns": 1}
        self.write(dict(ORACLE_SESSION, days={yesterday: half, today: rest}), ORACLE_SUBAGENT)
        text = self.report()
        days = dict((line.split()[0], line) for line in text.splitlines()
                    if re.match(r"^\d{4}-\d{2}-\d{2}", line))
        self.assertEqual(sorted(days), sorted([yesterday, today]))
        spread = sum(float(line.split()[-1]) for line in days.values())
        self.assertAlmostEqual(spread, ORACLE_USD, places=2)

    def test_by_role_reports_dollar_percentiles_over_the_delegated_rows(self):
        self.write(ORACLE_SESSION, ORACLE_SUBAGENT)
        text = self.report(by="role")
        self.assertIn("usd p50", text.splitlines()[0])
        self.assertIn("Explore", text)
        self.assertIn("0.0878", text)

    def test_the_report_still_runs_with_no_price_file_at_all(self):
        self.write(ORACLE_SESSION, ORACLE_SUBAGENT)
        with unittest.mock.patch.object(harness, "PRICES_PATH", self.home / "gone.json"):
            text = self.report()
        self.assertIn("no price table", text)
        self.assertIn("unpriced: 1 run(s)", text)


class StalenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore)
        self.path = Path(self.tmp.name) / "prices.json"

    def _restore(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home

    def _doctor(self, as_of):
        self.path.write_text(json.dumps({"schema_version": 1, "models": {
            "test-model": dict(TABLE["test-model"], as_of=as_of)}}), encoding="utf-8")
        with unittest.mock.patch.object(harness, "PRICES_PATH", self.path), \
             unittest.mock.patch.object(harness, "_version_of", return_value="stub"), \
             unittest.mock.patch.object(harness.shutil, "which", return_value=None), \
             unittest.mock.patch.object(harness, "_diff_lines", return_value=[]):
            with loud() as out:
                harness.cmd_doctor(argparse.Namespace())
        return out.getvalue()

    def test_newest_as_of_is_the_one_that_counts(self):
        table = {"a": {"as_of": "2026-01-01"}, "b": {"as_of": "2026-09-21"}, "c": {}}
        self.assertEqual(harness.newest_as_of(table), "2026-09-21")
        self.assertEqual(harness.newest_as_of({"c": {}}), "")

    def test_an_unparseable_date_has_no_age(self):
        self.assertIsNone(harness.price_age_days("last Tuesday"))

    def test_doctor_warns_when_the_newest_price_is_over_ninety_days_old(self):
        stale = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 120 * 86400))
        text = self._doctor(stale)
        self.assertIn(f"newest as_of {stale}", text)
        self.assertIn("over 90 days old", text)

    def test_doctor_is_quiet_about_a_price_read_this_month(self):
        fresh = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 86400))
        text = self._doctor(fresh)
        self.assertIn(f"newest as_of {fresh}", text)
        self.assertNotIn("over 90 days old", text)

    def test_the_shipped_table_is_fresh_today(self):
        age = harness.price_age_days(harness.newest_as_of(harness.load_prices({})))
        self.assertIsNotNone(age)
        self.assertLessEqual(age, harness.PRICE_STALE_DAYS)


if __name__ == "__main__":
    unittest.main()
