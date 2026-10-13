# SPDX-License-Identifier: MIT
"""The Rules surface of the Harness periods route (AH-S371, #1323)."""
import base64
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as period_tests  # noqa: E402
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import rules  # noqa: E402

NOW = period_tests.NOW
ROUTE = "/api/harness/periods"
days_ago = period_tests.days_ago
stamp = period_tests.stamp


def detector(did, precision=0.9, status="measured", below_floor=False):
    return {"id": did, "status": status, "precision": precision if status == "measured" else None,
            "recall": None, "below_floor": below_floor if status == "measured" else None,
            "reason": ""}


def rule_row(unit, rule, detectors, selection_state="on", tokens=200, kind="rules"):
    """One row as `rule_health.report` prints it, trimmed to the fields the surface reads."""
    return {"id": "primitives/rules/%s.md" % unit, "root": "primitives", "rule": rule,
            "module": "%s/%s" % (kind, unit), "kind": kind, "unit": unit,
            "path": "primitives/rules/%s.md" % unit, "state": "measured" if detectors else "dark",
            "detectors": detectors, "selection_state": selection_state,
            "tokens": {"status": "measured", "tokens": tokens, "estimand": "soft estimate",
                       "reason": ""},
            "try_without": {"available": True, "reason": ""}}


# Hand-typed in the shape of `rule_health.report`; the real one runs the engines as subprocesses.
REPORT = {"rows": [
    rule_row("transcript-hygiene", "Read narrowly",
             [detector("transcript-hygiene/whole-file-cat", 0.95),
              detector("transcript-hygiene/model-wrote-no-cap", 0.8)], tokens=247),
    rule_row("secrets", "Secret hygiene", [detector("secrets/secret-in-write", 0.6, below_floor=True)]),
    rule_row("working-style", "Working style", [], tokens=464),
    rule_row("secrets", "A shadowed copy", [], tokens=1),
    rule_row("voice", "Voice stance", [detector("voice/banned-opener")], kind="stances"),
]}


def session(when, hits, kind="session", **extra):
    return period_tests.usage_row(when, kind=kind, rules=hits, **extra)


class RulesSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.home = period_tests.Home(self)
        patcher = mock.patch.object(rules, "load_report", return_value=REPORT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, unit=None):
        return periods.efficacy_rows("rules", unit, home=self.home.root, now=NOW)

    def test_each_switchable_rule_is_one_period_with_its_hits_and_the_note(self):
        self.home.usage([
            session(days_ago(20), {"transcript-hygiene/whole-file-cat": 5,
                                   "transcript-hygiene/model-wrote-no-cap": 1}),
            # A renamed detector counts under its successor, as `usage --rules` folds it.
            session(days_ago(12), {"transcript-hygiene/brief-without-cap": 2,
                                   "secrets/secret-in-write": 1}),
            session(days_ago(6), {"secrets/secret-in-write": 0}),
            session(days_ago(4), {}),
            # Not evidence: a worker, an errored read and a row before the detectors existed.
            session(days_ago(3), {"transcript-hygiene/whole-file-cat": 50}, kind="worker"),
            session(days_ago(2), {"transcript-hygiene/whole-file-cat": 50}, rules_error="boom"),
            period_tests.usage_row(days_ago(1)),
        ])
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        by_item = {row["item_id"]: row for row in answer["rows"]}
        self.assertEqual(sorted(by_item), ["secrets", "transcript-hygiene", "working-style"])
        self.assertEqual([r["item_label"] for r in answer["rows"]],
                         ["Secret hygiene", "Read narrowly", "Working style"])
        self.assertTrue(all(r["period"]["end"] is None and r["setting_label"] == "on"
                            for r in answer["rows"]))
        self.assertEqual(by_item["transcript-hygiene"]["metrics"],
                         {"sessions": 4, "hits": 8, "fired_in": "2 (50%)",
                          "precision": "0.80 (lowest of 2)", "context_tokens": 247})
        self.assertEqual(by_item["secrets"]["metrics"],
                         {"sessions": 4, "hits": 1, "fired_in": "1 (25%)",
                          "precision": "0.60, below floor", "context_tokens": 200})
        self.assertEqual(by_item["working-style"]["metrics"],
                         {"sessions": 4, "hits": "no detector", "fired_in": "no detector",
                          "precision": "no detector", "context_tokens": 464})

    def test_a_switch_applied_through_changes_splits_the_rule_into_before_and_after(self):
        self.home.usage([session(days_ago(n), {"secrets/secret-in-write": 2}) for n in (20, 10)]
                        + [session(days_ago(n), {}) for n in (3, 2, 1)])
        prior = base64.b64encode(json.dumps({"rules": {}}).encode()).decode()
        intent = {"apply_id": "a1", "phase": "intent", "ts": stamp(days_ago(4)),
                  "prior_config": prior,
                  "config": [{"key": "rules.secrets", "action": "set", "value": "off",
                              "applied": "off"}]}
        self.home.applies([intent, {"apply_id": "a1", "phase": "completed",
                                    "ts": stamp(days_ago(4))}])
        answer = self.answer("secrets")
        self.assertNotIn("note", answer)
        before, after = answer["rows"]
        self.assertEqual((before["setting_label"], before["status"]), ("default", "measured"))
        self.assertEqual((after["setting_label"], after["status"], after["measuring_day"]),
                         ("off", "measuring", 5))
        self.assertEqual(before["period"]["end"], after["period"]["start"])
        self.assertEqual((before["metrics"]["hits"], before["metrics"]["fired_in"]), (4, "2 (100%)"))
        self.assertEqual((after["metrics"]["hits"], after["metrics"]["fired_in"]), (0, "0 (0%)"))

    def test_a_period_without_a_measured_session_says_so(self):
        self.home.usage([period_tests.usage_row(days_ago(2))])
        metrics = self.answer("secrets")["rows"][0]["metrics"]
        self.assertEqual((metrics["sessions"], metrics["fired_in"]), (0, "no measured session"))

    def test_precision_and_cost_say_what_was_not_read(self):
        self.assertEqual(rules.precision_text([detector("a", status="unavailable")]), "unavailable")
        self.assertEqual(rules.precision_text([detector("a", status="not measured")]), "not measured")
        self.assertEqual(rules.precision_text([detector("a", 0.9),
                                               detector("b", status="not measured")]),
                         "0.90 (lowest of 2), 1 unscored")
        self.assertEqual(rules.tokens_value({"status": "not measured", "reason": "x"}), "not measured")
        self.assertEqual(rules._fired_text(1, 400), "1 (<1%)")

    def test_the_report_is_reused_within_its_ttl(self):
        mock.patch.stopall()
        rules.clear_cache()
        self.addCleanup(rules.clear_cache)
        with mock.patch.object(rules.rule_health, "report", return_value=REPORT) as report:
            rules.load_report()
            rules.load_report()
        self.assertEqual(report.call_count, 1)


class RulesRouteTests(studio_security.StudioSecurityFixture):
    """The route runs in a detached Studio, so it reads this checkout's real rule report."""

    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [period_tests.usage_row(now - dt.timedelta(days=n), rules={}) for n in (9, 3)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_the_route_answers_period_stamped_rule_rows(self):
        status, headers, body = self.get(ROUTE + "?surface=rules&days=30")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual((document["surface"], document["note"]), ("rules", periods.NOTE))
        self.assertTrue(document["rows"], "this checkout ships rules")
        for row in document["rows"]:
            self.assertEqual(set(row["metrics"]), {"sessions", "hits", "fired_in", "precision",
                                                   "context_tokens"}, row["item_id"])
            self.assertEqual(row["metrics"]["sessions"], 2)
            self.assertIsNone(row["period"]["end"])
            self.assertTrue(row["period"]["start"].endswith("Z"))
            self.assertEqual(row["evals_href"], "/evals?surface=rules&item=" + row["item_id"])

    def test_anonymous_or_a_foreign_host_is_refused(self):
        status, _headers, body = self.get(ROUTE + "?surface=rules", cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))
        status, _headers, _body = self.request("GET", ROUTE + "?surface=rules",
                                               {"Cookie": self.cookie_value}, host="evil.localhost")
        self.assertEqual(status, 403)

    def test_a_bad_window_is_400(self):
        status, _headers, body = self.get(ROUTE + "?surface=rules&days=0")
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))


if __name__ == "__main__":
    unittest.main()
