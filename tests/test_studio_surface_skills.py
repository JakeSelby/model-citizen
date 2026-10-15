# SPDX-License-Identifier: MIT
"""The Skills surface: sessions that loaded and invoked each skill, per period (AH-S378, #1330).

Every transcript is synthetic, written into a temporary home with the transcript index's own
test helpers.
"""
import base64
import datetime as dt
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
import test_studio_transcripts as tx  # noqa: E402
from harness_core.studio import periods, transcripts  # noqa: E402
from harness_core.studio.surfaces import skills  # noqa: E402

NOW = base.NOW


def session_entries(when, loaded, invoked=(), commands=()):
    """A session at `when`: a skill listing, one prompt per slash command, and one reply holding
    a `Skill` call for each name in `invoked`."""
    entries = [tx.listing(0, list(loaded)), tx.prompt(1)]
    entries += [tx.prompt(2, "<command-name>/%s</command-name>" % name) for name in commands]
    calls = [tx.skill_call("t%d" % index, name) for index, name in enumerate(invoked)]
    entries.append(tx.reply(3, "m-" + base.stamp(when), tools=calls))
    for minute, entry in enumerate(entries):
        entry["timestamp"] = base.stamp(when + dt.timedelta(minutes=minute))
    return entries


class Fixture:
    def __init__(self, case, now=NOW):
        self.home = base.Home(case)
        self.files = tx.Transcripts(self.home)
        self.now = now

    def session(self, session_id, days_ago, loaded, invoked=(), commands=()):
        when = self.now - dt.timedelta(days=days_ago)
        path = self.files.write(session_id, session_entries(when, loaded, invoked, commands))
        stamp = when.timestamp() + 600
        os.utime(str(path), (stamp, stamp))
        return path

    def warm(self):
        transcripts.sessions(self.home.root, days=30, now=self.now)


class SkillsSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(self)
        self.home = self.fixture.home

    def answer(self, unit=None):
        return periods.efficacy_rows("skills", unit, home=self.home.root, now=NOW)

    def by_item(self, answer):
        return {row["item_id"]: row for row in answer["rows"]}

    def test_each_skill_counts_the_sessions_that_loaded_and_invoked_it(self):
        self.fixture.session("s1", 12, ["plan-authoring", "sandbox"], invoked=["plan-authoring"])
        self.fixture.session("s2", 5, ["plan-authoring", "sandbox"], invoked=["plan-authoring",
                                                                            "plan-authoring"])
        self.fixture.session("s3", 2, ["plan-authoring", "sandbox", "design-loop"],
                             commands=["design-loop"])
        self.fixture.warm()
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        rows = self.by_item(answer)
        self.assertEqual(sorted(rows), ["design-loop", "plan-authoring", "sandbox"])
        plan = rows["plan-authoring"]
        self.assertEqual(plan["metrics"], {"sessions": 3, "loaded_in": 3, "invoked_in": 2,
                                           "invocations": 3, "use": skills.INVOKED,
                                           "invoked_rate": 0.667})
        self.assertEqual((rows["design-loop"]["metrics"]["invoked_in"],
                          rows["design-loop"]["metrics"]["invocations"]), (1, 1))
        self.assertEqual(set(plan), {"item_id", "item_label", "setting_label", "period", "status",
                                     "window_days", "metrics", "evals_href"})

    def test_rows_are_period_stamped_from_the_first_session_that_named_the_skill(self):
        self.fixture.session("s1", 12, ["plan-authoring"])
        self.fixture.session("s2", 3, ["plan-authoring", "sandbox"])
        self.fixture.warm()
        rows = self.by_item(self.answer())
        plan, sandbox = rows["plan-authoring"], rows["sandbox"]
        # A session is stamped when it ended: its last entry, two minutes after it opened.
        ended = NOW - dt.timedelta(days=12) + dt.timedelta(minutes=2)
        self.assertEqual(plan["period"], {"start": periods.iso(ended), "end": None})
        self.assertEqual(plan["status"], "measured")
        self.assertEqual((sandbox["status"], sandbox["measuring_day"]), ("measuring", 3))
        self.assertEqual(plan["setting_label"], "on")
        self.assertEqual(plan["evals_href"], "/evals?surface=skills&item=plan-authoring")

    def test_a_skill_loaded_but_never_invoked_is_marked_so(self):
        self.fixture.session("s1", 4, ["sandbox", "plan-authoring"], invoked=["plan-authoring"])
        self.fixture.session("s2", 2, ["sandbox", "plan-authoring"])
        self.fixture.warm()
        metrics = self.by_item(self.answer())["sandbox"]["metrics"]
        self.assertEqual((metrics["loaded_in"], metrics["invoked_in"], metrics["use"]),
                         (2, 0, skills.NEVER_INVOKED))
        self.assertEqual(metrics["invoked_rate"], 0)
        summary = skills.summary(home=self.home.root, now=NOW)
        marked = {row["skill"]: row["use"] for row in summary["skills"]}
        self.assertEqual(marked, {"plan-authoring": skills.INVOKED,
                                  "sandbox": skills.NEVER_INVOKED})

    def test_a_skill_invoked_without_a_listing_has_no_rate(self):
        self.fixture.session("s1", 2, [], invoked=["personal-skill"])
        self.fixture.warm()
        metrics = self.by_item(self.answer())["personal-skill"]["metrics"]
        self.assertEqual((metrics["loaded_in"], metrics["invoked_in"], metrics["use"],
                          metrics["invoked_rate"]), (0, 1, skills.INVOKED, skills.NOT_LOADED))

    def test_a_switch_applied_through_the_studio_splits_the_skill_into_two_periods(self):
        self.fixture.session("s1", 15, ["sandbox"], invoked=["sandbox"])
        self.fixture.session("s2", 2, ["sandbox"])
        self.fixture.warm()
        prior = base64.b64encode(json.dumps({"skills": {}}).encode()).decode()
        self.home.applies([
            {"apply_id": "a1", "phase": "intent", "ts": base.stamp(base.days_ago(6)),
             "prior_config": prior,
             "config": [{"key": "skills.sandbox", "action": "set", "value": "off",
                         "applied": "off"}]},
            {"apply_id": "a1", "phase": "completed", "ts": base.stamp(base.days_ago(6))}])
        before, after = self.answer("sandbox")["rows"]
        self.assertEqual((before["setting_label"], after["setting_label"]), ("on", "off"))
        self.assertEqual(before["period"]["end"], after["period"]["start"])
        self.assertEqual((before["metrics"]["invoked_in"], after["metrics"]["invoked_in"]), (1, 0))
        self.assertEqual((before["status"], after["status"]), ("measured", "measuring"))

    def test_a_session_outside_the_window_is_not_counted(self):
        self.fixture.session("old", 45, ["sandbox"], invoked=["sandbox"])
        self.fixture.session("new", 1, ["sandbox"])
        transcripts.sessions(self.home.root, days=60, now=NOW)
        metrics = self.by_item(self.answer())["sandbox"]["metrics"]
        self.assertEqual((metrics["sessions"], metrics["invoked_in"]), (1, 0))

    def test_a_cold_index_answers_at_once_and_says_it_is_still_indexing(self):
        self.fixture.session("s1", 3, ["sandbox"])
        self.fixture.session("s2", 2, ["sandbox"])
        with mock.patch.object(transcripts, "warm") as warm, \
                mock.patch.object(transcripts, "build", side_effect=AssertionError("parsed")):
            cold = self.answer()
            summary = skills.summary(home=self.home.root, now=NOW)
        warm.assert_called()
        self.assertEqual(cold["rows"], [])
        self.assertEqual((summary["indexing"], summary["pending"], summary["sessions"]),
                         (True, 2, 0))
        transcripts.index_file(self.home.root, self.fixture.files.project / "s1.jsonl")
        with mock.patch.object(transcripts, "warm"):
            partial = self.by_item(self.answer())["sandbox"]["metrics"]
        self.assertEqual(partial["indexing"], "still indexing · 1 transcript pending")
        self.assertEqual(partial["sessions"], 1)
        self.fixture.warm()
        with mock.patch.object(transcripts, "warming", return_value=False):
            warmed = self.by_item(self.answer())["sandbox"]["metrics"]
            done = skills.summary(home=self.home.root, now=NOW)
        self.assertNotIn("indexing", warmed)
        self.assertEqual((done["indexing"], done["pending"], done["sessions"]), (False, 0, 2))

    def test_the_summary_names_its_basis_as_observational(self):
        summary = skills.summary(home=self.home.root, now=NOW)
        self.assertEqual(summary, {"window_days": 30, "sessions": 0, "indexing": False,
                                   "pending": 0, "basis": skills.BASIS, "skills": []})
        self.assertIn("Observational", skills.BASIS)
        self.assertNotIn("evidence", skills.BASIS.lower())

    def test_answer_refuses_a_bad_window(self):
        for days in ("0", "x", "366", "-1", "٣"):
            self.assertEqual(skills.answer(days, home=self.home.root),
                             (400, {"error": "invalid_request"}), days)
        status, body = skills.answer(None, home=self.home.root)
        self.assertEqual((status, body["window_days"]), (200, 30))

    def test_the_surface_is_registered(self):
        self.assertIn("skills", periods.surfaces())


class SkillsRouteTests(base.studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        self.now = dt.datetime.now(dt.timezone.utc)
        files = tx.Transcripts(types.SimpleNamespace(root=self.home))
        for session_id, age, loaded, invoked in (("r1", 3, ["sandbox", "plan-authoring"],
                                                  ["plan-authoring"]),
                                                 ("r2", 1, ["sandbox"], [])):
            when = self.now - dt.timedelta(days=age)
            path = files.write(session_id, session_entries(when, loaded, invoked))
            stamp = when.timestamp() + 600
            os.utime(str(path), (stamp, stamp))
        self.files = files
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        status, _headers, body = self.request("GET", path,
                                              {"Cookie": self.cookie_value} if cookie else {})
        return status, json.loads(body)

    def test_a_cold_index_answers_with_the_indexing_state(self):
        status, document = self.get("/api/harness/skills")
        self.assertEqual(status, 200, document)
        self.assertEqual((document["indexing"], document["pending"]), (True, 2))

    def test_the_periods_route_answers_skill_periods(self):
        transcripts.sessions(self.home, days=30)
        status, document = self.get(base.ROUTE + "?surface=skills")
        self.assertEqual(status, 200, document)
        self.assertEqual((document["surface"], document["note"]), ("skills", periods.NOTE))
        rows = {row["item_id"]: row for row in document["rows"]}
        self.assertEqual(sorted(rows), ["plan-authoring", "sandbox"])
        self.assertEqual(rows["sandbox"]["metrics"]["use"], skills.NEVER_INVOKED)
        self.assertEqual(rows["plan-authoring"]["metrics"]["invoked_in"], 1)
        self.assertEqual(rows["sandbox"]["status"], "measuring")
        self.assertIsNone(rows["sandbox"]["period"]["end"])
        self.assertTrue(rows["sandbox"]["period"]["start"].endswith("Z"))

    def test_the_skills_route_answers_the_summary(self):
        transcripts.sessions(self.home, days=30)
        status, document = self.get("/api/harness/skills?days=30")
        self.assertEqual(status, 200, document)
        self.assertEqual(set(document), {"window_days", "sessions", "indexing", "pending",
                                         "basis", "skills"})
        self.assertEqual((document["sessions"], document["indexing"]), (2, False))
        self.assertEqual(document["skills"], [
            {"skill": "plan-authoring", "loaded_sessions": 1, "invoked_sessions": 1,
             "invocations": 1, "use": skills.INVOKED},
            {"skill": "sandbox", "loaded_sessions": 2, "invoked_sessions": 0, "invocations": 0,
             "use": skills.NEVER_INVOKED}])

    def test_anonymous_is_refused(self):
        for path in (base.ROUTE + "?surface=skills", "/api/harness/skills"):
            self.assertEqual(self.get(path, cookie=False), (401, {"error": "unauthorized"}), path)

    def test_a_bad_query_is_400(self):
        for query in ("?days=0", "?days=x", "?days=366", "?days=30&days=7", "?surface=skills"):
            self.assertEqual(self.get("/api/harness/skills" + query),
                             (400, {"error": "invalid_request"}), query)
        self.assertEqual(self.get(base.ROUTE + "?surface=skills&days=0"),
                         (400, {"error": "invalid_request"}))

    def test_a_post_is_not_a_route(self):
        status, _headers, _body = self.request("POST", "/api/harness/skills",
                                               {"Cookie": self.cookie_value,
                                                "Content-Type": "application/json"}, b"{}")
        self.assertGreaterEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
