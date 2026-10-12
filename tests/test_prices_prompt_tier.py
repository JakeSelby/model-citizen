# SPDX-License-Identifier: MIT
"""The Claude 5 rates in `policy/prices.json`, and the prompt-length tier Claude Haiku 5.5 is billed by.

Run: python3 -m unittest tests.test_prices_prompt_tier
"""
import importlib.util
import json
import unittest

from test_cost_bench import BENCH
from test_harness import REPO

ABL = BENCH.ablations
PRICING = BENCH.PRICING
SHIPPED = json.loads((REPO / "policy" / "prices.json").read_text(encoding="utf-8"))["models"]
SPEC = importlib.util.spec_from_file_location("layer_scorecard_tier", REPO / "scripts" / "layer_scorecard.py")
SCORECARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORECARD)

# Read from https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-12 (USD per
# million tokens). Cache hits on Opus 5.5 and Sonnet 5.5 are 0.05x base input, on Fable 5.1
# 0.025x; Haiku 5.5 is billed at a higher tier once a request's prompt passes 100,000 tokens.
CLAUDE_5 = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25,
                         "cache_write": 12.5, "cache_write_5m": 12.5, "cache_write_1h": 20.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2,
                        "cache_write": 5.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.1,
                          "cache_write": 2.5, "cache_write_5m": 2.5, "cache_write_1h": 4.0},
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_read": 0.01,
                         "cache_write": 0.125, "cache_write_5m": 0.125, "cache_write_1h": 0.2},
}
HAIKU_LONG = {"over_prompt_tokens": 100000, "input": 0.5, "output": 2.5, "cache_read": 0.05,
              "cache_write": 0.625, "cache_write_5m": 0.625, "cache_write_1h": 1.0}
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
TOKENS = {"input": 1000000, "output": 1000000, "cache_read": 1000000, "cache_write": 0}


def part(max_prompt=None, **tokens):
    out = dict(TOKENS, **tokens)
    if max_prompt is not None:
        out["max_prompt"] = max_prompt
    return out


def row(parts):
    return {"kind": "session", "runtime": "claude-code", "by_model": parts}


class ShippedRates(unittest.TestCase):
    def test_each_claude_5_rate_matches_the_pricing_page(self):
        for model, rates in CLAUDE_5.items():
            with self.subTest(model=model):
                entry = SHIPPED[model]
                self.assertEqual(dict((k, entry[k]) for k in rates), rates)
                self.assertEqual(entry["source"], SOURCE)

    def test_haiku_5_5_carries_its_long_prompt_tier(self):
        self.assertEqual(SHIPPED["claude-haiku-5-5"]["long_context"], HAIKU_LONG)

    def test_no_other_entry_is_tiered(self):
        self.assertEqual([m for m, e in SHIPPED.items() if "long_context" in e], ["claude-haiku-5-5"])


class PromptRate(unittest.TestCase):
    def setUp(self):
        self.rate = PRICING.usable_rate(SHIPPED["claude-haiku-5-5"])

    def test_a_prompt_at_the_threshold_takes_the_base_rate(self):
        self.assertEqual(PRICING.prompt_rate(self.rate, 100000)["input"], 0.1)

    def test_a_prompt_over_the_threshold_takes_the_long_rate(self):
        rate = PRICING.prompt_rate(self.rate, 100001)
        self.assertEqual((rate["input"], rate["cache_read"], rate["output"]), (0.5, 0.05, 2.5))
        self.assertNotIn("over_prompt_tokens", rate)

    def test_an_unknown_prompt_on_a_tiered_model_is_unpriced(self):
        self.assertIsNone(PRICING.prompt_rate(self.rate, None))

    def test_a_mix_over_the_threshold_is_unpriced_not_charged_at_either_tier(self):
        self.assertIsNone(PRICING.prompt_rate(self.rate, 100001, many=True))
        self.assertEqual(PRICING.prompt_rate(self.rate, 99999, many=True)["input"], 0.1)

    def test_an_untiered_model_ignores_the_prompt(self):
        rate = PRICING.usable_rate(SHIPPED["claude-sonnet-5-5"])
        self.assertEqual(PRICING.prompt_rate(rate, None), rate)

    def test_a_malformed_tier_leaves_the_entry_unusable(self):
        for block in ({"input": 0.5}, dict(HAIKU_LONG, over_prompt_tokens="100000"),
                      dict(HAIKU_LONG, over_prompt_tokens=0), "100000"):
            with self.subTest(block=block):
                self.assertIsNone(PRICING.usable_rate(dict(SHIPPED["claude-haiku-5-5"], long_context=block)))


class RowCost(unittest.TestCase):
    def setUp(self):
        self.table = PRICING.load_prices({}, REPO / "policy" / "prices.json")

    def test_a_haiku_part_under_the_threshold_is_priced_at_the_base_rate(self):
        cost = PRICING.row_cost(row({"claude-haiku-5-5": part(max_prompt=80000)}), self.table)
        self.assertAlmostEqual(cost, 0.1 + 0.5 + 0.01)

    def test_a_haiku_part_over_the_threshold_leaves_the_row_unpriced(self):
        self.assertIsNone(PRICING.row_cost(row({"claude-haiku-5-5": part(max_prompt=150000)}), self.table))

    def test_a_haiku_part_without_its_largest_prompt_leaves_the_row_unpriced(self):
        self.assertIsNone(PRICING.row_cost(row({"claude-haiku-5-5": part()}), self.table))

    def test_a_haiku_row_without_a_breakdown_is_unpriced(self):
        flat = dict(TOKENS, kind="session", runtime="claude-code", model="claude-haiku-5-5")
        self.assertIsNone(PRICING.row_cost(flat, self.table))

    def test_sonnet_5_5_cache_reads_are_charged_at_ten_cents(self):
        cost = PRICING.row_cost(row({"claude-sonnet-5-5": part(input=0, output=0)}), self.table)
        self.assertAlmostEqual(cost, 0.1)


class ReadersApplyTheTier(unittest.TestCase):
    def test_the_static_figure_leaves_out_a_model_priced_by_prompt_length(self):
        models = BENCH._cache_rates(REPO)
        self.assertIn("claude-sonnet-5-5", models)
        self.assertNotIn("claude-haiku-5-5", models)

    def test_a_first_turn_is_repriced_at_its_own_prompts_tier(self):
        prices = {"claude-haiku-5-5": SHIPPED["claude-haiku-5-5"]}
        long = [{"model": "claude-haiku-5-5", "cache_read": 1000000, "prompt": 1000000}]
        self.assertAlmostEqual(BENCH.normalised_cost(1.0, long, prices), round(1.0 + (0.625 - 0.05), 6))
        short = [{"model": "claude-haiku-5-5", "cache_read": 50000, "prompt": 60000}]
        self.assertAlmostEqual(BENCH.normalised_cost(1.0, short, prices), round(1.0 + 50000 * (0.125 - 0.01) / 1e6, 6))
        self.assertIsNone(BENCH.normalised_cost(1.0, [{"model": "claude-haiku-5-5", "cache_read": 5}], prices))

    def test_an_ablation_turn_is_charged_at_its_envelopes_tier(self):
        rates = SHIPPED["claude-haiku-5-5"]
        small = {"input": 1000, "cache_read": 50000, "cache_write": 1000, "output": 2000}
        large = dict(small, cache_read=150000)
        self.assertAlmostEqual(ABL.turn_cost(rates, small), (1000 * 0.1 + 50000 * 0.01 + 1000 * 0.125 + 2000 * 0.5) / 1e6)
        self.assertAlmostEqual(ABL.turn_cost(rates, large), (1000 * 0.5 + 150000 * 0.05 + 1000 * 0.625 + 2000 * 2.5) / 1e6)

    def test_the_layer_scorecard_leaves_a_tiered_model_unpriced(self):
        static = json.loads((REPO / "benchmarks" / "static.json").read_text(encoding="utf-8"))
        table = PRICING.load_prices({}, REPO / "policy" / "prices.json")
        rows = [{"model": m, "turns": 3} for m in ("claude-haiku-5-5", "claude-sonnet-5-5")]
        cells = SCORECARD.prefix({"entries": ["instructions/global"]}, rows, static, table)
        self.assertIsNone(cells["claude-haiku-5-5"]["usd_session_start"])
        self.assertIsNotNone(cells["claude-sonnet-5-5"]["usd_session_start"])


if __name__ == "__main__":
    unittest.main()
