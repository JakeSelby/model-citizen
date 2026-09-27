"""The micro replay tier is validated and scored offline; no test calls a model."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args


MICRO = BENCH.micro
DETECTOR_IDS = BENCH.replay_detect.load_detectors().DETECTORS


def task(mechanism):
    return {"id": "tiny", "kind": "synthetic", "parent_sha": "a" * 40, "good_sha": None,
            "prompt": ["do the tiny thing"], "tests": {"oracle": "tiny"}, "max_turns": 4,
            "mechanism": mechanism}


def row(arm="harness", rep=1, **values):
    built = {"task": "tiny", "arm": arm, "rep": rep, "passed": True, "error": False,
             "cost_usd": 0.01, "wall_seconds": 2.0, "date": "2026-09-27", "tier": "micro",
             "harness_version": "0.14.0", "harness_sha": "a" * 40, "tag": "candidate",
             "model": "claude-haiku-4-5-20251001", "cli_version": "2.1", "bucket": ""}
    built.update(values)
    return built


class ManifestTests(unittest.TestCase):
    def test_the_committed_manifest_pins_a_small_model_and_every_mechanism_is_scoreable(self):
        document = MICRO.load_document(BENCH.ROOT / MICRO.TASKS)
        self.assertEqual(document["tier"], "micro")
        self.assertEqual(document["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(MICRO.check_manifest(document, DETECTOR_IDS), [])
        self.assertEqual(len(document["tasks"]), 3)

    def test_an_unknown_detector_and_an_unreadable_stream_are_not_silently_no(self):
        bad = task({"id": "voice", "fired": "any-hit", "detectors": ["not/a-detector"]})
        self.assertIn("no such detector", MICRO.mechanism_errors(bad, DETECTOR_IDS)[0])
        good = task({"id": "voice", "fired": "any-hit", "detectors": [next(iter(DETECTOR_IDS))]})
        self.assertIsNone(MICRO.fired(good, row(), []))


class ScoringTests(unittest.TestCase):
    def test_stream_counts_and_offline_detectors_each_score_true_false_or_unknown(self):
        spawn = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        self.assertTrue(MICRO.fired(spawn, row(spawns=1), []))
        self.assertFalse(MICRO.fired(spawn, row(spawns=0), []))
        self.assertIsNone(MICRO.fired(spawn, row(spawns=None), []))

        voice = task({"id": "voice", "fired": "none-hit", "detectors": ["voice/one"]})
        identity = {"task": "tiny", "arm": "harness", "rep": 1, "detector": "voice/one"}
        self.assertTrue(MICRO.fired(voice, row(), [dict(identity, count=0)]))
        self.assertFalse(MICRO.fired(voice, row(), [dict(identity, count=1)]))
        self.assertIsNone(MICRO.fired(voice, row(), [dict(identity, count=None)]))

    def test_each_result_and_report_names_pass_mechanism_and_cost(self):
        spec = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        scored = MICRO.score_rows([row(spawns=1)], [spec], [])
        self.assertEqual((scored[0]["mechanism"], scored[0]["mechanism_fired"]), ("delegation", True))
        report = MICRO.report_lines(scored)[0]
        self.assertIn("pass, delegation fired: yes, 0.0100 USD", report)

    def test_micro_history_has_no_production_ratio_or_claim(self):
        spec = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        rows = MICRO.score_rows([row("bare", spawns=0), row("harness", spawns=1)], [spec], [])
        built = MICRO.history_row(rows, "series", BENCH.ARMS, {})
        self.assertEqual(built["tier"], "micro")
        self.assertNotIn("ratio", built)
        text = MICRO.render_history([built], BENCH.ARMS)
        self.assertIn("did each mechanism fire", text)
        self.assertIn("never as what the harness costs or saves", text)


class TierIsolationTests(unittest.TestCase):
    def test_histories_refuse_to_mix_micro_and_production_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text(json.dumps({"date": "2026-09-27", "series": "p",
                                        "harness_version": "0.14.0", "harness_sha": "a" * 40}) + "\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "refusing to mix micro"):
                BENCH.upsert_history(path, {"date": "2026-09-27", "tier": "micro", "series": "m",
                                                   "harness_version": "0.14.0", "harness_sha": "a" * 40})

    def test_micro_defaults_are_pinned_and_bounded_below_two_dollars(self):
        seen = {}

        def verify(args, tasks):
            seen.update(model=args.model, reps=args.reps, run_cap=args.run_cap,
                        spend_cap=args.spend_cap, tasks=len(tasks))
            return 0

        with mock.patch.object(BENCH, "verify_command", side_effect=verify):
            self.assertEqual(BENCH.main(["replay", "--tier", "micro", "--verify-tasks"]), 0)
        self.assertEqual(seen, {"model": "claude-haiku-4-5-20251001", "reps": 2,
                                "run_cap": 0.1, "spend_cap": 1.9, "tasks": 3})
        maximum = seen["tasks"] * len(BENCH.ARMS) * seen["reps"] * seen["run_cap"] \
            + len(BENCH.ARMS) * MICRO.PREFLIGHT_CAP_USD
        self.assertLess(maximum, 2.0)

    def test_the_pinned_model_cannot_be_overridden(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "pins model"):
                BENCH.main(["replay", "--tier", "micro", "--model", "another-model",
                            "--verify-tasks"])

    def test_a_real_micro_replay_requires_raw_streams_for_mechanism_scoring(self):
        with self.assertRaisesRegex(SystemExit, "requires --raw"):
            BENCH.main(["replay", "--tier", "micro"])

    def test_a_complete_micro_set_enriches_results_and_writes_only_micro_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            raw = Path(tmp) / "raw"
            raw.mkdir()
            args = replay_args(tmp, tier="micro", model=None, raw=str(raw))
            manifest_task = dict(task({"id": "verification", "fired": "any-hit",
                                       "detectors": ["verification/no-verify"]}), id="demo")
            Path(args.tasks).write_text(json.dumps({"tier": "micro", "model": "claude-test",
                                                    "tasks": [manifest_task]}), encoding="utf-8")
            detections = [dict(task="demo", arm=arm, rep=1, detector="verification/no-verify",
                               count=1 if arm == "harness" else 0)
                          for arm in BENCH.ARMS]
            fake = FakeArms()
            with mock.patch.object(BENCH, "ROOT", repo), \
                    mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                    mock.patch.object(BENCH.arms, "egress", fake.egress), \
                    mock.patch.object(BENCH, "replay", fake_replay), \
                    mock.patch.object(BENCH.replay_detect, "detect_rows", return_value=detections), \
                    mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                    redirect_stdout(io.StringIO()) as out:
                self.assertEqual(BENCH.cmd_replay(args), 0)
            rows = BENCH.read_jsonl(Path(tmp) / "out" / "v1" / BENCH.RESULTS)
            self.assertEqual([(r["tier"], r["mechanism_fired"]) for r in rows],
                             [("micro", False), ("micro", True)])
            history = Path(tmp) / "history"
            self.assertTrue((history / MICRO.HISTORY_NAME).is_file())
            self.assertTrue((history / MICRO.HISTORY_MD_NAME).is_file())
            self.assertFalse((history / BENCH.HISTORY.name).exists())
            self.assertIn("verification fired: yes", out.getvalue())


if __name__ == "__main__":
    unittest.main()
