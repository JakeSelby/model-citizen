# SPDX-License-Identifier: MIT
"""A Workflow-tool agent's finished line in the usage feed carries no soft budget.

The Workflow tool launches its agents itself: no spawn hook routed them and no brief budgeted them,
so the usage ledger records one as `unconfined` with both budgets null. The feed used to price the
same agent against the budget of whatever role its type named, and could call it over a budget it
never had. The stop now classifies the agent from the transcript path it was handed, by the
ledger's own rule, before the path is redacted for the journal, so a transcript outside this home
or reached through a symlink is classified the same as one under it. Every case here also asks the
ledger about the same file and requires the two to agree.

Run: python3 -m unittest discover tests
"""
import importlib.util
import os
import shutil
import unittest
from pathlib import Path

from test_usage_feed import HOOK, Fixture, MEASURE, append, assistant, write

#: Twice the default variant's `gatherer` budget of 8,500 output tokens and 15 tool calls.
OVER = [("w1", 17000, tuple("t%d" % i for i in range(20)))]
UNBUDGETED = "usage-feed: gatherer finished at 17,000 output tokens and 20 tool calls"
BUDGETED = UNBUDGETED + " — over budget 2.0× its budget of 8,500 / 15"


def load_log():
    spec = importlib.util.spec_from_file_location(
        "harness_usage_log_workflow", str(HOOK.with_name("usage-log.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOG = load_log()


class WorkflowAgentLines(Fixture):
    def reported(self):
        append(self.transcript, [assistant("m1", 10)])
        return [line for line in self.submit() if "finished" in line]

    def elsewhere(self, agent_id, *parts):
        """A gatherer transcript outside this home, as under a `CLAUDE_CONFIG_DIR` elsewhere."""
        path = Path(self.tmp.name, "elsewhere", "projects", "p", self.SESSION, "subagents",
                    *parts) / ("agent-" + agent_id + ".jsonl")
        write(path, [assistant(mid, output, tools) for mid, output, tools in OVER])
        path.with_name(path.stem + ".meta.json").write_text('{"agentType": "gatherer"}')
        return path

    def assertLine(self, agent_id, path, expected):
        """The feed's line for a stop handed `path`, and the ledger's verdict on the same file."""
        self.stop(agent_id, agent_type="gatherer", agent_transcript_path=str(path))
        self.assertEqual(self.reported(), [expected])
        row = LOG._agent_row(Path(os.path.realpath(str(path))))
        self.assertEqual(bool(row.get("unconfined")), expected == UNBUDGETED, row)

    def test_a_workflow_agent_named_for_a_budgeted_role_is_unbudgeted(self):
        self.agent("www", "gatherer", OVER, workflow="wf_1")
        self.assertLine("www", self.agent_path("www", "wf_1"), UNBUDGETED)
        self.assertIs(self.journal()[-1]["workflow"], True)

    def test_a_workflow_agent_outside_this_home_is_unbudgeted(self):
        # `redact` journals no path at all for this one, so only the stop-time flag can tell.
        path = self.elsewhere("wxx", "workflows", "wf_4")
        self.assertLine("wxx", path, UNBUDGETED)
        self.assertEqual(self.journal()[-1]["path"], "")

    def test_a_workflow_agent_reached_through_a_symlink_is_unbudgeted(self):
        # A home under /var handed a path under /private/var: the prefix test fails on the text.
        self.agent("wyy", "gatherer", OVER, workflow="wf_5")
        link = Path(self.tmp.name, "linked-home")
        os.symlink(str(self.home), str(link))
        path = link / self.agent_path("wyy", "wf_5").relative_to(self.home)
        self.assertLine("wyy", path, UNBUDGETED)
        self.assertEqual(self.journal()[-1]["path"], "")

    def test_any_wf_parent_is_a_workflow_agent_as_the_ledger_rules(self):
        path = self.elsewhere("wzz", "wf_6")
        self.assertLine("wzz", path, UNBUDGETED)

    def test_a_workflow_agent_found_by_the_fallback_lookup_is_unbudgeted(self):
        # No transcript path in the payload: the stop finds the file one level deeper itself.
        self.agent("wvv", "gatherer", OVER, workflow="wf_2")
        self.stop("wvv", agent_type="gatherer", agent_transcript_path="")
        self.assertIn("/workflows/wf_2/", self.journal()[-1]["path"])
        self.assertEqual(self.reported(), [UNBUDGETED])

    def test_an_agent_tool_spawn_of_the_same_role_keeps_its_budget_line(self):
        self.agent("sss", "gatherer", OVER)
        self.assertLine("sss", self.agent_path("sss"), BUDGETED)
        self.assertNotIn("workflow", self.journal()[-1])

    def test_an_agent_tool_spawn_outside_this_home_keeps_its_budget_line(self):
        self.assertLine("stt", self.elsewhere("stt"), BUDGETED)

    def test_the_measure_line_still_follows_an_unbudgeted_figure(self):
        self.agent("wuu", "gatherer", [("u1", 400, ("t1",))], workflow="wf_3")
        self.stop("wuu", agent_type="gatherer",
                  agent_transcript_path=str(self.agent_path("wuu", "wf_3")))
        append(self.transcript, [assistant("m1", 10)])
        self.assertEqual(self.submit()[-1], MEASURE)

    def test_a_ledger_that_will_not_import_leaves_the_budget_line(self):
        # The feed never fails over an import; without the ledger's rule it prices as before.
        hooks = Path(self.tmp.name, "hooks")
        hooks.mkdir()
        shutil.copy(str(HOOK), str(hooks / HOOK.name))
        (hooks / "usage-log.py").write_text("raise ImportError('gone')\n", encoding="utf-8")
        feed_spec = importlib.util.spec_from_file_location("harness_feed_no_log",
                                                           str(hooks / HOOK.name))
        feed = importlib.util.module_from_spec(feed_spec)
        feed_spec.loader.exec_module(feed)
        self.assertFalse(feed.workflow_agent(str(self.agent_path("wqq", "wf_7"))))


if __name__ == "__main__":
    unittest.main()
