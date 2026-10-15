# SPDX-License-Identifier: MIT
"""Subagent, worker and `--by role` rows in the usage log.

A session's own transcript never held its subagents' tokens, so every total was short by
whatever delegation cost. These tests hold the two halves together: the session row sums its
subagents, and the per-agent rows attribute the same tokens without becoming sessions.

Run: python3 -m unittest discover tests
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from isolation import without_config_dir

from test_usage import REPO, _load, harness, usage_log

PRICING = _load("roles_pricing", REPO / "policy" / "hooks" / "pricing.py")

# Cut from a real `codex exec --json` worker run (codex-cli 0.155.0-alpha.16.4) on 2026-10-15: the
# event shapes and the `turn.completed` totals are as recorded, the message and command text are
# replaced and all but one of its 27 command executions dropped.
CODEX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"


def adapter(case, runtime):
    sys.path.insert(0, str(REPO / "lib"))
    case.addCleanup(lambda: sys.path.remove(str(REPO / "lib")))
    from harness_core import workers
    return workers.adapter(REPO, runtime)


def recorded_codex_usage(case, name="worker-exec-json.jsonl"):
    run_dir = Path(tempfile.mkdtemp())
    case.addCleanup(lambda: (run_dir / "stdout.log").unlink() or run_dir.rmdir())
    (run_dir / "stdout.log").write_bytes((CODEX_FIXTURES / name).read_bytes())
    return adapter(case, "codex").usage(run_dir, run_dir)

HOOK = REPO / "claude" / "hooks" / "usage-log.py"
STAMPS = [time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 600 + i * 60))
          for i in range(6)]


def message(mid, stamp, output, tools=(), model="model-a"):
    content = [{"type": "tool_use", "id": t, "name": "Read", "input": {}} for t in tools]
    return {"type": "assistant", "sessionId": "s-1", "cwd": "", "timestamp": stamp,
            "effort": "medium",
            "message": {"id": mid, "model": model, "content": content,
                        "usage": {"input_tokens": 1, "output_tokens": output,
                                  "cache_read_input_tokens": 10,
                                  "cache_creation_input_tokens": 2}}}


def write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


class Fixture(unittest.TestCase):
    """One session transcript with two subagent transcripts beside it, as Claude Code lays them out."""

    AGENTS = {"aaa": {"type": "gatherer", "model": "model-haiku", "depth": 1,
                      "messages": [("a1", 100, ("t1", "t2")), ("a2", 300, ("t3",))]},
              "bbb": {"type": "reviewer", "model": "model-opus", "depth": 2,
                      "messages": [("b1", 700, ("t4",))]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self.restore)
        self.project = self.home / ".claude" / "projects" / "a-repo"
        self.transcript = write(self.project / "s-1.jsonl", [
            {"type": "user", "sessionId": "s-1", "cwd": "", "timestamp": STAMPS[0]},
            message("m1", STAMPS[1], 50),
            message("m2", STAMPS[2], 50, model="model-b"),
        ])
        subagents = self.project / "s-1" / "subagents"
        for agent_id, spec in self.AGENTS.items():
            write(subagents / ("agent-%s.jsonl" % agent_id),
                  [message(mid, STAMPS[3], out, tools, spec["model"])
                   for mid, out, tools in spec["messages"]])
            (subagents / ("agent-%s.meta.json" % agent_id)).write_text(json.dumps(
                {"agentType": spec["type"], "description": "a brief", "toolUseId": "tu-" + agent_id,
                 "spawnDepth": spec["depth"], "model": spec["model"]}), encoding="utf-8")

    def restore(self):
        if self._home is not None:
            os.environ["HOME"] = self._home

    @property
    def state(self):
        return self.home / ".local/state/agent-harness/usage.jsonl"

    def rows(self):
        return [json.loads(line) for line in self.state.read_text().splitlines()]

    def record(self):
        """The SessionEnd worker, run the way the hook runs it: a subprocess over a temp HOME."""
        out = subprocess.run([sys.executable, str(HOOK), "--worker", str(self.transcript), "s-1", ""],
                             capture_output=True, text=True, env=dict(without_config_dir(), HOME=str(self.home)),
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return self.rows()

    def report(self, by="day", days=30, rescan=False, rules=False):
        args = harness.argparse.Namespace(days=days, by=by, rules=rules, rescan=rescan)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(harness.cmd_usage(args), 0)
        return buf.getvalue()


class SubagentRows(Fixture):
    def test_the_session_total_is_its_own_tokens_plus_its_subagents(self):
        rows = self.record()
        session = [r for r in rows if r["kind"] == "session"]
        self.assertEqual(len(session), 1)
        # Two parent messages at 50, two subagent messages at 100 and 300, one at 700.
        self.assertEqual(session[0]["output"], 50 + 50 + 100 + 300 + 700)
        self.assertEqual(session[0]["input"], 5)
        self.assertEqual(session[0]["cache_read"], 50)
        self.assertEqual(session[0]["cache_write"], 10)
        self.assertEqual(session[0]["turns"], 2)
        self.assertEqual(session[0]["subagents"], 2)

    def test_each_subagent_gets_a_row_naming_its_type_model_and_tool_calls(self):
        rows = {r["agent_id"]: r for r in self.record() if r["kind"] == "subagent"}
        self.assertEqual(sorted(rows), ["aaa", "bbb"])
        self.assertEqual(rows["aaa"]["agent_type"], "gatherer")
        self.assertEqual(rows["aaa"]["model"], "model-haiku")
        self.assertEqual(rows["aaa"]["tool_calls"], 3)
        self.assertEqual(rows["aaa"]["output"], 400)
        self.assertEqual(rows["aaa"]["spawn_depth"], 1)
        self.assertEqual(rows["aaa"]["effort"], "medium")
        self.assertIs(rows["aaa"]["rerouted"], False)
        self.assertEqual(rows["aaa"]["session_id"], "s-1")
        self.assertEqual(rows["bbb"]["agent_type"], "reviewer")
        self.assertEqual(rows["bbb"]["tool_calls"], 1)
        self.assertEqual(rows["bbb"]["spawn_depth"], 2)

    def test_a_subagent_row_holds_counts_and_no_text(self):
        for row in (r for r in self.record() if r["kind"] == "subagent"):
            self.assertEqual(set(row) & {"description", "prompt", "text", "input_text"}, set())

    def test_a_repeated_message_is_summed_once_for_a_subagent_too(self):
        path = self.project / "s-1" / "subagents" / "agent-aaa.jsonl"
        doubled = json.loads(path.read_text().splitlines()[0])
        path.write_text(path.read_text() + json.dumps(doubled) + "\n", encoding="utf-8")
        rows = {r["agent_id"]: r for r in self.record() if r["kind"] == "subagent"}
        self.assertEqual(rows["aaa"]["output"], 400)
        self.assertEqual(rows["aaa"]["tool_calls"], 3)

    def streamed(self, path, figures):
        """One message id written as several records, as a streamed response is written.

        The early records carry a partial `output_tokens`; only the last carries the whole
        response. The other three fields repeat unchanged and must not multiply.
        """
        write(path, [{"type": "assistant", "sessionId": "s-1", "cwd": "", "timestamp": STAMPS[3],
                      "message": {"id": "one", "model": "model-a", "content": [],
                                  "usage": {"input_tokens": 12, "output_tokens": figure,
                                            "cache_read_input_tokens": 340,
                                            "cache_creation_input_tokens": 56}}}
                     for figure in figures])

    def test_a_message_id_counts_its_final_output_figure_not_its_first(self):
        self.streamed(self.project / "s-1" / "subagents" / "agent-aaa.jsonl", [8, 120, 900])
        row = [r for r in self.record() if r.get("agent_id") == "aaa"][0]
        self.assertEqual(row["output"], 900)
        self.assertEqual((row["input"], row["cache_read"], row["cache_write"]), (12, 340, 56))

    def test_a_truncated_or_reordered_tail_cannot_lower_the_figure(self):
        self.streamed(self.project / "s-1" / "subagents" / "agent-aaa.jsonl", [8, 900, 120])
        row = [r for r in self.record() if r.get("agent_id") == "aaa"][0]
        self.assertEqual(row["output"], 900)

    def test_the_session_scan_takes_the_final_figure_the_same_way(self):
        self.streamed(self.transcript, [8, 120, 900])
        session = [r for r in self.record() if r["kind"] == "session"][0]
        self.assertEqual(session["output"], 900 + 400 + 700)
        self.assertEqual(session["input"], 12 + 3)
        self.assertEqual(session["turns"], 1)

    def test_a_subagent_transcript_without_its_meta_file_still_counts(self):
        (self.project / "s-1" / "subagents" / "agent-bbb.meta.json").unlink()
        rows = {r["agent_id"]: r for r in self.record() if r["kind"] == "subagent"}
        self.assertEqual(rows["bbb"]["agent_type"], "unknown")
        self.assertEqual(rows["bbb"]["model"], "model-opus")
        self.assertIsNone(rows["bbb"]["workflow"])


class OneSourcePerMessage(Fixture):
    """A delegated token is the session's once, however many files record the message."""

    def test_a_sidechain_line_and_a_subagent_file_of_the_same_ids_count_once(self):
        """Older Claude Code wrote a subagent's turns into the session file as sidechain lines;
        newer Claude Code writes them to the agent's own file. A transcript carrying both would
        pay for every delegated token twice if the two totals were added."""
        plain = [r for r in self.record() if r["kind"] == "session"][0]
        sidechain = []
        for spec in self.AGENTS.values():
            for mid, out, tools in spec["messages"]:
                entry = message(mid, STAMPS[4], out, tools, spec["model"])
                entry["isSidechain"] = True
                sidechain.append(entry)
        write(self.transcript, [json.loads(ln) for ln in self.transcript.read_text().splitlines()]
              + sidechain)
        doubled = [r for r in self.record() if r["kind"] == "session"][0]
        self.assertEqual(doubled["output"], plain["output"])
        self.assertEqual(doubled["input"], plain["input"])
        self.assertEqual(doubled["cache_read"], plain["cache_read"])
        # The agent's own row still carries its own file's total, whole.
        rows = {r["agent_id"]: r for r in self.record() if r["kind"] == "subagent"}
        self.assertEqual(rows["aaa"]["output"], 400)

    def test_an_unidentified_line_in_two_files_is_two_messages(self):
        """A line with no message id cannot be matched across files, so it is never merged."""
        anon = {"type": "assistant", "sessionId": "s-1", "cwd": "", "timestamp": STAMPS[2],
                "message": {"model": "model-a", "content": [],
                            "usage": {"output_tokens": 500, "input_tokens": 0,
                                      "cache_read_input_tokens": 0,
                                      "cache_creation_input_tokens": 0}}}
        write(self.transcript, [anon])
        write(self.project / "s-1" / "subagents" / "agent-aaa.jsonl", [anon])
        (self.project / "s-1" / "subagents" / "agent-bbb.jsonl").unlink()
        session = [r for r in self.record() if r["kind"] == "session"][0]
        self.assertEqual(session["output"], 1000)


class WorkflowAgents(Fixture):
    """The Workflow tool nests its agents a directory deeper; the walk is recursive for it."""

    def nested(self, with_meta=True):
        directory = self.project / "s-1" / "subagents" / "workflows" / "wf_7f3"
        write(directory / "agent-ccc.jsonl", [message("c1", STAMPS[4], 250, ("t9",))])
        if with_meta:
            (directory / "agent-ccc.meta.json").write_text(json.dumps(
                {"agentType": "researcher", "spawnDepth": 2, "model": "model-haiku"}),
                encoding="utf-8")

    def test_a_nested_workflow_agent_is_found_and_names_its_workflow(self):
        self.nested()
        row = [r for r in self.record() if r.get("agent_id") == "ccc"][0]
        self.assertEqual(row["agent_type"], "researcher")
        self.assertEqual(row["workflow"], "wf_7f3")
        self.assertEqual((row["output"], row["tool_calls"]), (250, 1))

    def test_its_tokens_reach_the_session_total(self):
        before = [r for r in self.record() if r["kind"] == "session"][0]["output"]
        self.nested()
        after = [r for r in self.record() if r["kind"] == "session"][0]["output"]
        self.assertEqual(after - before, 250)

    def test_a_nested_agent_with_no_meta_file_is_recorded_as_unknown(self):
        self.nested(with_meta=False)
        row = [r for r in self.record() if r.get("agent_id") == "ccc"][0]
        self.assertEqual(row["agent_type"], "unknown")
        self.assertEqual(row["workflow"], "wf_7f3")
        self.assertEqual(row["output"], 250)


class RescanRows(Fixture):
    def test_a_rescan_is_idempotent_and_makes_no_pseudo_session_row(self):
        self.assertEqual(usage_log.rescan(30), 1)
        first = self.rows()
        self.assertEqual(usage_log.rescan(30), 1)
        self.assertEqual(self.rows(), first)
        self.assertEqual([r["kind"] for r in first].count("session"), 1)
        self.assertEqual({r["session_id"] for r in first}, {"s-1"})

    def test_an_agent_transcript_loose_in_a_project_directory_is_never_a_session(self):
        """The glob cannot reach the real layout, so the guard is what holds if it moves."""
        write(self.project / "agent-ccc.jsonl", [message("c1", STAMPS[4], 10)])
        usage_log.rescan(30)
        rows = self.rows()
        self.assertEqual({r["session_id"] for r in rows}, {"s-1"})
        self.assertNotIn("ccc", [r.get("agent_id") for r in rows])
        self.assertEqual([r["kind"] for r in rows].count("session"), 1)

    def test_a_rescan_of_many_transcripts_rewrites_the_file_once(self):
        """Upserting per transcript took the lock and rewrote the whole file each time."""
        for i in range(2, 8):
            entry = message("m%d" % i, STAMPS[2], 10)
            entry["sessionId"] = "s-%d" % i
            write(self.project / ("s-%d.jsonl" % i), [entry])
        writes = []
        original = usage_log.os.replace
        usage_log.os.replace = lambda src, dst: (writes.append(dst), original(src, dst))[1]
        try:
            self.assertEqual(usage_log.rescan(30), 7)
        finally:
            usage_log.os.replace = original
        self.assertEqual(len(writes), 1)
        self.assertEqual(len([r for r in self.rows() if r["kind"] == "session"]), 7)

    def test_a_row_written_before_kind_existed_reports_and_a_rescan_upgrades_it(self):
        legacy = {"session_id": "s-1", "runtime": "claude-code", "repo": "a-repo",
                  "models": ["model-a"], "ended": STAMPS[5], "input": 5, "output": 100,
                  "cache_read": 50, "cache_write": 10, "turns": 2, "subagents": 0}
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        self.assertIn("a-repo", self.report(by="repo"))
        self.assertIn("100", self.report(by="repo"))
        usage_log.rescan(30)
        sessions = [r for r in self.rows() if (r.get("kind") or "session") == "session"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["kind"], "session")
        self.assertEqual(sessions[0]["output"], 1200)


class RoleReport(Fixture):
    def write_rows(self, rows):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def agent(self, i, agent_type, output, tool_calls):
        return {"kind": "subagent", "runtime": "claude-code", "session_id": "s-1",
                "agent_id": "a%d" % i, "agent_type": agent_type, "model": "model-a",
                "effort": "medium", "input": 1, "output": output, "cache_read": 0,
                "cache_write": 0, "tool_calls": tool_calls, "spawn_depth": 1,
                "rerouted": False, "started": STAMPS[0], "ended": STAMPS[5]}

    def test_percentiles_are_the_nearest_rank_of_each_sample(self):
        # Ten values, so nearest rank puts p50 at the 5th, p75 at the 8th and p90 at the 9th.
        # The two dollar cells are dashes because `model-a` is in no price table; dollars over
        # a priced sample are in tests/test_usage_prices.py. The two return cells are dashes
        # because these rows predate the return measurement: tests/test_return_path_measure.py.
        self.write_rows([self.agent(i, "gatherer", (i + 1) * 100, i + 1) for i in range(10)]
                        + [self.agent(99, "reviewer", 4242, 7)])
        lines = {ln.split()[0]: ln.split() for ln in self.report(by="role").splitlines()[2:]}
        # Both samples are under 30 runs, so both are marked as the small samples they are.
        self.assertEqual(lines["gatherer"][1:],
                         ["10", "500", "800", "900", "-", "-", "5", "8", "9", "-", "-", "-", "n<30"])
        self.assertEqual(lines["reviewer"][1:],
                         ["1", "4,242", "4,242", "4,242", "-", "-", "7", "7", "7", "-", "-", "-", "n<30"])

    def test_a_run_whose_runtime_reported_no_counts_is_named_not_averaged_as_zero(self):
        blind = dict(self.agent(3, "gatherer", 400, 4), output=None, tool_calls=None)
        self.write_rows([self.agent(i, "gatherer", 100, 1) for i in range(2)] + [blind])
        row = [ln for ln in self.report(by="role").splitlines() if ln.startswith("gatherer")][0]
        self.assertEqual(row.split()[1:], ["3", "100", "100", "100", "-", "-", "1", "1", "1", "-", "-", "1", "n<30"])

    def test_unmeasured_counts_the_runs_missing_a_tool_call_figure(self):
        """It is a count of what the column above it could not be taken over, so a run with
        tokens but no tool-call figure is named even though its output was measured."""
        self.write_rows([self.agent(0, "gatherer", 100, 1),
                         dict(self.agent(1, "gatherer", 200, 0), tool_calls=None),
                         dict(self.agent(2, "gatherer", 300, 0), tool_calls=None)])
        row = [ln for ln in self.report(by="role").splitlines() if ln.startswith("gatherer")][0]
        self.assertEqual(row.split()[1:], ["3", "200", "300", "300", "-", "-", "1", "1", "1", "-", "-", "2", "n<30"])

    def test_a_worker_row_groups_under_its_role_beside_a_subagent(self):
        worker = {"kind": "worker", "runtime": "codex", "session_id": "w1", "agent_id": "w1",
                  "agent_type": "reviewer", "model": "model-x", "effort": "high", "input": 9,
                  "output": 250, "cache_read": 0, "cache_write": 0, "tool_calls": None,
                  "spawn_depth": 1, "rerouted": False, "started": STAMPS[0], "ended": STAMPS[5]}
        self.write_rows([self.agent(0, "reviewer", 150, 2), worker])
        row = [ln for ln in self.report(by="role").splitlines() if ln.startswith("reviewer")][0]
        # Two runs of one role across two runtimes: p50 is the lower, p75 the worker's.
        self.assertEqual(row.split()[1:4], ["2", "150", "250"])

    def test_an_empty_window_says_so_rather_than_printing_an_empty_table(self):
        self.write_rows([])
        self.assertIn("no subagent or worker runs recorded", self.report(by="role"))

    def test_the_token_groupings_never_count_a_subagent_row(self):
        self.record()
        for grouping in ("day", "repo", "model"):
            total = [ln for ln in self.report(by=grouping).splitlines() if ln.startswith("TOTAL")][0]
            self.assertEqual(total.split()[1:4], ["1", "5", "1,200"])

    def test_the_token_groupings_do_count_a_worker_row(self):
        """A role-run worker has no session row of its own, so leaving workers out of the
        totals would hide their spend in every report there is."""
        self.write_rows([{"kind": "session", "runtime": "claude-code", "session_id": "s-1",
                          "repo": "alpha", "models": ["model-a"], "ended": STAMPS[5],
                          "input": 10, "output": 100, "cache_read": 0, "cache_write": 0},
                         {"kind": "worker", "runtime": "codex", "session_id": "w1",
                          "agent_id": "w1", "agent_type": "reviewer", "repo": "alpha",
                          "ended": STAMPS[5], "input": 5, "output": 250, "cache_read": 0,
                          "cache_write": 0, "tool_calls": None},
                         self.agent(0, "gatherer", 999, 3)])
        total = [ln for ln in self.report(by="repo").splitlines() if ln.startswith("TOTAL")][0]
        self.assertEqual(total.split()[1:4], ["2", "15", "350"])

    def test_rules_by_role_is_refused_rather_than_quietly_regrouped(self):
        self.write_rows([self.agent(0, "gatherer", 100, 1)])
        args = harness.argparse.Namespace(days=30, by="role", rules=True, rescan=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(harness.cmd_usage(args), 2)
        self.assertEqual(buf.getvalue(), "")


class WorkerIngest(Fixture):
    """`harness role run` writes a status record; the log reads its totals, never a guess."""

    def status(self, **extra):
        run = self.home / ".local/state/agent-harness/workers" / ("f" * 32)
        run.mkdir(parents=True)
        record = {"schema_version": 1, "id": run.name, "role": "reviewer", "runtime": "codex",
                  "model": "model-x", "effort": "high", "workspace": "/tmp/project",
                  "status": "completed", "started_at": time.time() - 120,
                  "finished_at": time.time() - 60}
        record.update(extra)
        (run / "status.json").write_text(json.dumps(record), encoding="utf-8")
        return record

    def test_a_worker_with_reported_totals_becomes_a_row_under_its_role(self):
        self.status(usage={"input": 9, "output": 250, "cache_read": 4, "cache_write": 0})
        rows = [r for r in self.record() if r["kind"] == "worker"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["agent_type"], rows[0]["runtime"]), ("reviewer", "codex"))
        self.assertEqual((rows[0]["output"], rows[0]["cache_read"]), (250, 4))
        self.assertEqual(rows[0]["repo"], "project")
        self.assertIsNone(rows[0]["tool_calls"])
        self.assertTrue(rows[0]["ended"] > rows[0]["started"])

    def test_a_worker_whose_runtime_reported_nothing_keeps_unknown_totals(self):
        self.status()
        row = [r for r in self.record() if r["kind"] == "worker"][0]
        self.assertIsNone(row["output"])
        self.assertIsNone(row["input"])

    def test_a_run_that_did_not_complete_is_not_recorded(self):
        """A timed-out or failed run has no total worth comparing against another role's."""
        self.status(status="timed-out", usage={"output": 250})
        self.assertEqual([r for r in self.record() if r["kind"] == "worker"], [])

    def test_a_run_older_than_the_window_is_not_even_opened(self):
        record = self.status(usage={"output": 250})
        path = self.home / ".local/state/agent-harness/workers" / record["id"] / "status.json"
        stale = time.time() - 60 * 86400
        os.utime(path, (stale, stale))
        self.assertEqual([r for r in self.record() if r["kind"] == "worker"], [])

    def test_an_unusable_timestamp_falls_back_to_the_file_rather_than_going_unreportable(self):
        """A row the report cannot place in any window is a row nobody ever sees."""
        self.status(started_at=None, finished_at="not a time", usage={"output": 250})
        row = [r for r in self.record() if r["kind"] == "worker"][0]
        self.assertTrue(row["ended"] > STAMPS[0], row["ended"])
        self.assertEqual(row["started"], row["ended"])
        self.assertIn("reviewer", self.report(by="role"))

    def test_a_codex_worker_recorded_from_its_exec_stream_is_a_priced_row(self):
        """Every completed Codex worker went unpriced while the adapter missed `turn.completed`."""
        self.status(model="gpt-5.6-sol", usage=recorded_codex_usage(self))
        row = [r for r in self.record() if r["kind"] == "worker"][0]
        self.assertEqual((row["input"], row["cache_read"], row["output"]), (85288, 1128064, 16014))
        cost = PRICING.row_cost(row, PRICING.shipped_prices())
        self.assertIsNotNone(cost)
        self.assertGreater(cost, 0)

    def test_a_worker_row_never_displaces_a_session_of_the_same_runtime(self):
        self.status(usage={"output": 250})
        rows = self.record()
        self.assertEqual(sorted(r["kind"] for r in rows),
                         ["session", "subagent", "subagent", "worker"])


class AdapterUsage(unittest.TestCase):
    """Each worker adapter reads its own runtime's report of what the run cost."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)

    def adapter(self, runtime):
        return adapter(self, runtime)

    def test_the_claude_envelope_yields_the_four_token_fields(self):
        (self.run_dir / "stdout.log").write_text(json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": "done",
             "usage": {"input_tokens": 11, "output_tokens": 22,
                       "cache_read_input_tokens": 33, "cache_creation_input_tokens": 44}}))
        self.assertEqual(self.adapter("claude-code").usage(self.run_dir, self.run_dir),
                         {"input": 11, "output": 22, "cache_read": 33, "cache_write": 44})

    def test_an_envelope_without_a_usage_object_reports_nothing_rather_than_zeroes(self):
        (self.run_dir / "stdout.log").write_text(json.dumps({"type": "result", "result": "done"}))
        self.assertEqual(self.adapter("claude-code").usage(self.run_dir, self.run_dir), {})

    def test_the_recorded_exec_stream_yields_its_turn_totals_net_of_the_cached_share(self):
        self.assertEqual(recorded_codex_usage(self),
                         {"input": 1213352 - 1128064, "cache_read": 1128064, "output": 16014,
                          "cache_write": 0})

    def test_a_recorded_failed_turn_reports_nothing_rather_than_zeroes(self):
        self.assertEqual(recorded_codex_usage(self, "worker-exec-json-failed.jsonl"), {})

    def test_a_legacy_token_count_stream_takes_the_last_cumulative_snapshot(self):
        (self.run_dir / "stdout.log").write_text("".join(json.dumps(e) + "\n" for e in [
            {"msg": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}}}},
            {"msg": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 300, "cached_input_tokens": 200, "output_tokens": 30}}}},
            {"msg": {"type": "agent_message", "message": "done"}},
        ]))
        self.assertEqual(self.adapter("codex").usage(self.run_dir, self.run_dir),
                         {"input": 100, "cache_read": 200, "output": 30})

    def test_a_stream_with_no_token_event_reports_nothing(self):
        (self.run_dir / "stdout.log").write_text('{"msg": {"type": "agent_message"}}\n')
        self.assertEqual(self.adapter("codex").usage(self.run_dir, self.run_dir), {})


if __name__ == "__main__":
    unittest.main()
