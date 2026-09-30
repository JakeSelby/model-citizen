"""The replay's micro tier (#512): validated and scored offline; no test calls a model."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, options
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args


MICRO = BENCH.micro
DETECTOR_IDS = BENCH.replay_detect.load_detectors().DETECTORS
DETECTOR = "voice/banned-opener"
REPO = BENCH.ROOT


def task(mechanism, task_id="tiny"):
    return {"id": task_id, "kind": "synthetic", "parent_sha": "a" * 40, "good_sha": None,
            "prompt": ["do the tiny thing"], "tests": {"oracle": "tiny"}, "max_turns": 4,
            "mechanism": mechanism}


def row(arm="harness", rep=1, **values):
    built = {"task": "tiny", "arm": arm, "rep": rep, "passed": True, "error": False,
             "cost_usd": 0.01, "wall_seconds": 2.0, "date": "2026-09-30", "tier": "micro",
             "harness_version": "0.15.0", "harness_sha": "a" * 40, "tag": "v0.15.0",
             "model": "claude-haiku-4-5-20251001", "cli_version": "2.1", "bucket": "", "effort": "high"}
    built.update(values)
    return built


def committed():
    return MICRO.load_document(REPO / MICRO.TASKS)


class ManifestTests(unittest.TestCase):
    def test_the_committed_manifest_pins_a_small_model_and_every_mechanism_is_scoreable(self):
        document = committed()
        self.assertEqual(document["tier"], "micro")
        self.assertEqual(document["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(MICRO.check_manifest(document, DETECTOR_IDS), [])
        self.assertEqual([t["mechanism"]["id"] for t in document["tasks"]],
                         ["delegation", "stop-gate", "output-style"])
        self.assertEqual(len(BENCH.load_tasks(REPO / MICRO.TASKS)), 3)

    def test_every_micro_task_starts_from_a_commit_on_main_and_has_its_oracle(self):
        for spec in committed()["tasks"]:
            with self.subTest(task=spec["id"]):
                self.assertEqual(subprocess.run(
                    ["git", "-C", str(REPO), "cat-file", "-e", spec["parent_sha"] + "^{commit}"]).returncode, 0)
                oracle = BENCH._oracle(REPO, spec["tests"]["oracle"])
                self.assertTrue(callable(oracle.check) and callable(oracle.solve))

    def test_the_hook_count_is_the_glob_at_the_task_s_parent_sha(self):
        spec = [t for t in committed()["tasks"] if t["id"] == "micro-output-style"][0]
        listed = subprocess.check_output(["git", "ls-tree", "--name-only", spec["parent_sha"], "claude/hooks/"],
                                         cwd=str(REPO)).decode("utf-8").split()
        oracle = BENCH._oracle(REPO, "micro_hook_count")
        self.assertEqual(oracle.EXPECTED_HOOKS, len([n for n in listed if n.endswith(".py")]))

    def test_the_renamed_function_exists_at_the_task_s_parent_sha(self):
        spec = [t for t in committed()["tasks"] if t["id"] == "micro-stop-gate"][0]
        source = subprocess.check_output(["git", "show", spec["parent_sha"] + ":bin/harness"],
                                         cwd=str(REPO)).decode("utf-8")
        self.assertIn("def strip_claude_settings(", source)
        self.assertNotIn("unmerge_claude_settings", source)

    def test_an_unknown_detector_is_refused_and_an_unread_stream_is_not_silently_no(self):
        bad = task({"id": "voice", "fired": "any-hit", "detectors": ["not/a-detector"]})
        self.assertIn("no such detector", MICRO.mechanism_errors(bad, DETECTOR_IDS)[0])
        good = task({"id": "voice", "fired": "any-hit", "detectors": [DETECTOR]})
        self.assertEqual(MICRO.mechanism_errors(good, DETECTOR_IDS), [])
        self.assertIsNone(MICRO.fired(good, row(), []))

    def test_each_malformed_mechanism_is_named(self):
        cases = ((None, "has no mechanism"), ({"id": "x", "fired": "sometimes"}, "must be one of"),
                 ({"id": "x", "fired": "above-zero", "field": "tokens"}, "reads one of"),
                 ({"id": "x", "fired": "any-hit"}, "names its detectors"),
                 ({"id": "x", "fired": "any-hit", "detectors": [{"bad": "id"}]}, "non-empty strings"),
                 ({"id": "x", "fired": "none-hit", "detectors": [DETECTOR, DETECTOR]}, "must be unique"))
        for spec, message in cases:
            with self.subTest(spec=spec):
                self.assertIn(message, MICRO.mechanism_errors(task(spec), DETECTOR_IDS)[0])

    def test_a_micro_manifest_names_its_tier_and_its_one_model(self):
        errors = MICRO.check_manifest({"tier": "production", "tasks": []}, DETECTOR_IDS)
        self.assertEqual(len(errors), 2)
        self.assertIn("tier micro", errors[0])
        self.assertIn("names the one model", errors[1])


class ScoringTests(unittest.TestCase):
    def test_stream_counts_score_true_false_or_unknown(self):
        spawn = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        self.assertTrue(MICRO.fired(spawn, row(spawns=1), []))
        self.assertFalse(MICRO.fired(spawn, row(spawns=0), []))
        self.assertIsNone(MICRO.fired(spawn, row(spawns=None), []))
        self.assertIsNone(MICRO.fired(spawn, row(), []))

    def test_offline_detectors_score_true_false_or_unknown_per_run(self):
        voice = task({"id": "voice", "fired": "none-hit", "detectors": [DETECTOR]})
        identity = {"task": "tiny", "arm": "harness", "rep": 1, "detector": DETECTOR}
        self.assertTrue(MICRO.fired(voice, row(), [dict(identity, count=0)]))
        self.assertFalse(MICRO.fired(voice, row(), [dict(identity, count=2)]))
        self.assertIsNone(MICRO.fired(voice, row(), [dict(identity, count=None, error="no stream")]))
        # Another run's detection never scores this one.
        self.assertIsNone(MICRO.fired(voice, row(), [dict(identity, rep=2, count=0)]))
        hit = task({"id": "voice", "fired": "any-hit", "detectors": [DETECTOR]})
        self.assertTrue(MICRO.fired(hit, row(), [dict(identity, count=1)]))

    def test_each_result_and_report_names_pass_mechanism_and_cost(self):
        spec = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        scored = MICRO.score_rows([row(spawns=1), row("bare", spawns=0, passed=False, cost_usd=None),
                                   row(task="elsewhere")], [spec], [])
        self.assertEqual([(r["mechanism"], r["mechanism_fired"]) for r in scored],
                         [("delegation", True), ("delegation", False), (None, None)])
        report = MICRO.report_lines(scored)
        self.assertIn("pass, delegation fired: yes, 0.0100 USD", report[0])
        self.assertIn("fail, delegation fired: no, cost unknown", report[1])
        errored = MICRO.report_lines([dict(scored[0], error=True, error_kind="timeout")])[0]
        self.assertIn("error (timeout)", errored)

    def test_micro_history_has_no_ratio_verdict_or_cost_claim(self):
        spec = task({"id": "delegation", "fired": "above-zero", "field": "spawns"})
        rows = MICRO.score_rows([row("bare", spawns=0), row("harness", spawns=1)], [spec], [])
        built = MICRO.history_row(rows, "series", BENCH.ARMS, {})
        self.assertEqual((built["tier"], built["effort"], built["runs"]), ("micro", "high", 2))
        for key in ("ratio", "status", "sm2", "threshold"):
            self.assertNotIn(key, built)
        self.assertEqual(built["per_task"]["tiny"]["harness"]["fired"], 1)
        self.assertEqual(built["per_task"]["tiny"]["bare"]["fired"], 0)
        text = MICRO.render_history([built], BENCH.ARMS)
        self.assertIn("did each mechanism fire", text)
        self.assertIn("never as what the harness costs or saves", text)


class TierIsolationTests(unittest.TestCase):
    def history(self, tmp, row_):
        path = Path(tmp) / "history.jsonl"
        path.write_text(json.dumps(row_) + "\n", encoding="utf-8")
        return path

    def test_a_micro_row_never_enters_a_production_history(self):
        base = {"date": "2026-09-30", "series": "p", "harness_version": "0.15.0", "harness_sha": "a" * 40}
        with tempfile.TemporaryDirectory() as tmp:
            path = self.history(tmp, base)
            with self.assertRaisesRegex(SystemExit, "refusing to mix micro rows into the production"):
                BENCH.upsert_history(path, dict(base, tier="micro", series="m"))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_a_production_row_never_enters_a_micro_history(self):
        base = {"date": "2026-09-30", "series": "m", "harness_version": "0.15.0", "harness_sha": "a" * 40}
        with tempfile.TemporaryDirectory() as tmp:
            path = self.history(tmp, dict(base, tier="micro"))
            with self.assertRaisesRegex(SystemExit, "refusing to mix production rows into the micro"):
                BENCH.upsert_history(path, dict(base, series="p"))

    def test_the_micro_files_and_series_differ_from_production(self):
        self.assertNotIn(MICRO.HISTORY_NAME, (BENCH.HISTORY.name, BENCH.HISTORY_MD.name))
        self.assertNotIn(MICRO.HISTORY_MD_NAME, (BENCH.HISTORY.name, BENCH.HISTORY_MD.name))


class ProtocolTests(unittest.TestCase):
    def verified(self, *extra):
        seen = {}

        def verify(args, tasks):
            seen.update(model=args.model, reps=args.reps, run_cap=args.run_cap, spend_cap=args.spend_cap,
                        tasks=len(tasks), manifest=args.tasks)
            return 0

        with mock.patch.object(BENCH, "verify_command", side_effect=verify):
            self.assertEqual(BENCH.main(["replay", "--tier", "micro", "--verify-tasks"] + list(extra)), 0)
        return seen

    def test_the_micro_defaults_are_five_reps_under_the_pre_registered_stop(self):
        seen = self.verified()
        self.assertEqual(Path(seen.pop("manifest")), BENCH.ROOT / MICRO.TASKS)
        self.assertEqual(seen, {"model": "claude-haiku-4-5-20251001", "reps": 5, "run_cap": 0.1,
                                "spend_cap": 4.55, "tasks": 3})
        ceiling = MICRO.ceiling_usd(seen["tasks"], seen["reps"], len(BENCH.ARMS))
        self.assertAlmostEqual(ceiling, 3.10)
        self.assertLess(ceiling, seen["spend_cap"])

    def test_a_flag_may_change_the_reps_and_caps_but_not_the_model(self):
        seen = self.verified("--reps", "2", "--run-cap", "0.05", "--spend-cap", "1")
        self.assertEqual((seen["reps"], seen["run_cap"], seen["spend_cap"]), (2, 0.05, 1.0))
        with redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "pins model claude-haiku-4-5-20251001, not other"):
                BENCH.main(["replay", "--tier", "micro", "--model", "other", "--verify-tasks"])

    def test_the_production_defaults_are_unchanged(self):
        seen = {}

        def verify(args, tasks):
            seen.update(reps=args.reps, run_cap=args.run_cap, manifest=args.tasks)
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "tasks.json"
            manifest.write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
            with mock.patch.object(BENCH, "verify_command", side_effect=verify):
                BENCH.main(["replay", "--tasks", str(manifest), "--verify-tasks"])
        self.assertEqual((seen["reps"], seen["run_cap"]), (BENCH.DEFAULT_REPS, BENCH.RUN_CAP_USD))
        with tempfile.TemporaryDirectory() as tmp:
            args = replay_args(tmp, reps=None, run_cap=None)
        args.tasks = None  # a Namespace built without `tier` is the production tier
        BENCH.resolve_tier(args)
        self.assertEqual((args.tier, Path(args.tasks), args.reps), ("production", BENCH.ROOT / BENCH.TASKS,
                                                                    BENCH.DEFAULT_REPS))

    def test_a_real_micro_run_needs_raw_streams(self):
        with self.assertRaisesRegex(SystemExit, "needs --raw"):
            BENCH.main(["replay", "--tier", "micro", "--exploratory", "--tag", "v1"])

    def test_a_pair_is_refused_with_the_micro_tier(self):
        with self.assertRaisesRegex(SystemExit, "--pair is refused with --tier micro"):
            BENCH.main(["replay", "--tier", "micro", "--pair", "benchmarks/ablations/x.json", "--dry-run"])

    def test_an_invalid_micro_manifest_is_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "micro.json"
            manifest.write_text(json.dumps({"model": "m", "tasks": [task(None)]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "invalid micro manifest:\n  a micro manifest has tier micro"):
                BENCH.main(["replay", "--tier", "micro", "--tasks", str(manifest), "--verify-tasks"])

    def test_the_micro_preflight_runs_at_its_own_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch(["garbage", "garbage"])
            checks, spent = BENCH.preflight([TASK], options(tmp, preflight_cap=MICRO.PREFLIGHT_CAP_USD), launch)
        commands = [c for c, _ in launch.calls if "--max-budget-usd" in c]
        self.assertEqual([c[c.index("--max-budget-usd") + 1] for c in commands], ["0.05", "0.05"])
        self.assertAlmostEqual(spent, 0.10)  # no readable cost counts each at its own cap

    def test_the_micro_tier_keeps_the_contamination_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, contamination_checker=lambda *args: ["micro-delegation: exposed"])
            opts["stamp"] = dict(opts["stamp"], tier="micro")
            with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, Launch([]))
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("micro-delegation: exposed", err.getvalue())


class MicroSetTests(unittest.TestCase):
    def run_set(self, tmp, exploratory=False):
        repo = harness_repo(Path(tmp) / "repo")
        raw = Path(tmp) / "raw"
        raw.mkdir()
        args = replay_args(tmp, tier="micro", model=None, raw=str(raw), reps=None, run_cap=None,
                           spend_cap=None, exploratory=exploratory,
                           **({"pre_registration": None} if exploratory else {}))
        spec = task({"id": "voice", "fired": "any-hit", "detectors": [DETECTOR]}, "demo")
        Path(args.tasks).write_text(json.dumps({"tier": "micro", "model": "claude-test", "tasks": [spec]}),
                                    encoding="utf-8")
        detections = [dict(task="demo", arm=arm, rep=1, detector=DETECTOR, count=1 if arm == "harness" else 0)
                      for arm in BENCH.ARMS]
        fake, seen = FakeArms(), {}

        def replay(tasks, opts, launch=None, out=None):
            seen.update(opts)
            return fake_replay(tasks, opts, launch, out)

        with mock.patch.object(BENCH, "ROOT", repo), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", replay), \
                mock.patch.object(BENCH.replay_detect, "detect_saved", return_value=detections), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(BENCH.cmd_replay(args), 0)
        return out.getvalue(), seen

    def test_a_complete_set_scores_its_rows_and_writes_only_the_micro_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            printed, opts = self.run_set(tmp)
            rows = BENCH.read_jsonl(Path(tmp) / "out" / "v1" / BENCH.RESULTS)
            self.assertEqual([(r["arm"], r["tier"], r["mechanism"], r["mechanism_fired"]) for r in rows],
                             [("bare", "micro", "voice", False), ("harness", "micro", "voice", True)])
            history = Path(tmp) / "history"
            kept = BENCH.read_jsonl(history / MICRO.HISTORY_NAME)
            self.assertEqual([r["tier"] for r in kept], ["micro"])
            self.assertTrue((history / MICRO.HISTORY_MD_NAME).is_file())
            self.assertFalse((history / BENCH.HISTORY.name).exists())
            self.assertFalse((history / BENCH.HISTORY_MD.name).exists())
            self.assertIn("voice fired: yes", printed)
            self.assertEqual((opts["model"], opts["reps"], opts["run_cap"], opts["spend_cap"],
                              opts["preflight_cap"]), ("claude-test", 5, 0.1, 4.55, 0.05))

    def test_the_micro_series_is_not_the_production_series_over_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_set(tmp)
            micro_series = BENCH.read_jsonl(Path(tmp) / "history" / MICRO.HISTORY_NAME)[0]["series"]
            manifest = Path(tmp) / "tasks.json"
            production = BENCH.hashlib.sha256(manifest.read_bytes() + b"claude-test|container").hexdigest()[:8]
            self.assertNotEqual(micro_series, production)

    def test_an_exploratory_micro_set_writes_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_set(tmp, exploratory=True)
            self.assertFalse((Path(tmp) / "history").exists())
            self.assertTrue((Path(tmp) / "out" / "v1" / BENCH.RESULTS).is_file())


if __name__ == "__main__":
    unittest.main()
