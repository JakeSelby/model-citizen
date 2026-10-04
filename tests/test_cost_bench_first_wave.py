"""A first-wave trial (#1216): the replay stops it once its first spawns are out, leaves it
unscored, and its row names the bound that ended it along with each spawn's brief budget. Docker
is a fake replaying recorded streams from tests/fixtures/first-wave; no model is called."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, Launch, options
from test_cost_bench_pack import pack_task
from test_replay_spawns import FIXTURES, FakePopen, lines

PACK = BENCH.replay_pack
SPAWNS = BENCH.replay_spawns


def first_wave(tmp):
    pack, task = pack_task(tmp)
    return pack, dict(task, first_wave=True, requires_skills=["bmad-deep-recon"])


class FirstWaveTrialTests(unittest.TestCase):
    def run_trial(self, stream, arm="harness", stop=True):
        scored = []

        def scorer(task, workdir, repo):
            scored.append(task["id"])
            return True, ""

        with tempfile.TemporaryDirectory() as tmp:
            pack, task = first_wave(tmp)
            self.addCleanup(PACK.close_pack, pack)
            launch = Launch([""])  # `docker kill` prints nothing
            popen = FakePopen(lines(stream))
            real = SPAWNS.first_wave_launch
            with mock.patch.object(BENCH.replay_spawns, "first_wave_launch",
                                   side_effect=lambda base: real(base, popen)):
                row = BENCH.run_one(task, 1, arm, options(tmp, scorer=scorer, raw=str(Path(tmp) / "raw")), launch)
            saved = (Path(tmp) / "raw" / BENCH.replay_detect.raw_name(task["id"], arm, 1)).read_text(encoding="utf-8")
        return row, launch, scored, saved

    def test_the_trial_stops_at_its_first_wave_and_says_so(self):
        row, launch, scored, saved = self.run_trial("fanout-harness.jsonl")
        self.assertEqual((row["ended_by"], row["first_wave_spawns"], row["error"], row["passed"]),
                         ("first-wave", 2, False, None))
        self.assertEqual(scored, [])
        self.assertIsNone(row["cost_usd"])  # no priced result; the spend ledger counts the run cap
        kills = [c[0] for c in launch.calls if c[0][:2] == ["docker", "kill"]]
        self.assertEqual(len(kills), 1)
        self.assertEqual([s["brief_budget"] for s in row["spawn_briefs"]], [True, True])
        self.assertEqual([s["model"] for s in row["spawn_briefs"]], ["claude-haiku-4-5-20251001", "claude-sonnet-5"])
        self.assertTrue(row["required_skills_loaded"])
        self.assertEqual(row["spawns"], 2)
        self.assertNotIn('"t3"', saved)

    def test_a_workflow_launch_is_a_first_wave_too(self):
        row, _, _, _ = self.run_trial("workflow-harness.jsonl")
        self.assertEqual((row["ended_by"], row["first_wave_spawns"]), ("first-wave", 1))

    def test_a_trial_its_turn_bound_ends_first_is_unscored_and_names_the_bound(self):
        scored = []
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = first_wave(tmp)
            self.addCleanup(PACK.close_pack, pack)
            # The same run with no spawn call: nothing stops it, and the CLI's turn bound ends it.
            bounded = [line for line in lines("fanout-harness.jsonl") if '"tool_use"' not in line]
            real = SPAWNS.first_wave_launch
            with mock.patch.object(BENCH.replay_spawns, "first_wave_launch",
                                   side_effect=lambda base: real(base, FakePopen(bounded))):
                row = BENCH.run_one(task, 1, "harness", options(tmp, scorer=lambda *a: scored.append(1)),
                                    Launch([]))
        self.assertEqual((row["ended_by"], row["error"], row["passed"], row["cost_usd"]),
                         ("max-turns", False, None, 0.42))
        self.assertEqual((row["first_wave_spawns"], scored), (0, []))

    def test_the_bare_arm_reads_its_written_briefs(self):
        row, _, _, _ = self.run_trial("fanout-bare.jsonl", arm="bare")
        self.assertEqual(row["ended_by"], "first-wave")
        self.assertEqual([(s["brief_source"], s["brief_budget"]) for s in row["spawn_briefs"]], [("written", False)])
        self.assertIsNone(row["required_skills_loaded"])  # its init event lists no skills


class ExitCode(FakePopen):
    """A `docker run` that ends with the given exit code when nothing stops it."""
    def __init__(self, source, code):
        super().__init__(source)
        self.code = code

    def wait(self):
        self.returncode = 137 if self.killed else self.code
        return self.returncode


class FirstWaveEndingTests(unittest.TestCase):
    """How a first-wave trial nothing stopped is recorded, by the way its CLI run ended."""

    def run_ending(self, subtype, is_error, code=0):
        result = json.dumps({"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 4,
                             "total_cost_usd": 0.42, "usage": {}, "session_id": "s1"}) + "\n"
        source = [line for line in lines("fanout-harness.jsonl")
                  if '"tool_use"' not in line and '"type":"result"' not in line] + [result]
        scored = []
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = first_wave(tmp)
            self.addCleanup(PACK.close_pack, pack)
            real = SPAWNS.first_wave_launch
            with mock.patch.object(BENCH.replay_spawns, "first_wave_launch",
                                   side_effect=lambda base: real(base, ExitCode(source, code))):
                row = BENCH.run_one(task, 1, "harness", options(tmp, scorer=lambda *a: scored.append(1)),
                                    Launch([]))
        self.assertEqual(scored, [])  # never scored, however it ended
        self.assertIsNone(row["passed"])
        return row

    def test_its_turn_bound_is_not_an_error_and_has_no_miss_ratio(self):
        row = self.run_ending("error_max_turns", True)
        self.assertEqual((row["ended_by"], row["error"], row["error_kind"], row["cache_miss_ratio"]),
                         ("max-turns", False, "", None))

    def test_its_spend_bound_is_not_an_error_either(self):
        row = self.run_ending("error_max_budget_usd", True)
        self.assertEqual((row["ended_by"], row["error"], row["cache_miss_ratio"]), ("run-cap", False, None))

    def test_any_other_cli_error_is_an_error(self):
        row = self.run_ending("error_during_execution", True)
        self.assertEqual((row["ended_by"], row["error"], row["error_kind"], row["cache_miss_ratio"]),
                         ("error_during_execution", True, "error_during_execution", None))

    def test_a_finished_run_with_a_nonzero_exit_is_an_error(self):
        row = self.run_ending("success", False, code=1)
        self.assertEqual((row["ended_by"], row["error"], row["error_kind"], row["cache_miss_ratio"]),
                         ("finished", True, "success", None))

    def test_a_clean_finish_stays_unscored_with_its_miss_ratio(self):
        row = self.run_ending("success", False)
        self.assertEqual((row["ended_by"], row["error"], row["error_kind"]), ("finished", False, ""))
        self.assertIsNotNone(row["cache_miss_ratio"])


class OrdinaryTrialTests(unittest.TestCase):
    def test_a_task_without_first_wave_is_launched_and_scored_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = pack_task(tmp)
            self.addCleanup(PACK.close_pack, pack)
            launch = Launch(["".join(lines("fanout-bare.jsonl"))])
            with mock.patch.object(BENCH.replay_spawns, "first_wave_launch") as wrap:
                row = BENCH.run_one(task, 1, "bare", options(tmp), launch)
        wrap.assert_not_called()
        self.assertEqual((row["outcome"], row["ended_by"], row["first_wave_spawns"]), ("pass", "finished", None))
        self.assertEqual(len(row["spawn_briefs"]), 1)
        self.assertEqual(row["workflow_launch_hooks"], [])


if __name__ == "__main__":
    unittest.main()
