# SPDX-License-Identifier: MIT
"""A Workflow-tool agent's finished line in the usage feed carries no soft budget.

The Workflow tool launches its agents itself: no spawn hook routed them and no brief budgeted them,
so the usage ledger records one as `unconfined` with both budgets null. The feed used to price the
same agent against the budget of whatever role its type named, and could call it over a budget it
never had. It now keys on the transcript sitting under `subagents/workflows/wf_*/`, as the ledger
does, while an agent spawned through the Agent tool keeps its budget clause.

Run: python3 -m unittest discover tests
"""
import unittest

from test_usage_feed import Fixture, MEASURE, append, assistant, load_feed

#: Twice the default variant's `gatherer` budget of 8,500 output tokens and 15 tool calls.
OVER = [("w1", 17000, tuple("t%d" % i for i in range(20)))]


class WorkflowAgentLines(Fixture):
    def reported(self):
        append(self.transcript, [assistant("m1", 10)])
        return [line for line in self.submit() if "finished" in line]

    def test_a_workflow_agent_named_for_a_budgeted_role_is_unbudgeted(self):
        self.agent("www", "gatherer", OVER, workflow="wf_1")
        self.stop("www", agent_type="gatherer",
                  agent_transcript_path=str(self.agent_path("www", "wf_1")))
        self.assertEqual(self.reported(), [
            "usage-feed: gatherer finished at 17,000 output tokens and 20 tool calls"])

    def test_a_workflow_agent_found_by_the_fallback_lookup_is_unbudgeted(self):
        # No transcript path in the payload: the stop finds the file one level deeper itself.
        self.agent("wvv", "gatherer", OVER, workflow="wf_2")
        self.stop("wvv", agent_type="gatherer", agent_transcript_path="")
        self.assertIn("/workflows/wf_2/", self.journal()[-1]["path"])
        lines = self.reported()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("budget", lines[0])

    def test_an_agent_tool_spawn_of_the_same_role_keeps_its_budget_line(self):
        self.agent("sss", "gatherer", OVER)
        self.stop("sss", agent_type="gatherer")
        self.assertEqual(self.reported(), [
            "usage-feed: gatherer finished at 17,000 output tokens and 20 tool calls"
            " — over budget 2.0× its budget of 8,500 / 15"])

    def test_the_measure_line_still_follows_an_unbudgeted_figure(self):
        self.agent("wuu", "gatherer", [("u1", 400, ("t1",))], workflow="wf_3")
        self.stop("wuu", agent_type="gatherer",
                  agent_transcript_path=str(self.agent_path("wuu", "wf_3")))
        append(self.transcript, [assistant("m1", 10)])
        self.assertEqual(self.submit()[-1], MEASURE)


class WorkflowTranscriptShape(unittest.TestCase):
    def setUp(self):
        self.feed = load_feed()

    def test_the_redacted_and_absolute_workflow_paths_match(self):
        for path in ("~/.claude/projects/p/s/subagents/workflows/wf_ab/agent-a1.jsonl",
                     "/h/.claude/projects/p/s/subagents/workflows/wf_ab/agent-a1.jsonl"):
            self.assertTrue(self.feed.workflow_transcript(path), path)

    def test_a_spawned_agent_or_a_missing_path_does_not(self):
        for path in ("~/.claude/projects/p/s/subagents/agent-a1.jsonl",
                     "~/.claude/projects/p/s/subagents/workflows/other/agent-a1.jsonl",
                     "", None, 7):
            self.assertFalse(self.feed.workflow_transcript(path), repr(path))

    def test_stop_line_drops_the_budget_for_a_workflow_record_only(self):
        table = {"roles": {"gatherer": {"budget_output_tokens": 100, "budget_tool_calls": 1}}}
        spawned = {"id": "a1", "type": "gatherer", "output": 400, "tool_calls": 2,
                   "path": "~/p/s/subagents/agent-a1.jsonl"}
        workflow = dict(spawned, path="~/p/s/subagents/workflows/wf_1/agent-a1.jsonl")
        self.feed.row_for = lambda _table, _type: table["roles"]["gatherer"]
        line, ratio = self.feed.stop_line(table, [1.5], workflow)
        self.assertIsNone(ratio)
        self.assertNotIn("budget", line)
        line, ratio = self.feed.stop_line(table, [1.5], spawned)
        self.assertEqual(ratio, 4.0)
        self.assertIn("over budget", line)


if __name__ == "__main__":
    unittest.main()
