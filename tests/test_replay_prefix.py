"""The warmth-free prefix figure (#514): `first_call_context` is the first call's whole prompt,
input plus cache write plus cache read, so a cold and a warm stream of one prompt agree, and a
stream that leaves a field out gives None, never a smaller sum."""
import unittest

from test_cost_bench import BENCH, result
from test_replay_parity import stream


def first_call(**usage):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"model": "claude-test", "usage": usage}}


class PrefixFigureTests(unittest.TestCase):
    def test_a_cold_and_a_warm_stream_of_one_prompt_give_equal_totals(self):
        cold = BENCH.parse_result(stream(first_call(input_tokens=7, cache_creation_input_tokens=20000,
                                                    cache_read_input_tokens=0), result()))
        warm = BENCH.parse_result(stream(first_call(input_tokens=7, cache_creation_input_tokens=0,
                                                    cache_read_input_tokens=20000), result()))
        self.assertEqual(cold["first_call_context"], 20007)
        self.assertEqual(cold["first_call_context"], warm["first_call_context"])
        self.assertNotEqual(cold["first_call_cache_write"], warm["first_call_cache_write"])

    def test_a_missing_field_gives_none(self):
        for missing in BENCH.PREFIX_FIELDS:
            usage = {"input_tokens": 7, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 40}
            usage.pop(missing)
            with self.subTest(missing=missing):
                parsed = BENCH.parse_result(stream(first_call(**usage), result()))
                self.assertIsNone(parsed["first_call_context"])

    def test_only_the_first_call_with_usage_counts(self):
        parsed = BENCH.parse_result(stream(
            first_call(input_tokens=1, cache_creation_input_tokens=2, cache_read_input_tokens=3),
            first_call(input_tokens=100, cache_creation_input_tokens=200, cache_read_input_tokens=300),
            result()))
        self.assertEqual(parsed["first_call_context"], 6)


if __name__ == "__main__":
    unittest.main()
