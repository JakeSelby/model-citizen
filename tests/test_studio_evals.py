# SPDX-License-Identifier: MIT
"""The Evals page's readers and routes (AH-S381, #1333).

Run: python3 -m unittest discover -s tests -p test_studio_evals.py"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from test_harness import REPO  # noqa: E402
from test_studio_periods import Home, NOW, days_ago, usage_row  # noqa: E402
from test_studio_surface_rules import REPORT  # noqa: E402
from harness_core.studio import evals, periods, server  # noqa: E402
from harness_core.studio.surfaces import rules as rules_surface  # noqa: E402

TRANSCRIPTS = "harness_core.studio.transcripts"
OBSERVATIONAL_KEYS = {"label", "standard_label", "note"}


def stance_change(home):
    """A voice change eleven days ago: its after period has finished measuring."""
    rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 15, 12)]
    rows += [usage_row(days_ago(n), voice="concise") for n in (10, 5, 1)]
    home.usage(rows)


def fixture_home(case):
    """A fixture home, with the Rules surface's detector report fixed so no surface reads the
    real home when every surface is asked."""
    patcher = mock.patch.object(rules_surface, "load_report", return_value=REPORT)
    patcher.start()
    case.addCleanup(patcher.stop)
    return Home(case)


def assert_observational(case, answer):
    case.assertEqual(answer["label"], "observational")
    case.assertEqual(answer["standard_label"], "exploratory")
    case.assertEqual(answer["note"], evals.OBSERVATIONAL_NOTE)
    case.assertNotIn("evidence", json.dumps(sorted(answer)).lower(),
                     "an observational answer names no field evidence")
    case.assertNotIn("evidence", answer["note"].lower())


class PreregistrationTests(unittest.TestCase):
    def test_the_repository_lists_its_committed_pre_registrations(self):
        answer = evals.preregistrations(REPO)
        files = sorted(p.name for p in (REPO / evals.PREREGISTRATION_DIR).glob("*.md"))
        self.assertEqual(sorted(item["file"] for item in answer["items"]), files)
        pilot = next(i for i in answer["items"] if i["file"] == "2026-10-01-power-pilot.md")
        self.assertTrue(pilot["title"].startswith("Power pilot"))
        self.assertEqual((pilot["date"], pilot["label"]), ("2026-10-01", "pre-registered"))
        self.assertGreaterEqual(pilot["deviations"], 1)

    def test_title_date_status_and_deviations_are_read_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / evals.PREREGISTRATION_DIR
            folder.mkdir(parents=True)
            (folder / "2026-09-01-old.md").write_text("# Old plan\n", encoding="utf-8")
            (folder / "2026-10-02-new.md").write_text(
                "# New plan\n\n## Run\n\n- **Question:** does it?\n- **Status:** running\n"
                "- **Date registered:** 2026-10-03\n\n## Deviation log\n\n- None yet.\n"
                "- 2026-10-04: a fix.\n- 2026-10-05: another.\n", encoding="utf-8")
            (folder / "notes.txt").write_text("ignored", encoding="utf-8")
            items = evals.preregistrations(root)["items"]
        self.assertEqual([i["title"] for i in items], ["New plan", "Old plan"])
        self.assertEqual((items[0]["date"], items[0]["status"], items[0]["question"],
                          items[0]["deviations"]), ("2026-10-03", "running", "does it?", 2))
        self.assertEqual((items[1]["date"], items[1]["status"]), ("2026-09-01", None))

    def test_no_directory_is_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(evals.preregistrations(Path(tmp))["items"], [])


class CompatibilityTests(unittest.TestCase):
    def test_the_catalog_is_read_through_the_compatibility_reader(self):
        from harness_core import compatibility
        with mock.patch.object(compatibility, "catalog", wraps=compatibility.catalog) as reader:
            answer = evals.compatibility_evidence(REPO)
        reader.assert_called_once_with(REPO)
        self.assertEqual(answer["state"], "ready")
        self.assertIn(answer["qualification_source"], ("current", "drifted"))
        qualified = [c for c in answer["clients"] if c["status"] == "qualified"]
        self.assertTrue(qualified)
        self.assertTrue(all(r["path"].startswith("compatibility/evidence/")
                            for c in qualified for r in c["records"]))

    def test_a_catalog_the_reader_refuses_is_unavailable_with_its_reason(self):
        def refuse(_root):
            raise ValueError("evidence digest mismatch")
        answer = evals.compatibility_evidence(REPO, reader=refuse)
        self.assertEqual((answer["state"], answer["reason"], answer["clients"]),
                         ("unavailable", "evidence digest mismatch", []))


class BeforeAfterTests(unittest.TestCase):
    def setUp(self):
        self.home = fixture_home(self)

    def test_a_boundary_shows_the_period_before_and_after_it(self):
        stance_change(self.home)
        answer = evals.before_after(self.home.root, days=30, now=NOW, surface="stances")
        assert_observational(self, answer)
        voice = [b for b in answer["boundaries"] if b["item_id"] == "voice"]
        self.assertEqual(len(voice), 1)
        boundary = voice[0]
        self.assertEqual((boundary["from"], boundary["to"], boundary["source"]),
                         ("answer-card", "concise", "usage"))
        self.assertEqual(boundary["before"]["metrics"]["sessions"], 3)
        self.assertEqual(boundary["after"]["status"], "measured")
        sessions = next(c for c in boundary["changes"] if c["key"] == "sessions")
        self.assertEqual((sessions["before"], sessions["after"], sessions["delta"]), (3, 3, 0))
        self.assertEqual(boundary["href"],
                         "/evals?tab=before-after&surface=stances&item=voice")
        self.assertEqual(answer["surfaces"], {"stances": "ok"})

    def test_no_change_is_no_boundary(self):
        self.home.usage([usage_row(days_ago(n)) for n in (5, 3)])
        self.assertEqual(evals.before_after(self.home.root, now=NOW)["boundaries"], [])

    def test_any_boundary_source_is_carried_through_as_named(self):
        named = [{"unit": "voice", "value": "a", "start": "2026-10-01T00:00:00Z",
                  "source": "none"},
                 {"unit": "voice", "value": "b", "start": "2026-10-05T00:00:00Z",
                  "source": "baseline"}]
        row = {"item_label": "Voice", "setting_label": "x", "period": {}, "status": "measured",
               "metrics": {"m": 1, "model": "opus"}}
        with mock.patch.object(periods, "periods", return_value=named), \
                mock.patch.object(periods, "efficacy_rows",
                                  return_value={"rows": [row, dict(row, metrics={"m": 3})]}):
            found = evals.surface_boundaries("stances", days=30, home=self.home.root)
        self.assertEqual(found[0]["source"], "baseline")
        self.assertEqual(found[0]["changes"],
                         [{"key": "m", "before": 1, "after": 3, "delta": 2.0},
                          {"key": "model", "before": "opus", "after": None, "delta": None}])

    def test_a_failing_surface_is_named_and_the_rest_stand(self):
        stance_change(self.home)
        periods.register_surface("broken", lambda ctx: 1 / 0, lambda *a: {})
        self.addCleanup(periods._REGISTRY.pop, "broken", None)
        answer = evals.before_after(self.home.root, now=NOW)
        self.assertEqual(answer["surfaces"]["broken"], "error")
        self.assertTrue(any(b["item_id"] == "voice" for b in answer["boundaries"]))

    def test_a_finished_after_period_is_a_home_item(self):
        stance_change(self.home)
        items = [i for i in evals.ready_items(self.home.root, now=NOW)
                 if i["id"].startswith("evals:stances:voice:")]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(set(item), {"id", "source", "kind", "severity", "title", "detail", "at",
                                     "action"})
        self.assertEqual((item["source"], item["kind"], item["severity"]),
                         ("evals", "eval-ready", "low"))
        self.assertIn("Observational", item["detail"])
        self.assertNotIn("evidence", (item["title"] + item["detail"]).lower())
        self.assertTrue(item["action"]["href"].startswith("/evals?"))
        every = evals.ready_items(self.home.root, now=NOW)
        self.assertTrue(all(i["id"].startswith("evals:") for i in every), every)
        self.assertTrue(all(i["action"]["href"].startswith("/") for i in every), every)

    def test_a_period_still_measuring_is_not_yet_a_home_item(self):
        rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 15)]
        rows += [usage_row(days_ago(n), voice="concise") for n in (3, 1)]
        self.home.usage(rows)
        self.assertEqual([i for i in evals.ready_items(self.home.root, now=NOW)
                          if ":voice:" in i["id"]], [])


def call(session, ts, tokens, event=None):
    return {"session_id": session, "ts": ts, "context_tokens": tokens, "event": event,
            "cost": None, "turn": 0, "sidechain": False}


class AutoTrimTests(unittest.TestCase):
    def test_context_before_and_after_the_first_reset(self):
        fake = [
            {"session_id": "s1", "ts": "2026-10-10T00:00:0%dZ" % i, "context_tokens": tokens,
             "after_reset": i >= 3}
            for i, tokens in enumerate((100_000, 200_000, 300_000, 40_000, 60_000))]
        fake.append({"session_id": "s2", "ts": "2026-10-10T01:00:00Z", "context_tokens": 5,
                     "after_reset": False})
        answer = evals.auto_trim(Path("/nowhere"), days=7, reader=lambda h, d, n: fake)
        assert_observational(self, answer)
        self.assertEqual(answer["state"], "ready")
        self.assertEqual((answer["sessions_with_reset"], answer["sessions_without_reset"]), (1, 1))
        self.assertEqual(answer["before"], {"calls": 3, "median_context": 200_000,
                                            "mean_context": 200_000, "over_warn_share": 0.667})
        self.assertEqual(answer["after"]["median_context"], 50_000)
        self.assertEqual(answer["median_change"], -150_000)
        self.assertEqual(answer["sessions"][0]["reset_at"], "2026-10-10T00:00:03Z")

    def test_no_reset_in_the_window_is_empty(self):
        fake = [{"session_id": "s", "ts": "t", "context_tokens": 1, "after_reset": False}]
        answer = evals.auto_trim(Path("/nowhere"), reader=lambda h, d, n: fake)
        self.assertEqual((answer["state"], answer["median_change"]), ("empty", None))

    def test_without_the_transcript_index_it_is_unavailable(self):
        with mock.patch.dict(sys.modules, {TRANSCRIPTS: None}):
            self.assertIsNone(evals._context_calls(Path("/nowhere"), 7))
            answer = evals.auto_trim(Path("/nowhere"))
        assert_observational(self, answer)
        self.assertEqual(answer["state"], "unavailable")
        self.assertIn("transcript index", answer["reason"])

    def test_the_adapter_reads_the_index_calls_contract(self):
        seen = {}

        def calls(home, *, days=30, now=None, session_id=None, sidechain=False):
            seen.update(home=home, days=days, now=now)
            return [call("a", "2026-10-10T00:00:02Z", 30, "compact"),
                    call("a", "2026-10-10T00:00:01Z", 200),
                    call("a", "2026-10-10T00:00:03Z", 40),
                    call("b", "2026-10-10T00:00:01Z", 10, "reseed")]
        module = types.ModuleType(TRANSCRIPTS)
        module.calls = calls
        with mock.patch.dict(sys.modules, {TRANSCRIPTS: module}):
            found = evals._context_calls(Path("/h"), 9, NOW)
        self.assertEqual(seen, {"home": Path("/h"), "days": 9, "now": NOW})
        self.assertEqual([(c["session_id"], c["context_tokens"], c["after_reset"]) for c in found],
                         [("a", 200, False), ("a", 30, True), ("a", 40, True), ("b", 10, True)])


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.home = fixture_home(self)

    def test_bad_queries_are_refused(self):
        for kind, query, error in (
                ("before-after", {"days": "0"}, "invalid_request"),
                ("before-after", {"days": "x"}, "invalid_request"),
                ("before-after", {"days": "366"}, "invalid_request"),
                ("before-after", {"surface": ""}, "invalid_request"),
                ("before-after", {"surface": "nonesuch"}, "unknown_surface"),
                ("auto-trim", {"surface": "stances"}, "invalid_request"),
                ("preregistrations", {"days": "3"}, "invalid_request"),
                ("nonesuch", {}, "invalid_request")):
            with self.subTest(kind=kind, query=query):
                self.assertEqual(evals.answer(kind, query, repo=REPO, home=self.home.root),
                                 (400, {"error": error}))

    def test_each_answer_matches_its_route_schema(self):
        for kind, schema in (("preregistrations", server.EVALS_PREREGISTRATIONS),
                             ("evidence", server.EVALS_COMPATIBILITY),
                             ("before-after", server.EVALS_BEFORE_AFTER),
                             ("auto-trim", server.EVALS_AUTO_TRIM)):
            with self.subTest(kind=kind):
                status, payload = evals.answer(kind, {}, repo=REPO, home=self.home.root)
                self.assertEqual(status, 200, payload)
                schema.validate(payload)


class EvalsRouteTests(studio_security.StudioSecurityFixture):
    ROUTES = ("/api/evals/preregistrations", "/api/evals/evidence",
              "/api/evals/before-after?days=30", "/api/evals/auto-trim?days=30")

    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [usage_row(now - dt.timedelta(days=n), voice="answer-card") for n in (20, 12)]
        rows += [usage_row(now - dt.timedelta(days=n), voice="concise") for n in (2, 1)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_each_route_answers_its_shape(self):
        for path in self.ROUTES:
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200, body)
                self.assertEqual(headers.get_content_type(), "application/json")
        _s, _h, body = self.get("/api/evals/before-after?days=30&surface=stances&item=voice")
        document = json.loads(body)
        self.assertEqual(document["label"], "observational")
        self.assertEqual([(b["from"], b["to"]) for b in document["boundaries"]],
                         [("answer-card", "concise")])
        _s, _h, body = self.get("/api/evals/preregistrations")
        self.assertTrue(json.loads(body)["items"])

    def test_anonymous_is_refused(self):
        for path in self.ROUTES:
            status, _headers, body = self.get(path, cookie=False)
            self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}), path)

    def test_bad_params_are_400(self):
        for query, error in (("?days=0", "invalid_request"), ("?days=abc", "invalid_request"),
                             ("?days=30&days=31", "invalid_request"),
                             ("?other=1", "invalid_request"),
                             ("?surface=nonesuch", "unknown_surface")):
            status, _headers, body = self.get("/api/evals/before-after" + query)
            self.assertEqual((status, json.loads(body)), (400, {"error": error}), query)
        status, _headers, body = self.get("/api/evals/auto-trim?days=-1")
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))

    def test_routes_without_a_query_refuse_one(self):
        status, _headers, _body = self.get("/api/evals/preregistrations?days=3")
        self.assertEqual(status, 404)

    def test_the_routes_are_read_only_and_name_their_commands(self):
        table = {(r.method, r.path): r for r in server.ROUTES.entries}
        for kind in evals.KINDS:
            route = table[("GET", "/api/evals/" + kind)]
            self.assertEqual((route.parity_exemption, route.request_media_type), (None, None))
            self.assertEqual(route.cli_command, evals.CLI_COMMANDS[kind])
            self.assertFalse(any(r.path == "/api/evals/" + kind and r.method == "POST"
                                 for r in server.ROUTES.entries))


class EvalsCliTests(unittest.TestCase):
    def test_the_cli_prints_the_observational_note_and_refuses_bad_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
            env.update(HOME=tmp, HARNESS_HOME=tmp)
            cli = [sys.executable, str(REPO / "bin" / "harness"), "evals"]
            done = subprocess.run(cli + ["auto-trim"], env=env, capture_output=True, text=True,
                                  timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(done.stdout.startswith(evals.OBSERVATIONAL_NOTE), done.stdout)
            done = subprocess.run(cli + ["before-after", "--days", "0", "--json"], env=env,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual((done.returncode, json.loads(done.stdout)),
                             (2, {"error": "invalid_request"}))


if __name__ == "__main__":
    unittest.main()
