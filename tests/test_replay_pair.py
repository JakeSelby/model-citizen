"""The one-policy replay pair (#754): a reference and a treatment arm of one harness image that
differ in one session-scoped selection, run beside the bare arm. Parity refuses any other
difference, each arm reports passes, Cost-of-Pass, wall time and re-spawns at a stronger class,
and each harness arm's decision-provider calls are copied out of its container, joined by session
id and priced, an unpriced call named and never zero. Every launch is a fake; no test builds an
image or calls a model."""
import io
import json
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH, PRICES, TASK, Launch, arm_record, load, options
from test_harness import REPO

PAIR = load("replay_pair")
STATS = load("replay_stats")
PRICING = PAIR.load_pricing(REPO)
FACTOR = "HARNESS_STANCE_DELEGATION"
MANIFEST = {"schema": 1, "name": "delegation-off", "tag": "v9.9.9", "factor": FACTOR,
            "reference": None, "treatment": "off"}
TIERS = {"frontier": "fable", "strong": "opus", "standard": "sonnet", "light": "haiku"}


def stream(*messages):
    return "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages)


def spawn(tool_id, prompt, session="s1", model="claude-sonnet-5"):
    return {"type": "assistant", "session_id": session, "parent_tool_use_id": None,
            "message": {"model": model, "content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {"prompt": prompt}}]}}


def thread(tool_id, model, session="s1"):
    return {"type": "assistant", "session_id": session, "parent_tool_use_id": tool_id,
            "message": {"model": model, "content": [{"type": "text", "text": "done"}]}}


def finished(session="s1", cost=0.5):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
            "total_cost_usd": cost, "session_id": session,
            "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30,
                      "cache_read_input_tokens": 40}}


def run_output(session, cost=0.5):
    return stream({"type": "system", "subtype": "init", "session_id": session, "skills": ["a"]},
                  finished(session, cost))


def decision(session, model="claude-test", input_tokens=1000, output_tokens=100, ms=1500.0, partial=False,
             point="routing"):
    row = {"kind": "decision", "session_id": session, "point": point, "model": model, "ms": ms,
           "input": input_tokens, "output": output_tokens, "cache_read": 0, "cache_write": 0}
    if partial:
        row.update(partial=True, input=None, output=None, cache_read=None, cache_write=None)
    return row


def ledger_text(*rows):
    session = {"kind": "session", "session_id": "s-other", "model": "claude-test", "input": 5}
    return "".join(json.dumps(r) + "\n" for r in (session,) + rows)


class PairLaunch(Launch):
    """`Launch`, plus `docker cp` writing a recorded ledger to its destination and `docker kill`."""
    def __init__(self, outputs, ledgers=(), cp_exit=0):
        super().__init__(outputs)
        self.ledgers, self.cp_exit = list(ledgers), cp_exit

    def __call__(self, command, **kwargs):
        if command[:2] == ["docker", "cp"]:
            self.calls.append((command, kwargs))
            if self.cp_exit:
                return types.SimpleNamespace(stdout="", stderr="Error: could not find the file", returncode=self.cp_exit)
            Path(command[3]).write_text(self.ledgers.pop(0) if self.ledgers else "", encoding="utf-8")
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ["docker", "kill"]:
            self.calls.append((command, kwargs))
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        return super().__call__(command, **kwargs)


def pair_options(tmp, manifest=None, **over):
    manifest = dict(manifest or MANIFEST, sha256="0" * 64)
    harness = arm_record("harness")
    opts = options(tmp, reps=1, arm_names=PAIR.ARMS, pair=manifest, selections=PAIR.selections(manifest),
                   arms={"bare": arm_record("bare"), "reference": harness, "treatment": harness},
                   decisions=Path(tmp) / "decisions")
    opts.update(over)
    return opts


def verbs(launch):
    """Each Docker call as `(verb, arm)`: run, cp, kill or rm, and the arm its container is named for."""
    out = []
    for command, _ in launch.calls:
        text = " ".join(command)
        arm = next((a for a in PAIR.ARMS if "-%s-" % a in text), None)
        out.append((command[1], arm))
    return out


class ReSpawnTests(unittest.TestCase):
    """AC2 and AC4: a brief spawned again on a stronger model class is counted, from the stream."""

    def parse(self, *messages):
        return BENCH.parse_result(stream(*messages + (finished(),)))

    def test_the_same_brief_on_haiku_then_sonnet_is_one_respawn_up_and_the_session_is_read(self):
        parsed = self.parse(spawn("t1", "Fix the failing test"), thread("t1", "claude-haiku-4-5"),
                            spawn("t2", "fix the  failing test"), thread("t2", "claude-sonnet-5"))
        self.assertEqual((parsed["respawns_up"], parsed["spawns_unranked"]), (1, 0))
        self.assertEqual(parsed["session_ids"], ["s1"])

    def test_the_same_brief_on_the_same_model_is_no_respawn_up(self):
        parsed = self.parse(spawn("t1", "Fix it"), thread("t1", "claude-sonnet-5"),
                            spawn("t2", "Fix it"), thread("t2", "claude-sonnet-5"))
        self.assertEqual(parsed["respawns_up"], 0)

    def test_a_weaker_class_after_a_stronger_one_is_no_respawn_up(self):
        parsed = self.parse(spawn("t1", "Fix it"), thread("t1", "claude-opus-5"),
                            spawn("t2", "Fix it"), thread("t2", "claude-haiku-4-5"))
        self.assertEqual(parsed["respawns_up"], 0)

    def test_different_briefs_are_no_respawn(self):
        parsed = self.parse(spawn("t1", "Write the parser for the ledger file"), thread("t1", "claude-haiku-4-5"),
                            spawn("t2", "Summarise the release notes for users"), thread("t2", "claude-opus-5"))
        self.assertEqual(parsed["respawns_up"], 0)

    def test_an_unknown_model_or_a_silent_thread_is_unranked_never_ranked(self):
        parsed = self.parse(spawn("t1", "Fix it"), thread("t1", "some-other-model"),
                            spawn("t2", "Fix it"), spawn("t3", "Fix it"), thread("t3", "claude-opus-5"))
        self.assertEqual((parsed["respawns_up"], parsed["spawns_unranked"]), (0, 2))

    def test_single_document_output_leaves_both_counts_unknown(self):
        parsed = BENCH.parse_result(json.dumps([spawn("t1", "Fix it"), thread("t1", "claude-haiku-4-5"), finished()]))
        self.assertIsNone(parsed["respawns_up"])
        self.assertIsNone(parsed["spawns_unranked"])

    def test_the_class_table_is_the_bindings(self):
        self.assertEqual(set(PAIR.load_tiers(REPO)), set(PAIR.CLASS_ORDER))
        self.assertEqual(PAIR.model_class("claude-haiku-4-5", TIERS), "light")
        self.assertIsNone(PAIR.model_class("gpt-test", TIERS))


class ManifestTests(unittest.TestCase):
    def write(self, tmp, **over):
        path = Path(tmp) / "pair.json"
        path.write_text(json.dumps(dict(MANIFEST, **over)), encoding="utf-8")
        return path

    def test_a_valid_manifest_loads_with_its_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = PAIR.load(self.write(tmp))
        self.assertEqual(loaded["factor"], FACTOR)
        self.assertEqual(len(loaded["sha256"]), 64)
        self.assertEqual(PAIR.selections(loaded), {"bare": {}, "reference": {FACTOR: None},
                                                   "treatment": {FACTOR: "off"}})

    def test_each_malformed_manifest_is_refused_by_name(self):
        cases = {"factor": ({"factor": "CLAUDE_CODE_EFFORT_LEVEL"}, "is not HARNESS_STANCE_"),
                 "equal": ({"reference": "off"}, "reference and treatment are equal"),
                 "tag": ({"tag": "v1 v2"}, "tag must be one ref"),
                 "extra": ({"image": "x"}, "unknown key(s) image"),
                 "schema": ({"schema": 2}, "schema must be 1")}
        for label, (over, message) in cases.items():
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit, msg=label) as caught:
                PAIR.load(self.write(tmp, **over))
            self.assertIn(message, str(caught.exception), msg=label)

    def test_harness_mode_is_an_allowed_factor(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(PAIR.load(self.write(tmp, factor="HARNESS_MODE"))["factor"], "HARNESS_MODE")


class ParityTests(unittest.TestCase):
    """AC1: the pair runs only when the factor is the one difference."""

    def specs(self, tmp, **over):
        opts = pair_options(tmp, **over)
        return BENCH.arm_spec("reference", opts), BENCH.arm_spec("treatment", opts)

    def test_a_pair_differing_only_in_the_factor_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference, treatment = self.specs(tmp)
        self.assertEqual(PAIR.parity(reference, treatment, FACTOR), [])
        self.assertNotIn(FACTOR, reference["env"])
        self.assertEqual(treatment["env"][FACTOR], "off")
        self.assertEqual(reference["argv"], treatment["argv"])

    def test_two_differing_keys_are_refused_with_the_second_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference, treatment = self.specs(tmp, selections={
                "bare": {}, "reference": {FACTOR: None}, "treatment": {FACTOR: "off", "HARNESS_STANCE_COST": "frugal"}})
        self.assertEqual(PAIR.parity(reference, treatment, FACTOR),
                         ["env HARNESS_STANCE_COST: reference None, treatment 'frugal'"])

    def test_another_image_id_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = dict(arm_record("harness"), image_id="sha256:" + "9" * 64)
            opts = pair_options(tmp)
            opts["arms"]["treatment"] = other
            reasons = PAIR.parity(BENCH.arm_spec("reference", opts), BENCH.arm_spec("treatment", opts), FACTOR)
        self.assertEqual(len(reasons), 1)
        self.assertTrue(reasons[0].startswith("image_id: "), reasons)

    def test_an_equal_factor_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference, treatment = self.specs(tmp, selections={
                "bare": {}, "reference": {FACTOR: "off"}, "treatment": {FACTOR: "off"}})
        self.assertEqual(PAIR.parity(reference, treatment, FACTOR),
                         ["env %s: both arms 'off'; the factor must differ" % FACTOR])

    def test_a_refused_pair_launches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([])
            opts = pair_options(tmp)
            opts["arms"]["treatment"] = dict(arm_record("harness"), image_id="sha256:" + "9" * 64)
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
        self.assertIn("image_id", str(caught.exception))
        self.assertEqual((launch.calls, launch.probes), ([], []))

    def test_a_factor_the_resolver_cannot_see_is_refused_before_any_launch(self):
        """`tiered` is the delegation default, so null against it selects nothing."""
        manifest = dict(MANIFEST, treatment="tiered")
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([])
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], pair_options(tmp, manifest=manifest), launch)
        self.assertIn("selects nothing the resolver sees", str(caught.exception))
        self.assertEqual((launch.calls, launch.probes), ([], []))

    def test_an_unresolvable_profile_is_refused_as_unknown(self):
        self.assertIn("cannot be resolved", PAIR.effective_difference(None, "abc"))
        self.assertIsNone(PAIR.effective_difference("abc", "def"))


class ScheduleTests(unittest.TestCase):
    def test_three_arms_rotate_the_lead_across_all_three(self):
        order = [(rep, arm) for _, rep, arm in BENCH.schedule([TASK], 3, PAIR.ARMS)]
        self.assertEqual(order, [(1, "bare"), (1, "reference"), (1, "treatment"),
                                 (2, "reference"), (2, "treatment"), (2, "bare"),
                                 (3, "treatment"), (3, "bare"), (3, "reference")])

    def write(self, tmp):
        tasks = Path(tmp) / "tasks.json"
        tasks.write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
        head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], universal_newlines=True).strip()
        pair = Path(tmp) / "pair.json"
        pair.write_text(json.dumps(dict(MANIFEST, tag=head)), encoding="utf-8")
        return tasks, pair, head

    def test_a_dry_run_prints_tasks_times_reps_times_three_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, pair, _ = self.write(tmp)
            out = io.StringIO()
            with redirect_stdout(out):
                code = BENCH.main(["replay", "--tasks", str(tasks), "--pair", str(pair), "--model", "m",
                                   "--reps", "2", "--exploratory", "--dry-run"])
        self.assertEqual(code, 0)
        lines = [line.split() for line in out.getvalue().splitlines() if line.startswith("    demo rep ")]
        self.assertEqual([(l[2], l[3]) for l in lines], [("1", "bare"), ("1", "reference"), ("1", "treatment"),
                                                         ("2", "reference"), ("2", "treatment"), ("2", "bare")])

    def test_the_flags_a_pair_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, pair, head = self.write(tmp)
            base = ["replay", "--tasks", str(tasks), "--pair", str(pair), "--model", "m", "--exploratory"]
            for extra, message in ((["--stance-cost", "frugal", "--dry-run"], "--stance-cost is refused"),
                                   (["--tag", "v0.1.0", "--dry-run"], "runs the one tag"),
                                   (["--tag", head, "--tag", head, "--dry-run"], "runs the one tag"),
                                   ([], "a pair needs --spend-cap")):
                with self.assertRaises(SystemExit, msg=extra) as caught, redirect_stdout(io.StringIO()):
                    BENCH.main(base + extra)
                self.assertIn(message, str(caught.exception), msg=extra)


class LedgerCopyTests(unittest.TestCase):
    """Decision 3: a harness arm's container is kept, its ledger copied out, then removed."""

    def test_run_copy_remove_for_each_harness_arm_and_no_copy_for_bare(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s-bare"), run_output("s-ref"), run_output("s-treat")],
                                [ledger_text(), ledger_text(decision("s-treat"))])
            opts = pair_options(tmp)
            rows, stopped = BENCH.replay([TASK], opts, launch)
            self.assertFalse(stopped)
            self.assertEqual(verbs(launch), [("run", "bare"), ("run", "reference"), ("cp", "reference"),
                                             ("rm", "reference"), ("run", "treatment"), ("cp", "treatment"),
                                             ("rm", "treatment")])
            runs = [c for c, _ in launch.calls if c[1] == "run"]
            self.assertEqual(runs[0][:3], ["docker", "run", "--rm"])
            for command in runs[1:]:
                self.assertNotIn("--rm", command)
                self.assertEqual(len([p for p in command if p == "-v"]), 1)  # still one mount
            copy = [c for c, _ in launch.calls if c[1] == "cp"][0]
            home = arm_record("harness")["manifest"]["roots"]["home"]  # the image user's, never the host's
            self.assertEqual(copy[2].split(":", 1)[1], home + "/" + PAIR.LEDGER)
            self.assertEqual([r["decision_ledger"] for r in rows], ["absent", "read", "read"])
            self.assertEqual([r["selection"] for r in rows], [{}, {FACTOR: None}, {FACTOR: "off"}])
            self.assertEqual({r["ablation"]["name"] for r in rows}, {"delegation-off"})
            self.assertIn("%s=off" % FACTOR, runs[2])
            self.assertFalse(any(p.startswith(FACTOR) for p in runs[0] + runs[1]))
            kept = PAIR.decisions_file(opts["decisions"], "demo", "treatment", 1).read_text(encoding="utf-8")
            self.assertEqual([json.loads(line)["kind"] for line in kept.splitlines()], ["decision"])
            self.assertEqual(PAIR.decisions_file(opts["decisions"], "demo", "reference", 1).read_text(encoding="utf-8"), "")
            self.assertEqual([r["session_ids"] for r in rows], [["s-bare"], ["s-ref"], ["s-treat"]])
            self.assertEqual([r["respawns_up"] for r in rows], [0, 0, 0])

    def test_a_timeout_kills_copies_then_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s-bare"), subprocess.TimeoutExpired("docker", 1), run_output("s-treat")],
                                [ledger_text(), ledger_text()])
            rows, _ = BENCH.replay([TASK], pair_options(tmp), launch)
        self.assertEqual(verbs(launch)[1:5], [("run", "reference"), ("kill", "reference"), ("cp", "reference"),
                                              ("rm", "reference")])
        self.assertEqual((rows[1]["error_kind"], rows[1]["decision_ledger"]), ("timeout", "read"))

    def test_a_failed_copy_is_unknown_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s-bare"), run_output("s-ref"), run_output("s-treat")], cp_exit=1)
            rows, _ = BENCH.replay([TASK], pair_options(tmp), launch)
        self.assertEqual(rows[0]["decision_ledger"], "absent")
        for row in rows[1:]:
            self.assertTrue(row["decision_ledger"].startswith("unknown: docker cp exit 1"), row["decision_ledger"])
        self.assertEqual([v for v, _ in verbs(launch)].count("rm"), 2)  # removed whatever happened

    def test_the_two_arm_path_keeps_no_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s1"), run_output("s2")])
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), launch)
        self.assertEqual([c[:3] for c, _ in launch.calls], [["docker", "run", "--rm"]] * 2)
        self.assertNotIn("decision_ledger", rows[0])

    def test_the_selection_goes_to_harness_arms_by_value_only(self):
        self.assertNotIn(FACTOR, BENCH.arm_env("bare", selection={FACTOR: "off"}))
        self.assertEqual(BENCH.arm_env("reference", selection={FACTOR: "off"})[FACTOR], "off")
        self.assertNotIn(FACTOR, BENCH.arm_env("reference", selection={FACTOR: None}))


def attempt(arm, rep=1, task="demo", passed=True, cost=0.5, sessions=("s1",), ledger="read", wall=100.0,
            respawns=0, unranked=0, error=False, skills=1):
    return {"task": task, "arm": arm, "rep": rep, "passed": None if error else passed, "error": error,
            "outcome": "pass" if passed and not error else "fail", "cost_usd": cost, "task_long": False,
            "session_ids": list(sessions), "decision_ledger": "absent" if arm == "bare" else ledger,
            "wall_seconds": wall, "respawns_up": respawns, "spawns_unranked": unranked,
            "init_surface_source": "cli-init", "init_skills": skills, "observed_effort": None,
            "ablation": {"name": "delegation-off", "sha256": "0" * 64, "schema": 1, "factors": [FACTOR]},
            "selection": {} if arm == "bare" else {FACTOR: "off" if arm == "treatment" else None}}


class DecisionRollupTests(unittest.TestCase):
    """AC3: decision calls count in their arm; an unpriced one is named and never zero."""

    def rollup(self, decisions, rows=None):
        rows = rows or [attempt("reference"), attempt("treatment")]
        return PAIR.decision_rollup(rows, decisions, PRICES, PRICING)

    def test_two_priced_rows_add_their_exact_cost(self):
        out = self.rollup({("demo", "treatment", 1): [decision("s1"), decision("s1", input_tokens=2000, output_tokens=0)],
                           ("demo", "reference", 1): []})
        # 1000 x 2.0 + 100 x 10.0 = 3000 per million; 2000 x 2.0 = 4000 per million
        self.assertEqual(out["treatment"]["priced_usd"], 0.007)
        self.assertEqual((out["treatment"]["calls"], out["treatment"]["unpriced"]), (2, []))
        self.assertEqual((out["reference"]["calls"], out["reference"]["priced_usd"]), (0, 0.0))

    def test_a_partial_row_is_named_and_leaves_the_with_decisions_figure_undefined(self):
        decisions = {("demo", "treatment", 1): [decision("s1"), decision("s1", partial=True, point="review")],
                     ("demo", "reference", 1): []}
        out = self.rollup(decisions)
        self.assertEqual(out["treatment"]["unpriced"],
                         [{"task": "demo", "rep": 1, "point": "review", "model": "claude-test", "reason": "partial"}])
        rows = [attempt("bare"), attempt("reference"), attempt("treatment")]
        result = PAIR.summarise(rows, decisions, PRICES, resamples=50, pricing=PRICING)
        treatment = result["arms"]["treatment"]
        self.assertIsNone(treatment["cost_of_pass_with_decisions"])
        self.assertIn("1 unpriced decision call(s)", treatment["with_decisions_undefined"])
        self.assertEqual(treatment["cost_of_pass"], 0.5)  # the worker figure is still shown

    def test_a_model_missing_from_the_table_is_unpriced_with_its_reason(self):
        out = self.rollup({("demo", "treatment", 1): [decision("s1", model="unlisted-model")],
                           ("demo", "reference", 1): []})
        self.assertEqual(out["treatment"]["unpriced"][0]["reason"], "no price for unlisted-model")

    def test_a_foreign_session_is_counted_in_the_arm_and_named_unmatched(self):
        out = self.rollup({("demo", "treatment", 1): [decision("s-elsewhere")], ("demo", "reference", 1): []})
        self.assertEqual(out["treatment"]["calls"], 1)
        self.assertEqual(out["treatment"]["priced_usd"], 0.003)
        self.assertEqual(out["treatment"]["unmatched"][0]["session_id"], "s-elsewhere")

    def test_an_unread_ledger_is_unknown(self):
        rows = [attempt("reference", ledger="unknown: docker cp exit 1"), attempt("treatment")]
        out = self.rollup({("demo", "treatment", 1): []}, rows)
        self.assertEqual(out["reference"]["unknown"],
                         [{"task": "demo", "rep": 1, "reason": "unknown: docker cp exit 1"}])

    def test_a_malformed_saved_decision_line_reads_as_unknown_and_the_report_completes(self):
        rows = [attempt("bare"), attempt("reference"), attempt("treatment")]
        with tempfile.TemporaryDirectory() as tmp:
            PAIR.decisions_file(tmp, "demo", "reference", 1).write_text("", encoding="utf-8")
            PAIR.decisions_file(tmp, "demo", "treatment", 1).write_text(
                json.dumps(decision("s1")) + "\n{not json\n", encoding="utf-8")
            decisions = PAIR.read_decisions(tmp, rows)
        self.assertNotIn(("demo", "treatment", 1), decisions)
        result = PAIR.summarise(rows, decisions, PRICES, resamples=50, pricing=PRICING)
        treatment = result["arms"]["treatment"]
        self.assertEqual(treatment["decisions"]["unknown"],
                         [{"task": "demo", "rep": 1, "reason": "the saved decision rows are missing"}])
        self.assertIsNone(treatment["cost_of_pass_with_decisions"])

    def test_latency_is_reported_as_a_share_and_never_added_to_wall_time(self):
        rows = [attempt("bare"), attempt("reference"), attempt("treatment", wall=100.0)]
        decisions = {("demo", "treatment", 1): [decision("s1", ms=5000.0)], ("demo", "reference", 1): []}
        treatment = PAIR.summarise(rows, decisions, PRICES, resamples=50, pricing=PRICING)["arms"]["treatment"]
        self.assertEqual(treatment["wall_seconds"], 100.0)
        self.assertEqual(treatment["decisions"]["decision_seconds"], 5.0)
        self.assertEqual(treatment["decisions"]["share_of_wall"], 0.05)


def pair_rows(tasks=2, reps=5):
    rows = []
    for t in range(tasks):
        for rep in range(1, reps + 1):
            rows += [attempt("bare", rep, "t%d" % t, passed=rep % 2 == 0, cost=0.4),
                     attempt("reference", rep, "t%d" % t, cost=0.6, respawns=rep % 2, wall=120.0),
                     attempt("treatment", rep, "t%d" % t, passed=rep != 3, cost=0.5, wall=90.0)]
    return rows


class PairSummaryTests(unittest.TestCase):
    """AC2, decision 5 and post-run parity."""

    def summarise(self, rows, decisions=None):
        decisions = decisions if decisions is not None else {
            (r["task"], r["arm"], r["rep"]): [] for r in rows if r["arm"] != "bare"}
        return PAIR.summarise(rows, decisions, PRICES, resamples=200, pricing=PRICING)

    def test_each_arm_reports_passes_cost_per_pass_wall_time_and_respawns(self):
        result = self.summarise(pair_rows())
        reference, treatment, bare = (result["arms"][a] for a in ("reference", "treatment", "bare"))
        self.assertEqual((reference["passes"], reference["attempts"]), (10, 10))
        self.assertEqual(reference["cost_of_pass"], 0.6)
        self.assertEqual((treatment["passes"], treatment["cost_of_pass"]), (8, 0.625))
        self.assertEqual((bare["passes"], bare["cost_of_pass"]), (4, 1.0))
        self.assertEqual((reference["wall_seconds"], reference["wall_seconds_per_attempt"]), (1200.0, 120.0))
        self.assertEqual((reference["respawns_up"], reference["respawns_up_per_attempt"]), (6, 0.6))
        self.assertEqual(treatment["cost_of_pass_with_decisions"], 0.625)
        self.assertTrue(result["parity"]["ok"])
        text = PAIR.render(result)
        for arm in PAIR.ARMS:
            self.assertIn("  %s: " % arm, text)
        for phrase in ("    Cost-of-Pass: workers", "    wall time", "re-spawns at a stronger class", "    decision calls"):
            self.assertEqual(text.count(phrase), 3, phrase)

    def test_plot_is_refused_before_any_pair_analysis_runs(self):
        def analyse(*args, **kwargs):
            raise AssertionError("summarised before refusing --plot")
        original, BENCH.replay_pair.summarise = BENCH.replay_pair.summarise, analyse
        try:
            with self.assertRaises(SystemExit) as refused:
                BENCH.summarise_pair(pair_rows(1, 1), Path("rows.jsonl"), types.SimpleNamespace(plot=True))
        finally:
            BENCH.replay_pair.summarise = original
        self.assertIn("--plot draws a two-arm result", str(refused.exception))

    def test_an_unknown_respawn_count_is_unknown_not_zero(self):
        rows = pair_rows(1, 1)
        rows[1]["respawns_up"] = None
        self.assertIsNone(self.summarise(rows)["arms"]["reference"]["respawns_up"])

    def test_the_pair_carries_paired_intervals_and_no_verdict(self):
        result = self.summarise(pair_rows())
        self.assertEqual(sorted(result["comparisons"]), ["reference_over_bare", "treatment_over_bare",
                                                         "treatment_over_reference"])
        main = result["comparisons"]["treatment_over_reference"]["workers"]
        self.assertEqual(main["ratio"], round(0.625 / 0.6, 4))
        self.assertEqual(len(main["ratio_interval"]), 2)
        self.assertEqual(main["difference"], -0.2)
        self.assertTrue(main["sm2_eligible"])
        for comparison in result["comparisons"].values():
            for basis in comparison.values():
                self.assertNotIn("verdict", basis)
        self.assertNotIn("verdict", PAIR.render(result))

    def test_analyse_on_a_renamed_pair_equals_analyse_on_bare_and_harness(self):
        rows = [r for r in pair_rows() if r["arm"] in ("reference", "treatment")]
        renamed = [dict(r, arm="bare" if r["arm"] == "reference" else "harness") for r in rows]
        pair = STATS.analyse(rows, resamples=300, arms=("reference", "treatment"))
        base = STATS.analyse(renamed, resamples=300)
        for key in ("ratio", "ratio_interval", "difference", "difference_interval"):
            self.assertEqual(pair[key], base[key], key)
        self.assertEqual(pair["arms"]["treatment"], base["arms"]["harness"])

    def test_the_with_decisions_interval_uses_the_priced_calls(self):
        rows = pair_rows(2, 5)
        decisions = {(r["task"], r["arm"], r["rep"]): ([decision("s1")] if r["arm"] == "treatment" else [])
                     for r in rows if r["arm"] != "bare"}
        result = self.summarise(rows, decisions)
        self.assertEqual(result["arms"]["treatment"]["cost_of_pass_with_decisions"], round(10 * 0.503 / 8, 6))
        self.assertEqual(result["comparisons"]["treatment_over_reference"]["with_decisions"]["ratio"],
                         round((10 * 0.503 / 8) / 0.6, 4))

    def test_a_pair_whose_surfaces_differ_is_refused_with_the_trial_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = pair_rows(1, 5)
            for row in rows:
                if row["arm"] == "treatment" and row["rep"] == 2:
                    row["init_skills"] = 7
            results = Path(tmp) / "results.jsonl"
            results.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = BENCH.main(["summarise", "--results", str(results), "--resamples", "50", "--json"])
        self.assertEqual(code, 1)
        parity = json.loads(out.getvalue())["parity"]
        self.assertFalse(parity["ok"])
        self.assertEqual(parity["reasons"], ["t0 rep 2: loaded surface differs in init_skills"])

    def test_a_replayed_pair_summarises_its_copied_decision_calls(self):
        """End to end on fakes: a treatment call copied out of its container is priced into its arm."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            launch = PairLaunch([run_output("s-bare"), run_output("s-ref"), run_output("s-treat")],
                                [ledger_text(), ledger_text(decision("s-treat", model="claude-haiku-4-5"),
                                                            decision("s-treat", partial=True))])
            BENCH.replay([TASK], pair_options(tmp, decisions=out_dir / PAIR.DECISIONS), launch,
                         out=out_dir / BENCH.RESULTS)
            printed, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(printed), redirect_stderr(errors):
                code = BENCH.main(["summarise", "--results", str(out_dir), "--resamples", "50", "--json"])
        self.assertEqual(code, 0, errors.getvalue())
        treatment = json.loads(printed.getvalue())["arms"]["treatment"]
        self.assertEqual(treatment["decisions"]["calls"], 2)
        self.assertEqual(treatment["decisions"]["unmatched"], [])
        self.assertGreater(treatment["decisions"]["priced_usd"], 0)  # the checkout's own table prices it
        self.assertEqual([u["reason"] for u in treatment["decisions"]["unpriced"]], ["partial"])
        self.assertIsNone(treatment["cost_of_pass_with_decisions"])


if __name__ == "__main__":
    unittest.main()
