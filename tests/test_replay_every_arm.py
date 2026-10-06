"""`summarise` reports every declared arm (#1228): each arm's own figures, and each arm against bare
and against the harness default with task-clustered intervals, per stratum; SM-2 stays harness
against bare, and a config arm's comparison is secondary unless the pre-registration names it
primary. The runner keeps each run's final diff beside its stream for the judge. Synthetic rows
and fake launches only; no test builds an image or calls a model."""
import argparse
import datetime
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, options, result
from test_experiment_protocol import PROTOCOL, filled_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import replay_judge  # noqa: E402
import replay_stats  # noqa: E402

ARMS = ("bare", "harness", "frugal")
COST = {"bare": 1.0, "harness": 0.8, "frugal": 0.6}
MODELS = ("claude-a", "claude-b")


def arm_rows(model=None, tasks=("t1", "t2", "t3"), reps=5, arms=ARMS, stamp=None):
    """Three arms on every task and trial; frugal fails rep 5 of t1, and every row carries one
    lower-is-better metric whose value is the arm's cost."""
    rows = []
    for task in tasks:
        for rep in range(1, reps + 1):
            for arm in arms:
                row = dict(stamp or {}, task=task, arm=arm, rep=rep, error=False,
                           passed=not (arm == "frugal" and task == "t1" and rep == 5),
                           cost_usd=COST[arm], task_long=False,
                           metric_directions={"edits": "lower"}, metrics={"edits": COST[arm] * 10})
                if model:
                    row.update(model=model, stratum=model)
                rows.append(row)
    return rows


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def summarise(results, as_json, **over):
    values = dict(results=str(results), seed=replay_stats.SEED, resamples=200, json=as_json, plot=None,
                  break_even=7.6, correction=None, pool=False)
    values.update(over)
    out = io.StringIO()
    with redirect_stdout(out):
        code = BENCH.cmd_summarise(argparse.Namespace(**values))
    return code, out.getvalue()


def by_label(comparisons):
    return {c["label"]: c for c in comparisons}


def git(repo, *args):
    """Git in `repo` alone: `-C` does not override an inherited GIT_DIR or GIT_WORK_TREE, so every
    fixture call runs with the scrubbed environment the bench itself gives git."""
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t"] + list(args),
                          check=True, env=BENCH.scrubbed_env(), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()


def committed_plan(repo, name, text):
    """(plan path relative to `repo`, commit): `text` committed to a new repository at `repo`."""
    git(repo, "init", "-q")
    path = Path(repo) / PROTOCOL.DIRECTORY / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", str(path))
    git(repo, "commit", "-qm", "docs(benchmarks): pre-register %s" % name)
    return path.relative_to(repo).as_posix(), git(repo, "rev-parse", "HEAD")


def stray_repository(tmp):
    """Variables naming another repository, as a caller inside a Git hook or worktree passes on."""
    other = Path(tmp) / "other"
    git(tmp, "init", "-q", str(other))
    return {"GIT_DIR": git(other, "rev-parse", "--absolute-git-dir"), "GIT_WORK_TREE": str(other)}


class AnalyseArmsTests(unittest.TestCase):
    def test_every_arm_is_reported_and_compared_against_bare_and_harness(self):
        found = replay_stats.analyse_arms(arm_rows(), 1, 200)
        self.assertEqual(found["arm_names"], list(ARMS))
        self.assertEqual(found["arms"]["frugal"]["passes"], 14)
        self.assertEqual(found["arms"]["frugal"]["cost_of_pass"], round(0.6 * 15 / 14, 6))
        self.assertEqual([c["label"] for c in found["comparisons"]],
                         ["harness vs bare", "frugal vs bare", "frugal vs harness"])
        frugal = by_label(found["comparisons"])["frugal vs harness"]
        self.assertEqual((frugal["reference"], frugal["treatment"]), ("harness", "frugal"))
        self.assertEqual(frugal["ratio"], round((0.6 * 15 / 14) / 0.8, 4))
        self.assertEqual(frugal["difference"], round(14 / 15 - 1, 4))
        self.assertEqual(len(frugal["ratio_interval"]), 2)
        self.assertLessEqual(frugal["difference_interval"][0], frugal["difference"])

    def test_config_comparisons_are_secondary_and_only_sm2_is_marked_sm2(self):
        found = by_label(replay_stats.analyse_arms(arm_rows(), 1, 200)["comparisons"])
        self.assertEqual({k: (c["role"], c["sm2"]) for k, c in found.items()},
                         {"harness vs bare": ("primary", True), "frugal vs bare": ("secondary", False),
                          "frugal vs harness": ("secondary", False)})

    def test_a_named_comparison_is_primary_and_an_unknown_one_is_refused(self):
        found = by_label(replay_stats.analyse_arms(arm_rows(), 1, 200, ("frugal vs bare",))["comparisons"])
        self.assertEqual(found["frugal vs bare"]["role"], "primary")
        self.assertEqual(found["frugal vs harness"]["role"], "secondary")
        with self.assertRaises(ValueError) as caught:
            replay_stats.analyse_arms(arm_rows(), 1, 200, ("maintainer vs bare",))
        self.assertIn("maintainer vs bare", str(caught.exception))

    def test_a_run_without_harness_or_with_a_task_missing_in_one_arm_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            replay_stats.analyse_arms(arm_rows(arms=("bare", "frugal")), 1, 200)
        self.assertIn("no harness arm", str(caught.exception))
        rows = [r for r in arm_rows() if not (r["arm"] == "frugal" and r["task"] == "t2")]
        with self.assertRaises(ValueError) as caught:
            replay_stats.analyse_arms(rows, 1, 200)
        self.assertIn("frugal", str(caught.exception))


class SummariseEveryArmTests(unittest.TestCase):
    def test_two_strata_report_every_arm_and_every_comparison_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, arm_rows(MODELS[0]) + arm_rows(MODELS[1]))
            code, out = summarise(results, True)
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual(sorted(document["strata"]), list(MODELS))
        self.assertNotIn("pooled", document)
        for report in document["strata"].values():
            self.assertEqual(sorted(report["arms"]), sorted(ARMS))
            self.assertIn(report["verdict"], (replay_stats.SUPPORTED, replay_stats.INCONCLUSIVE,
                                              replay_stats.NOT_SUPPORTED))
            self.assertEqual(report["ratio"], 0.8)  # SM-2 stays harness over bare
            found = by_label(report["comparisons"])
            self.assertEqual(found["frugal vs bare"]["role"], "secondary")
            self.assertEqual(found["frugal vs bare"]["metrics"]["metrics"]["edits"]["difference"], -4)
            self.assertEqual(found["frugal vs harness"]["metrics"]["treatment"], "frugal")
            self.assertEqual(report["metrics"]["treatment"], "harness")
            self.assertEqual(sorted(report["reliability"]["pass_k"]["arms"]), sorted(ARMS))
            self.assertIsNone(report["pre_registration"])

    def test_the_text_report_labels_each_comparison_in_each_stratum(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, arm_rows(MODELS[0]) + arm_rows(MODELS[1]))
            code, out = summarise(results, False)
        self.assertEqual(code, 0)
        self.assertIn("SM-2 result over 3 task(s)", out)
        for model in MODELS:
            section = out.split("== stratum %s ==" % model)[1].split("== stratum")[0]
            self.assertIn("  frugal: 14/15 passed", section)
            self.assertIn("  harness vs bare (primary, SM-2): Cost-of-Pass ratio 0.800", section)
            self.assertIn("  frugal vs bare (secondary): Cost-of-Pass ratio", section)
            self.assertIn("  frugal vs harness (secondary): Cost-of-Pass ratio", section)
            self.assertIn("Oracle metrics, frugal vs harness (secondary):", section)

    def test_a_two_arm_set_reports_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, arm_rows(arms=("bare", "harness")))
            _code, out = summarise(results, True)
        self.assertNotIn("comparisons", json.loads(out))

    def test_plot_is_refused_for_a_run_with_config_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / BENCH.RESULTS
            write_rows(results, arm_rows())
            with self.assertRaises(SystemExit):
                summarise(results, False, plot=str(Path(tmp) / "p.svg"))

    def test_a_pre_registration_can_name_a_config_comparison_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.summarise_named_primary(Path(tmp), {})
        found = by_label(report["comparisons"])
        self.assertEqual(found["frugal vs bare"]["role"], "primary")
        self.assertEqual(found["frugal vs harness"]["role"], "secondary")
        self.assertEqual(report["primary_named"], ["frugal vs bare"])

    def test_an_inherited_git_dir_does_not_redirect_the_plan_lookup_or_the_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            stray = stray_repository(tmp)
            report = self.summarise_named_primary(Path(tmp), stray)
            self.assertEqual(git(stray["GIT_WORK_TREE"], "rev-list", "--all"), "")  # nothing landed there
        self.assertEqual(report["primary_named"], ["frugal vs bare"])

    def summarise_named_primary(self, tmp, inherited):
        """The JSON report of a run whose plan names `frugal vs bare` primary, with `inherited`
        variables in the environment while the plan is committed and the run summarised."""
        repo = tmp / "repo"
        repo.mkdir()
        today = datetime.date.today().isoformat()
        text = re.sub(r"- \*\*Primary arm comparisons:\*\*.*\n(  .*\n)*",
                      "- **Primary arm comparisons:** `frugal vs bare`, because the claim is frugal's\n",
                      filled_plan(today))
        with mock.patch.dict("os.environ", inherited):
            plan, commit = committed_plan(repo, "%s-arms.md" % today, text)
            stamp = {"evidence": "pre-registered", "pre_registration": plan, "pre_registration_commit": commit}
            results = tmp / BENCH.RESULTS
            write_rows(results, arm_rows(stamp=stamp))
            with mock.patch.object(BENCH, "ROOT", repo):
                _code, out = summarise(results, True)
        return json.loads(out)

    def test_the_template_names_no_primary_comparison(self):
        template = (Path(__file__).resolve().parents[1] / "docs" / "pre-registration-template.md").read_text()
        self.assertIn("- **Primary arm comparisons:**", template)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            plan, commit = committed_plan(repo, "2026-01-01-arms.md", template)
            rows = [{"evidence": "pre-registered", "pre_registration": plan, "pre_registration_commit": commit}]
            self.assertEqual(BENCH.primary_comparisons(rows, repo)[1], ())

    def test_a_field_that_names_no_comparison_is_refused(self):
        for value in ("`Frugal vs bare`", "`frugal versus bare`"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                today = datetime.date.today().isoformat()
                text = re.sub(r"- \*\*Primary arm comparisons:\*\*.*\n(  .*\n)*",
                              "- **Primary arm comparisons:** %s, because the claim is frugal's\n" % value,
                              filled_plan(today))
                plan, commit = committed_plan(repo, "%s-arms.md" % today, text)
                rows = [{"evidence": "pre-registered", "pre_registration": plan, "pre_registration_commit": commit}]
                with self.assertRaises(SystemExit) as caught:
                    BENCH.primary_comparisons(rows, repo)
                self.assertIn(value, str(caught.exception))

    def test_exploratory_rows_name_no_primary_comparison_and_read_no_plan(self):
        rows = [{"evidence": "exploratory", "pre_registration": None, "pre_registration_commit": None}]
        self.assertEqual(BENCH.primary_comparisons(rows, "/nonexistent"), (None, ()))


class TaskIdTests(unittest.TestCase):
    """A task id becomes a file name under `--raw` (`diff_name`), so a custom manifest cannot reach
    outside that folder through it."""

    def test_an_id_with_a_path_part_is_refused(self):
        for bad in ("../x", "a/b", "..", ".hidden", "-x", "a\\b", "", 7):
            with self.subTest(id=bad), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "tasks.json"
                path.write_text(json.dumps({"tasks": [dict(TASK, id=bad)]}), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    BENCH.load_tasks(path)

    def test_every_shipped_task_id_is_a_plain_name(self):
        root = Path(__file__).resolve().parents[1]
        ids = []
        for manifest in ("benchmarks/tasks.json", "benchmarks/micro/tasks.json",
                         "tests/fixtures/ablation-tasks.json"):
            document = json.loads((root / manifest).read_text(encoding="utf-8"))
            ids += [t["id"] for t in document.get("tasks", [])]
            ids += [e["task"]["id"] for e in document.get("retired", [])]
        self.assertGreater(len(ids), 10)
        self.assertEqual([i for i in ids if not BENCH.TASK_ID.match(i)], [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [dict(TASK, id="link-alias.v2_1")]}), encoding="utf-8")
            self.assertEqual(BENCH.load_tasks(path)[0]["id"], "link-alias.v2_1")


class AgentEdits(Launch):
    """A fake scored run that edits the mounted tree, plants a filter, an fsmonitor and a diff
    driver in its `.git/config` that would each touch `marker` if the host ran them, and replies."""
    def __init__(self, outputs, marker):
        super().__init__(outputs)
        self.marker = marker

    def __call__(self, command, **kwargs):
        if command[:2] == ["docker", "run"] and BENCH.arms.WORKDIR_PROBE not in command:
            mount = next(part for part in command if part.endswith(":" + BENCH.arms.WORKDIR))
            tree = Path(mount[:-len(":" + BENCH.arms.WORKDIR)])
            (tree / "file.txt").write_text("one\ntwo\n", encoding="utf-8")
            (tree / "added.txt").write_text("new\n", encoding="utf-8")
            (tree / ".gitattributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
            with (tree / ".git" / "config").open("a", encoding="utf-8") as config:
                config.write("[core]\n\tfsmonitor = touch %s\n[filter \"evil\"]\n\tclean = touch %s\n"
                             "[diff \"evil\"]\n\tcommand = touch %s\n" % ((self.marker,) * 3))
        return super().__call__(command, **kwargs)


class DiffSavingTests(unittest.TestCase):
    def run_one(self, tmp, raw=True):
        marker = Path(tmp) / "host-ran-agent-config"
        stream = json.dumps([dict(result(), result="done")])
        opts = options(tmp, reps=1)
        if raw:
            opts["raw"] = str(Path(tmp) / "raw")
        rows, _ = BENCH.replay([TASK], opts, AgentEdits([stream, stream], str(marker)))
        return rows, marker

    def test_each_run_saves_its_final_diff_beside_its_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, marker = self.run_one(tmp)
            self.assertEqual(sorted(r["arm"] for r in rows), ["bare", "harness"])
            for row in rows:
                path = Path(row["diff_path"])
                self.assertEqual(path, Path(tmp) / "raw" / ("demo-%s-1.diff" % row["arm"]))
                self.assertIsNone(row["diff_error"])
                data = path.read_bytes()
                self.assertEqual(row["diff_bytes"], len(data))
                text = data.decode("utf-8")
                self.assertIn("+two", text)
                self.assertIn("+++ b/added.txt", text)
                self.assertIn("+new", text)
            self.assertFalse(marker.exists(), "the host ran a command the agent wrote into .git/config")

    def test_the_judge_reads_the_saved_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = self.run_one(tmp)
            row = rows[0]
            stream = Path(row["diff_path"]).with_suffix(".json")
            with mock.patch.object(replay_judge, "find_stream", lambda *a: stream):
                found = replay_judge.read_run(Path(tmp) / BENCH.RESULTS, row)
        self.assertEqual(found["diff_source"], "diff")
        self.assertIn("+two", found["diff"])

    def test_without_raw_no_diff_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = self.run_one(tmp, raw=False)
        for row in rows:
            self.assertEqual((row["diff_path"], row["diff_bytes"], row["diff_error"]), (None, None, None))

    def test_a_failed_diff_is_recorded_not_passed_off_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "tree"
            workdir.mkdir()
            got = BENCH.save_diff({"raw": str(Path(tmp) / "raw"), "tmp": tmp}, "demo", "bare", 1, workdir, "f" * 40)
        self.assertIsNone(got["diff_path"])
        self.assertTrue(got["diff_error"].startswith("git read-tree"))


if __name__ == "__main__":
    unittest.main()
