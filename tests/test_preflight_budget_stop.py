"""A preflight the CLI's `--max-budget-usd` stopped is reported as a budget stop (#1165)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from test_cost_bench import BENCH, GREEN, TASK, Launch, gate_reply, options


MICRO = BENCH.micro
# A synthetic stream in the shape the CLI wrote when the harness arm's first turn passed a 0.05 USD
# preflight cap before its tool result came back: `result` subtype `error_max_budget_usd`.
BUDGET_STOP = Path(__file__).parent / "fixtures" / "preflight" / "budget-stop-harness.jsonl"
STOPPED_AT = 0.05


def stream():
    return BUDGET_STOP.read_text(encoding="utf-8")


def first_turn_usd(text, model="claude-haiku-4-5"):
    """The first assistant turn's list price from `policy/prices.json`, with the run's output tokens:
    the stream ended on that turn, so all of its output belongs to it."""
    prices = json.loads((BENCH.ROOT / "policy" / "prices.json").read_text(encoding="utf-8"))["models"][model]
    messages = [json.loads(line) for line in text.splitlines() if line.strip()]
    usage = next(m["message"]["usage"] for m in messages if m.get("type") == "assistant")
    output = sum(u["outputTokens"] for u in messages[-1]["modelUsage"].values())
    return (usage["input_tokens"] * prices["input"] + usage["cache_creation_input_tokens"] * prices["cache_write_1h"]
            + usage["cache_read_input_tokens"] * prices["cache_read"] + output * prices["output"]) / 1e6


class PreflightBudgetStopTests(unittest.TestCase):
    def test_the_check_names_the_budget_stop_its_cost_and_its_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN), stream()])
            checks, spent = BENCH.preflight([TASK], options(tmp, preflight_cap=STOPPED_AT), launch)
        bare, harness = checks
        self.assertTrue(bare["passed"])
        self.assertFalse(bare["budget_stop"])
        self.assertFalse(harness["passed"])
        self.assertTrue(harness["budget_stop"])
        self.assertEqual((harness["cost_usd"], harness["cap_usd"]), (0.055585, STOPPED_AT))
        self.assertEqual(harness["reply"], "a budget stop at 0.0556 USD reported, against its 0.05 USD cap")
        self.assertAlmostEqual(spent, 0.1 + 0.055585)  # the reported cost, not the cap

    def test_the_refusal_reports_a_budget_stop_and_never_no_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN), stream()])
            opts = options(tmp, reps=1, skip_preflight=False, preflight_cap=STOPPED_AT)
            with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(len(launch.calls), 2)  # no scored run launched
        printed = err.getvalue()
        self.assertIn("the harness arm's preflight stopped at its budget: 0.0556 USD reported, cap 0.05 USD",
                      printed)
        self.assertNotIn("no reply", printed)
        self.assertNotIn("harness arm's gate is red", printed)

    def test_the_micro_caps_clear_the_measured_harness_first_turn(self):
        """The caps are sized from this turn: a preflight needs it and a warm turn, and a run must
        leave the harness arm most of its cap for the task after paying for it."""
        first = first_turn_usd(stream())
        self.assertAlmostEqual(first, 0.055585, places=6)  # what the CLI reported for it
        self.assertGreaterEqual(MICRO.PREFLIGHT_CAP_USD, 2 * first)
        self.assertGreaterEqual(MICRO.RUN_CAP_USD - first, 0.75 * MICRO.RUN_CAP_USD)
        self.assertLessEqual(MICRO.ceiling_usd(3, MICRO.REPS, 2), 8.00)
        self.assertLessEqual(MICRO.SPEND_CAP_USD, MICRO.ceiling_usd(3, MICRO.REPS, 2))


if __name__ == "__main__":
    unittest.main()
