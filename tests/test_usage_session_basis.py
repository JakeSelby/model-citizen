# SPDX-License-Identifier: MIT
"""`usage --by session` and the pricing basis every priced JSON report carries."""
import argparse
import contextlib
import io
import json
import time
import unittest
from unittest import mock

from test_usage import harness

NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
TABLE = {"model-a": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25,
                     "as_of": "2026-08-01"},
         "model-b": {"input": 3.0, "output": 6.0, "cache_read": 0.3, "cache_write": 3.75,
                     "as_of": "2026-09-15"}}


def row(**extra):
    value = {"kind": "session", "runtime": "claude-code", "session_id": "s-1", "repo": "alpha",
             "models": ["model-a"], "started": NOW, "ended": NOW, "input": 10, "output": 20,
             "cache_read": 60, "cache_write": 10}
    value.update(extra)
    return value


class SessionGroupingAndBasisTests(unittest.TestCase):
    def run_usage(self, rows, prices=None, config=None, **overrides):
        values = {"days": 30, "by": "day", "rules": False, "rescan": False, "stance": None,
                  "conflicts": False, "json": True, "action": None}
        values.update(overrides)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(harness, "usage_ledger", return_value=list(rows)), \
                mock.patch.object(harness, "load_prices",
                                  return_value=TABLE if prices is None else prices), \
                mock.patch.object(harness, "load_config", return_value=config or {}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = harness.cmd_usage(argparse.Namespace(**values))
        return code, out.getvalue(), err.getvalue()

    def document(self, rows, **overrides):
        code, out, err = self.run_usage(rows, **overrides)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def test_session_groups_codex_children_with_their_parent_and_names_the_rest(self):
        rows = [
            row(session_id="cc-1"),
            # A Claude Code subagent's tokens are inside its session's row: never a second run.
            row(kind="subagent", session_id="cc-1", agent_type="gatherer", model="model-a"),
            row(runtime="codex", session_id="cx-1", models=["model-b"]),
            row(kind="subagent", runtime="codex", session_id="cx-1", model="model-b"),
            row(kind="worker", session_id="w-1", agent_type="reviewer", model="model-a"),
            {"kind": "studio_run", "runtime": "studio", "run_id": "run-1", "spend_usd": 0.5,
             "ended": NOW},
            row(session_id=""),
        ]
        data = self.document(rows, by="session")
        runs = {group["name"]: group["runs"] for group in data["groups"]}
        self.assertEqual(runs, {"cc-1": 1, "cx-1": 2, "w-1": 1, "run-1": 1, "(no session)": 1})
        self.assertEqual(data["totals"]["runs"], 6)
        self.assertEqual(data["by"], "session")
        studio = next(group for group in data["groups"] if group["name"] == "run-1")
        self.assertEqual(studio["usd"], 0.5)

    def test_session_totals_equal_the_day_totals_for_the_same_window(self):
        rows = [row(session_id="a"), row(session_id="b", models=["model-b"])]
        by_session = self.document(rows, by="session")["totals"]
        by_day = self.document(rows, by="day")["totals"]
        self.assertEqual(by_session, by_day)

    def test_rules_refuse_the_session_grouping(self):
        code, out, err = self.run_usage([row()], by="session", rules=True)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--by session is not one of them", err)

    def test_priced_reports_carry_the_oldest_date_among_the_prices_they_used(self):
        older = row(kind="worker", agent_type="reviewer", model="model-a", models=None)
        newer = row(kind="worker", agent_type="reviewer", model="model-b", models=["model-b"])
        for by in ("day", "model", "repo", "session", "role"):
            with self.subTest(by=by):
                only_newer = self.document([newer], by=by)
                self.assertEqual(only_newer["cost_basis"], "list_price_equivalent")
                # model-a's older entry is in the table but priced nothing here.
                self.assertEqual(only_newer["price_as_of"], "2026-09-15")
                self.assertEqual(self.document([older, newer], by=by)["price_as_of"],
                                 "2026-08-01")

    def test_nothing_priced_from_the_table_has_no_pricing_date(self):
        data = self.document([row()], prices={})
        self.assertEqual((data["cost_basis"], data["price_as_of"]),
                         ("list_price_equivalent", None))
        studio_only = self.document([{"kind": "studio_run", "runtime": "studio",
                                      "run_id": "run-1", "spend_usd": 0.5, "ended": NOW}])
        self.assertIsNone(studio_only["price_as_of"])

    def test_an_undated_entry_or_override_makes_the_pricing_date_unknown(self):
        undated = dict(TABLE, **{"model-c": {"input": 1.0, "output": 1.0, "cache_read": 0.1,
                                             "cache_write": 1.0}})
        data = self.document([row(models=["model-c"])], prices=undated)
        self.assertEqual(data["price_as_of"], "unknown")
        # The merge keeps the shipped date on an overridden entry; the override itself has none.
        overridden = self.document([row()], config={"prices": {"model-a": {"output": 9.0}}})
        self.assertEqual(overridden["price_as_of"], "unknown")
        dated = self.document([row()], config={"prices": {"model-a": {"output": 9.0,
                                                                      "as_of": "2026-09-30"}}})
        self.assertEqual(dated["price_as_of"], "2026-08-01")

    def test_provider_rebuild_and_adherence_reports_carry_the_basis(self):
        provider = {"kind": harness.decision_ledger.KIND, "point": "grade-bash", "mode": "ask",
                    "model": "model-b", "input": 10, "output": 5, "cache_read": 0,
                    "cache_write": 0, "ended": NOW, "status": "ok", "ms": 12}
        data = self.document([provider], by="provider")
        self.assertEqual((data["cost_basis"], data["price_as_of"]),
                         ("list_price_equivalent", "2026-09-15"))

        scope = {"sessions": 1, "calls": 3, "comparisons": 2, "unpriced_calls": 1,
                 "unpriced_breaks": 2, "priced_spend_usd": 0.1, "causes": [], "unknown_breaks": 0}

        def analyse(_projects, _cutoff, days, table, pricing):
            pricing.price_for(table, "model-a")
            return {"schema": 1, "days": days, "shortfall_tokens": 1, "long_session_calls": 50,
                    "files": {}, "scopes": {"long": dict(scope, unpriced_breaks=1),
                                            "all": scope}}

        with mock.patch.object(harness.rebuilds, "analyse", side_effect=analyse):
            rebuild = self.document([], by="rebuild")
        self.assertEqual((rebuild["cost_basis"], rebuild["price_as_of"]),
                         ("list_price_equivalent", "2026-08-01"))
        # The window's unpriced counts are the `all` scope's, which holds every long session.
        self.assertEqual((rebuild["unpriced"], rebuild["unpriced_calls"]), (2, 1))

        def estimate(_rows, _adherence, _intervals, _calls, table, pricing, *_rest):
            pricing.price_for(table, "model-b")
            return {}

        hooks = mock.MagicMock()
        hooks.read_rows.return_value = []
        with mock.patch.object(harness, "load_hook_module", return_value=hooks), \
                mock.patch.object(harness.soft_estimates, "report", side_effect=estimate), \
                mock.patch.object(harness.soft_estimates, "summary",
                                  return_value={"groups": [], "labels": {}, "footer": {}}):
            adherence = self.document([], by="adherence")
        self.assertEqual((adherence["cost_basis"], adherence["price_as_of"]),
                         ("list_price_equivalent", "2026-09-15"))

    def test_the_cli_accepts_the_session_grouping(self):
        parser_help = io.StringIO()
        with contextlib.redirect_stdout(parser_help), self.assertRaises(SystemExit):
            harness.main(["usage", "--help"])
        self.assertIn("session", parser_help.getvalue())


if __name__ == "__main__":
    unittest.main()
