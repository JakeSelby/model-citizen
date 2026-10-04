"""Oracle checks may return named metrics beside pass or fail (#1143): the declaration, the
verdict a check returns, the row fields a run records, and the per-arm report. No container is
started and no model called; a fixture check runs in a local Python standing in for the scorer's
container."""
import io
import json
import math
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH, TASK, Launch, options, result
from test_replay_pack import CANARY, make_pack, task_spec

METRICS = BENCH.oracle_metrics
PACK = BENCH.replay_pack
DECLARED = {"correctness": "higher", "lines_out_of_scope": "lower"}

CHECK_WITH_METRICS = ("# %s\nimport math\ndef check(root):\n    return {'pass': True, 'errors': [],\n"
                      "            'metrics': {'correctness': 0.75, 'lines_out_of_scope': math.nan}}\n" % CANARY)
CHECK_PASS_ONLY = "# %s\ndef check(root):\n    return []\n" % CANARY
CHECK_MALFORMED = "# %s\ndef check(root):\n    return {'pass': 'yes', 'metrics': {'correctness': 1}}\n" % CANARY


def local_scorer(command, **kwargs):
    """The scorer's container, played by a local Python reading the check on stdin."""
    done = subprocess.run([sys.executable, "-"], input=kwargs["input"], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)
    return types.SimpleNamespace(stdout=done.stdout, stderr=done.stderr, returncode=done.returncode)


def pack_task(task_dir, source, metrics=None):
    (Path(task_dir) / "check.py").write_text(source, encoding="utf-8")
    task = {"id": "metric-task", "kind": "pack", "tests": {"oracle": "metric-task"},
            "pack": {"task_dir": str(task_dir)}}
    if metrics is not None:
        task["metrics"] = metrics
    return task


class DeclarationTests(unittest.TestCase):
    def test_a_declaration_names_each_metric_and_a_known_direction(self):
        self.assertEqual(METRICS.declaration_errors(None, "t"),
                         ["t: metrics must be a non-empty object of name to higher or lower"])
        self.assertEqual(METRICS.declaration_errors(DECLARED, "t"), [])
        self.assertEqual(METRICS.declaration_errors({}, "t"),
                         ["t: metrics must be a non-empty object of name to higher or lower"])
        errors = METRICS.declaration_errors({"Bad Name": "higher", "speed": "faster"}, "t")
        self.assertIn("t: metric name 'Bad Name' is not lower_snake_case", errors)
        self.assertIn("t: metric speed has unknown direction 'faster'; use higher or lower", errors)

    def test_the_pack_carries_a_declaration_and_refuses_an_unknown_direction(self):
        good = [task_spec("short-one", metrics=DECLARED)]
        bad = [task_spec("short-one", metrics={"correctness": "up"})]
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "good"), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            self.assertNotIn("metrics", PACK.load_set(pack, "production", "production")[0][0])
            pack = PACK.open_pack(make_pack(Path(tmp) / "declared", tasks=good), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            self.assertEqual(PACK.load_set(pack, "production", "production")[0][0]["metrics"], DECLARED)
            pack = PACK.open_pack(make_pack(Path(tmp) / "bad", tasks=bad), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            with self.assertRaisesRegex(SystemExit, "metric correctness has unknown direction 'up'"):
                PACK.load_set(pack, "production", "production")

    def test_the_in_repository_manifest_refuses_an_unknown_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [dict(TASK, metrics={"correctness": "sideways"})]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "unknown direction 'sideways'"):
                BENCH.load_tasks(path)
            path.write_text(json.dumps({"tasks": [dict(TASK, metrics=DECLARED)]}), encoding="utf-8")
            self.assertEqual(BENCH.load_tasks(path)[0]["metrics"], DECLARED)

    def test_the_in_repository_manifest_refuses_a_null_declaration_and_keeps_an_omitted_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [dict(TASK, metrics=None)]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "metrics must be a non-empty object"):
                BENCH.load_tasks(path)
            path.write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
            self.assertNotIn("metrics", BENCH.load_tasks(path)[0])

    def test_an_issue_task_may_not_declare_metrics_its_unit_tests_cannot_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [dict(TASK, kind="issue", metrics=DECLARED)]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "an issue task's unit tests report no metrics"):
                BENCH.load_tasks(path)
            path.write_text(json.dumps({"tasks": [dict(TASK, kind="issue")]}), encoding="utf-8")
            self.assertEqual(BENCH.load_tasks(path)[0]["kind"], "issue")

    def test_the_pack_refuses_a_null_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "null", tasks=[task_spec("short-one", metrics=None)]),
                                  harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            with self.assertRaisesRegex(SystemExit, "metrics must be a non-empty object"):
                PACK.load_set(pack, "production", "production")


class VerdictTests(unittest.TestCase):
    def test_the_original_error_list_keeps_its_meaning(self):
        self.assertEqual(METRICS.verdict([]), (True, "", None))
        self.assertEqual(METRICS.verdict(["a", "b", "c", "d"]), (False, "a; b; c", None))
        self.assertEqual(METRICS.verdict({"pass": False, "errors": ["no answer"]}), (False, "no answer", None))

    def test_declared_metrics_are_recorded_and_a_bad_value_is_unknown_never_zero(self):
        got = METRICS.verdict({"pass": True, "metrics": {"correctness": 1, "lines_out_of_scope": float("inf")}},
                              DECLARED)
        self.assertEqual(got, (True, "", {"metrics": {"correctness": 1.0, "lines_out_of_scope": None},
                                          "metric_errors": ["lines_out_of_scope: inf is not a finite number"]}))
        for bad in ("3", True, math.nan, 10 ** 400, [1]):
            recorded = METRICS.verdict({"pass": True, "metrics": {"correctness": bad, "lines_out_of_scope": 2}},
                                       DECLARED)[2]
            self.assertIsNone(recorded["metrics"]["correctness"], bad)
            self.assertEqual(len(recorded["metric_errors"]), 1, bad)
        recorded = METRICS.verdict({"pass": False, "metrics": {"correctness": None}}, DECLARED)[2]
        self.assertEqual(recorded, {"metrics": {"correctness": None, "lines_out_of_scope": None},
                                    "metric_errors": ["lines_out_of_scope: not reported"]})
        self.assertEqual(METRICS.verdict([], DECLARED)[2]["metric_errors"],
                         ["correctness: not reported", "lines_out_of_scope: not reported"])

    def test_a_malformed_verdict_is_a_broken_check(self):
        for value, message in (({"pass": "yes"}, "pass is not true or false"),
                               ({"pass": True, "score": 1}, "unknown key"),
                               ({"pass": True, "metrics": [1]}, "metrics are not an object"),
                               ({"pass": True, "errors": "x"}, "errors are not a list"),
                               ({"pass": True, "metrics": {"speed": 1}}, "undeclared metric"),
                               ([1], "not all strings"), (True, "not a list or an object")):
            with self.assertRaisesRegex(ValueError, message):
                METRICS.verdict(value, DECLARED)
        with self.assertRaisesRegex(ValueError, "undeclared metric"):
            METRICS.verdict({"pass": True, "metrics": {"correctness": 1}})


class ScoreTests(unittest.TestCase):
    def test_a_fixture_check_returning_metrics_reaches_the_scorer_through_its_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = pack_task(tmp, CHECK_WITH_METRICS, DECLARED)
            got = BENCH.score(task, tmp, None, "model-citizen-arm-bare:test", local_scorer)
        self.assertEqual(got, (True, "", {"metrics": {"correctness": 0.75, "lines_out_of_scope": None},
                                          "metric_errors": ["lines_out_of_scope: nan is not a finite number"]}))

    def test_a_fixture_check_returning_only_pass_scores_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = BENCH.score(pack_task(tmp, CHECK_PASS_ONLY), tmp, None, "model-citizen-arm-bare:test", local_scorer)
        self.assertEqual(got, (True, ""))

    def test_a_malformed_fixture_check_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = pack_task(tmp, CHECK_MALFORMED, DECLARED)
            with self.assertRaisesRegex(ValueError, "pass is not true or false"):
                BENCH.score(task, tmp, None, "model-citizen-arm-bare:test", local_scorer)


class RowTests(unittest.TestCase):
    def run_one(self, task, scorer):
        with tempfile.TemporaryDirectory() as tmp:
            return BENCH.run_one(task, 1, "bare", options(tmp, scorer=scorer), Launch([json.dumps([result()])]))

    def test_a_task_without_metrics_writes_no_metric_field(self):
        plain = self.run_one(TASK, lambda *a: (True, ""))
        self.assertFalse({"metrics", "metric_directions", "metric_errors"} & set(plain))
        self.assertEqual(plain["outcome"], "pass")

    def test_a_declaring_task_records_its_metrics_and_directions(self):
        task = dict(TASK, metrics=DECLARED)
        recorded = {"metrics": {"correctness": 0.5, "lines_out_of_scope": None},
                    "metric_errors": ["lines_out_of_scope: 'x' is not a finite number"]}
        row = self.run_one(task, lambda *a: (True, "", recorded))
        self.assertEqual((row["metrics"], row["metric_errors"]), (recorded["metrics"], recorded["metric_errors"]))
        self.assertEqual(row["metric_directions"], DECLARED)

    def test_a_broken_check_is_a_check_error_with_every_metric_unknown(self):
        def broken(*a):
            raise ValueError("the check's pass is not true or false")
        row = self.run_one(dict(TASK, metrics=DECLARED), broken)
        self.assertEqual((row["error"], row["error_kind"], row["outcome"]), (True, "check: ValueError", "fail"))
        self.assertEqual(row["metrics"], {"correctness": None, "lines_out_of_scope": None})


def metric_rows(spec, direction="lower"):
    """Rows from `{task: {arm: [value, ...]}}` for one metric, `tokens_read`; every run passes."""
    out = []
    for task, arms in spec.items():
        for arm, values in arms.items():
            for rep, value in enumerate(values, 1):
                out.append({"task": task, "arm": arm, "rep": rep, "error": False, "passed": True,
                            "cost_usd": 1.0, "task_long": False, "metrics": {"tokens_read": value},
                            "metric_directions": {"tokens_read": direction}, "metric_errors": []})
    return out


class SummaryTests(unittest.TestCase):
    SPEC = {"t%d" % t: {"bare": [100.0 + t, 110.0 + t], "harness": [60.0 + t, None]} for t in range(4)}

    def test_each_metric_is_reported_per_arm_and_task_with_the_clustered_interval(self):
        got = METRICS.summarise(metric_rows(self.SPEC), seed=3, resamples=200)
        metric = got["metrics"]["tokens_read"]
        self.assertEqual((got["method"], got["seed"], got["resamples"]), (BENCH.replay_stats.METHOD, 3, 200))
        self.assertEqual(metric["tasks"]["t1"], {"bare": {"mean": 106.0, "n": 2, "unknown": 0},
                                                 "harness": {"mean": 61.0, "n": 1, "unknown": 1}})
        self.assertEqual(metric["arms"]["harness"], {"mean": 61.5, "n": 4, "unknown": 4})
        self.assertEqual((metric["difference"], metric["paired_tasks"]), (-45.0, 4))
        self.assertLess(metric["difference_interval"][1], 0)
        self.assertEqual(metric["reading"], "better")  # fewer tokens read, and lower is better
        self.assertEqual(METRICS.summarise(metric_rows(self.SPEC, "higher"), 3, 200)["metrics"]["tokens_read"]
                         ["reading"], "worse")
        self.assertEqual(got, METRICS.summarise(metric_rows(self.SPEC), seed=3, resamples=200))

    def test_no_known_value_in_one_arm_leaves_the_difference_unavailable(self):
        rows = metric_rows({"t0": {"bare": [1.0], "harness": [None]}})
        metric = METRICS.summarise(rows, resamples=50)["metrics"]["tokens_read"]
        self.assertEqual((metric["difference"], metric["reading"]), (None, "unavailable"))

    def test_finite_values_whose_mean_or_difference_overflows_stay_unknown(self):
        self.assertEqual(METRICS._mean([1e308, 1e308]), 1e308)
        rows = metric_rows({"t%d" % t: {"bare": [-1e308, -1e308], "harness": [1e308, 1e308]} for t in range(2)})
        metric = METRICS.summarise(rows, resamples=50)["metrics"]["tokens_read"]
        self.assertEqual(metric["arms"]["harness"]["mean"], 1e308)
        self.assertEqual((metric["difference"], metric["difference_interval"], metric["reading"]),
                         (None, None, "unavailable"))
        self.assertEqual(metric["reason"], "the difference between the arms is not a finite number")
        json.dumps(metric, allow_nan=False)  # nothing non-finite reaches the report

    def test_rows_without_metrics_have_no_report_and_a_bad_row_is_refused(self):
        self.assertIsNone(METRICS.summarise([{"task": "t", "arm": "bare", "rep": 1}]))
        self.assertEqual(METRICS.render(None), "")
        rows = metric_rows({"t0": {"bare": [float("nan")], "harness": [1.0]}})
        with self.assertRaisesRegex(ValueError, "not a finite number or null"):
            METRICS.summarise(rows, resamples=50)
        rows = metric_rows({"t0": {"bare": [1.0], "harness": [1.0]}})
        rows[1]["metric_directions"] = {"tokens_read": "higher"}
        with self.assertRaisesRegex(ValueError, "inconsistent directions"):
            METRICS.summarise(rows, resamples=50)

    def test_summarise_adds_the_metrics_beside_sm2_from_the_saved_rows(self):
        rows = metric_rows(self.SPEC)
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(BENCH.main(["summarise", "--results", tmp, "--json", "--seed", "3",
                                             "--resamples", "200"]), 0)
            printed = json.loads(out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                BENCH.main(["summarise", "--results", tmp, "--seed", "3", "--resamples", "200"])
        self.assertEqual(printed["metrics"], METRICS.summarise(rows, seed=3, resamples=200))
        self.assertEqual(printed["verdict"], BENCH.replay_stats.analyse(rows, 3, 200)["verdict"])
        self.assertIn("tokens_read (lower is better)", out.getvalue())
        self.assertIn("    t1: bare 106 (n 2, unknown 0), harness 61 (n 1, unknown 1)", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class VerifyTests(unittest.TestCase):
    """`verify_tasks` on a task declaring metrics: the check's pass decides, the metrics do not."""

    def verify(self, tmp, verdicts):
        tasks = [task_spec("short-one", metrics=DECLARED)]
        pack = PACK.open_pack(make_pack(Path(tmp) / "pack", tasks=tasks), harness_root=Path(tmp) / "harness")
        self.addCleanup(PACK.close_pack, pack)
        task = PACK.load_set(pack, "production", "production")[0][0]
        answers = [json.dumps(v) for v in verdicts]

        def launch(command, **kwargs):
            if command[-2:] == ["python3", "-"] and BENCH.SOLVED_MARK in kwargs["input"]:
                return types.SimpleNamespace(stdout=BENCH.SOLVED_MARK + "ok\n", returncode=0)
            if command[-2:] == ["python3", "-"]:
                return types.SimpleNamespace(stdout=BENCH.ORACLE_MARK + answers.pop(0) + "\n", returncode=0)
            return types.SimpleNamespace(stdout="", returncode=0)

        errors = BENCH.verify_tasks([task], None, Path(tmp) / "verify", "model-citizen-arm-bare:test", [], launch)
        self.assertEqual(answers, [])
        return errors

    def test_a_metric_bearing_check_fails_before_and_passes_on_the_known_good_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = self.verify(tmp, [{"pass": False, "metrics": {"correctness": 0}, "errors": ["no answer"]},
                                       {"pass": True, "metrics": {"correctness": 1, "lines_out_of_scope": None}}])
        self.assertEqual(errors, [])

    def test_a_metric_bearing_check_failing_on_the_known_good_tree_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = self.verify(tmp, [{"pass": False, "metrics": {"correctness": 0}},
                                       {"pass": False, "metrics": {"correctness": 1}, "errors": ["still wrong"]}])
        self.assertEqual(errors, ["short-one: the check fails on the known-good tree (still wrong)"])
