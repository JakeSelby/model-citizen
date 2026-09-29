# SPDX-License-Identifier: MIT
"""Machine-readable usage reports use the same aggregates as the text reports."""
import argparse
import contextlib
import io
import json
import subprocess
import sys
import time
import unittest
from unittest import mock

from test_usage import harness


NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def session(**extra):
    row = {"kind": "session", "runtime": "claude-code", "session_id": "s-1",
           "repo": "alpha", "models": ["model-a"], "started": NOW, "ended": NOW,
           "input": 10, "output": 20, "cache_read": 60, "cache_write": 10,
           "turns": 2, "subagents": 0, "raw_vs_deduped": 1.25,
           "stances": {"cost": "balanced"}, "rules": {"rule/one": 2}}
    row.update(extra)
    return row


def delegated(**extra):
    row = {"kind": "worker", "runtime": "claude-code", "session_id": "w-1",
           "agent_type": "reviewer", "repo": "alpha", "model": "model-a",
           "started": NOW, "ended": NOW, "input": 1, "output": 100,
           "cache_read": 0, "cache_write": 0, "tool_calls": 4,
           "return_path": "resolvable", "return_over_budget": False}
    row.update(extra)
    return row


class JsonReportTests(unittest.TestCase):
    def report(self, rows=(), prices=None, **overrides):
        values = {"days": 30, "by": "day", "rules": False, "rescan": False,
                  "stance": None, "conflicts": False, "json": True, "action": None}
        values.update(overrides)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(harness, "usage_ledger", return_value=list(rows)), \
                mock.patch.object(harness, "load_prices", return_value=prices or {}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = harness.cmd_usage(argparse.Namespace(**values))
        return code, out.getvalue(), err.getvalue()

    def document(self, rows=(), **overrides):
        code, out, err = self.report(rows, **overrides)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def test_cli_help_advertises_json_output(self):
        result = subprocess.run([sys.executable, str(harness.REPO / "bin/harness"),
                                 "usage", "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--json", result.stdout)

    def test_token_groupings_keep_names_numbers_unknown_buckets_and_totals(self):
        row = session(profile_fingerprint=None)
        cases = {"day": NOW[:10], "repo": "alpha", "model": "model-a",
                 "profile": "(unattributed)"}
        for by, name in cases.items():
            with self.subTest(by=by):
                data = self.document([row], by=by)
                self.assertEqual((data["schema_version"], data["report"], data["by"]),
                                 (1, "usage", by))
                self.assertEqual(data["groups"][0]["name"], name)
                self.assertEqual(data["groups"][0]["tokens"],
                                 {"input": 10, "output": 20, "cache_read": 60,
                                  "cache_write": 10})
                self.assertEqual(data["groups"][0]["cache_hit_rate"], 0.75)
                self.assertEqual(data["totals"]["runs"], 1)

        unknown = self.document([session(stances_source="rescan")], by="stance", stance="cost")
        self.assertEqual(unknown["groups"][0]["name"], "(unknown)")

    def test_unknown_tokens_spend_and_cache_denominator_stay_unknown_with_counts(self):
        data = self.document([session(input=None, output=float("inf"), cache_read=None,
                                      cache_write=0, partial=True)])
        group = data["groups"][0]
        self.assertEqual(group["tokens"], {"input": None, "output": None,
                                            "cache_read": None, "cache_write": 0})
        self.assertEqual(group["unknown_token_runs"],
                         {"input": 1, "output": 1, "cache_read": 1, "cache_write": 0})
        self.assertIsNone(group["cache_hit_rate"])
        self.assertIsNone(group["cache_hit_denominator"])
        self.assertIsNone(group["usd"])
        self.assertEqual(group["unpriced_runs"], 1)
        self.assertNotIn("Infinity", json.dumps(data))

    def test_denominatorless_cache_rate_is_null_and_counted(self):
        group = self.document([session(input=0, cache_read=0, cache_write=0)])["groups"][0]
        self.assertIsNone(group["cache_hit_rate"])
        self.assertEqual(group["cache_hit_denominator"], 0)

    def test_role_json_keeps_workflow_and_unnamed_groups(self):
        data = self.document([delegated(), delegated(session_id="w-2", agent_type=None),
                              delegated(session_id="w-3", workflow="wf-1")], by="role")
        groups = {group["name"]: group for group in data["groups"]}
        self.assertEqual(set(groups), {"reviewer", "(unnamed)", "(workflow)"})
        self.assertEqual(groups["reviewer"]["output"]["p50"], 100)
        self.assertEqual(groups["(workflow)"]["runs"], 1)
        self.assertEqual(data["workflow_runs"], 1)

    def test_partially_unpriced_group_does_not_report_known_spend_as_total(self):
        rates = {"model-a": {name: 1.0 for name in harness.RATE_FIELDS}}
        data = self.document([delegated(), delegated(session_id="w-2", model="unknown")],
                             by="role", prices=rates)
        group = data["groups"][0]
        self.assertEqual(group["unpriced_runs"], 1)
        self.assertIsNone(group["usd"]["p50"])
        self.assertNotIn("priced_usd", group)

    def test_rules_json_covers_rule_repo_and_stance_aggregates(self):
        rows = [session(), session(session_id="s-2", repo="beta",
                                   rules={"rule/one": 1}, stances={"cost": "frugal"})]
        with mock.patch.object(harness, "rule_ids", return_value=["rule/one"]), \
                mock.patch.object(harness, "rule_coverage_lines", return_value=["coverage"]):
            rule = self.document(rows, rules=True)
            self.assertEqual(rule["groups"], [{"id": "rule/one", "hits": 3,
                                                "sessions": 2, "of": 2, "share": 1.0,
                                                "note": ""}])
            repo = self.document(rows, rules=True, by="repo")
            self.assertEqual([group["name"] for group in repo["groups"]], ["alpha", "beta"])
            stance = self.document(rows, rules=True, by="stance")
            self.assertEqual([group["name"] for group in stance["groups"]],
                             ["cost=balanced", "cost=frugal"])

    def test_prefix_provider_decision_and_conflict_reports_are_structured(self):
        prefix = self.document([session()], by="prefix")
        self.assertEqual(prefix["groups"][0]["session_id"], "s-1")
        self.assertAlmostEqual(prefix["groups"][0]["ratio"], 1 / 7)

        provider_row = {"kind": harness.decision_ledger.KIND, "ended": NOW,
                        "point": "grade", "mode": "shadow", "status": "ok",
                        "input": 5, "ms": float("nan")}
        provider = self.document([provider_row], by="provider")
        self.assertEqual(provider["groups"][0]["statuses"]["ok"], 1)
        self.assertIsNone(provider["groups"][0]["latency_ms"]["p50"])
        self.assertNotIn("NaN", json.dumps(provider))

        fake = mock.Mock()
        fake.read_rows.return_value = [{"ts": NOW, "point": "gate", "outcome": "pass"}]
        fake.joined.side_effect = lambda rows: rows
        with mock.patch.object(harness, "load_hook_module", return_value=fake):
            decision = self.document([], by="decision")
        self.assertEqual(decision["groups"][0]["outcomes"], {"pass": 1})

        conflict_data = {"path": "/tmp/decisions.jsonl", "groups": [
            {"week": "2026-09-28", "merges": 2, "conflicted": 1,
             "conflict_share": 0.5, "overlaps": 3, "denied": 1}]}
        with mock.patch.object(harness.intents, "conflict_summary", return_value=conflict_data):
            conflicts = self.document([], conflicts=True)
        self.assertEqual(conflicts["groups"], conflict_data["groups"])

    def test_unpriced_provider_spend_is_null_with_explicit_count(self):
        row = {"kind": harness.decision_ledger.KIND, "ended": NOW, "point": "grade",
               "mode": "shadow", "status": "ok", "input": 5}
        group = self.document([row], by="provider")["groups"][0]
        self.assertIsNone(group["usd"])
        self.assertEqual(group["unpriced_calls"], 1)
        self.assertNotIn("priced_usd", group)

    def test_rescan_is_inside_every_json_report_envelope(self):
        completed = subprocess.CompletedProcess([], 0, "rescanned 2 row(s)\n", "")
        decisions = mock.Mock()
        decisions.read_rows.return_value = []
        decisions.joined.return_value = []
        conflict_data = {"path": "/tmp/decisions.jsonl", "groups": []}
        cases = ({"conflicts": True}, {"by": "decision"}, {"by": "role"},
                 {"rules": True, "by": "repo"}, {"by": "provider"})
        with mock.patch.object(harness.subprocess, "run", return_value=completed), \
                mock.patch.object(harness, "load_hook_module", return_value=decisions), \
                mock.patch.object(harness.intents, "conflict_summary", return_value=conflict_data):
            for values in cases:
                with self.subTest(values=values):
                    data = self.document([], rescan=True, **values)
                    self.assertEqual(data["rescan"], "rescanned 2 row(s)")
                    self.assertEqual(data["groups"], [])

    def test_nonfinite_prefix_and_rule_metrics_remain_explicitly_unknown(self):
        infinite = json.loads("1e309")
        prefix = self.document([session(cache_read=infinite)], by="prefix")
        self.assertIsNone(prefix["groups"][0]["cache_read"])
        self.assertIsNone(prefix["groups"][0]["ratio"])
        self.assertEqual(prefix["totals"]["unknown"], 1)
        with mock.patch.object(harness, "rule_coverage_lines", return_value=[]):
            rules = self.document([session(rules={"rule/one": infinite})], rules=True)
        self.assertEqual(rules["groups"], [])
        self.assertEqual(rules["errored_sessions"], 1)
        self.assertEqual(rules["measured_sessions"], 0)
        self.assertEqual(harness.folded_rules({"rules": {"rule/one": infinite}}, {}), {"rule/one": 0})

    def test_stance_selector_rejects_every_nonstance_grouping(self):
        for by in ("day", "repo", "model", "role", "profile", "prefix", "provider"):
            with self.subTest(by=by):
                code, output, error = self.report([], by=by, stance="cost")
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn("--stance", error)

    def test_empty_windows_are_valid_json(self):
        for values in ({}, {"by": "role"}, {"by": "provider"}, {"by": "prefix"},
                       {"rules": True}):
            with self.subTest(values=values):
                data = self.document([], **values)
                self.assertEqual(data["groups"], [])

        decisions = mock.Mock()
        decisions.read_rows.return_value = []
        decisions.joined.return_value = []
        with mock.patch.object(harness, "load_hook_module", return_value=decisions):
            self.assertEqual(self.document([], by="decision")["groups"], [])
        conflict_data = {"path": "/tmp/decisions.jsonl", "groups": []}
        with mock.patch.object(harness.intents, "conflict_summary", return_value=conflict_data):
            self.assertEqual(self.document([], conflicts=True)["groups"], [])

    def test_text_and_json_render_the_same_token_aggregate(self):
        rates = {"model-a": {name: 1000000.0 for name in harness.RATE_FIELDS}}
        data = self.document([session()], prices=rates)
        group = data["groups"][0]
        code, text, err = self.report([session()], json=False, prices=rates)
        self.assertEqual((code, err), (0, ""))
        cells = next(line for line in text.splitlines() if line.startswith(NOW[:10])).split()
        self.assertEqual(cells[1:6], [str(group["runs"])]
                         + [f"{group['tokens'][name]:,}" for name in harness.RATE_FIELDS])
        self.assertEqual(cells[6:], [f"{group['cache_hit_rate']:.0%}",
                                     f"{group['usd']:.2f}"])

    def test_errors_and_export_emit_no_success_json(self):
        code, out, err = self.report([], rules=True, by="model")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not one of them", err)

        code, out, err = self.report([], rules=True, by="stance", stance="cost")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("cannot be combined", err)

        code, out, err = self.report([], action="export")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage export does not support --json", err)


if __name__ == "__main__":
    unittest.main()
