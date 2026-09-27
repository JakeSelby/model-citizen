"""`cost_bench.py detect` (#510): every rule detector over saved `-p` streams, offline. The
streams are synthetic, under tests/fixtures/replay-detect; nothing here calls a model."""
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args

DETECT = BENCH.replay_detect
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replay-detect"
MODULE = DETECT.load_detectors()


def by_detector(rows, source):
    return dict((r["detector"], r) for r in rows if r.get("source") == source)


def copy_of(name, tmp):
    target = Path(tmp) / name
    shutil.copytree(str(FIXTURES / name), str(target))
    return target


class StreamDetectionTests(unittest.TestCase):
    def detect_raw(self, tmp):
        raw = copy_of("raw", tmp)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(BENCH.main(["detect", "--raw", str(raw)]), 0)
        return raw, BENCH.read_jsonl(raw / BENCH.DETECTIONS), out.getvalue()

    def test_a_known_spawn_and_gate_run_fire_delegation_and_verification_at_their_turns(self):
        """Turn 1 reads, turn 2 spawns, turn 3 runs the command the subagent returned, turn 4
        commits past the hooks."""
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, _ = self.detect_raw(tmp)
            run = by_detector(rows, "gate-run-harness-1.json")
            self.assertEqual(run["delegation/executed-from-summary"]["turns"], [3])
            self.assertEqual(run["verification/no-verify"]["turns"], [4])
            self.assertEqual(run["transcript-hygiene/model-wrote-no-cap"]["turns"], [2])
            self.assertEqual(run["delegation/executed-from-summary"]["rule"], "delegation")
            self.assertEqual(run["verification/no-verify"]["count"], 1)

    def test_every_detector_gets_a_row_per_run_zero_counts_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, out = self.detect_raw(tmp)
            bare = by_detector(rows, "gate-run-bare-1.json")
            self.assertEqual(set(bare), set(MODULE.DETECTORS))
            self.assertTrue(all(r["count"] == 0 and r["turns"] == [] for r in bare.values()))
            self.assertEqual((bare["verification/no-verify"]["task"], bare["verification/no-verify"]["arm"],
                              bare["verification/no-verify"]["rep"]), ("gate-run", "bare", 1))
            self.assertIn("3 run(s), 1 unreadable", out)

    def test_a_subagents_own_messages_are_not_the_runs(self):
        """The subagent `cat`s a whole file; the stream tags it with its parent, so it is not a hit."""
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, _ = self.detect_raw(tmp)
            self.assertEqual(by_detector(rows, "gate-run-harness-1.json")
                             ["transcript-hygiene/whole-file-cat"]["count"], 0)

    def test_a_stream_with_no_model_call_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, _ = self.detect_raw(tmp)
            lone = by_detector(rows, "gate-run-harness-2.json")
            self.assertEqual(len(lone), len(MODULE.DETECTORS))
            self.assertTrue(all(r["count"] is None and "unreadable" in r["error"] for r in lone.values()))

    def test_the_preflights_are_not_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, _ = self.detect_raw(tmp)
            self.assertNotIn("preflight-harness.json", set(r["source"] for r in rows))

    def test_stance_gated_detectors_run_in_both_arms(self):
        """`commits/missing-trailer` is gated on a stance neither arm's stream names."""
        with tempfile.TemporaryDirectory() as tmp:
            _, rows, _ = self.detect_raw(tmp)
            self.assertEqual(by_detector(rows, "gate-run-harness-1.json")["commits/missing-trailer"]["turns"], [4])

    def test_a_split_response_is_one_turn_and_task_is_read_as_agent(self):
        messages = [
            {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "a"}]}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Task", "input": {"prompt": "go"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
            {"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": "done"}]}},
        ]
        events = DETECT.stream_events(messages)
        self.assertEqual([(e["kind"], e["turn"]) for e in events],
                         [("assistant_text", 1), ("tool_use", 1), ("tool_result", 1), ("assistant_text", 2)])
        self.assertEqual(events[1]["name"], "Agent")
        self.assertEqual(events[2]["tool_name"], "Agent")
        self.assertEqual([e.get("final") for e in events if e["kind"] == "assistant_text"], [False, True])

    def test_a_detector_that_raises_is_unknown_for_that_detector_only(self):
        def broken(events, ctx):
            raise RuntimeError("boom")
        registry = MODULE.Registry(list(DETECT.ungated(MODULE)) + [
            MODULE.Detector("test/broken", "testing", "session", broken)])
        text = (FIXTURES / "raw" / "gate-run-harness-1.json").read_text(encoding="utf-8")
        rows = dict((r["detector"], r) for r in DETECT.run_rows({"task": "t"}, text, BENCH.cli_messages,
                                                               MODULE, registry))
        self.assertEqual((rows["test/broken"]["count"], rows["test/broken"]["error"]), (None, "RuntimeError"))
        self.assertEqual(rows["verification/no-verify"]["count"], 1)

    def test_a_missing_raw_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                BENCH.main(["detect", "--raw", str(Path(tmp) / "absent")])


class BackfillTests(unittest.TestCase):
    def test_backfill_writes_detections_beside_results_and_leaves_results_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_of("evidence", tmp)
            results = root / "set-a" / "results" / BENCH.RESULTS
            before = hashlib.sha256(results.read_bytes()).hexdigest(), os.stat(str(results)).st_mtime_ns
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(BENCH.main(["detect", "--backfill", str(root)]), 0)
            self.assertEqual((hashlib.sha256(results.read_bytes()).hexdigest(), os.stat(str(results)).st_mtime_ns),
                             before)
            rows = BENCH.read_jsonl(results.parent / BENCH.DETECTIONS)
            run = by_detector(rows, "gate-run-harness-1.json")
            self.assertEqual(run["verification/no-verify"]["turns"], [4])
            self.assertEqual((run["verification/no-verify"]["tag"], run["verification/no-verify"]["harness_version"]),
                             ("v1", "1.0.0"))
            missing = [r for r in rows if r["rep"] == 2]
            self.assertEqual(len(missing), len(MODULE.DETECTORS))
            self.assertTrue(all(r["count"] is None and r["error"] == "no raw output" for r in missing))
            self.assertIn("3 run(s), 1 without a readable stream", out.getvalue())

    def test_two_streams_of_one_name_are_ambiguous_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_of("evidence", tmp)
            other = root / "set-a" / "transcripts" / "live-set"
            other.mkdir()
            shutil.copy(str(root / "set-a" / "transcripts" / "gate-run-bare-1.json"), str(other))
            with redirect_stdout(io.StringIO()):
                BENCH.main(["detect", "--backfill", str(root)])
            rows = BENCH.read_jsonl(root / "set-a" / "results" / BENCH.DETECTIONS)
            bare = [r for r in rows if r["arm"] == "bare"]
            self.assertTrue(all(r["count"] is None and r["error"].startswith("ambiguous") for r in bare))

    def test_a_root_with_no_results_says_so(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(BENCH.main(["detect", "--backfill", tmp]), 0)
        self.assertIn("no results.jsonl", err.getvalue())


class MechanismTests(unittest.TestCase):
    def rows(self):
        base = {"task": "t", "arm": "harness"}
        return [dict(base, rep=1, detector="a/x", count=2), dict(base, rep=1, detector="a/y", count=0),
                dict(base, rep=2, detector="a/x", count=0), dict(base, rep=2, detector="a/y", count=0),
                dict(base, rep=3, detector="a/x", count=None), dict(base, arm="bare", rep=1, detector="a/y", count=5),
                {"task": "quiet", "arm": "harness", "rep": 1, "detector": "a/x", "count": 0}]

    def test_mechanisms_count_the_harness_arms_firing_runs_and_hits_per_task(self):
        self.assertEqual(DETECT.mechanisms(self.rows()),
                         {"t": {"runs": 2, "fired": {"a/x": {"runs": 1, "hits": 2}}},
                          "quiet": {"runs": 1, "fired": {}}})

    def test_history_renders_what_fired_per_task(self):
        lines = DETECT.render_mechanisms(DETECT.mechanisms(self.rows()))
        self.assertEqual(lines, ["    fired in harness arm, quiet: none in 1 run(s)",
                                 "    fired in harness arm, t: a/x in 1 of 2 run(s), 2 hit(s)"])


def raw_replay(raw):
    """`fake_replay`, with each run's stream written where `--raw` keeps it."""
    def replay(tasks, opts, launch=None, out=None):
        rows, stopped = fake_replay(tasks, opts, launch, out)
        for row in rows:
            source = FIXTURES / "raw" / ("gate-run-%s-1.json" % row["arm"])
            shutil.copy(str(source), str(Path(raw) / DETECT.raw_name(row["task"], row["arm"], row["rep"])))
        return rows, stopped
    return replay


class ReplayDetectsTests(unittest.TestCase):
    def test_a_replay_with_raw_writes_detections_and_the_history_says_what_fired(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            raw = Path(tmp) / "raw"
            raw.mkdir()
            fake = FakeArms()
            with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                    mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                    mock.patch.object(BENCH.arms, "egress", fake.egress), \
                    mock.patch.object(BENCH, "replay", raw_replay(raw)), \
                    mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), redirect_stdout(io.StringIO()):
                self.assertEqual(BENCH.cmd_replay(replay_args(tmp, raw=str(raw))), 0)
            rows = BENCH.read_jsonl(Path(tmp) / "out" / "v1" / BENCH.DETECTIONS)
            self.assertEqual(len(rows), 2 * len(MODULE.DETECTORS))
            history = BENCH.read_jsonl(Path(tmp) / "history" / "history.jsonl")[-1]
            fired = history["mechanisms"]["demo"]
            self.assertEqual(fired["runs"], 1)
            self.assertEqual(fired["fired"]["verification/no-verify"], {"runs": 1, "hits": 1})
            text = (Path(tmp) / "history" / "history.md").read_text(encoding="utf-8")
            self.assertIn("fired in harness arm, demo:", text)

    def test_a_replay_without_raw_writes_no_detections_and_no_mechanisms(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            fake = FakeArms()
            with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                    mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                    mock.patch.object(BENCH.arms, "egress", fake.egress), \
                    mock.patch.object(BENCH, "replay", fake_replay), \
                    mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), redirect_stdout(io.StringIO()):
                self.assertEqual(BENCH.cmd_replay(replay_args(tmp)), 0)
            self.assertFalse((Path(tmp) / "out" / "v1" / BENCH.DETECTIONS).exists())
            self.assertNotIn("mechanisms", BENCH.read_jsonl(Path(tmp) / "history" / "history.jsonl")[-1])


if __name__ == "__main__":
    unittest.main()
