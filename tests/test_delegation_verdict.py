"""Whether the delegation stance fired, per task, read from recorded `stream-json` alone (#429).

The streams here are the shape `claude -p --output-format stream-json --verbose` writes: an `init`
event listing the session's tools, assistant messages whose `parent_tool_use_id` names the thread,
and a priced result. No test launches an agent or calls a model."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH, TASK, Launch, options, result

VERDICT = BENCH.delegation_verdict
OFFERED = ["Bash", "Read", "Grep", "Glob", "Agent", "Edit"]
NOT_OFFERED = ["Bash", "Read", "Grep", "Glob", "Edit"]


def init(tools=OFFERED):
    return {"type": "system", "subtype": "init", "session_id": "s", "model": "claude-test-20260101",
            "tools": tools}


def say(thread, tools, first=0):
    """An assistant message calling `tools` in `thread`; None is the main thread. Its calls' ids run
    `toolu_<first>` upward."""
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"model": "claude-test-20260101", "usage": {"cache_read_input_tokens": 0},
                        "content": [{"type": "tool_use", "id": "toolu_%d" % (first + i), "name": name,
                                     "input": {}} for i, name in enumerate(tools)]}}


def answer(use_id, error=False, thread=None):
    """The `tool_result` the CLI returns for call `use_id`; an error is a denied or blocked call."""
    return {"type": "user", "parent_tool_use_id": thread,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id,
                                                     "is_error": error, "content": "done"}]}}


def stream(*events, cost=0.5):
    """One run's output as stream-json: one JSON message per line, ending in its result."""
    return "\n".join(json.dumps(event) for event in list(events) + [result(cost)]) + "\n"


def run(arm, rep, output, task="demo", passed=True):
    """A replay row as `run_one` writes it, its diagnostic fields parsed from `output`."""
    parsed = BENCH.parse_result(output)
    return dict({field: parsed[field] for field in BENCH.STREAM_FIELDS}, task=task, arm=arm, rep=rep,
                error=False, passed=passed, cost_usd=parsed["cost_usd"], task_long=False)


# Shaped like the 2026-09-22 finding on `hook-inventory`: the spawn tool offered, 18 files read on
# the main thread, and no `Agent` block anywhere.
EIGHTEEN_READS = stream(init(), say(None, ["Read"] * 10), say(None, ["Read"] * 8))
SMALL = stream(init(), say(None, ["Read", "Grep"]))
SPAWNED = stream(init(), say(None, ["Read", "Agent"]), say("toolu_1", ["Read"] * 9 + ["Grep"] * 8), cost=0.4)
RESULT_ONLY = json.dumps(result())


def errored(row, kind="timeout", cost=2.0):
    """`row` as `_attempt` returns a run it could not finish: `error` set, a timeout priced at the cap."""
    return dict(row, error=True, error_kind=kind, passed=None, cost_usd=cost)


def cell(bare, harness, task="demo"):
    """Rows for one task: each output is one rep of its arm."""
    return ([run("bare", rep, out, task) for rep, out in enumerate(bare, 1)]
            + [run("harness", rep, out, task) for rep, out in enumerate(harness, 1)])


class SpawnOfferedTests(unittest.TestCase):
    def test_the_init_tool_list_says_whether_a_spawn_tool_was_offered(self):
        for tools, expected in ((OFFERED, True), (["Read", "Task"], True), (NOT_OFFERED, False)):
            self.assertIs(BENCH.parse_result(stream(init(tools), say(None, ["Read"])))["spawn_offered"],
                          expected, msg=tools)

    def test_no_init_event_or_no_tool_list_is_unknown(self):
        broken = dict(init(), tools="Agent")
        for events in ([say(None, ["Read"])], [broken, say(None, ["Read"])]):
            self.assertIsNone(BENCH.parse_result(stream(*events))["spawn_offered"])
        self.assertIsNone(BENCH.parse_result(RESULT_ONLY)["spawn_offered"])


class GatherCountTests(unittest.TestCase):
    def test_gather_calls_are_counted_by_thread_and_a_workflow_launch_is_not_a_spawn(self):
        output = stream(init(), say(None, ["Read", "Read", "Glob", "Agent", "Bash"]),
                        say("toolu_3", ["Grep", "Grep", "Read", "Agent"], 10),  # a nested spawn
                        say("toolu_13", ["Glob"], 20),  # inside the nested spawn
                        say("toolu_9", ["Glob"], 30),  # a thread no spawn started
                        say(None, ["Workflow", "Edit"], 40))
        parsed = BENCH.parse_result(output)
        self.assertEqual((parsed["gather_calls"], parsed["absorbed_calls"], parsed["spawns"],
                          parsed["workflow_launches"]), (8, 4, 2, 1))
        self.assertEqual(parsed["tool_counts"]["Workflow"], 1)

    def test_a_spawn_whose_result_is_an_error_is_not_a_spawn(self):
        """Regression: a denied or hook-blocked `Agent` call counted as a spawn from its `tool_use` alone."""
        denied = stream(init(), say(None, ["Agent"]), answer("toolu_0", error=True),
                        say(None, ["Read"] * 18, 10))
        parsed = BENCH.parse_result(denied)
        self.assertEqual((parsed["spawns"], parsed["absorbed_calls"], parsed["tool_counts"]["Agent"]), (0, 0, 1))
        allowed = stream(init(), say(None, ["Agent"]), say("toolu_0", ["Read"], 10), answer("toolu_0"))
        self.assertEqual((BENCH.parse_result(allowed)["spawns"], BENCH.parse_result(allowed)["absorbed_calls"]),
                         (1, 1))
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [denied] * 4))
        self.assertEqual(said["verdict"], "missed-above-break-even")

    def test_a_spawn_inside_a_workflow_thread_is_not_the_run_s_spawn(self):
        """Regression: any thread with a parent counted, so a Workflow agent calling `Agent` made a spawning run."""
        output = stream(init(), say(None, ["Workflow"]), say("toolu_0", ["Read", "Agent"], 10),
                        say("toolu_11", ["Read"] * 9, 20))
        parsed = BENCH.parse_result(output)
        self.assertEqual((parsed["spawns"], parsed["absorbed_calls"], parsed["gather_calls"],
                          parsed["workflow_launches"], parsed["tool_counts"]["Agent"]), (0, 0, 10, 1, 1))
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [output] * 4))
        self.assertEqual(said["verdict"], "missed-above-break-even")

    def test_output_with_no_assistant_message_is_unknown_never_zero(self):
        """Regression: a result-only stream read `spawns` 0, which a verdict would take as a decline."""
        parsed = BENCH.parse_result(RESULT_ONLY)
        for field in VERDICT.COUNT_FIELDS:
            self.assertIsNone(parsed[field], msg=field)
        self.assertEqual(parsed["tool_counts"], {})

    def test_the_older_single_document_form_still_counts(self):
        parsed = BENCH.parse_result(json.dumps([init(), say(None, ["Read", "Grep"]), result()]))
        self.assertEqual((parsed["gather_calls"], parsed["spawns"], parsed["spawn_offered"]), (2, 0, True))

    def test_a_row_without_raw_output_backfills_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "harness", "rep": 1}], Path(tmp))
        self.assertEqual(len(missing), 1)
        for field in ("spawn_offered",) + VERDICT.COUNT_FIELDS:
            self.assertIsNone(rows[0][field], msg=field)

    def test_backfill_derives_the_new_fields_from_kept_raw_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "demo-harness-1.json").write_text(EIGHTEEN_READS, encoding="utf-8")
            rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "harness", "rep": 1}], Path(tmp))
        self.assertEqual(missing, [])
        self.assertEqual((rows[0]["spawn_offered"], rows[0]["gather_calls"], rows[0]["spawns"]), (True, 18, 0))


class VerdictTests(unittest.TestCase):
    def test_a_task_above_break_even_the_harness_read_itself_is_missed(self):
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [EIGHTEEN_READS] * 4))
        self.assertEqual((said["verdict"], said["size"], said["above_break_even"]),
                         ("missed-above-break-even", 18, True))

    def test_a_small_task_the_harness_did_itself_is_declined_below_break_even(self):
        said = VERDICT.verdict(cell([SMALL] * 3, [SMALL] * 4))
        self.assertEqual((said["verdict"], said["size"]), ("declined-below-break-even", 2))

    def test_a_size_at_the_break_even_is_declined_not_missed(self):
        self.assertEqual(VERDICT.verdict(cell([SMALL] * 2, [SMALL] * 4), break_even=2)["verdict"],
                         "declined-below-break-even")

    def test_spawning_in_the_registered_share_of_runs_is_fired(self):
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [SPAWNED] * 3 + [EIGHTEEN_READS]))
        self.assertEqual(said["verdict"], "fired")
        self.assertEqual((said["harness"]["spawning_runs"], said["harness"]["absorbed_calls"]), (3, 51))
        self.assertEqual((said["harness"]["spawning_usd"], said["harness"]["not_spawning_usd"]), (0.4, 0.5))
        self.assertEqual(said["harness"]["label"], VERDICT.LABEL)

    def test_one_spawn_short_of_the_share_is_missed_not_fired(self):
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [SPAWNED] * 2 + [EIGHTEEN_READS] * 2))
        self.assertEqual(said["verdict"], "missed-above-break-even")

    def test_a_fire_below_break_even_is_fired_with_its_size_shown(self):
        said = VERDICT.verdict(cell([SMALL] * 2, [SPAWNED] * 4))
        self.assertEqual((said["verdict"], said["size"], said["above_break_even"]), ("fired", 2, False))

    def test_a_session_never_offered_the_spawn_tool_is_not_offered(self):
        cannot = stream(init(NOT_OFFERED), say(None, ["Read"] * 18))
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 2, [cannot, EIGHTEEN_READS]))
        self.assertEqual(said["verdict"], "not-offered")

    def test_workflow_launches_are_reported_but_never_fire(self):
        launched = stream(init(), say(None, ["Workflow"]), say(None, ["Read"] * 18))
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 2, [launched] * 4))
        self.assertEqual((said["verdict"], said["harness"]["workflow_launches"]),
                         ("missed-above-break-even", 4))

    def test_the_bare_arm_is_the_control_and_its_spawns_are_counted_without_a_verdict(self):
        said = VERDICT.verdict(cell([SPAWNED] * 2, [EIGHTEEN_READS] * 2))
        self.assertEqual(said["bare"]["spawning_runs"], 2)
        self.assertNotIn("verdict", said["bare"])
        self.assertEqual(said["size"], 18)  # the bare arm's main-thread and subagent gathers alike


class ErroredRowTests(unittest.TestCase):
    """Regression: `verdict` read errored rows as whole runs (a timeout, an effort mismatch)."""

    def test_an_errored_run_is_left_out_of_the_share_and_counted(self):
        harness = [run("harness", rep, out) for rep, out in enumerate([SPAWNED] * 3 + [EIGHTEEN_READS], 1)]
        timed_out = errored(run("harness", 5, EIGHTEEN_READS))
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, []) + harness + [timed_out])
        self.assertEqual(said["verdict"], "fired")
        self.assertEqual((said["harness"]["runs"], said["harness"]["errored"]), (4, 1))
        self.assertIn("errored runs left out: harness 1, bare 0", VERDICT.task_line("demo", said))

    def test_an_errored_bare_run_does_not_move_the_size(self):
        bare = [run("bare", 1, EIGHTEEN_READS), run("bare", 2, EIGHTEEN_READS),
                errored(run("bare", 3, SMALL)), errored(run("bare", 4, SMALL), "effort: observed low, pinned high")]
        said = VERDICT.verdict(bare + cell([], [EIGHTEEN_READS] * 4))
        self.assertEqual((said["size"], said["bare"]["errored"], said["verdict"]), (18, 2, "missed-above-break-even"))

    def test_a_timed_out_run_s_cap_is_not_a_cost(self):
        rows = [run("harness", 1, EIGHTEEN_READS), errored(run("harness", 2, EIGHTEEN_READS))]
        self.assertEqual(VERDICT.cost_split(rows)["not_spawning_usd"], 0.5)

    def test_too_few_clean_runs_after_errors_is_unknown(self):
        rows = cell([EIGHTEEN_READS] * 4, [SPAWNED] * 3) + [errored(run("harness", 4, SPAWNED))]
        self.assertEqual(VERDICT.verdict(rows)["verdict"], "unknown")
        only = cell([EIGHTEEN_READS] * 4, []) + [errored(run("harness", n, SPAWNED)) for n in range(1, 5)]
        self.assertEqual(VERDICT.verdict(only)["verdict"], "unknown")


class UnknownIsNeverAVerdictTests(unittest.TestCase):
    def assertUnknown(self, rows):
        self.assertEqual(VERDICT.verdict(rows)["verdict"], "unknown")

    def test_harness_runs_with_no_spawn_count_are_unknown(self):
        self.assertUnknown(cell([EIGHTEEN_READS] * 2, [RESULT_ONLY] * 4))

    def test_fewer_than_the_minimum_clean_harness_runs_is_unknown(self):
        """Regression: `needed(0.75, 1)` is 1, so a one-run replay read `fired`."""
        self.assertEqual(VERDICT.MIN_RUNS, 4)
        self.assertUnknown(cell([EIGHTEEN_READS], [SPAWNED]))
        self.assertUnknown(cell([EIGHTEEN_READS] * 4, [SPAWNED] * 3))
        self.assertUnknown(cell([SMALL] * 4, [SMALL] * 3))

    def test_a_bare_run_that_reported_no_gather_count_leaves_the_size_unknown(self):
        """The size is a median over every clean bare run, never over the ones that happened to report."""
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 3 + [RESULT_ONLY], [EIGHTEEN_READS] * 4))
        self.assertEqual((said["size"], said["verdict"]), (None, "unknown"))

    def test_a_task_with_no_bare_size_is_unknown_even_when_the_harness_spawned(self):
        self.assertUnknown(cell([RESULT_ONLY] * 2, [SPAWNED] * 2))
        self.assertUnknown([run("harness", 1, SPAWNED)])

    def test_one_run_of_unknown_spawns_blocks_a_decline(self):
        self.assertUnknown(cell([SMALL] * 2, [SMALL] * 3 + [RESULT_ONLY]))

    def test_one_run_whose_spawn_offer_is_unknown_blocks_a_miss(self):
        no_init = stream(say(None, ["Read"] * 18))
        self.assertUnknown(cell([EIGHTEEN_READS] * 2, [EIGHTEEN_READS] * 3 + [no_init]))

    def test_rows_saved_before_the_fields_existed_are_unknown(self):
        old = [{"task": "demo", "arm": arm, "rep": 1, "spawns": 0, "cost_usd": 0.5} for arm in ("bare", "harness")]
        self.assertUnknown(old)

    def test_unknown_runs_cannot_undo_a_proven_fire(self):
        said = VERDICT.verdict(cell([EIGHTEEN_READS] * 4, [SPAWNED] * 3 + [RESULT_ONLY]))
        self.assertEqual(said["verdict"], "fired")

    def test_an_empty_group_has_no_mean_cost(self):
        split = VERDICT.cost_split([run("harness", 1, EIGHTEEN_READS)])
        self.assertIsNone(split["spawning_usd"])
        self.assertIsNone(VERDICT.cost_split([run("harness", 1, RESULT_ONLY)])["not_spawning_usd"])

    def test_the_share_needs_whole_runs_and_at_least_one(self):
        self.assertEqual([VERDICT.needed(0.75, n) for n in (1, 4, 5)], [1, 3, 4])
        self.assertEqual(VERDICT.needed(0.6, 5), 3)  # 0.6 * 5 is 3.0000000000000004 in floating point
        self.assertEqual(VERDICT.needed(0, 5), 1)


REGISTERED = {"evidence": "pre-registered", "pre_registration": "docs/plans/demo.md",
              "pre_registration_commit": "b" * 40}


def scored(bare, harness, tasks=("t0", "t1", "t2")):
    """A saved set SM-2 can analyse, each task with the given outputs per arm."""
    return [row for task in tasks for row in cell(bare, harness, task)]


class ReportTests(unittest.TestCase):
    def test_the_report_names_its_inputs_and_tallies_every_verdict(self):
        rows = cell([EIGHTEEN_READS] * 2, [EIGHTEEN_READS] * 4, "big") + cell([SMALL] * 2, [RESULT_ONLY] * 4, "odd")
        block = VERDICT.report(rows)
        self.assertEqual((block["break_even"], block["break_even_source"], block["absorbable"], block["min_runs"]),
                         (7.6, "FR-34, hypothetical", ["Read", "Grep", "Glob"], 4))
        self.assertEqual(block["verdicts"]["missed-above-break-even"], 1)
        self.assertEqual(block["verdicts"]["unknown"], 1)
        self.assertEqual(sum(block["verdicts"].values()), 2)
        text = VERDICT.render(block)
        self.assertIn("adherence, descriptive, not causal", text)
        self.assertIn("break-even 7.6 absorbed calls (FR-34, hypothetical)", text)
        self.assertIn("big: missed-above-break-even (exploratory), size 18 gather calls", text)

    def test_an_overridden_break_even_is_labelled_an_override(self):
        """Regression: `--break-even 9.5` still printed "FR-34, hypothetical"."""
        block = VERDICT.report(cell([EIGHTEEN_READS] * 2, [EIGHTEEN_READS] * 4), break_even=9.5)
        self.assertEqual((block["break_even"], block["break_even_source"]), (9.5, "override, --break-even"))
        self.assertIn("break-even 9.5 absorbed calls (override, --break-even)", VERDICT.render(block))
        self.assertNotIn("FR-34", VERDICT.render(block))

    def test_only_rows_from_a_named_pre_registration_are_registered(self):
        rows = cell([EIGHTEEN_READS] * 2, [SPAWNED] * 4)
        self.assertFalse(VERDICT.report(rows)["registered"])
        text = VERDICT.render(VERDICT.report(rows))
        self.assertIn("exploratory, not from a registered run", text)
        self.assertIn("demo: fired (exploratory),", text)
        stamped = [dict(r, **REGISTERED) for r in rows]
        block = VERDICT.report(stamped)
        self.assertTrue(block["registered"])
        self.assertIn("demo: fired, size", VERDICT.render(block))
        self.assertNotIn("exploratory", VERDICT.render(block))
        for spoiled in (dict(stamped[0], evidence="exploratory", pre_registration=None),
                        dict(stamped[0], pre_registration=None)):
            self.assertFalse(VERDICT.report([spoiled] + stamped[1:])["registered"])
        self.assertFalse(VERDICT.report([])["registered"])

    def test_the_history_row_carries_the_block_and_renders_one_line_per_task(self):
        rows = [dict(r, date="2026-01-01", harness_version="9.9.9", harness_sha="a" * 40, tag="v9.9.9",
                     model="claude-test", cli_version="1.0", cost_normalised_usd=r["cost_usd"], **REGISTERED)
                for r in scored([EIGHTEEN_READS] * 4, [SPAWNED] * 4)]
        made = BENCH.history_row(rows, "s1")
        self.assertEqual(made["delegation"]["break_even"], VERDICT.BREAK_EVEN_CALLS)
        self.assertTrue(made["delegation"]["registered"])
        self.assertEqual(made["delegation"]["verdicts"]["fired"], 3)
        self.assertEqual(made["sm2"], BENCH.sm2(rows))
        lines = [line for line in BENCH.render_history([made]).splitlines() if line.startswith("    delegation: ")]
        self.assertEqual(len(lines), 4)  # the heading and one per task
        self.assertIn("t0: fired, size", lines[1])
        older = dict(made)
        older.pop("delegation")
        self.assertNotIn("delegation:", BENCH.render_history([older]))

    def test_summarise_prints_the_block_under_an_unchanged_sm2_report(self):
        rows = scored([EIGHTEEN_READS] * 4, [EIGHTEEN_READS] * 4)
        with tempfile.TemporaryDirectory() as tmp:
            BENCH.write_jsonl(Path(tmp) / BENCH.RESULTS, rows)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(BENCH.main(["summarise", "--results", tmp, "--resamples", "200"]), 0)
            sm2 = BENCH.replay_stats.render(BENCH.replay_stats.analyse(rows, BENCH.replay_stats.SEED, 200))
            self.assertTrue(out.getvalue().startswith(sm2))
            tail = out.getvalue()[len(sm2):]
            self.assertIn("break-even 7.6 absorbed calls", tail)
            self.assertEqual(tail.count(": missed-above-break-even"), 3)
            out = io.StringIO()
            with redirect_stdout(out):
                BENCH.main(["summarise", "--results", tmp, "--resamples", "200", "--json", "--break-even", "20"])
            printed = json.loads(out.getvalue())
        block = printed.pop("delegation")
        sm2 = BENCH.replay_stats.analyse(rows, BENCH.replay_stats.SEED, 200)
        self.assertEqual(printed, json.loads(json.dumps(sm2, sort_keys=True)))  # SM-2's fields, unchanged
        self.assertEqual((block["break_even"], block["break_even_source"]), (20.0, "override, --break-even"))
        self.assertEqual(block["verdicts"]["declined-below-break-even"], 3)


class RunnerTests(unittest.TestCase):
    def test_replayed_rows_carry_the_fields_from_the_arm_s_own_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([EIGHTEEN_READS, SPAWNED]))
        by_arm = {row["arm"]: row for row in rows}
        self.assertEqual((by_arm["bare"]["spawn_offered"], by_arm["bare"]["gather_calls"],
                          by_arm["bare"]["spawns"]), (True, 18, 0))
        self.assertEqual((by_arm["harness"]["spawns"], by_arm["harness"]["absorbed_calls"],
                          by_arm["harness"]["workflow_launches"]), (1, 17, 0))
        self.assertEqual(VERDICT.verdict(rows)["verdict"], "unknown")  # one run is below MIN_RUNS


if __name__ == "__main__":
    unittest.main()
