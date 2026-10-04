"""The long-session tier (#1193): a pack's scripted multi-turn scenarios, loaded and validated with
their checks held back, driven one resumed CLI session per scenario, arm and rep, scored at each
checkpoint on the segment's stream, and summarised with scenarios as clusters. No image is built,
no container started and no model called: the CLI and Docker are fakes."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, Launch, mounts, options
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args
from test_replay_pack import CANARY, git, harness_checkout, make_pack

PACK = BENCH.replay_pack
SESSION = BENCH.replay_session
TIERS = {"standard": "sonnet", "light": "haiku"}
MAIN, SUB = "claude-sonnet-5", "claude-haiku-4-5-20251001"
CHECK = ("# %s\ndef check(root, stream=None):\n    ok = (root / 'answer.txt').is_file()\n"
         "    return {'pass': ok, 'metrics': {'segment_cost_usd': None}, 'errors': []}\n" % CANARY)
SOLUTION = "# %s\ndef solve(root):\n    (root / 'answer.txt').write_text('42')\n" % CANARY


def scenario_spec(**over):
    spec = {"schema_version": 1, "id": "demo-session", "workspace": "app", "source": "written for a test",
            "caps": {"max_user_turns": 5, "max_agent_turns_per_user_turn": 7, "max_cost_usd_hint": 4.0},
            "checkpoints": {"cp1": {"metrics": {"segment_cost_usd": "lower"}},
                            "cp2": {"metrics": {"segment_cost_usd": "lower"}}},
            "turns": [{"prompt": "Look around."},
                      {"prompt": "Write answer.txt.", "checkpoint": "cp1"},
                      {"branch": {"on": "cp1", "pass": "Good, go on.", "fail": "That is wrong; fix it, then go on."}},
                      {"prompt": "Finish.", "checkpoint": "cp2"},
                      {"prompt": "Thanks."}]}
    spec.update(over)
    return spec


def scenario_pack(root, spec=None, check=CHECK, solution=SOLUTION, extra_sets=None):
    """A committed pack holding one task set and a long-session set of one scenario."""
    root = Path(root)
    spec = spec or scenario_spec()
    folder = root / "scenarios" / spec["id"]
    for kind, text in (("checks", check), ("solutions", solution)):
        (folder / kind).mkdir(parents=True)
        for name in spec["checkpoints"]:
            (folder / kind / (name + ".py")).write_text(text, encoding="utf-8")
    (folder / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    sets = {"production": {"tasks": ["short-one", "long-one"]},
            "long-session": {"tier": "long-session", "model": MAIN, "scenarios": [spec["id"]]}}
    sets.update(extra_sets or {})
    return make_pack(root, sets=sets)


def opened(tmp, **kwargs):
    pack = PACK.open_pack(scenario_pack(Path(tmp) / "pack", **kwargs), harness_root=Path(tmp) / "harness")
    return pack


def usage(context):
    return {"input_tokens": 10, "cache_creation_input_tokens": context // 2,
            "cache_read_input_tokens": context - 10 - context // 2, "output_tokens": 5}


def turn_stream(cost=1.0, context=1000, sub_cost=0.0, subtype="success", session="s-1", turns=3):
    """One turn's stream-json: init, a main-thread call, a subagent call when `sub_cost`, a result."""
    lines = [{"type": "system", "subtype": "init", "session_id": session, "model": MAIN},
             {"type": "assistant", "parent_tool_use_id": None, "session_id": session,
              "message": {"id": "m", "model": MAIN, "usage": usage(context), "content": []}}]
    model_usage = {MAIN: {"inputTokens": 10, "outputTokens": 5, "cacheCreationInputTokens": 100,
                          "cacheReadInputTokens": 900, "costUSD": cost - sub_cost}}
    if sub_cost:
        lines.append({"type": "assistant", "parent_tool_use_id": "t1", "session_id": session,
                      "message": {"id": "s", "model": SUB, "usage": usage(context * 10), "content": []}})
        model_usage[SUB] = {"inputTokens": 1, "outputTokens": 2, "cacheCreationInputTokens": 3,
                            "cacheReadInputTokens": 4, "costUSD": sub_cost}
    lines.append({"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": turns,
                  "total_cost_usd": cost, "session_id": session, "modelUsage": model_usage})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def scenario(**over):
    """A loaded scenario as `load_scenarios` returns it, without a pack on disk."""
    spec = scenario_spec(**over)
    order = [t["checkpoint"] for t in spec["turns"] if "checkpoint" in t]
    return {"id": spec["id"], "kind": "scenario", "caps": spec["caps"], "turns": spec["turns"],
            "checkpoint_order": order,
            "checkpoints": {n: {"metrics": spec["checkpoints"][n]["metrics"], "check_file": "unused"} for n in order}}


class FakeCli:
    """Stands in for one turn's container: records each call and replays the scripted streams."""
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, number, prompt, budget, resume):
        self.calls.append({"number": number, "prompt": prompt, "budget": budget, "resume": resume})
        out = self.outputs.pop(0)
        return out if isinstance(out, dict) else {"stdout": out, "returncode": 0}


def run(outputs, verdicts=None, cap=4.0, spec=None):
    cli, seen = FakeCli(outputs), []
    verdicts = dict(verdicts or {})

    def checker(name, stream):
        seen.append((name, stream))
        return verdicts.get(name, True), "ok", {"metrics": {"segment_cost_usd": 1.0}, "metric_errors": [],
                                                 "metric_stream": True}

    rows = SESSION.run_session(spec or scenario(), {"arm": "bare", "rep": 1, "scenario": "demo-session"}, cap,
                               cli, checker, TIERS)
    return rows, cli, seen


class ScenarioLoadingTests(unittest.TestCase):
    def test_a_long_session_set_loads_its_scenarios_and_the_workspace_never_holds_a_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = opened(tmp)
            try:
                scenarios, manifest = PACK.load_scenarios(pack, "long-session")
                (item,) = scenarios
                self.assertEqual((manifest["tier"], manifest["model"]), ("long-session", MAIN))
                self.assertEqual(item["checkpoint_order"], ["cp1", "cp2"])
                self.assertEqual(item["max_turns"], 7)
                self.assertTrue(PACK.is_pack(item) and PACK.is_scenario(item))
                tree = PACK.materialize(item, Path(tmp) / "tree")
                self.assertFalse([p for p in tree.rglob("*.py") if CANARY in p.read_text(encoding="utf-8")])
                self.assertEqual(len(PACK.held_back_files(item)), 4)
                self.assertIn("def check(root, stream=None)", PACK.check_source(PACK.checkpoint_task(item, "cp2")))
            finally:
                PACK.close_pack(pack)

    def test_a_long_session_set_is_refused_to_a_task_tier_and_must_list_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = opened(tmp)
            try:
                with self.assertRaisesRegex(SystemExit, "pass --tier long-session"):
                    PACK.load_set(pack, "long-session", "production")
                with self.assertRaisesRegex(SystemExit, "pass --tier production"):
                    PACK.load_scenarios(pack, "production")
            finally:
                PACK.close_pack(pack)
        errors = PACK.document_errors({"schema_version": 1, "name": "p", "version": "1", "canary": "c" * 30,
                                       "break_even_calls": 7.6, "workspaces": {"app": {"gate": [["true"]]}},
                                       "sets": {"long-session": {"tier": "long-session", "tasks": ["a"]}}})
        self.assertEqual(errors, ["set long-session has no list of unique scenario ids"])

    def test_a_malformed_scenario_is_refused_before_anything_runs(self):
        bad = scenario_spec(turns=[{"branch": {"on": "cp1", "pass": "a", "fail": "b"}},
                                   {"prompt": "x", "checkpoint": "cp1"}, {"prompt": "y", "checkpoint": "cp1"}],
                            caps={"max_user_turns": 2, "max_agent_turns_per_user_turn": 0, "max_cost_usd_hint": 1})
        with tempfile.TemporaryDirectory() as tmp:
            pack = opened(tmp, spec=bad, check="def check(root, stream=None):\n    return []\n")
            try:
                with self.assertRaises(SystemExit) as caught:
                    PACK.load_scenarios(pack, "long-session")
            finally:
                PACK.close_pack(pack)
        text = str(caught.exception)
        for reason in ("branches on 'cp1', not an earlier checkpoint", "more than max_user_turns",
                       "max_agent_turns_per_user_turn is not a positive integer", "'cp1' is undeclared or repeated",
                       "declares checkpoints no turn runs: cp2", "checks/cp1.py does not carry the pack's canary"):
            self.assertIn(reason, text)

    def test_a_harness_commit_holding_a_checkpoint_check_is_contaminated(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = opened(tmp)
            try:
                (item,), _ = PACK.load_scenarios(pack, "long-session")
                clean = harness_checkout(Path(tmp) / "clean")
                leaked = harness_checkout(Path(tmp) / "leaked", {"copied.py": SOLUTION.replace(CANARY, "no canary")})
                self.assertEqual(PACK.contamination_errors(item, clean), [])
                self.assertFalse(PACK.contamination_errors(item, leaked))  # different bytes, no canary
                exact = harness_checkout(Path(tmp) / "exact", {"x.py": CHECK})
                errors = PACK.contamination_errors(item, exact)
                self.assertTrue(any("checks/cp1.py" in e for e in errors), errors)
            finally:
                PACK.close_pack(pack)


class SessionDriverTests(unittest.TestCase):
    def test_turns_run_in_order_in_one_session_each_later_turn_resuming_it(self):
        rows, cli, _ = run([turn_stream(0.5)] * 5)
        self.assertEqual([c["number"] for c in cli.calls], [1, 2, 3, 4, 5])
        self.assertEqual([c["resume"] for c in cli.calls], [False, True, True, True, True])
        self.assertEqual([c["budget"] for c in cli.calls], [4.0, 3.5, 3.0, 2.5, 2.0])
        self.assertEqual(cli.calls[0]["prompt"], "Look around.")
        self.assertEqual(rows[-1]["stopped"], None)

    def test_a_branch_takes_pass_or_fail_by_the_earlier_checkpoint(self):
        for verdict, prompt in ((True, "Good, go on."), (False, "That is wrong; fix it, then go on.")):
            with self.subTest(verdict=verdict):
                rows, cli, _ = run([turn_stream(0.5)] * 5, {"cp1": verdict})
                self.assertEqual(cli.calls[2]["prompt"], prompt)
                self.assertEqual(rows[-1]["branches"], [{"on": "cp1", "taken": "pass" if verdict else "fail",
                                                         "turn": 3}])
        self.assertEqual(SESSION.choose_prompt({"branch": {"on": "x", "pass": "p", "fail": "f"}}, {})[0], "f")

    def test_the_spend_cap_stops_the_session_and_unreached_checkpoints_are_not_passed(self):
        rows, cli, seen = run([turn_stream(1.0)] * 5, cap=2.5)
        self.assertEqual(len(cli.calls), 3)  # 0, 1 and 2 spent are under 2.5; 3 is not
        cp1, cp2, session = rows
        self.assertTrue(cp1["reached"] and cp1["passed"])
        self.assertEqual((cp2["reached"], cp2["passed"], cp2["metrics"]), (False, False, {"segment_cost_usd": None}))
        self.assertEqual((session["stopped"], session["error"], session["cost_usd"]), ("cap", False, 3.0))
        self.assertEqual([name for name, _ in seen], ["cp1"])

    def test_the_cli_budget_stop_and_the_user_turn_cap_stop_the_session(self):
        rows, cli, _ = run([turn_stream(0.5), turn_stream(0.7, subtype=SESSION.BUDGET_STOP)])
        self.assertEqual((len(cli.calls), rows[-1]["stopped"]), (2, "cap"))
        capped = scenario(caps={"max_user_turns": 3, "max_agent_turns_per_user_turn": 7, "max_cost_usd_hint": 4.0})
        rows, cli, _ = run([turn_stream(0.1)] * 5, spec=capped)
        self.assertEqual((len(cli.calls), rows[-1]["stopped"], rows[-1]["user_turns_run"]), (3, "max_user_turns", 3))

    def test_a_turn_that_hits_its_agent_turn_cap_ends_but_the_session_goes_on(self):
        rows, cli, _ = run([turn_stream(0.2), turn_stream(0.2, subtype=SESSION.TURN_CAP_STOP)] + [turn_stream(0.2)] * 3)
        self.assertEqual((len(cli.calls), rows[-1]["agent_turn_cap_hits"], rows[-1]["stopped"]), (5, 1, None))

    def test_a_timeout_counts_what_was_left_of_the_cap_and_errors_the_session(self):
        rows, cli, _ = run([turn_stream(1.0), {"stdout": "", "timeout": True}])
        session = rows[-1]
        self.assertEqual((session["error"], session["error_kind"], session["cost_usd"]), (True, "timeout", 4.0))
        self.assertEqual(session["cost_per_turn"], [1.0, 3.0])
        self.assertIsNone(session["passed"])
        refused = run([{"stdout": turn_stream(1.0), "error_kind": "installed-checkout-read"}])[0][-1]
        self.assertEqual((refused["error"], refused["error_kind"], refused["user_turns_run"]),
                         (True, "installed-checkout-read", 1))

    def test_each_checkpoint_scores_the_stream_of_every_turn_since_the_previous_one(self):
        rows, _, seen = run([turn_stream(0.25, session="a"), turn_stream(0.5, session="b"),
                             turn_stream(1.0, session="c"), turn_stream(0.75, session="d"), turn_stream(0.1)])
        (first, one), (second, two) = seen
        self.assertEqual((first, second), ("cp1", "cp2"))
        results = lambda text: [json.loads(l)["total_cost_usd"] for l in text.splitlines()
                                if json.loads(l)["type"] == "result"]
        self.assertEqual(results(one), [0.25, 0.5])
        self.assertEqual(results(two), [1.0, 0.75])
        self.assertEqual((rows[0]["cost_usd"], rows[1]["cost_usd"]), (0.75, 1.75))
        self.assertEqual((rows[0]["segment_turns"], rows[1]["segment_turns"]), ([1, 2], [3, 4]))
        self.assertEqual((rows[0]["cumulative_cost_usd"], rows[1]["cumulative_cost_usd"]), (0.75, 2.5))

    def test_a_check_that_cannot_run_errors_the_session(self):
        def broken(name, stream):
            raise RuntimeError("no oracle")
        rows = SESSION.run_session(scenario(), {}, 4.0, FakeCli([turn_stream(0.1)] * 5), broken, TIERS)
        self.assertEqual((rows[-1]["error"], rows[-1]["error_kind"]), (True, "check cp1: RuntimeError"))


class RowShapeTests(unittest.TestCase):
    CHECKPOINT_FIELDS = {"row_kind", "checkpoint", "checkpoint_index", "reached", "turn", "passed", "outcome",
                         "error", "error_kind", "segment_turns", "cost_usd", "input_tokens",
                         "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
                         "main_peak_context_tokens", "cost_by_tier", "cumulative_cost_usd", "metrics",
                         "metric_directions", "metric_errors", "metric_stream", "check_detail"}
    SESSION_FIELDS = {"row_kind", "cost_usd", "input_tokens", "cache_creation_input_tokens",
                      "cache_read_input_tokens", "output_tokens", "cost_by_tier", "main_peak_context_tokens",
                      "main_mean_context_tokens", "user_turns_planned", "user_turns_run", "stopped", "error",
                      "error_kind", "session_cap_usd", "checkpoints_total", "checkpoints_passed", "passed",
                      "outcome", "cost_per_turn", "cumulative_cost_usd", "main_peak_context_per_turn",
                      "agent_turns_per_turn", "cost_per_turn_slope", "branches", "agent_turn_cap_hits"}

    def test_one_row_per_checkpoint_and_one_per_session_with_tokens_context_and_tiers(self):
        streams = [turn_stream(0.5, context=1000 * n, sub_cost=0.1 if n == 2 else 0.0) for n in range(1, 6)]
        rows, _, _ = run(streams)
        self.assertEqual([r["row_kind"] for r in rows], ["checkpoint", "checkpoint", "session"])
        for row in rows[:2]:
            self.assertLessEqual(self.CHECKPOINT_FIELDS, set(row))
            self.assertEqual((row["arm"], row["rep"], row["scenario"]), ("bare", 1, "demo-session"))
        self.assertLessEqual(self.SESSION_FIELDS, set(rows[-1]))
        cp1, cp2, session = rows
        self.assertEqual((cp1["checkpoint_index"], cp2["checkpoint_index"]), (1, 2))
        self.assertEqual(cp1["metrics"], {"segment_cost_usd": 1.0})
        self.assertEqual(cp1["main_peak_context_tokens"], 2000)  # the subagent's larger call is not main thread
        self.assertEqual(cp1["cost_by_tier"], {"light": 0.1, "standard": 0.9})
        self.assertEqual(cp1["cache_read_input_tokens"], 1804)
        self.assertEqual(session["cost_per_turn"], [0.5] * 5)
        self.assertEqual(session["cumulative_cost_usd"], [0.5, 1.0, 1.5, 2.0, 2.5])
        self.assertEqual(session["main_peak_context_per_turn"], [1000, 2000, 3000, 4000, 5000])
        self.assertEqual((session["main_peak_context_tokens"], session["main_mean_context_tokens"]), (5000, 3000.0))
        self.assertEqual((session["cost_per_turn_slope"], session["checkpoints_passed"], session["outcome"]),
                         (0.0, 2, "pass"))
        self.assertEqual(session["output_tokens"], 5 * 5 + 2)

    def test_the_slope_is_least_squares_on_turn_number(self):
        self.assertEqual(SESSION.slope([1.0, 2.0, 3.0]), 1.0)
        self.assertEqual(SESSION.slope([0.5, 0.5, 2.0]), 0.75)
        self.assertIsNone(SESSION.slope([1.0]))


def workspace_scenario(tmp):
    """A loaded scenario whose workspace is a real directory, for the container path."""
    item = scenario()
    workspace = Path(tmp) / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("app\n", encoding="utf-8")
    item["pack"] = {"task_dir": str(Path(tmp) / "scn"), "workspace": str(workspace), "gate": [["true"]],
                    "canary": CANARY, "name": "test-pack", "source": str(tmp)}
    return item


class ContainerSessionTests(unittest.TestCase):
    def run_session(self, outputs, **over):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch(outputs)
            memories = []
            original = launch.__call__

            def reading(command, **kwargs):
                memories.extend(Path(m.split(":")[0]).read_text(encoding="utf-8") for m in mounts(command)
                                if m.endswith(":%s:ro" % BENCH.arms.MANAGED_MEMORY))
                return original(command, **kwargs)

            opts = options(tmp, model=MAIN, run_cap=None, stamp={"date": "2026-01-01", "model": MAIN},
                           session_scorer=lambda task, workdir, repo, stream: (True, "", None), **over)
            rows = BENCH.run_long_session(workspace_scenario(tmp), 2, "harness", opts, reading)
        return rows, launch, memories

    def test_every_turn_is_a_fresh_container_resuming_one_cli_session_from_one_store(self):
        rows, launch, memories = self.run_session([turn_stream(0.5)] * 5)
        commands = [c for c, _ in launch.calls]
        self.assertEqual(len(commands), 5)
        session_id = rows[-1]["session_id"]
        argv = [c[c.index("claude"):] for c in commands]
        self.assertEqual(argv[0][argv[0].index("--session-id") + 1], session_id)
        for later in argv[1:]:
            self.assertEqual(later[later.index("--resume") + 1], session_id)
            self.assertNotIn("--session-id", later)
        for one in argv:
            self.assertNotIn("--no-session-persistence", one)
            self.assertEqual(one[one.index("--max-turns") + 1], "7")
        self.assertEqual([one[one.index("--max-budget-usd") + 1] for one in argv], ["4", "3.5", "3", "2.5", "2"])
        stores = {m for c in commands for m in mounts(c) if m.endswith(":" + BENCH.arms.SESSION_STORE)}
        self.assertEqual(len(stores), 1)
        self.assertEqual(len(set(memories)), 1)  # one cold-cache nonce opens the session, kept every turn
        self.assertIn(rows[-1]["cache_nonce"], memories[0])
        self.assertEqual({r["session_id"] for r in rows}, {session_id})
        self.assertEqual({(r["tier"], r["arm"], r["rep"]) for r in rows}, {("long-session", "harness", 2)})
        self.assertFalse(rows[-1]["error"], rows[-1]["error_kind"])

    def test_the_session_store_is_refused_under_a_host_path(self):
        with self.assertRaisesRegex(SystemExit, "under the host path"):
            BENCH.arms.run_command("img", None, ["true"], "none", session_store=str(Path.home() / "sessions"))

    def test_a_one_prompt_run_keeps_no_session(self):
        self.assertIn("--no-session-persistence", BENCH.arm_command("claude", "m", "p"))

    def test_the_replay_loop_writes_every_row_and_counts_spend_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = workspace_scenario(tmp)
            calls = []

            def driver(number, prompt, budget, resume):
                calls.append(number)
                return {"stdout": turn_stream(1.0), "returncode": 0}

            opts = options(tmp, model=MAIN, run_cap=None, reps=1, spend_cap=6.0, session_driver=driver,
                           stamp={"date": "2026-01-01", "model": MAIN},
                           session_scorer=lambda task, workdir, repo, stream: (True, "", None))
            with mock.patch.object(BENCH, "probe_workdirs"), mock.patch.object(BENCH.arms, "admit"), \
                    mock.patch.object(BENCH.arms, "admit_pair"):
                rows, stopped = BENCH.replay([item], opts, Launch([]), out=Path(tmp) / "results.jsonl")
                saved = BENCH.read_jsonl(Path(tmp) / "results.jsonl")
        # Two sessions of a 4 USD cap would pass 6 USD, so the second never starts.
        self.assertTrue(stopped)
        self.assertEqual(len(rows), 3)
        self.assertEqual(saved, json.loads(json.dumps(rows)))


def session_rows(arm, scenario_id, rep, cost, passes, slope_costs, tier_split=0.0):
    base = {"tier": "long-session", "arm": arm, "scenario": scenario_id, "task": scenario_id, "rep": rep,
            "model": MAIN, "cache_nonce": "%s-%s-%d" % (arm, scenario_id, rep)}
    rows = [dict(base, row_kind="checkpoint", checkpoint="cp%d" % i, checkpoint_index=i, passed=p)
            for i, p in enumerate(passes, 1)]
    rows.append(dict(base, row_kind="session", cost_usd=cost, cost_per_turn=slope_costs,
                     cost_per_turn_slope=SESSION.slope(slope_costs), main_peak_context_tokens=100000 + cost,
                     cost_by_tier={"standard": cost * (1 - tier_split), "light": cost * tier_split}, error=False))
    return rows


class SummaryTests(unittest.TestCase):
    def rows(self):
        out = []
        for scenario_id, scale in (("a", 1.0), ("b", 2.0), ("c", 3.0)):
            for rep in (1, 2):
                out += session_rows("bare", scenario_id, rep, 10.0 * scale, [True, False], [1.0, 2.0, 3.0])
                out += session_rows("harness", scenario_id, rep, 5.0 * scale, [True, True], [1.0, 1.0, 1.0], 0.4)
        return out

    def test_per_arm_measures_carry_scenario_clustered_intervals(self):
        result = SESSION.summarise(self.rows(), tiers=TIERS, resamples=200)
        self.assertEqual(result["clusters"], ["a", "b", "c"])
        self.assertEqual(list(result["arms"]), ["bare", "harness"])
        bare, harness = result["arms"]["bare"], result["arms"]["harness"]
        self.assertEqual(bare["checkpoint_pass_rate"]["estimate"], 0.5)
        self.assertEqual(harness["checkpoint_pass_rate"], {"estimate": 1.0, "interval": [1.0, 1.0]})
        self.assertEqual(bare["cost_per_session_usd"]["estimate"], 20.0)
        low, high = bare["cost_per_session_usd"]["interval"]
        self.assertTrue(10.0 <= low <= 20.0 <= high <= 30.0)
        self.assertEqual((bare["cost_per_turn_slope_usd"]["estimate"], harness["cost_per_turn_slope_usd"]["estimate"]),
                         (1.0, 0.0))
        self.assertEqual(bare["cheaper_tier_cost_share"]["estimate"], 0.0)
        self.assertAlmostEqual(harness["cheaper_tier_cost_share"]["estimate"], 0.4)
        self.assertEqual(bare["checkpoints"]["a"], {"cp1": 1.0, "cp2": 0.0})
        self.assertEqual(bare["cost_per_turn_curve"]["b"], [1.0, 2.0, 3.0])
        against = result["against"]["harness"]
        self.assertEqual(against["cost_per_session_ratio"]["estimate"], 0.5)
        self.assertEqual(against["checkpoint_pass_rate_difference"]["estimate"], 0.5)
        self.assertIn("cost on cheaper tiers", SESSION.render(result))

    def test_summarise_dispatches_a_long_session_set_and_states_its_cache_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / BENCH.RESULTS
            BENCH.write_jsonl(path, self.rows())
            args = argparse.Namespace(results=str(path), seed=1, resamples=50, plot=None, json=True,
                                      break_even=3, correction=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(BENCH.cmd_summarise(args), 0)
        report = json.loads(out.getvalue())
        self.assertEqual((report["tier"], report["cache_basis"]), ("long-session", "cold"))
        self.assertEqual(report["method"], "scenario-clustered percentile bootstrap")


class DryRunTests(unittest.TestCase):
    def args(self, tmp, **over):
        source = Path(tmp) / "pack"
        if not source.exists():
            scenario_pack(source)
        values = dict(pack=str(source), pack_ref=None, pack_digest=None, pack_set=None, tier="long-session",
                      pair=None, break_even=7.6, model=None, reps=None, run_cap=None, spend_cap=None)
        values.update(over)
        args = replay_args(tmp, **{k: v for k, v in values.items() if k in ("exploratory", "dry_run", "tag")})
        for key, value in values.items():
            setattr(args, key, value)
        args.tasks = None
        return args

    def replay_cli(self, tmp, args):
        fake, out, err = FakeArms(), io.StringIO(), io.StringIO()
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", fake_replay), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = BENCH.cmd_replay(args)
        return code, out.getvalue(), err.getvalue(), fake

    def test_a_dry_run_lists_every_planned_session_with_its_cap_and_the_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.args(tmp, dry_run=True, exploratory=True)
            code, out, _, fake = self.replay_cli(tmp, args)
        self.assertEqual((code, fake.built), (0, []))
        self.assertEqual((args.model, args.reps), (MAIN, 3))
        self.assertIn("long-session tier: 6 session(s): 1 scenario(s) x 2 arm(s) x 3 rep(s)", out)
        self.assertIn("scenario demo-session: 5 user turn(s) (cap 5), 2 checkpoint(s), 7 agent turns per user "
                      "turn, 4 USD per session", out)
        self.assertIn("ceiling, before any spend: 24.00 USD", out)
        self.assertIn("contamination demo-session: clean", out)
        for rep in (1, 2, 3):
            for arm in ("bare", "harness"):
                self.assertIn("demo-session rep %d %s" % (rep, arm), out)

    def test_run_cap_lowers_each_session_cap_and_the_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, out, _, _ = self.replay_cli(tmp, self.args(tmp, dry_run=True, exploratory=True, run_cap=1.5, reps=1))
        self.assertEqual(code, 0)
        self.assertIn("1.5 USD per session", out)
        self.assertIn("ceiling, before any spend: 3.00 USD", out)

    def test_another_model_a_pair_and_a_tier_without_a_pack_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            for over, reason in (({"model": "claude-other"}, "pins model"),
                                 ({"pack": None}, "needs --pack"),
                                 ({"verify_tasks": True}, "does not read scenarios")):
                with self.subTest(over=over):
                    args = self.args(tmp, dry_run=True, exploratory=True, **over)
                    with self.assertRaisesRegex(SystemExit, reason):
                        self.replay_cli(tmp, args)

    def test_the_ceiling_counts_every_session_at_its_cap(self):
        items = [scenario(caps={"max_user_turns": 5, "max_agent_turns_per_user_turn": 7, "max_cost_usd_hint": h})
                 for h in (15.0, 12.0, 15.0)]
        self.assertEqual(SESSION.ceiling_usd(items, 3, 3, None, 0.25), 378.75)
        self.assertEqual(SESSION.ceiling_usd(items, 1, 1, 10.0), 30.0)


if __name__ == "__main__":
    unittest.main()
