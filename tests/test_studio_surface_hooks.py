# SPDX-License-Identifier: MIT
"""The Hooks surface: decisions grouped by hook, with liveness (AH-S372, #1324)."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import hooks  # noqa: E402

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
ROUTE = "/api/harness/periods"


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


OWN = object()


def decision(when, point, answer, module=OWN, **extra):
    row = {"kind": "decision", "decision_id": "%s-%s-%s" % (point, answer, stamp(when)),
           "point": point, "session_id": "s1", "ts": stamp(when), "deterministic_answer": answer,
           "outcome": None, "module": "hooks/" + point if module is OWN else module,
           "schema_version": 1}
    row.update(extra)
    return row


def days_ago(n, hours=0):
    return NOW - dt.timedelta(days=n, hours=hours)


class Home:
    def __init__(self, case):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name)) / "home"
        self.state = self.root / ".local" / "state" / "agent-harness"
        self.config_dir = self.root / ".config" / "agent-harness"
        self.state.mkdir(parents=True)
        self.config_dir.mkdir(parents=True)
        periods.clear_cache()
        case.addCleanup(periods.clear_cache)

    def decisions(self, rows, name="decisions.jsonl"):
        path = self.state / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def config(self, value):
        (self.config_dir / "config.json").write_text(json.dumps(value))


FIXTURE = [
    decision(days_ago(3), "grade-bash", "deny"),
    decision(days_ago(2), "grade-bash", "ask"),
    decision(days_ago(1), "grade-bash", "allow", sampled=True),
    decision(days_ago(1, 2), "governance", "allow", module="hooks/grade-bash"),
    decision(days_ago(0, 3), "grade-bash", "deny", error="RuntimeError"),
    decision(days_ago(1), "stop-gate", "blocked"),
    decision(days_ago(0, 1), "stop-gate", "released"),
    decision(days_ago(10), "steer-polling", "note"),
    decision(days_ago(2), "evasion-deny", "deny", module=None),
    {"kind": "outcome", "decision_id": "x", "point": "stop-gate", "ts": stamp(days_ago(0)),
     "outcome": "passed", "module": "hooks/stop-gate"},
]


class HooksSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)

    def answer(self, days=30):
        return periods.efficacy_rows("hooks", home=self.home.root, now=NOW, days=days)

    def metrics(self, days=30):
        return {row["item_id"]: row["metrics"] for row in self.answer(days)["rows"]}

    def assert_grade_bash(self):
        found = self.metrics()["grade-bash"]
        self.assertEqual((found["fires"], found["blocks"], found["warns"], found["errors"]),
                         (5, 2, 1, 1))

    def test_decisions_group_by_module_with_their_points_and_figures(self):
        self.home.decisions(FIXTURE)
        found = self.metrics()
        self.assert_grade_bash()
        self.assertEqual(found["grade-bash"]["points"], "governance · grade-bash")
        self.assertEqual(found["grade-bash"]["last_fired"], "2026-10-12 09:00 UTC")
        self.assertEqual(found["stop-gate"]["blocks"], 1)
        self.assertEqual(found["stop-gate"]["fires"], 2, "an outcome row is not a fire")

    def test_a_broken_block_count_fails_the_figures_assertion(self):
        self.home.decisions(FIXTURE)
        with mock.patch.object(hooks, "BLOCK_ANSWERS", ()):
            with self.assertRaises(AssertionError):
                self.assert_grade_bash()

    def test_liveness_labels(self):
        self.home.decisions(FIXTURE)
        self.home.config({"hooks": {"validate-plan-card": "off"}})
        found = self.metrics()
        self.assertEqual(found["grade-bash"]["liveness"], "erroring")
        self.assertEqual(found["stop-gate"]["liveness"], "live")
        self.assertEqual(found["steer-polling"]["liveness"], "silent", "last decision 10 days ago")
        self.assertEqual(found["intent-overlap"]["liveness"], "silent", "no decision at all")
        self.assertEqual(found["intent-overlap"]["last_fired"], "not in window")
        self.assertEqual(found["validate-plan-card"]["liveness"], "off")
        self.assertEqual(found["point:evasion-deny"]["liveness"], "built in")

    def test_a_hook_that_never_writes_decisions_is_not_observable_never_silent(self):
        self.home.decisions(FIXTURE)
        found = self.metrics()
        for hook in ("usage-log", "usage-feed", "pre-compact", "approvals"):
            self.assertEqual(found[hook]["liveness"], "not observable", hook)
        rows = {row["item_id"]: row for row in self.answer()["rows"]}
        self.assertEqual(rows["usage-log"]["setting_label"], "on")

    def test_silence_is_judged_against_seven_days(self):
        self.home.decisions([decision(days_ago(6, 23), "steer-polling", "note")])
        self.assertEqual(self.metrics()["steer-polling"]["liveness"], "live")
        self.home.decisions([decision(days_ago(7, 1), "steer-polling", "note")])
        periods.clear_cache()
        self.assertEqual(self.metrics()["steer-polling"]["liveness"], "silent")

    def test_decision_logging_off_is_not_recorded_rather_than_silent(self):
        self.home.config({"telemetry": {"decisions": False}})
        self.assertEqual(self.metrics()["grade-bash"]["liveness"], "not recorded")

    def test_the_rotated_log_is_read_so_a_rotation_does_not_silence_every_hook(self):
        self.home.decisions([decision(days_ago(0, 1), "stop-gate", "released")])
        older = self.home.decisions([decision(days_ago(2), "grade-bash", "deny")],
                                    name="decisions.jsonl.1")
        stale = self.home.decisions([decision(days_ago(60), "brief-guard", "cap")],
                                    name="decisions.jsonl.2")
        os.utime(str(older), (days_ago(2).timestamp(),) * 2)
        os.utime(str(stale), (days_ago(60).timestamp(),) * 2)
        found = self.metrics()
        self.assertEqual((found["grade-bash"]["fires"], found["grade-bash"]["liveness"]),
                         (1, "live"))
        self.assertEqual(found["brief-guard"]["fires"], 0)

    def test_window_bounds_the_counts(self):
        self.home.decisions(FIXTURE)
        self.assertEqual(self.metrics(days=2)["grade-bash"]["fires"], 4)

    def test_one_period_per_hook_with_the_note(self):
        self.home.decisions(FIXTURE)
        document = self.answer()
        self.assertEqual(document["note"], periods.NOTE)
        ids = [row["item_id"] for row in document["rows"]]
        self.assertEqual(len(ids), len(set(ids)), "one period per hook")
        row = next(r for r in document["rows"] if r["item_id"] == "grade-bash")
        self.assertEqual((row["item_label"], row["period"]["end"], row["status"]),
                         ("Command grader", None, "measured"))
        self.assertEqual(row["period"]["start"], "2026-09-12T12:00:00Z")

    def test_an_empty_home_lists_every_configured_hook(self):
        rows = self.answer()["rows"]
        self.assertTrue({"grade-bash", "stop-gate", "usage-log"} <= {r["item_id"] for r in rows})


class HooksRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [decision(now - dt.timedelta(hours=1), "grade-bash", "deny")]
        (state / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_the_route_answers_hook_rows(self):
        status, headers, body = self.get(ROUTE + "?surface=hooks&days=30")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual(set(document), {"surface", "rows", "note"})
        row = next(r for r in document["rows"] if r["item_id"] == "grade-bash")
        self.assertEqual(set(row), {"item_id", "item_label", "setting_label", "period", "status",
                                    "window_days", "metrics", "evals_href"})
        self.assertEqual((row["metrics"]["fires"], row["metrics"]["blocks"],
                          row["metrics"]["liveness"]), (1, 1, "live"))
        self.assertIsNone(row["period"]["end"])

    def test_a_bad_window_is_400(self):
        status, _headers, body = self.get(ROUTE + "?surface=hooks&days=0")
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))

    def test_anonymous_is_refused_like_its_neighbours(self):
        status, _headers, body = self.get(ROUTE + "?surface=hooks", cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))



class HookDetailTests(unittest.TestCase):
    """One hook's page: `hooks.detail` and `GET /api/harness/hook`."""

    def setUp(self):
        self.home = Home(self)

    def detail(self, unit="grade-bash", days=7):
        return hooks.detail(unit, days=days, home=self.home.root, now=NOW)

    def assert_outcomes(self, found):
        totals = {name: sum(day[name] for day in found["by_day"]) for name in hooks.OUTCOMES}
        self.assertEqual(totals, {"block": 1, "warn": 1, "allow": 2, "error": 1})

    def test_shape_and_summary_match_the_hooks_page(self):
        self.home.decisions(FIXTURE)
        found = self.detail(days=30)
        self.assertEqual(set(found), {"hook", "label", "built_in", "core", "window_days", "summary",
                                      "by_day", "recent", "settings"})
        self.assertEqual(found["summary"], self.metrics_row())
        self.assertEqual((found["label"], found["core"], found["built_in"]),
                         ("Command grader", True, False))

    def metrics_row(self):
        rows = periods.efficacy_rows("hooks", "grade-bash", home=self.home.root, now=NOW, days=30)
        return rows["rows"][0]["metrics"]

    def test_decisions_by_day_and_outcome(self):
        self.home.decisions(FIXTURE)
        found = self.detail()
        self.assertEqual(len(found["by_day"]), 8, "one bar per day, today included")
        self.assertEqual(found["by_day"][-1]["day"], "2026-10-12")
        self.assert_outcomes(found)
        self.assertEqual(found["by_day"][-1]["error"], 1, "an errored deny counts as an error")

    def test_a_broken_outcome_split_fails_the_assertion(self):
        self.home.decisions(FIXTURE)
        with mock.patch.object(hooks, "BLOCK_ANSWERS", ()):
            with self.assertRaises(AssertionError):
                self.assert_outcomes(self.detail())

    def test_recent_decisions_carry_session_and_hash_never_the_input(self):
        rows = [decision(days_ago(1), "grade-bash", "deny", input="git push --force",
                         input_sha256="ab" * 32, reason="Grade 3")] + FIXTURE
        self.home.decisions(rows)
        recent = self.detail()["recent"]
        self.assertEqual([r["ts"] for r in recent], sorted((r["ts"] for r in recent), reverse=True))
        stop = next(r for r in recent if r["input_hash"])
        self.assertEqual(stop, {"ts": stamp(days_ago(1)), "point": "grade-bash", "answer": "deny",
                                "outcome": "block", "session": "s1", "input_hash": "ab" * 32,
                                "reason": "Grade 3"})
        self.assertNotIn("git push", json.dumps(recent))

    def test_settings_name_the_switch_modes_and_source(self):
        self.home.config({"hooks": {"grade-bash": "off"},
                          "governance": {"jev": {"mode": "shadow", "modes": {"grade-bash": "act"}}}})
        found = self.detail()["settings"]
        self.assertEqual(found["enabled"], "off")
        self.assertEqual(found["modes"]["grade-bash"], "act")
        self.assertEqual(found["source"], "policy/hooks/grade-bash.py")
        self.assertEqual(self.detail("brief-guard")["settings"]["modes"], {"brief-guard": "shadow"})
        self.assertEqual(self.detail("usage-log")["settings"]["modes"], {})

    def test_a_built_in_point_has_a_page(self):
        self.home.decisions(FIXTURE)
        found = self.detail("point:evasion-deny")
        self.assertTrue(found["built_in"])
        self.assertEqual(found["settings"]["enabled"], "always on")
        self.assertEqual(found["summary"]["liveness"], "built in")

    def test_an_unknown_hook_is_404_and_a_bad_window_400(self):
        self.assertEqual(hooks.answer("no-such-hook", home=self.home.root),
                         (404, {"error": "unknown_hook"}))
        self.assertEqual(hooks.answer("grade-bash", "0", home=self.home.root),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(hooks.answer("", home=self.home.root), (400, {"error": "invalid_request"}))


class HookDetailRouteTests(HooksRouteTests):
    """The detail route over HTTP; inherits the seeded home and the session cookie."""

    DETAIL = "/api/harness/hook"

    def test_the_route_answers_one_hook(self):
        status, headers, body = self.get(self.DETAIL + "?name=grade-bash&days=7")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual(document["hook"], "grade-bash")
        self.assertEqual(sum(day["block"] for day in document["by_day"]), 1)
        self.assertEqual(document["recent"][0]["session"], "s1")

    def test_an_unknown_hook_is_404(self):
        status, _headers, body = self.get(self.DETAIL + "?name=no-such-hook")
        self.assertEqual((status, json.loads(body)), (404, {"error": "unknown_hook"}))

    def test_anonymous_detail_is_refused(self):
        status, _headers, body = self.get(self.DETAIL + "?name=grade-bash", cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))


if __name__ == "__main__":
    unittest.main()
