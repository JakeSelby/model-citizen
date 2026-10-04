"""A resumed turn's cost is the change in the session's totals (#1243). A resumed `claude -p` turn's
result reports `total_cost_usd` and `modelUsage` as the session's running totals, so a turn's cost,
tokens and cost by tier are the change since the previous result, the session's spend is the
latest total, and each checkpoint's check is given the totals as they stood before its segment.
The figures below are made up; no container is started and no model called."""
import json
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH, Launch, mounts, options
from test_oracle_stream import fixture_task, local_container
from test_replay_long_session import CANARY, MAIN, SUB, TIERS, FakeCli, scenario, workspace_scenario

SESSION = BENCH.replay_session


def figures(inputs, outputs, writes, reads, cost):
    return {"inputTokens": inputs, "outputTokens": outputs, "cacheCreationInputTokens": writes,
            "cacheReadInputTokens": reads, "costUSD": cost}


# The session's running totals after each of five turns; the subagent model first appears on turn 2.
TOTALS = [
    (0.40, {MAIN: figures(10, 100, 1000, 0, 0.40)}),
    (1.10, {MAIN: figures(25, 300, 1500, 4000, 0.90), SUB: figures(5, 50, 200, 100, 0.20)}),
    (1.85, {MAIN: figures(30, 450, 1800, 9000, 1.50), SUB: figures(9, 80, 300, 300, 0.35)}),
    (2.15, {MAIN: figures(40, 500, 1900, 15000, 1.80), SUB: figures(9, 80, 300, 300, 0.35)}),
    (2.60, {MAIN: figures(41, 600, 2000, 22000, 2.20), SUB: figures(10, 90, 310, 320, 0.40)}),
]


def resumed_stream(total, model_usage, turn_usage=None, subtype="success", session="s-1"):
    """One turn's stream as a resumed session reports it: running totals, and its own `usage`."""
    lines = [{"type": "system", "subtype": "init", "session_id": session, "model": MAIN},
             {"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 2,
              "session_id": session, "total_cost_usd": total, "modelUsage": model_usage,
              "usage": turn_usage or {"input_tokens": 1, "output_tokens": 1}}]
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def run(totals=TOTALS, cap=4.0):
    cli, baselines = FakeCli([resumed_stream(t, u) for t, u in totals]), []

    def checker(name, stream, baseline):
        baselines.append((name, json.loads(json.dumps(baseline))))
        return True, "ok"

    rows = SESSION.run_session(scenario(), {"arm": "bare", "rep": 1}, cap, cli, checker, TIERS)
    return rows, cli, baselines


class ResumedTurnCostTests(unittest.TestCase):
    def test_each_turn_costs_the_change_in_the_session_total_and_the_session_the_last_total(self):
        rows, cli, _ = run()
        session = rows[-1]
        self.assertEqual(session["cost_per_turn"], [0.4, 0.7, 0.75, 0.3, 0.45])
        self.assertEqual(session["cumulative_cost_usd"], [0.4, 1.1, 1.85, 2.15, 2.6])
        self.assertEqual(session["cost_usd"], 2.6)
        self.assertEqual([c["budget"] for c in cli.calls], [4.0, 3.6, 2.9, 2.15, 1.85])

    def test_tokens_and_cost_by_tier_are_per_model_deltas(self):
        first = SESSION.turn_usage(resumed_stream(*TOTALS[0]), TIERS)
        second = SESSION.turn_usage(resumed_stream(*TOTALS[1]), TIERS, first["totals"])
        self.assertEqual(second["cost_usd"], 0.7)
        self.assertEqual(second["cost_by_tier"], {"light": 0.2, "standard": 0.5})
        self.assertEqual(second["tokens"], {"input_tokens": 20, "output_tokens": 250,
                                            "cache_creation_input_tokens": 700, "cache_read_input_tokens": 4100})
        session = run()[0][-1]
        self.assertEqual(session["cost_by_tier"], {"light": 0.4, "standard": 2.2})
        self.assertEqual(session["output_tokens"], 690)
        self.assertEqual(session["cache_read_input_tokens"], 22320)

    def test_turn_one_counts_against_zero_and_a_result_without_model_usage_uses_its_own_usage(self):
        own = {"input_tokens": 3, "cache_creation_input_tokens": 4, "cache_read_input_tokens": 5, "output_tokens": 6}
        first = SESSION.turn_usage(resumed_stream(0.4, {}, own), TIERS)
        self.assertEqual((first["cost_usd"], first["tokens"]), (0.4, own))
        self.assertEqual(first["totals"], {"total_cost_usd": 0.4, "modelUsage": {}})
        self.assertIsNone(SESSION.turn_usage("", TIERS, first["totals"])["cost_usd"])

    def test_the_cap_stops_the_session_only_once_the_latest_total_reaches_it(self):
        # Summed as turn costs, 0.9 + 1.2 already passes 2.0; the session has spent 1.2.
        totals = [(t, {MAIN: figures(1, 1, 1, 1, t)}) for t in (0.9, 1.2, 1.5, 2.2, 2.5)]
        rows, cli, _ = run(totals, cap=2.0)
        session = rows[-1]
        self.assertEqual(len(cli.calls), 4)
        self.assertEqual((session["stopped"], session["cost_usd"]), ("cap", 2.2))
        self.assertEqual(session["cost_per_turn"], [0.9, 0.3, 0.3, 0.7])

    def test_a_timeout_still_counts_at_the_rest_of_the_cap(self):
        cli = FakeCli([resumed_stream(*TOTALS[0]), {"stdout": "", "timeout": True}])
        rows = SESSION.run_session(scenario(), {}, 4.0, cli, lambda *a: (True, ""), TIERS)
        self.assertEqual((rows[-1]["cost_per_turn"], rows[-1]["cost_usd"], rows[-1]["error_kind"]),
                         ([0.4, 3.6], 4.0, "timeout"))

    def test_a_checkpoint_row_carries_the_change_across_its_segment(self):
        cp1, cp2, _ = run()[0]
        self.assertEqual((cp1["segment_turns"], cp1["cost_usd"], cp1["cumulative_cost_usd"]), ([1, 2], 1.1, 1.1))
        self.assertEqual((cp2["segment_turns"], cp2["cost_usd"], cp2["cumulative_cost_usd"]), ([3, 4], 1.05, 2.15))
        self.assertEqual(cp2["cost_by_tier"], {"light": 0.15, "standard": 0.9})
        self.assertEqual(cp2["output_tokens"], 230)

    def test_each_check_gets_the_totals_from_before_its_segment(self):
        _, _, baselines = run()
        self.assertEqual(baselines, [("cp1", {"total_cost_usd": 0, "modelUsage": {}}),
                                     ("cp2", {"total_cost_usd": 1.1, "modelUsage": TOTALS[1][1]})])


class ResumeLostTests(unittest.TestCase):
    def run_lost(self, third):
        cli = FakeCli([resumed_stream(*TOTALS[0]), resumed_stream(*TOTALS[1]), third, resumed_stream(*TOTALS[3])])
        rows = SESSION.run_session(scenario(), {}, 4.0, cli, lambda *a: (True, ""), TIERS)
        return rows[-1], cli

    def assert_lost(self, session, cli):
        self.assertEqual(len(cli.calls), 3)
        self.assertEqual((session["stopped"], session["error"], session["error_kind"]),
                         ("error", True, SESSION.RESUME_LOST))
        # Never a negative cost: the lost turn counts at the rest of the cap, as a timeout does.
        self.assertEqual((session["cost_per_turn"], session["cost_usd"]), ([0.4, 0.7, 2.9], 4.0))
        self.assertIsNone(session["output_tokens"])
        self.assertIsNone(session["passed"])

    def test_a_result_naming_another_session_errors_the_session(self):
        self.assert_lost(*self.run_lost(resumed_stream(*TOTALS[2], session="s-2")))

    def test_a_falling_total_cost_errors_the_session(self):
        self.assert_lost(*self.run_lost(resumed_stream(0.3, {MAIN: figures(1, 1, 1, 1, 0.3)})))

    def test_a_falling_model_usage_key_errors_the_session_even_when_the_cost_rose(self):
        fell = json.loads(json.dumps(TOTALS[2][1]))
        fell[SUB]["cacheReadInputTokens"] = 99  # below turn 2's 100
        self.assert_lost(*self.run_lost(resumed_stream(TOTALS[2][0], fell)))
        dropped = {MAIN: TOTALS[2][1][MAIN]}  # a model the session had used is gone
        self.assert_lost(*self.run_lost(resumed_stream(TOTALS[2][0], dropped)))

    def test_totals_that_hold_or_rise_are_not_lost(self):
        self.assertFalse(SESSION.totals_fell(SESSION.ZERO_TOTALS, {"total_cost_usd": 0.1, "modelUsage": {}}))
        same = {"total_cost_usd": TOTALS[3][0], "modelUsage": TOTALS[3][1]}
        self.assertFalse(SESSION.totals_fell({"total_cost_usd": TOTALS[2][0], "modelUsage": TOTALS[2][1]}, same))
        self.assertFalse(SESSION.totals_fell(same, same))


class BaselineFileTests(unittest.TestCase):
    def test_every_checkpoint_check_gets_its_baseline_file(self):
        seen = []

        def scorer(task, workdir, repo, stream, baseline):
            seen.append((task["id"], json.loads(Path(baseline).read_text(encoding="utf-8"))))
            return True, "", None

        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, model=MAIN, run_cap=None, stamp={"date": "2026-01-01", "model": MAIN},
                           session_scorer=scorer)
            launch = Launch([resumed_stream(t, u) for t, u in TOTALS])
            rows = BENCH.run_long_session(workspace_scenario(tmp), 1, "bare", opts, launch)
        self.assertEqual(rows[-1]["cost_usd"], 2.6)
        (first, one), (second, two) = seen
        self.assertTrue(first.endswith("cp1") and second.endswith("cp2"), (first, second))
        self.assertEqual(one, {"total_cost_usd": 0, "modelUsage": {}})
        self.assertEqual(two, {"total_cost_usd": 1.1, "modelUsage": TOTALS[1][1]})

    def test_the_baseline_is_mounted_read_only_and_reaches_the_check(self):
        check = ("# %s\nimport json\nfrom pathlib import Path\n\ndef check(root, stream=None):\n"
                 "    base = json.loads(Path('/session-baseline.json').read_text())\n"
                 "    return {'pass': True, 'metrics': {'tool_results': base['total_cost_usd'],"
                 " 'subagent_results': len(base['modelUsage'])}, 'errors': []}\n" % CANARY)
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            return local_container(command, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            stream = Path(tmp) / "segment.jsonl"
            stream.write_text(resumed_stream(*TOTALS[2]), encoding="utf-8")
            totals = {"total_cost_usd": TOTALS[1][0], "modelUsage": TOTALS[1][1]}
            baseline = BENCH.session_baseline(totals, Path(tmp) / "baseline.json")
            got = BENCH.score(fixture_task(tmp, check), tree, None, "model-citizen-arm-bare:test", launch,
                              stream=stream, baseline=baseline)
        self.assertEqual(got[2]["metrics"], {"tool_results": 1.1, "subagent_results": 2.0})
        self.assertEqual([m.split(":", 1)[1] for m in mounts(calls[0])],
                         ["/work", BENCH.arms.SESSION_STREAM + ":ro", BENCH.arms.SESSION_BASELINE + ":ro"])
        self.assertEqual(BENCH.arms.SESSION_BASELINE, "/session-baseline.json")


if __name__ == "__main__":
    unittest.main()
