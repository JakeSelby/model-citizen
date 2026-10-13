# SPDX-License-Identifier: MIT
"""The Roles surface: spawns, tier, effort, cost, budget and tier rewrites per role (AH-S374, #1326)."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import roles  # noqa: E402

NOW = base.NOW
days_ago = base.days_ago
# Hand-typed in the shape `usage-log.py` writes a subagent row, with tokens a shipped price prices.
MODEL = "claude-opus-5-5"


def agent_row(when, agent_type="builder", kind="subagent", output=1000, budget=2000, **extra):
    row = {"kind": kind, "runtime": "claude-code", "repo": "proj", "agent_type": agent_type,
           "model": MODEL, "effort": "medium", "tool_calls": 10, "workflow": None,
           "ended": base.stamp(when), "started": base.stamp(when - dt.timedelta(minutes=5)),
           "input": 1000, "output": output, "cache_read": 0, "cache_write": 0,
           "budget_output_tokens": budget, "budget_tool_calls": 50}
    row.update(extra)
    return row


def decision_row(when, answer, **fields):
    row = {"kind": "decision", "point": "tier-agent-spawns", "ts": base.stamp(when),
           "deterministic_answer": answer, "session_id": "s", "input": "brief"}
    row.update(fields)
    return row


def price(row):
    table = roles.price_table({})
    return roles.row_cost(row, table)


class RolesSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)

    def decisions(self, rows, name="decisions.jsonl"):
        (self.home.state / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def answer(self, unit=None):
        return periods.efficacy_rows("roles", unit, home=self.home.root, now=NOW)

    def by_item(self):
        return {row["item_id"]: row for row in self.answer()["rows"]}

    def test_each_role_has_spawns_tier_effort_and_cost(self):
        builds = [agent_row(days_ago(n)) for n in (9, 8, 3)]
        self.home.usage(builds + [agent_row(days_ago(2), "worker-c", effort="high")])
        found = self.by_item()
        self.assertEqual(sorted(found), ["builder", "worker-c"])
        builder = found["builder"]
        self.assertEqual(builder["setting_label"], "strong · medium effort")
        self.assertEqual(builder["item_label"], "builder")
        metrics = builder["metrics"]
        self.assertEqual((metrics["spawns"], metrics["model"], metrics["effort"]),
                         (3, MODEL, "medium"))
        each = price(builds[0])
        self.assertIsNotNone(each)
        self.assertEqual(metrics["cost"], roles.usd(each * 3))
        self.assertEqual(metrics["cost_per_spawn"], "%s · %s" % (roles.usd(each), roles.usd(each)))
        self.assertEqual(metrics["unpriced"], 0)
        self.assertEqual(found["worker-c"]["metrics"]["spawns"], 1)
        self.assertEqual(found["worker-c"]["setting_label"], "strong · high effort")

    def test_a_worker_row_counts_as_a_run_of_its_role(self):
        self.home.usage([agent_row(days_ago(4), "reviewer", kind="worker", model="unpriced-x")])
        reviewer = self.by_item()["reviewer"]["metrics"]
        self.assertEqual((reviewer["spawns"], reviewer["unpriced"]), (1, 1))
        self.assertNotIn("cost", reviewer)

    def test_over_budget_is_the_share_past_the_soft_budget(self):
        self.home.usage([agent_row(days_ago(5), output=3000), agent_row(days_ago(4)),
                         agent_row(days_ago(3), tool_calls=80), agent_row(days_ago(2)),
                         agent_row(days_ago(1), budget=None, budget_tool_calls=None)])
        self.assertEqual(self.by_item()["builder"]["metrics"]["over_budget"], "50% · 2 of 4")

    def test_a_role_without_a_budget_says_so(self):
        self.home.usage([agent_row(days_ago(2), budget=None, budget_tool_calls=None)])
        self.assertEqual(self.by_item()["builder"]["metrics"]["over_budget"], "no budget")

    def test_runtime_agents_share_one_built_in_row(self):
        self.home.usage([agent_row(days_ago(3), "Explore"), agent_row(days_ago(2), "Ampere"),
                         agent_row(days_ago(1), "general-purpose")])
        found = self.by_item()
        self.assertEqual(list(found), [roles.BUILT_IN_UNIT])
        row = found[roles.BUILT_IN_UNIT]
        self.assertEqual((row["item_label"], row["setting_label"], row["metrics"]["spawns"]),
                         (roles.BUILT_IN_LABEL, roles.BUILT_IN, 3))

    def test_workflow_agents_and_sessions_are_not_role_spawns(self):
        self.home.usage([agent_row(days_ago(3), "workflow-subagent", workflow="wf_1",
                                   unconfined=True),
                         base.usage_row(days_ago(2)), agent_row(days_ago(1))])
        found = self.by_item()
        self.assertEqual(list(found), ["builder"])
        self.assertEqual(found["builder"]["metrics"]["spawns"], 1)

    def test_tier_rewrites_from_the_decision_ledger_show_against_the_role(self):
        self.home.usage([agent_row(days_ago(5), "worker-b"), agent_row(days_ago(2))])
        self.decisions([decision_row(days_ago(4), "worker-b"),
                        decision_row(days_ago(3), "demoted", subagent_type="builder",
                                     subagent_type_requested="builder", model="sonnet",
                                     model_requested="opus"),
                        decision_row(days_ago(3), "deny", subagent_type_requested="builder"),
                        decision_row(days_ago(40), "worker-b"),
                        {"kind": "decision", "point": "grade-bash", "ts": base.stamp(days_ago(2)),
                         "deterministic_answer": "worker-b"}])
        self.decisions([decision_row(days_ago(6), "one-rung", subagent_type="worker-b")],
                       name="decisions.jsonl.1")
        found = self.by_item()
        self.assertEqual(found["worker-b"]["metrics"]["tier_rewrites"], 2)
        self.assertEqual(found["builder"]["metrics"]["tier_rewrites"], 1)
        # The earliest rewrite precedes the role's first row; the period still holds it.
        self.assertEqual(found["worker-b"]["period"]["start"], base.stamp(days_ago(6))[:19] + "Z")

    def test_no_setting_boundary_answers_one_period_per_role_with_the_note(self):
        self.home.usage([agent_row(days_ago(20), model="claude-opus-5", effort="high"),
                         agent_row(days_ago(3)), agent_row(days_ago(2))])
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        self.assertEqual(len(answer["rows"]), 1)
        row = answer["rows"][0]
        self.assertEqual((row["period"]["end"], row["status"]), (None, "measured"))
        self.assertEqual(row["metrics"]["model"], MODEL + " +1")
        self.assertEqual(row["metrics"]["effort"], "medium +1")

    def test_no_delegated_rows_answer_no_roles(self):
        self.home.usage([base.usage_row(days_ago(2))])
        self.assertEqual(self.answer(), {"surface": "roles", "rows": [], "note": periods.NOTE})

    def test_ledgers_are_only_read(self):
        self.home.usage([agent_row(days_ago(2))])
        self.decisions([decision_row(days_ago(2), "worker-b")])
        before = {p.name: p.read_bytes() for p in self.home.state.iterdir()}
        self.answer()
        self.assertEqual({p.name: p.read_bytes() for p in self.home.state.iterdir()}, before)


class RolesRouteTests(base.studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [agent_row(now - dt.timedelta(days=n)) for n in (12, 2)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_the_route_answers_role_periods(self):
        status, headers, body = self.get(base.ROUTE + "?surface=roles&days=30")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual(set(document), {"surface", "rows", "note"})
        self.assertEqual(document["surface"], "roles")
        [row] = document["rows"]
        self.assertEqual((row["item_id"], row["setting_label"], row["metrics"]["spawns"]),
                         ("builder", "strong · medium effort", 2))
        self.assertIsNone(row["period"]["end"])
        self.assertTrue(row["period"]["start"].endswith("Z"))

    def test_anonymous_and_foreign_hosts_are_refused(self):
        status, _headers, body = self.get(base.ROUTE + "?surface=roles", cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))
        status, _headers, _body = self.request("GET", base.ROUTE + "?surface=roles",
                                               {"Cookie": self.cookie_value}, host="evil.localhost")
        self.assertEqual(status, 403)

    def test_a_bad_query_is_400(self):
        status, _headers, body = self.get(base.ROUTE + "?surface=roles&days=0")
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))


if __name__ == "__main__":
    unittest.main()
