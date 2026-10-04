"""A first-wave trial (#1216): the replay stops it once its first spawns are out, leaves it
unscored, and its row names the bound that ended it along with each spawn's brief budget. Docker
is a fake replaying recorded streams from tests/fixtures/first-wave; no model is called."""
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
        self.assertEqual([s["model"] for s in row["spawn_briefs"]], ["claude-haiku-test", "claude-sonnet-test"])
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
