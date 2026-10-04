"""Several models in one run, each its own stratum (#1182): the runner gives each model its own
schedule, folder and history row, the dry run prices each, `summarise` reports each apart and
pools only what the pre-registration names, and an evidence bundle records the strata. No test
builds an image or calls a model."""
import argparse
import contextlib
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_evidence_bundle as bundle_tests
from test_cost_bench import BENCH
from test_cost_bench_tags import FakeArms, harness_repo, replay_args
from test_experiment_protocol import commit_plan, filled_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import replay_stats  # noqa: E402
import replay_strata  # noqa: E402

MODELS = ("claude-a", "claude-b")


def stratum_rows(model, tasks=("t1", "t2"), reps=5, stamp=None):
    """Two arms' rows for one stratum, every attempt passing, the harness arm cheaper."""
    rows = []
    for task in tasks:
        for rep in range(1, reps + 1):
            for arm in BENCH.ARMS:
                rows.append(dict(stamp or {}, task=task, arm=arm, rep=rep, model=model, stratum=model,
                                 error=False, passed=True, cost_usd=1.0 if arm == "bare" else 0.8,
                                 task_long=False))
    return rows


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def summarise_args(results, **over):
    values = dict(results=str(results), seed=replay_stats.SEED, resamples=200, json=False, plot=None,
                  break_even=7.6, correction=None, pool=False)
    values.update(over)
    return argparse.Namespace(**values)


def summarise(args, root=None):
    out = io.StringIO()
    patch = mock.patch.object(BENCH, "ROOT", Path(root)) if root else contextlib.nullcontext()
    with patch, contextlib.redirect_stdout(out):
        code = BENCH.cmd_summarise(args) if root is None else BENCH.summarise_strata(
            BENCH.read_jsonl(Path(args.results)), Path(args.results), args, root=root)
    return code, out.getvalue()


def fake_replay(tasks, opts, launch=None, out=None):
    """The rows a run would write for this stratum, saved where the runner said."""
    rows = [dict(opts["stamp"], task="demo", arm=arm, tag=opts["tag"], rep=1, error=False, passed=True,
                 cost_usd=1.0, cost_normalised_usd=1.0, cache_miss_ratio=0.5, change_note="",
                 **BENCH.arm_stamp(opts["arms"][arm]))
            for arm in BENCH.ARMS]
    write_rows(Path(out), rows)
    return rows, False


class ModelFlagTests(unittest.TestCase):
    def test_a_comma_list_and_a_repeated_flag_name_the_same_strata(self):
        self.assertEqual(replay_strata.parse_models("a,b"), ["a", "b"])
        self.assertEqual(replay_strata.parse_models(["a", "b, c"]), ["a", "b", "c"])
        self.assertEqual(replay_strata.parse_models("a"), ["a"])
        self.assertEqual(replay_strata.parse_models(None), [])

    def test_an_empty_or_repeated_model_is_refused(self):
        for value in ("a,,b", ["a", "a"], "a,a"):
            with self.assertRaises(SystemExit):
                replay_strata.parse_models(value)

    def test_the_parser_accepts_a_repeated_model_flag(self):
        with mock.patch.object(BENCH, "cmd_replay", lambda args: print(args.model) or 0), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            BENCH.main(["replay", "--model", "a", "--model", "b,c", "--exploratory"])
        self.assertEqual(out.getvalue().strip(), "['a', 'b,c']")


class StratifiedRunTests(unittest.TestCase):
    def run_replay(self, tmp, args, fake=None):
        fake = fake or FakeArms()
        out = io.StringIO()
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", fake_replay), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = BENCH.cmd_replay(args)
        return code, out.getvalue(), fake

    def test_each_stratum_gets_its_own_folder_rows_and_history_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, out, fake = self.run_replay(tmp, replay_args(tmp, model=[",".join(MODELS)]))
            self.assertEqual(code, 0)
            self.assertIn("2 strata, each run and reported on its own: claude-a, claude-b", out)
            for model in MODELS:
                rows = BENCH.read_jsonl(Path(tmp) / "out" / "v1" / model / BENCH.RESULTS)
                self.assertEqual({(r["model"], r["stratum"]) for r in rows}, {(model, model)})
                self.assertEqual(rows[0]["strata"], list(MODELS))
            # Each stratum builds and admits its own arms, as a run of that model alone would.
            self.assertEqual([d["arm"] for d in fake.built], ["bare", "harness"] * 2)
            history = BENCH.read_jsonl(Path(tmp) / "history" / "history.jsonl")
            self.assertEqual([(h["model"], h["stratum"]) for h in history], [(m, m) for m in MODELS])
            self.assertNotEqual(history[0]["series"], history[1]["series"])

    def test_one_model_writes_no_stratum_and_keeps_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, _, _ = self.run_replay(tmp, replay_args(tmp, model=["claude-a"]))
            self.assertEqual(code, 0)
            rows = BENCH.read_jsonl(Path(tmp) / "out" / "v1" / BENCH.RESULTS)
            self.assertNotIn("stratum", rows[0])

    def test_the_dry_run_lists_the_strata_and_prices_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = replay_args(tmp, model=list(MODELS), dry_run=True, skip_preflight=False)
            _, out, fake = self.run_replay(tmp, args)
            self.assertEqual(fake.built, [])
            self.assertIn("stratum 1 of 2: model claude-a", out)
            self.assertIn("stratum 2 of 2: model claude-b", out)
            for model in MODELS:
                # 1 task x 2 arms x 1 rep at 2 USD, and two preflights at their cap.
                worst = 2 * 2.0 + 2 * BENCH.PREFLIGHT_CAP_USD
                self.assertIn("stratum %s: worst case %.2f USD if all 2 run(s) reach 2 USD and all 2 "
                              "preflight(s)" % (model, worst), out)
                self.assertIn("model %s at effort high" % model, out)

    def test_the_micro_tier_takes_no_strata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                BENCH.cmd_replay(replay_args(tmp, model=list(MODELS), tier="micro"))
            self.assertIn("takes no strata", str(caught.exception))


class StratifiedSummaryTests(unittest.TestCase):
    def test_every_section_is_reported_per_stratum_and_not_pooled(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, stratum_rows("claude-a") + stratum_rows("claude-b"))
            code, out = summarise(summarise_args(results))
            self.assertEqual(code, 0)
            self.assertIn("== stratum claude-a ==", out)
            self.assertIn("== stratum claude-b ==", out)
            self.assertEqual(out.count("cache basis:"), 2)
            self.assertIn("not pooled", out)

    def test_json_nests_one_full_report_per_stratum(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, stratum_rows("claude-a") + stratum_rows("claude-b"))
            _, out = summarise(summarise_args(results, json=True))
            document = json.loads(out)
            self.assertEqual(sorted(document["strata"]), list(MODELS))
            self.assertNotIn("pooled", document)
            for report in document["strata"].values():
                self.assertEqual(report["ratio"], 0.8)
                self.assertIn("delegation", report)
                self.assertIn("cache_basis", report)

    def test_a_tag_folder_of_stratum_folders_is_read_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            for model in MODELS:
                write_rows(Path(tmp) / model / BENCH.RESULTS, stratum_rows(model))
            _, out = summarise(summarise_args(tmp, json=True))
            self.assertEqual(sorted(json.loads(out)["strata"]), list(MODELS))

    def test_sm2_refuses_rows_from_two_strata(self):
        with self.assertRaises(ValueError) as caught:
            replay_stats.analyse(stratum_rows("claude-a") + stratum_rows("claude-b"))
        self.assertIn("per stratum", str(caught.exception))

    def test_plot_is_refused_across_strata(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, stratum_rows("claude-a") + stratum_rows("claude-b"))
            with self.assertRaises(SystemExit):
                summarise(summarise_args(results, plot=str(Path(tmp) / "p.svg")))


class PoolingTests(unittest.TestCase):
    def registered(self, tmp, pooled):
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        today = datetime.date.today().isoformat()
        text = re.sub(r"- \*\*Pooled analysis:\*\*.*\n(  .*\n)*",
                      "- **Pooled analysis:** %s\n" % pooled, filled_plan(today))
        plan = commit_plan(repo, "%s-strata.md" % today, text)
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
        stamp = {"evidence": "pre-registered", "pre_registration": plan.relative_to(repo).as_posix(),
                 "pre_registration_commit": commit}
        results = Path(tmp) / BENCH.RESULTS
        write_rows(results, stratum_rows("claude-a", stamp=stamp) + stratum_rows("claude-b", stamp=stamp))
        return repo, results

    def test_pooling_is_refused_when_the_plan_names_no_pooled_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, results = self.registered(tmp, "none, each model is read apart")
            with self.assertRaises(SystemExit) as caught:
                summarise(summarise_args(results, pool=True), root=repo)
            self.assertIn("names no pooled analysis", str(caught.exception))

    def test_pooling_is_refused_for_exploratory_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            stamp = {"evidence": "exploratory", "pre_registration": None, "pre_registration_commit": None}
            write_rows(results, stratum_rows("claude-a", stamp=stamp) + stratum_rows("claude-b", stamp=stamp))
            with self.assertRaises(SystemExit) as caught:
                summarise(summarise_args(results, pool=True), root=tmp)
            self.assertIn("not pre-registered", str(caught.exception))

    def test_a_pre_registered_pooled_analysis_is_reported_beside_the_strata(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, results = self.registered(tmp, "task-and-model clusters across both strata")
            code, out = summarise(summarise_args(results, pool=True, json=True), root=repo)
            self.assertEqual(code, 0)
            document = json.loads(out)
            self.assertEqual(sorted(document["strata"]), list(MODELS))
            self.assertEqual(document["pooled"]["analysis"], "task-and-model clusters across both strata")
            self.assertEqual(document["pooled"]["tasks"], 4)  # each task-and-model pair is one cluster

    def test_the_template_leaves_pooling_unnamed(self):
        template = (Path(__file__).resolve().parents[1] / "docs" / "pre-registration-template.md").read_text()
        self.assertIn("- **Pooled analysis:**", template)
        self.assertIsNone(replay_strata.pooled_field(template))


class BundleStrataTests(unittest.TestCase):
    def setUp(self):
        self.base = bundle_tests.EvidenceBundleTest("test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        self.base.setUp()
        self.addCleanup(self.base.tearDown)

    def stratify(self, strata):
        index = self.base._index()
        index["design"]["strata"] = strata
        self.base._save_index(index)

    def test_a_bundle_records_its_stratum_among_the_strata(self):
        self.base._mutate_rows(lambda row: row.update(stratum="claude-sonnet-5"))
        self.stratify(["claude-sonnet-5", "claude-opus-5"])
        result = bundle_tests.EVIDENCE.verify(self.base.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["derived"]["strata"],
                         {"strata": ["claude-sonnet-5", "claude-opus-5"], "stratum": "claude-sonnet-5"})

    def test_rows_naming_a_stratum_the_design_omits_fail_item_three(self):
        self.base._mutate_rows(lambda row: row.update(stratum="claude-sonnet-5"))
        result = bundle_tests.EVIDENCE.verify(self.base.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("stratum the design does not record" in e for e in result["errors"]))

    def test_strata_without_the_design_model_or_off_stratum_rows_fail_item_three(self):
        self.base._mutate_rows(lambda row: row.update(stratum="claude-opus-5"))
        self.stratify(["claude-opus-5", "claude-haiku-4-5"])
        result = bundle_tests.EVIDENCE.verify(self.base.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("design's model among them" in e for e in result["errors"]))
        self.assertTrue(any("stratum is not the design's model" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
