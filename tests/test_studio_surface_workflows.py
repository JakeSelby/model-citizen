# SPDX-License-Identifier: MIT
"""The Workflows surface: Workflow-tool runs, agents per run and cost (AH-S374, #1326)."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
import test_studio_surface_roles as role_tests  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import roles, workflows  # noqa: E402

NOW = base.NOW
days_ago = base.days_ago


def workflow_row(when, run, **extra):
    row = role_tests.agent_row(when, "workflow-subagent", workflow=run, unconfined=True,
                               budget=None, budget_tool_calls=None)
    row.update(extra)
    return row


class WorkflowsSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)

    def answer(self):
        return periods.efficacy_rows("workflows", home=self.home.root, now=NOW)

    def test_runs_are_distinct_workflow_ids_with_their_agents_and_cost(self):
        rows = [workflow_row(days_ago(5), "wf_a"), workflow_row(days_ago(5), "wf_a"),
                workflow_row(days_ago(5), "wf_a"), workflow_row(days_ago(2), "wf_b")]
        self.home.usage(rows + [role_tests.agent_row(days_ago(1)), base.usage_row(days_ago(1))])
        answer = self.answer()
        [row] = answer["rows"]
        self.assertEqual((row["item_id"], row["item_label"], row["setting_label"]),
                         (workflows.UNIT, workflows.LABEL, workflows.SETTING))
        metrics = row["metrics"]
        self.assertEqual((metrics["runs"], metrics["agents"], metrics["agents_per_run"]),
                         (2, 4, 2.0))
        each = role_tests.price(rows[0])
        self.assertIsNotNone(each)
        self.assertEqual(metrics["cost"], roles.usd(each * 4))
        self.assertEqual(metrics["cost_per_run"], "%s · %s" % (roles.usd(each),
                                                                roles.usd(each * 3)))
        self.assertEqual(metrics["unpriced_runs"], 0)

    def test_a_run_with_an_unpriced_agent_is_unpriced_whole(self):
        self.home.usage([workflow_row(days_ago(3), "wf_a"),
                         workflow_row(days_ago(3), "wf_a", model="<synthetic>"),
                         workflow_row(days_ago(2), "wf_b")])
        metrics = self.answer()["rows"][0]["metrics"]
        self.assertEqual((metrics["runs"], metrics["unpriced_runs"]), (2, 1))
        self.assertEqual(metrics["cost"], roles.usd(role_tests.price(workflow_row(NOW, "wf_b"))))

    def test_one_period_with_the_note(self):
        self.home.usage([workflow_row(days_ago(20), "wf_a"), workflow_row(days_ago(2), "wf_b")])
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        self.assertEqual(len(answer["rows"]), 1)
        self.assertIsNone(answer["rows"][0]["period"]["end"])

    def test_no_workflow_rows_answer_no_items(self):
        self.home.usage([role_tests.agent_row(days_ago(2))])
        self.assertEqual(self.answer(), {"surface": "workflows", "rows": [], "note": periods.NOTE})


class WorkflowsRouteTests(base.studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [workflow_row(now - dt.timedelta(days=2), run) for run in ("wf_a", "wf_a", "wf_b")]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def test_the_route_answers_workflow_periods(self):
        status, _headers, body = self.request("GET", base.ROUTE + "?surface=workflows",
                                              {"Cookie": self.cookie_value})
        self.assertEqual(status, 200, body)
        document = json.loads(body)
        self.assertEqual((document["surface"], document["note"]), ("workflows", periods.NOTE))
        [row] = document["rows"]
        self.assertEqual((row["metrics"]["runs"], row["metrics"]["agents"]), (2, 3))
        self.assertEqual((row["status"], row["measuring_day"]), ("measuring", 3))

    def test_anonymous_is_refused(self):
        status, _headers, body = self.request("GET", base.ROUTE + "?surface=workflows")
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))


if __name__ == "__main__":
    unittest.main()
