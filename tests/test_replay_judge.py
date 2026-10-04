"""The blind pairwise diff judge (#1185), with a fake model throughout; no test here launches a
container or calls a model."""
import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_judge", REPO / "scripts" / "replay_judge.py")
J = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(J)

CONFIG = J.load_config()
DIMS = CONFIG["dimensions"]
REF, TREAT = "arm-zeta", "arm-omega"


def answer(pref):
    return json.dumps(dict((d, {"preference": pref[d] if isinstance(pref, dict) else pref, "reason": "r"})
                           for d in DIMS))


def sections(prompt):
    """The two responses as shown, from a judge prompt."""
    one = prompt.split("## Response 1", 1)[1].split("## Response 2", 1)[0]
    two = prompt.split("## Response 2", 1)[1]
    return one, two


class Fake(object):
    """A model that prefers the response holding `GOOD`, and records every prompt."""

    def __init__(self, rule=None):
        self.prompts = []
        self.rule = rule or (lambda one, two: "1" if "GOOD" in one else "2" if "GOOD" in two else "tie")

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return "Here you go:\n" + answer(self.rule(*sections(prompt)))


def run(diff, reply):
    return {"diff": diff, "reply": reply, "diff_source": "diff"}


def rows_and_runs(tasks=3, reps=2, good=TREAT):
    rows, runs = [], {}
    for t in range(tasks):
        for rep in range(1, reps + 1):
            for arm in (REF, TREAT):
                rows.append({"task": "t%d" % t, "arm": arm, "rep": rep, "error": False})
                body = "GOOD change" if arm == good else "plain change"
                runs[("t%d" % t, arm, rep)] = run("+%s %d" % (body, rep), "Done.")
    return rows, runs


def pairs_for(rows, runs, n=None, seed=7):
    return J.build_pairs(rows, lambda row: runs[(row["task"], row["arm"], row["rep"])],
                         {"t0": "Fix it."}, (REF, TREAT), n, seed)


def verdict(pid, task, prefs, lengths=(10, 20), passes=None, consistent=True):
    """A synthetic read verdict with `prefs` per dimension in the pairs file's terms."""
    return {"id": pid, "task": task, "error": None, "lengths": {"first": lengths[0], "second": lengths[1]},
            "passes": passes or [], "dimensions": dict(
                (d, {"preference": prefs[d] if isinstance(prefs, dict) else prefs, "consistent": consistent,
                     "reasons": ["r", "r"]}) for d in DIMS)}


class Pin(unittest.TestCase):
    def test_the_pin_names_a_model_effort_and_the_rubric_digest(self):
        self.assertTrue(CONFIG["model"].startswith("claude-"))
        self.assertIn(CONFIG["effort"], J.arms.EFFORT_LEVELS)
        self.assertEqual(CONFIG["kappa_floor"], 0.6)
        self.assertEqual(CONFIG["calibration_pairs"], 40)
        for dim in DIMS:
            self.assertIn(dim + ":", CONFIG["rubric_text"])

    def test_a_rubric_that_differs_from_its_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("judge.json", "rubric.md"):
                shutil.copy(str(J.CONFIG.parent / name), tmp)
            (Path(tmp) / "rubric.md").write_text("Prefer the longer one.\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                J.load_config(Path(tmp) / "judge.json")
            self.assertIn("pinned", str(caught.exception))


class Blinding(unittest.TestCase):
    def test_no_arm_or_run_identity_reaches_the_pairs_file_or_the_judge(self):
        rows, runs = rows_and_runs()
        pairs, key = pairs_for(rows, runs)
        document = json.dumps(J.pairs_document(pairs, CONFIG, 7))
        fake = Fake()
        for pair in pairs:
            J.judge_pair(pair, fake, CONFIG, seed=3)
        for text in [document] + fake.prompts:
            self.assertNotIn(REF, text)
            self.assertNotIn(TREAT, text)
            self.assertNotIn('"rep"', text)
        self.assertEqual(set(key), set(p["id"] for p in pairs))
        for cell in key.values():
            self.assertEqual({cell["first"], cell["second"]}, {REF, TREAT})

    def test_the_file_order_is_random_and_fixed_by_the_seed(self):
        rows, runs = rows_and_runs(tasks=8, reps=3)
        first = pairs_for(rows, runs, seed=11)
        self.assertEqual(first, pairs_for(rows, runs, seed=11))
        self.assertEqual({c["first"] for c in first[1].values()}, {REF, TREAT})
        self.assertNotEqual([c["first"] for c in first[1].values()],
                            [c["first"] for c in pairs_for(rows, runs, seed=12)[1].values()])

    def test_n_pairs_are_spread_over_tasks_and_errored_or_unreadable_runs_are_skipped(self):
        rows, runs = rows_and_runs(tasks=4, reps=5)
        pairs, _ = pairs_for(rows, runs, n=8)
        self.assertEqual(len(pairs), 8)
        self.assertEqual(sorted(set(p["task"] for p in pairs)), ["t0", "t1", "t2", "t3"])
        rows[0]["error"] = True  # t0 rep 1, reference arm
        runs[("t0", TREAT, 2)] = None
        _, key = pairs_for(rows, runs)
        held = set((c["task"], c["rep"]) for c in key.values())
        self.assertNotIn(("t0", 1), held)
        self.assertNotIn(("t0", 2), held)
        self.assertEqual(len(held), 18)

    def test_a_long_side_is_clipped_with_its_remainder_counted(self):
        rows, runs = rows_and_runs(tasks=1, reps=1)
        runs[("t0", REF, 1)] = run("x" * 50, "y")
        pairs, _ = J.build_pairs(rows, lambda row: runs[(row["task"], row["arm"], row["rep"])], {},
                                 (REF, TREAT), None, 7, max_chars=10)
        diffs = [pairs[0]["first"]["diff"], pairs[0]["second"]["diff"]]
        self.assertTrue(any(d.endswith("[... 40 more characters not shown]") for d in diffs))


class OrderSwap(unittest.TestCase):
    def test_each_pair_is_asked_twice_with_the_order_swapped(self):
        rows, runs = rows_and_runs(tasks=1, reps=1)
        pairs, key = pairs_for(rows, runs)
        fake = Fake()
        out = J.judge_pair(pairs[0], fake, CONFIG, seed=5)
        self.assertEqual(len(fake.prompts), 2)
        self.assertEqual(out["passes"][0]["shown"], out["passes"][1]["shown"][::-1])
        one, two = sections(fake.prompts[0])
        self.assertEqual((one.strip(), two.strip()), tuple(s.strip() for s in sections(fake.prompts[1])[::-1]))
        good_side = "first" if key["p001"]["first"] == TREAT else "second"
        for dim in DIMS:
            self.assertEqual(out["dimensions"][dim]["preference"], good_side)
            self.assertTrue(out["dimensions"][dim]["consistent"])
        self.assertEqual(out["model"], CONFIG["model"])
        self.assertEqual(out["rubric_sha256"], CONFIG["rubric_sha256"])

    def test_the_shown_order_is_fixed_by_the_seed_and_varies_across_pairs(self):
        rows, runs = rows_and_runs(tasks=10, reps=1)
        pairs, _ = pairs_for(rows, runs)
        orders = [J.judge_pair(p, Fake(), CONFIG, seed=9)["order"] for p in pairs]
        self.assertEqual(orders, [J.judge_pair(p, Fake(), CONFIG, seed=9)["order"] for p in pairs])
        self.assertEqual(len(set(tuple(o) for o in orders)), 2)

    def test_answers_that_change_with_the_order_are_ties_and_their_rate_is_reported(self):
        rows, runs = rows_and_runs(tasks=2, reps=2)
        pairs, _ = pairs_for(rows, runs)
        verdicts = [J.judge_pair(p, Fake(lambda one, two: "1"), CONFIG) for p in pairs]
        for v in verdicts:
            for dim in DIMS:
                self.assertEqual(v["dimensions"][dim]["preference"], "tie")
                self.assertFalse(v["dimensions"][dim]["consistent"])
        rate = J.inconsistency(verdicts, DIMS)
        self.assertEqual(rate["dimensions"][DIMS[0]], {"pairs": 4, "inconsistent": 4, "rate": 1.0})
        self.assertEqual(rate["errors"], 0)

    def test_an_unreadable_answer_is_an_error_never_a_tie(self):
        rows, runs = rows_and_runs(tasks=1, reps=1)
        pairs, _ = pairs_for(rows, runs)
        out = J.judge_pair(pairs[0], lambda prompt: "I prefer the first.", CONFIG)
        self.assertIsNone(out["dimensions"])
        self.assertIn("ValueError", out["error"])
        bad = json.dumps({DIMS[0]: {"preference": "both"}})
        with self.assertRaises(ValueError):
            J.parse_verdict(bad, DIMS)


class Kappa(unittest.TestCase):
    def test_kappa_matches_the_hand_computed_value(self):
        judge = ["first", "first", "second", "second"]
        human = ["first", "second", "second", "second"]
        # observed 0.75; chance 0.5 * 0.25 + 0.5 * 0.75 = 0.5; kappa (0.75 - 0.5) / 0.5
        self.assertAlmostEqual(J.cohen_kappa(judge, human), 0.5)
        self.assertEqual(J.cohen_kappa(human, human), 1.0)
        self.assertAlmostEqual(J.cohen_kappa(["first", "second"], ["second", "first"]), -1.0)

    def test_kappa_is_undefined_with_no_items_or_total_chance_agreement(self):
        self.assertIsNone(J.cohen_kappa([], []))
        self.assertIsNone(J.cohen_kappa(["tie", "tie"], ["tie", "tie"]))
        with self.assertRaises(ValueError):
            J.cohen_kappa(["tie"], [])


def calibration_set(judge_prefs, human_prefs, lengths=(10, 20)):
    verdicts = [verdict("p%03d" % i, "t%d" % (i % 3), pref, lengths) for i, pref in enumerate(judge_prefs)]
    labels = dict(("p%03d" % i, dict((d, pref) for d in DIMS)) for i, pref in enumerate(human_prefs))
    return verdicts, labels


class Admission(unittest.TestCase):
    def test_a_dimension_is_admitted_only_at_or_above_the_floor(self):
        human = ["first", "second", "tie"] * 4
        verdicts, labels = calibration_set(human, human)
        result = J.calibrate(verdicts, labels, CONFIG)
        self.assertEqual(result["admitted"], list(DIMS))
        self.assertEqual(result["agreement"][DIMS[0]]["kappa"], 1.0)
        self.assertEqual(result["agreement"][DIMS[0]]["confusion"]["tie"]["tie"], 4)
        judge = ["first", "first", "second", "second"]
        verdicts, labels = calibration_set(judge, ["first", "second", "second", "second"])
        self.assertEqual(J.calibrate(verdicts, labels, CONFIG)["admitted"], [])  # kappa 0.5 < 0.6
        self.assertEqual(J.calibrate(verdicts, labels, CONFIG, kappa_floor=0.5)["admitted"], list(DIMS))

    def test_only_pairs_both_rated_count_and_an_undefined_kappa_is_not_admitted(self):
        verdicts, labels = calibration_set(["tie", "tie"], ["tie", "tie"])
        labels["p999"] = dict((d, "first") for d in DIMS)  # labelled, never judged
        verdicts.append(dict(verdict("p002", "t0", "first"), error="RuntimeError: x", dimensions=None))
        result = J.calibrate(verdicts, labels, CONFIG)
        self.assertEqual(result["agreement"][DIMS[0]]["pairs"], 2)
        self.assertIsNone(result["agreement"][DIMS[0]]["kappa"])
        self.assertEqual(result["admitted"], [])
        self.assertEqual(result["inconsistency"]["errors"], 1)
        text = "\n".join(J.render_calibration(result))
        self.assertIn("not admitted", text)
        self.assertIn("1 verdict(s) could not be read", text)

    def test_labels_read_from_a_filled_pairs_file_or_the_form_download(self):
        filled = {"pairs": [{"id": "p001", "labels": {DIMS[0]: "first", DIMS[1]: None}},
                            {"id": "p002", "labels": {DIMS[0]: "maybe"}}]}
        self.assertEqual(J.read_labels(filled, DIMS), {"p001": {DIMS[0]: "first"}})
        download = {"labels": {"p001": {DIMS[2]: "tie"}}}
        self.assertEqual(J.read_labels(download, DIMS), {"p001": {DIMS[2]: "tie"}})
        with self.assertRaises(ValueError):
            J.read_labels({}, DIMS)


class Bias(unittest.TestCase):
    def test_a_judge_that_always_picks_the_first_shown_is_flagged(self):
        passes = [{"shown": ["first", "second"], "answer": json.loads(answer("1"))},
                  {"shown": ["second", "first"], "answer": json.loads(answer("1"))}]
        verdicts = [verdict("p%03d" % i, "t0", "tie", passes=passes, consistent=False) for i in range(10)]
        bias = J.position_bias(verdicts, DIMS)[DIMS[0]]
        self.assertEqual((bias["decided"], bias["shown_first"], bias["rate"]), (20, 20, 1.0))
        self.assertTrue(bias["biased"])

    def test_a_balanced_judge_is_not_flagged(self):
        rows, runs = rows_and_runs(tasks=5, reps=2)
        pairs, _ = pairs_for(rows, runs)
        verdicts = [J.judge_pair(p, Fake(), CONFIG) for p in pairs]
        bias = J.position_bias(verdicts, DIMS)[DIMS[0]]
        self.assertEqual(bias["rate"], 0.5)
        self.assertFalse(bias["biased"])
        self.assertIsNone(J.position_bias([], DIMS)[DIMS[0]]["rate"])

    def test_length_bias_compares_the_judge_with_the_labeller(self):
        # the second side is always longer; the judge always picks it, the labeller never does
        verdicts, labels = calibration_set(["second"] * 6, ["first"] * 6, lengths=(10, 90))
        cell = J.length_bias(verdicts, labels, DIMS)[DIMS[0]]
        self.assertEqual((cell["judge_rate"], cell["human_rate"], cell["gap"]), (1.0, 0.0, 1.0))
        verdicts, labels = calibration_set(["tie"] * 3, ["tie"] * 3)
        self.assertIsNone(J.length_bias(verdicts, labels, DIMS)[DIMS[0]]["gap"])


class Section(unittest.TestCase):
    def setUp(self):
        self.key, self.verdicts = {}, []
        for i in range(12):
            pid = "p%03d" % i
            first, second = (TREAT, REF) if i % 2 else (REF, TREAT)
            self.key[pid] = {"task": "t%d" % (i % 4), "rep": 1, "first": first, "second": second}
            winner = "first" if first == TREAT else "second"
            self.verdicts.append(verdict(pid, "t%d" % (i % 4), winner if i < 9 else "tie"))
        self.calibration = {"model": CONFIG["model"], "rubric_sha256": "x", "admitted": [DIMS[0]],
                            "agreement": dict((d, {}) for d in DIMS)}

    def test_win_rate_counts_a_tie_as_half_and_reports_only_admitted_dimensions(self):
        section, text = J.judge_section(self.verdicts, self.key, self.calibration, resamples=200)
        self.assertEqual(len(section["pairs"]), 1)
        pair = section["pairs"][0]
        self.assertEqual((pair["reference"], pair["treatment"]), (REF, TREAT) if REF < TREAT else (TREAT, REF))
        cell = pair["dimensions"][DIMS[0]]
        expected = 10.5 / 12 if pair["treatment"] == TREAT else 1.5 / 12
        self.assertAlmostEqual(cell["win_rate"], expected)
        self.assertEqual((cell["ties"], cell["tasks"], cell["pairs"]), (3, 4, 12))
        self.assertEqual(set(pair["dimensions"]), {DIMS[0]})
        self.assertEqual(section["not_admitted"], sorted(DIMS[1:]))
        self.assertIn("not admitted, so not reported", text)

    def test_the_interval_is_task_clustered_seeded_and_brackets_the_rate(self):
        first, _ = J.judge_section(self.verdicts, self.key, self.calibration, seed=4, resamples=300)
        again, _ = J.judge_section(self.verdicts, self.key, self.calibration, seed=4, resamples=300)
        cell = first["pairs"][0]["dimensions"][DIMS[0]]
        self.assertEqual(cell["interval"], again["pairs"][0]["dimensions"][DIMS[0]]["interval"])
        low, high = cell["interval"]
        self.assertLessEqual(low, cell["win_rate"])
        self.assertGreaterEqual(high, cell["win_rate"])
        # one task: every resample is that task, so the interval collapses to its rate
        only = dict((pid, dict(c, task="t0")) for pid, c in self.key.items())
        single, _ = J.judge_section(self.verdicts, only, self.calibration, resamples=50)
        cell = single["pairs"][0]["dimensions"][DIMS[0]]
        self.assertEqual(cell["interval"], [cell["win_rate"], cell["win_rate"]])

    def test_known_arms_sort_reference_first_and_no_admission_says_so(self):
        key = dict((pid, dict(c, first="harness" if c["first"] == TREAT else "bare",
                              second="harness" if c["second"] == TREAT else "bare"))
                   for pid, c in self.key.items())
        section, _ = J.judge_section(self.verdicts, key, self.calibration, resamples=20)
        self.assertEqual((section["pairs"][0]["reference"], section["pairs"][0]["treatment"]), ("bare", "harness"))
        _, text = J.judge_section(self.verdicts, key, dict(self.calibration, admitted=[]), resamples=20)
        self.assertIn("no dimension is admitted", text)
        json.dumps(section)


def stream(reply, edits=()):
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for name, spec in edits:
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": spec}]}}))
    lines.append(json.dumps({"type": "result", "result": reply, "is_error": False}))
    return "\n".join(lines) + "\n"


class ReadingRuns(unittest.TestCase):
    def test_the_reply_and_edits_come_from_the_stream_and_a_saved_diff_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            results.write_text("", encoding="utf-8")
            raw = Path(tmp) / "raw"
            raw.mkdir()
            edits = [("Edit", {"file_path": "/work/a.py", "old_string": "x = 1", "new_string": "x = 2"}),
                     ("Write", {"file_path": "/work/b.py", "content": "y = 3"}),
                     ("MultiEdit", {"file_path": "c.py", "edits": [{"old_string": "p", "new_string": "q"}]}),
                     ("Bash", {"command": "rm -rf d"})]
            (raw / "t1-bare-1.json").write_text(stream(" Done. ", edits), encoding="utf-8")
            got = J.read_run(results, {"task": "t1", "arm": "bare", "rep": 1})
            self.assertEqual(got["reply"], "Done.")
            self.assertEqual(got["diff_source"], "stream-edits")
            self.assertEqual(got["diff"].splitlines(), ["--- edit a.py", "-x = 1", "+x = 2", "--- write b.py",
                                                        "+y = 3", "--- multiedit c.py", "-p", "+q"])
            (raw / "t1-bare-1.diff").write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
            got = J.read_run(results, {"task": "t1", "arm": "bare", "rep": 1})
            self.assertEqual((got["diff_source"], got["diff"]), ("diff", "diff --git a/a.py b/a.py\n"))
            self.assertIsNone(J.read_run(results, {"task": "t2", "arm": "bare", "rep": 1}))
            (raw / "t3-bare-1.json").write_text(json.dumps({"type": "system"}) + "\n", encoding="utf-8")
            self.assertIsNone(J.read_run(results, {"task": "t3", "arm": "bare", "rep": 1}))


class Container(unittest.TestCase):
    def test_the_judge_runs_in_a_fresh_container_with_nothing_mounted(self):
        calls = []

        def launch(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, json.dumps(
                {"type": "result", "result": answer("tie"), "is_error": False}), "")

        ask = J.container_ask("model-citizen-arm-bare:x", CONFIG, "net-1", "http://proxy:3128", launch,
                              client={"PATH": "/usr/bin"})
        self.assertEqual(json.loads(ask("PROMPT")), json.loads(answer("tie")))
        command, kwargs = calls[0]
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("-i", command)
        self.assertNotIn("-v", command)
        self.assertEqual(command[command.index("--network") + 1], "net-1")
        self.assertIn(J.arms.CREDENTIAL, command)
        self.assertFalse(any(part.startswith(J.arms.CREDENTIAL + "=") for part in command))
        self.assertIn("HTTPS_PROXY=http://proxy:3128", command)
        tail = command[command.index("model-citizen-arm-bare:x") + 1:]
        self.assertEqual(tail, J.judge_command(CONFIG))
        self.assertEqual(tail[tail.index("--model") + 1], CONFIG["model"])
        self.assertEqual(tail[tail.index("--max-turns") + 1], "1")
        self.assertEqual(kwargs["input"], "PROMPT")
        self.assertNotIn("PROMPT", command)

    def test_a_failed_run_raises_and_the_pair_records_an_error(self):
        def launch(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, json.dumps(
                {"type": "result", "result": "", "is_error": True}), "")

        ask = J.container_ask("img", CONFIG, "none", None, launch, client={})
        with self.assertRaises(RuntimeError):
            ask("x")
        rows, runs = rows_and_runs(tasks=1, reps=1)
        pairs, _ = pairs_for(rows, runs)
        self.assertIn("RuntimeError", J.judge_pair(pairs[0], ask, CONFIG)["error"])


class CommandLine(unittest.TestCase):
    def test_export_writes_blind_pairs_a_key_and_a_form_that_cannot_run_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rows = []
            for t in range(3):
                for arm in ("bare", "harness"):
                    rows.append({"task": "t%d" % t, "arm": arm, "rep": 1, "error": False})
                    (tmp / ("t%d-%s-1.json" % (t, arm))).write_text(
                        stream("</script><b>%s</b>" % t, [("Write", {"file_path": "/work/f", "content": "z"})]),
                        encoding="utf-8")
            (tmp / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            (tmp / "tasks.json").write_text(json.dumps({"tasks": [{"id": "t0", "prompt": ["Do", "it."]}]}),
                                            encoding="utf-8")
            out = tmp / "label"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(J.main(["export", "--results", str(tmp / "results.jsonl"), "--tasks",
                                         str(tmp / "tasks.json"), "--out", str(out)]), 0)
            document = json.loads((out / "pairs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(document["pairs"]), 3)
            self.assertNotIn("harness", json.dumps(document["pairs"]))
            self.assertEqual([p["prompt"] for p in document["pairs"] if p["task"] == "t0"], ["Do\nit."])
            key = json.loads((out / "pairs.key.json").read_text(encoding="utf-8"))
            self.assertEqual(key["arms"], ["bare", "harness"])
            form = (out / "label.html").read_text(encoding="utf-8")
            data = re.search(r'<script id="data" type="application/json">(.*?)</script>', form, re.S).group(1)
            self.assertNotIn("</", data)
            self.assertEqual(json.loads(data)["pairs"], document["pairs"])
            self.assertNotIn("innerHTML", form)

    def test_calibrate_and_report_read_what_export_and_run_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            human = ["first", "second", "tie"] * 3
            verdicts, labels = calibration_set(human, human)
            (tmp / "v.jsonl").write_text("".join(json.dumps(v) + "\n" for v in verdicts), encoding="utf-8")
            (tmp / "labels.json").write_text(json.dumps({"labels": labels}), encoding="utf-8")
            key = dict(("p%03d" % i, {"task": "t%d" % (i % 3), "rep": 1, "first": "bare", "second": "harness"})
                       for i in range(9))
            (tmp / "key.json").write_text(json.dumps({"pairs": key}), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                J.main(["calibrate", "--verdicts", str(tmp / "v.jsonl"), "--labels", str(tmp / "labels.json"),
                        "--out", str(tmp / "cal.json")])
                J.main(["report", "--verdicts", str(tmp / "v.jsonl"), "--key", str(tmp / "key.json"),
                        "--calibration", str(tmp / "cal.json"), "--json"])
            self.assertIn("kappa 1.000", buf.getvalue())
            section = json.loads(buf.getvalue()[buf.getvalue().index("\n{") + 1:])
            self.assertEqual(section["admitted"], list(DIMS))
            self.assertAlmostEqual(section["pairs"][0]["dimensions"][DIMS[0]]["win_rate"], 0.5)

    def test_run_dry_run_calls_no_model_and_a_real_run_needs_a_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            pairs = Path(tmp) / "pairs.json"
            pairs.write_text(json.dumps({"pairs": [{"id": "p001"}]}), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                J.main(["run", "--pairs", str(pairs), "--image", "img", "--dry-run"])
            self.assertIn("2 time(s)", buf.getvalue())
            self.assertIn(CONFIG["model"], buf.getvalue())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                J.main(["run", "--pairs", str(pairs), "--image", "img", "--out", str(Path(tmp) / "v")])


if __name__ == "__main__":
    unittest.main()
