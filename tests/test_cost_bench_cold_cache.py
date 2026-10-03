"""Every replay trial starts with a cold prompt cache (#1174): each one mounts its own nonce as the
managed memory file, the first thing in the session segment, so no two trials of one task share a
cache prefix past the system prompt, and the report states the basis its costs stand on. No
container starts and no model is called: Docker is a fake."""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, mounts, options, result

ARMS = BENCH.arms


class ReadingLaunch(Launch):
    """`Launch`, also reading the managed memory file while it exists, since the trial removes it."""
    def __init__(self, outputs):
        super().__init__(outputs)
        self.memories = []

    def __call__(self, command, **kwargs):
        if command[:2] == ["docker", "run"]:
            mounted = [m for m in mounts(command) if m.endswith(":%s:ro" % ARMS.MANAGED_MEMORY)]
            self.memories.append([Path(m.split(":")[0]).read_text(encoding="utf-8") for m in mounted])
        return super().__call__(command, **kwargs)


def run_trials(schedule):
    with tempfile.TemporaryDirectory() as tmp:
        launch = ReadingLaunch([json.dumps([result()]) for _ in schedule])
        opts = options(tmp)
        rows = [BENCH.run_one(TASK, rep, arm, opts, launch) for rep, arm in schedule]
    return rows, launch


class ColdTrialTests(unittest.TestCase):
    def test_two_trials_of_one_task_and_arm_open_their_session_segments_differently(self):
        rows, launch = run_trials([(1, "harness"), (2, "harness")])
        self.assertEqual([len(m) for m in launch.memories], [1, 1])
        first, second = (m[0] for m in launch.memories)
        self.assertNotEqual(first, second)
        for row, memory in zip(rows, (first, second)):
            self.assertEqual(row["cache_basis"], BENCH.CACHE_COLD)
            self.assertIn(row["cache_nonce"], memory)
            self.assertFalse(row["error"], row["error_kind"])
        # The task prompt is the same, so the difference is the nonce, ahead of the session segment.
        argv = [call[0][call[0].index("claude"):] for call in launch.calls]
        self.assertEqual(argv[0], argv[1])

    def test_each_arm_of_a_rep_gets_its_own_nonce_and_the_arms_differ_by_nothing_else(self):
        rows, launch = run_trials([(1, "bare"), (1, "harness")])
        self.assertEqual(len({r["cache_nonce"] for r in rows}), 2)
        commands = [call[0] for call in launch.calls]
        shapes = [[part for part in m.split(":")[1:]] for c in commands for m in mounts(c)]
        self.assertEqual(shapes.count(["/etc/claude-code/CLAUDE.md", "ro"]), 2)

    def test_the_nonce_file_is_refused_like_any_mount_under_a_host_path(self):
        with self.assertRaises(SystemExit) as caught:
            ARMS.run_command("img", None, ["true"], "none", managed_memory=str(Path.home() / "trial.md"))
        self.assertIn("under the host path", str(caught.exception))


class CacheBasisTests(unittest.TestCase):
    def test_cold_only_when_every_row_has_its_own_nonce(self):
        self.assertEqual(BENCH.cache_basis([{"cache_nonce": "a"}, {"cache_nonce": "b"}]), BENCH.CACHE_COLD)
        for rows in ([{"cache_nonce": "a"}, {"cache_nonce": "a"}], [{"cache_nonce": "a"}, {}], []):
            self.assertEqual(BENCH.cache_basis(rows), BENCH.CACHE_SHARED)

    def summarise(self, rows, as_json):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BENCH.RESULTS
            BENCH.write_jsonl(path, rows)
            args = argparse.Namespace(results=str(path), seed=1, resamples=10, plot=None, json=as_json,
                                      break_even=3, correction=None)
            out = io.StringIO()
            with mock.patch.object(BENCH.replay_stats, "analyse", return_value={"verdict": "x"}), \
                    mock.patch.object(BENCH.replay_stats, "render", return_value="report\n"), \
                    mock.patch.object(BENCH.delegation_verdict, "report", return_value={}), \
                    mock.patch.object(BENCH.delegation_verdict, "render", return_value=""), \
                    contextlib.redirect_stdout(out):
                BENCH.cmd_summarise(args)
        return out.getvalue()

    def test_the_report_states_its_basis_in_text_and_json(self):
        cold = [{"arm": "bare", "rep": 1, "cache_nonce": "a"}, {"arm": "harness", "rep": 1, "cache_nonce": "b"}]
        old = [{"arm": "bare", "rep": 1}, {"arm": "harness", "rep": 1}]
        self.assertTrue(self.summarise(cold, False).startswith("cache basis: cold,"))
        self.assertTrue(self.summarise(old, False).startswith("cache basis: shared,"))
        self.assertEqual(json.loads(self.summarise(cold, True))["cache_basis"], "cold")
        self.assertEqual(json.loads(self.summarise(old, True))["cache_basis"], "shared")


    def test_pair_ablation_and_design_reports_state_their_basis_too(self):
        """Each format returns before the two-arm report, so each must stamp the basis itself."""
        formats = {"pair": (BENCH.replay_pair, "is_pair", "render"),
                   "ablation": (BENCH.ablations, "is_ablation", "render"),
                   "design": (BENCH.unit_economy, "is_design", "render")}
        cold = [{"arm": "a", "rep": 1, "cache_nonce": "x"}, {"arm": "b", "rep": 1, "cache_nonce": "y"}]
        old = [{"arm": "a", "rep": 1}, {"arm": "b", "rep": 1}]
        for name, (module, detect, render) in formats.items():
            for rows, basis in ((cold, "cold"), (old, "shared")):
                for as_json in (False, True):
                    with self.subTest(format=name, basis=basis, json=as_json):
                        with mock.patch.object(module, detect, return_value=True), \
                                mock.patch.object(module, "summarise", return_value={"parity": {"ok": True}}), \
                                mock.patch.object(module, render, return_value="%s report\n" % name), \
                                mock.patch.object(BENCH.replay_pair, "read_decisions", return_value=[]):
                            out = self.summarise(rows, as_json)
                        if as_json:
                            self.assertEqual(json.loads(out)["cache_basis"], basis)
                        else:
                            self.assertTrue(out.startswith("cache basis: %s," % basis), out)
                            self.assertIn("%s report" % name, out)

if __name__ == "__main__":
    unittest.main()
