# SPDX-License-Identifier: MIT
"""Home: the five attention sources, the digest, the five-item cap and `GET /api/home` (AH-S380, #1332).

Every home here is temporary and every reading synthetic. Rule health and the overview run engine
subprocesses, so their documents are stubbed at the function the source reads; drafts are stubbed
at the Changes inbox, which would otherwise list this checkout's real draft worktrees.
Run: python3 -m unittest discover -s tests -p test_studio_home.py
"""
import datetime as dt
import http.client
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
import test_studio_security as studio_security  # noqa: E402
import test_studio_transcripts as fixture  # noqa: E402
from harness_core import overview  # noqa: E402
from harness_core.studio import attention, home as studio_home, server, transcripts  # noqa: E402
from harness_core.studio.attention_sources import (  # noqa: E402
    context as context_source, drafts as drafts_source, drift as drift_source,
    hooks as hooks_source, rules as rules_source)
from harness_core.studio.surfaces import rules as rule_surface  # noqa: E402

ROUTE = "/api/home"
SOURCES = ("context", "drafts", "drift", "hooks", "rules")


def now():
    return dt.datetime.now(dt.timezone.utc)


def decision(when, point):
    return {"kind": "decision", "decision_id": "%s-%s" % (point, when.isoformat()), "point": point,
            "session_id": "s1", "ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "deterministic_answer": "allow", "outcome": None, "module": "hooks/" + point,
            "schema_version": 1}


def rule_row(unit, state="not-fired", selection="on", status="measured", reliable=True):
    return {"kind": "rules", "unit": unit, "rule": "Rule " + unit, "selection_state": selection,
            "hits": {"status": status, "reliable": reliable,
                     "last_fired": {"state": state, "days": 90}}}


def rule_report(*rows):
    return {"rows": list(rows)}


def drift_overview(status="current", items=()):
    return lambda: {"drift": {"status": status, "message": None if status != "failed" else "no read",
                              "items": list(items), "command": "citizen sync"}}


class HomeCase(unittest.TestCase):
    """A temporary home, a clean read cache, the overview and the slow engines stubbed."""

    def setUp(self):
        self.home = base.Home(self)
        studio_home.clear_cache()
        self.addCleanup(studio_home.clear_cache)
        saved = overview._SOURCE
        self.addCleanup(lambda: setattr(overview, "_SOURCE", saved))
        overview.install(drift_overview())
        self.rules = rule_report()
        self.drafts = {"schema_version": 1, "drafts": []}
        for target, name, value in ((rule_surface, "load_report", lambda: self.rules),
                                    (drafts_source.changes, "inbox", lambda *a: self.drafts)):
            patcher = mock.patch.object(target, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertLessEqual(set(SOURCES), set(attention.sources()))

    def queue(self):
        return attention.queue(self.home.root)

    def decisions(self, rows):
        (self.home.state / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def items(self, source):
        return [item for item in self.queue()["items"] if item["source"] == source]

    def transcript(self, session_id, contexts, age_hours=1):
        """A main-thread transcript, one call per context size, last written `age_hours` ago."""
        files = fixture.Transcripts(self.home)
        entries = [fixture.prompt(0)] + [fixture.reply(n + 1, "%s-m%d" % (session_id, n), read=size)
                                         for n, size in enumerate(contexts)]
        path = files.write(session_id, entries)
        when = time.time() - age_hours * 3600
        os.utime(str(path), (when, when))
        return path

    def warm(self):
        transcripts.warm(self.home.root, days=1)
        thread = transcripts._WARMING.get(str(self.home.root))
        if thread is not None:
            thread.join(30)


class RuleSourceTests(HomeCase):
    def test_a_rule_switched_on_that_never_fired_is_an_item_linked_to_rules(self):
        self.rules = rule_report(rule_row("read-narrowly"))
        found = self.items("rules")
        self.assertEqual([(i["id"], i["kind"], i["action"]) for i in found],
                         [("rules:read-narrowly", "rule-silent",
                           {"label": "Review rule", "href": "/harness/rules"})])
        self.assertIn("Exploratory", found[0]["detail"])

    def test_a_rule_that_fired_is_off_or_unmeasured_is_no_item(self):
        self.rules = rule_report(rule_row("a", state="fired"), rule_row("b", selection="off"),
                                 rule_row("c", status="not measured"))
        self.assertEqual(self.items("rules"), [])
        self.assertEqual(self.queue()["sources"]["rules"], "ok")

    def test_rule_health_failing_is_an_error_and_home_still_answers(self):
        self.rules = None  # `rules(None)` raises: the engine's document is unreadable.
        self.drafts["drafts"] = [{"name": "keep", "status": "tested", "created_at": None}]
        result = studio_home.report(self.home.root)
        self.assertEqual(result["queue"]["sources"]["rules"], "error")
        self.assertEqual([i["id"] for i in result["queue"]["items"]], ["drafts:keep"])


class HookSourceTests(HomeCase):
    def test_a_hook_that_fired_then_stopped_is_an_item_linked_to_its_page(self):
        self.decisions([decision(now() - dt.timedelta(days=10), "grade-bash"),
                             decision(now() - dt.timedelta(hours=2), "stop-gate")])
        found = self.items("hooks")
        self.assertEqual([(i["id"], i["kind"], i["action"]["href"]) for i in found],
                         [("hooks:grade-bash", "hook-silent", "/harness/hooks/grade-bash")])

    def test_a_live_hook_or_one_that_never_fired_is_no_item(self):
        self.decisions([decision(now() - dt.timedelta(hours=2), "grade-bash")])
        self.assertEqual(self.items("hooks"), [])
        self.assertEqual(self.queue()["sources"]["hooks"], "ok")

    def test_the_hooks_surface_failing_is_an_error(self):
        with mock.patch.object(hooks_source.periods, "efficacy_rows", side_effect=OSError("unreadable")):
            self.assertEqual(self.queue()["sources"]["hooks"], "error")

    def test_a_built_in_point_links_with_its_id_escaped(self):
        self.assertEqual(hooks_source.href("point:evasion-deny"), "/harness/hooks/point%3Aevasion-deny")


class DraftSourceTests(HomeCase):
    def test_tested_and_open_drafts_are_items_linked_to_their_card(self):
        self.drafts["drafts"] = [
            {"name": "ready one", "status": "tested", "created_at": "2026-10-14T10:00:00Z"},
            {"name": "fresh", "status": "open", "created_at": "2026-10-15T10:00:00Z"}]
        found = {i["id"]: i for i in self.items("drafts")}
        self.assertEqual(found["drafts:ready one"]["action"],
                         {"label": "Review draft", "href": "/changes?draft=ready+one"})
        self.assertEqual((found["drafts:ready one"]["severity"], found["drafts:fresh"]["severity"]),
                         ("medium", "low"))

    def test_applied_and_rolled_back_drafts_are_no_item(self):
        self.drafts["drafts"] = [{"name": "a", "status": "applied"}, {"name": "b", "status": "rolled back"}]
        self.assertEqual(self.items("drafts"), [])

    def test_the_inbox_failing_is_an_error(self):
        self.drafts = None
        self.assertEqual(self.queue()["sources"]["drafts"], "error")

    def test_the_inbox_is_read_for_this_home(self):
        self.queue()
        args = drafts_source.changes.inbox.call_args[0]
        self.assertEqual((args[1], args[2]), (self.home.root, self.home.state / "studio"))


class DriftSourceTests(HomeCase):
    def test_drift_is_one_item_linked_to_the_drift_on_home(self):
        overview.install(drift_overview("drift", ["~/.claude/CLAUDE.md differs", "rules/x.md missing"]))
        found = self.items("drift")
        self.assertEqual([(i["id"], i["action"]["href"]) for i in found], [("drift:projection", "/changes#governed-sync")])
        self.assertIn("2 projected files", found[0]["title"])
        self.assertEqual(studio_home.report(self.home.root)["drift"]["items"][1], "rules/x.md missing")

    def test_no_drift_is_no_item(self):
        self.assertEqual(self.items("drift"), [])
        self.assertEqual(self.queue()["sources"]["drift"], "ok")

    def test_an_unreadable_overview_is_an_error_never_a_clean_reading(self):
        overview.install(drift_overview("failed"))
        self.assertEqual(self.queue()["sources"]["drift"], "error")
        overview._SOURCE = None
        studio_home.clear_cache()
        result = studio_home.report(self.home.root)
        self.assertEqual((result["queue"]["sources"]["drift"], result["drift"]["status"]), ("error", "failed"))


class ContextSourceTests(HomeCase):
    def test_sessions_past_the_limit_and_past_the_warn_line_are_two_items_linked_to_sessions(self):
        self.transcript("big", [500000, 20000])
        self.transcript("warm", [200000])
        self.transcript("small", [20000])
        self.warm()
        found = {i["id"]: i for i in self.items("context")}
        self.assertEqual(sorted(found), ["context:over-limit", "context:over-warn"])
        self.assertEqual(found["context:over-limit"]["severity"], "high")
        self.assertEqual(found["context:over-limit"]["action"], {"label": "See sessions", "href": "/sessions"})
        self.assertIn("1 session in the last day", found["context:over-limit"]["title"])
        self.assertIn("observational reading", found["context:over-warn"]["detail"])

    def test_small_or_older_sessions_are_no_item(self):
        self.transcript("small", [20000])
        self.transcript("old", [500000], age_hours=30)
        self.warm()
        self.assertEqual(self.items("context"), [])

    def test_a_cold_index_answers_at_once_with_no_item(self):
        self.transcript("big", [500000])
        with mock.patch.object(transcripts, "warm"), \
                mock.patch.object(transcripts, "build", side_effect=AssertionError("parsed on the request")):
            self.assertEqual(self.items("context"), [])
            self.assertEqual(self.queue()["sources"]["context"], "ok")
            self.assertTrue(studio_home.report(self.home.root)["digest"]["indexing"])

    def test_the_index_failing_is_an_error(self):
        with mock.patch.object(context_source.transcripts, "snapshot", side_effect=OSError("no")):
            self.assertEqual(self.queue()["sources"]["context"], "error")


class DigestAndCapTests(HomeCase):
    def test_the_digest_sums_the_last_day_and_says_it_is_observational(self):
        self.transcript("big", [500000, 20000])
        self.transcript("warm", [200000])
        self.warm()
        digest = studio_home.report(self.home.root)["digest"]
        self.assertEqual({k: digest[k] for k in ("status", "sessions", "calls", "calls_over_limit",
                                                  "calls_over_warn", "sessions_over_limit",
                                                  "max_context", "indexing")},
                         {"status": "ready", "sessions": 2, "calls": 3, "calls_over_limit": 1,
                          "calls_over_warn": 2, "sessions_over_limit": 1, "max_context": 500010,
                          "indexing": False})
        self.assertEqual(digest["median_context"], 200010)
        self.assertIn("observational reading", digest["basis"])
        self.assertIn("not evidence", digest["basis"])

    def test_a_failing_digest_leaves_the_queue(self):
        self.drafts["drafts"] = [{"name": "keep", "status": "open"}]
        with mock.patch.object(studio_home, "digest", side_effect=OSError("no index")):
            result = studio_home.report(self.home.root)
        self.assertEqual(result["digest"], {"status": "unavailable", "message": "no index"})
        self.assertEqual([i["id"] for i in result["queue"]["items"]], ["drafts:keep"])

    def test_every_source_holding_caps_the_queue_at_five_with_one_action_each(self):
        self.rules = rule_report(rule_row("a"), rule_row("b"))
        self.drafts["drafts"] = [{"name": "d%d" % n, "status": "tested"} for n in range(3)]
        overview.install(drift_overview("drift", ["one"]))
        self.decisions([decision(now() - dt.timedelta(days=10), "grade-bash")])
        self.transcript("big", [500000])
        self.warm()
        result = studio_home.report(self.home.root)
        queue = result["queue"]
        self.assertEqual(len(queue["items"]), 5)
        self.assertEqual(queue["total"], 2 + 3 + 1 + 1 + 1)
        # Phase C registers its own sources (routines, evals) alongside these; none may fail.
        self.assertLessEqual(set(SOURCES), set(queue["sources"]))
        self.assertEqual(set(queue["sources"].values()), {"ok"})
        self.assertEqual(queue["items"][0]["id"], "context:over-limit")
        for item in queue["items"]:
            self.assertEqual(set(item["action"]), {"label", "href"})
            self.assertTrue(item["action"]["href"].startswith("/"))

    def test_a_slow_read_is_reused_until_it_expires(self):
        calls = []
        studio_home.register_read("probe", lambda home: calls.append(home) or len(calls))
        self.addCleanup(studio_home._READS.pop, "probe")
        self.assertEqual((studio_home.read("probe", self.home.root), studio_home.read("probe", self.home.root)), (1, 1))
        with mock.patch.object(studio_home.time, "monotonic", return_value=time.monotonic() + studio_home.READ_TTL + 1):
            self.assertEqual(studio_home.read("probe", self.home.root), 2)

    def test_a_failed_read_is_not_kept(self):
        answers = [OSError("first"), "second"]

        def flaky(home):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        studio_home.register_read("flaky", flaky)
        self.addCleanup(studio_home._READS.pop, "flaky")
        studio_home.prefetch(self.home.root)
        self.assertEqual(studio_home.read("flaky", self.home.root), "second")


class HomeRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        (state / "decisions.jsonl").write_text(
            json.dumps(decision(now() - dt.timedelta(days=10), "grade-bash")) + "\n")
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True, host=None):
        # The first Home read runs the rule-health engines, longer than the fixture's timeout.
        connection = http.client.HTTPConnection("127.0.0.1", self.started["port"], timeout=180)
        headers = {"Host": self.record["host"] if host is None else host}
        if cookie:
            headers["Cookie"] = self.cookie_value
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.headers, response.read()
        connection.close()
        return result

    def test_the_route_answers_the_digest_drift_and_a_capped_queue(self):
        status, headers, body = self.get(ROUTE)
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual(set(document), {"schema_version", "generated_at", "digest", "drift", "queue"})
        server.HOME.validate(document)
        self.assertEqual(set(document["queue"]), {"items", "total", "sources"})
        self.assertLessEqual(len(document["queue"]["items"]), attention.LIMIT)
        self.assertLessEqual(set(SOURCES), set(document["queue"]["sources"]))
        silent = [i for i in document["queue"]["items"] if i["id"] == "hooks:grade-bash"]
        self.assertEqual([i["action"]["href"] for i in silent], ["/harness/hooks/grade-bash"])
        self.assertIn("observational reading", document["digest"]["basis"])

    def test_anonymous_and_a_foreign_host_are_refused(self):
        status, _headers, body = self.get(ROUTE, cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))
        status, _headers, _body = self.get(ROUTE, host="evil.localhost")
        self.assertEqual(status, 403)

    def test_a_query_string_is_refused_like_every_route_without_one(self):
        status, _headers, _body = self.get(ROUTE + "?limit=9")
        self.assertEqual(status, 404)

    def test_the_route_is_registered_read_only_with_its_command(self):
        route = {(r.method, r.path): r for r in server.ROUTES.entries}[("GET", ROUTE)]
        self.assertEqual((route.query, route.parity_exemption, route.request_media_type),
                         ((), None, None))
        self.assertEqual(route.cli_command, studio_home.CLI_COMMAND)
        self.assertNotIn(("POST", ROUTE), {(r.method, r.path) for r in server.ROUTES.entries})


if __name__ == "__main__":
    unittest.main()
