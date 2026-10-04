"""A pair's kept harness arm with observation on: the usage ledger is copied out of the stopped
container and the native observation streams are archived from their host stage, and both happen
whether the run finished or timed out. Every launch is a fake; no test builds an image or calls a
model."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH, TASK
from test_replay_observation import env_values
from test_replay_pair import PAIR, PairLaunch, decision, ledger_text, pair_options, run_output, verbs
from harness_core import observer


class ObservedPairLaunch(PairLaunch):
    """`PairLaunch`, plus the native observer appending one event to its mounted stage on a run."""

    def __call__(self, command, **kwargs):
        values = env_values(command)
        if command[:2] == ["docker", "run"] and observer.LEDGER_ENV in values:
            mount = next(value for index, value in enumerate(command)
                         if index and command[index - 1] == "-v"
                         and value.endswith(":" + BENCH.arms.OBSERVATION_MOUNT))
            host = Path(mount.rsplit(":", 1)[0])
            payload = {"schema_version": observer.SCHEMA_VERSION, "event": "SessionStart",
                       "session_id": "session", "profile_fingerprint": values.get(observer.PROFILE_ENV),
                       "ts": "2026-09-29T00:00:00Z", "runtime": "claude-code"}
            (host / Path(values[observer.LEDGER_ENV]).name).write_text(json.dumps(payload) + "\n",
                                                                       encoding="utf-8")
        return super().__call__(command, **kwargs)


class KeptArmObservationTests(unittest.TestCase):
    def attempt(self, tmp, output):
        opts = pair_options(tmp)
        opts["observation_dir"] = BENCH.prepare_observation_dir(Path(tmp) / "output")
        launch = ObservedPairLaunch([output], [ledger_text(decision("s-ref"))])
        return BENCH._attempt(TASK, 1, "reference", opts, launch), opts, launch

    def assert_both_retained(self, row, opts, launch):
        self.assertEqual(row["decision_ledger"], PAIR.LEDGER_READ)
        kept = PAIR.decisions_file(opts["decisions"], "demo", "reference", 1).read_text(encoding="utf-8")
        self.assertEqual([json.loads(line)["kind"] for line in kept.splitlines()], ["decision"])
        self.assertEqual((row["observation_rows"], row["observation_errors"]), (1, 0))
        self.assertTrue((Path(opts["observation_dir"]) / "demo-reference-1.jsonl").is_file())
        self.assertEqual(sorted(Path(opts["tmp"]).glob("cost-observation-*")), [])
        run = [c for c, _ in launch.calls if c[1] == "run"][0]
        self.assertNotIn("--rm", run)
        self.assertEqual(len([p for p in run if p == "-v"]), 3)  # the snapshot, the stage and the cache nonce

    def test_a_finished_kept_arm_copies_its_ledger_and_archives_its_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, opts, launch = self.attempt(tmp, run_output("s-ref"))
            self.assertFalse(row["error"], row["error_kind"])
            self.assertEqual(verbs(launch), [("run", "reference"), ("cp", "reference"), ("rm", "reference")])
            self.assert_both_retained(row, opts, launch)

    def test_a_timed_out_kept_arm_still_copies_and_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, opts, launch = self.attempt(tmp, subprocess.TimeoutExpired("docker", 1))
            self.assertEqual(row["error_kind"], "timeout")
            self.assertEqual(verbs(launch), [("run", "reference"), ("kill", "reference"),
                                             ("cp", "reference"), ("rm", "reference")])
            self.assert_both_retained(row, opts, launch)

    def test_a_pair_preflight_archives_one_stream_per_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = pair_options(tmp)
            opts["observation_dir"] = BENCH.prepare_observation_dir(Path(tmp) / "output")
            BENCH.refuse_observation_collisions([TASK], opts)  # three arms, no shared stem
            launch = ObservedPairLaunch([run_output("s-%s" % arm) for arm in PAIR.ARMS])
            checks, _ = BENCH.preflight([TASK], opts, launch)
            self.assertEqual([c["arm"] for c in checks], list(PAIR.ARMS))
            for arm in PAIR.ARMS:
                self.assertTrue((Path(opts["observation_dir"]) / ("preflight-%s.jsonl" % arm)).is_file())


if __name__ == "__main__":
    unittest.main()
