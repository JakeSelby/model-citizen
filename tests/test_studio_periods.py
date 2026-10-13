# SPDX-License-Identifier: MIT
"""Setting periods for the Harness pages, and their route (AH-S369, #1321)."""
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import periods, server  # noqa: E402

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)
ROUTE = "/api/harness/periods"


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def usage_row(when, voice="concise", version="0.15.0", kind="session", fingerprint="a", **extra):
    row = {"kind": kind, "repo": "proj", "harness_version": version, "ended": stamp(when),
           "started": stamp(when - dt.timedelta(minutes=5)), "profile_fingerprint": fingerprint,
           "stances": {"voice": voice, "cost": "balanced"}, "input": 10, "output": 100,
           "cache_read": 900, "cache_write": 90}
    row.update(extra)
    return row


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

    def usage(self, rows):
        (self.state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def config(self, stances):
        (self.config_dir / "config.json").write_text(json.dumps({"stances": stances}, indent=2))

    def applies(self, rows):
        (self.state / "applies.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def days_ago(n, hours=0):
    return NOW - dt.timedelta(days=n, hours=hours)


class PeriodsModuleTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)

    def answer(self, surface="stances", unit=None):
        return periods.efficacy_rows(surface, unit, home=self.home.root, now=NOW)

    def test_a_stance_change_in_usage_rows_splits_two_periods_and_the_new_one_is_measuring(self):
        rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 15, 10)]
        rows += [usage_row(days_ago(n), voice="concise") for n in (3, 2, 1)]
        self.home.usage(rows)
        found = periods.periods("stances", "voice", home=self.home.root, now=NOW)
        self.assertEqual([(p["value"], p["source"], p["rows"]) for p in found],
                         [("answer-card", "usage", 3), ("concise", "usage", 3)])
        self.assertEqual(found[0]["end"], found[1]["start"])
        self.assertIsNone(found[1]["end"])
        self.assertTrue(all(p["attributable"] for p in found))
        self.assertEqual(set(found[0]), {"surface", "unit", "value", "start", "end", "source",
                                         "rows", "attributable", "overlaps"})
        answer = self.answer(unit="voice")
        self.assertNotIn("note", answer)
        before, after = answer["rows"]
        self.assertEqual((before["status"], after["status"]), ("measured", "measuring"))
        self.assertEqual(after["measuring_day"], 4)
        self.assertNotIn("measuring_day", before)
        self.assertEqual(after["window_days"], 7)
        self.assertEqual(after["metrics"], {"sessions": 3, "agents": 0, "output_tokens": 300,
                                            "cache_read_share": 0.9})
        self.assertEqual(set(after), {"item_id", "item_label", "setting_label", "period", "status",
                                      "measuring_day", "window_days", "metrics", "evals_href"})
        self.assertEqual((after["item_id"], after["item_label"], after["setting_label"]),
                         ("voice", "Voice", "concise"))

    def test_concurrent_values_are_overlap_not_a_boundary_and_fingerprints_never_split(self):
        rows = [usage_row(days_ago(n), voice="concise", fingerprint=str(n))
                for n in range(20, 1, -1)]
        rows += [usage_row(days_ago(n, 1), voice="answer-card") for n in (19, 10, 3)]
        self.home.usage(rows)
        self.home.config({"voice": "concise"})
        answer = self.answer(unit="voice")
        self.assertEqual(len(answer["rows"]), 1)
        self.assertEqual(answer["rows"][0]["setting_label"], "concise")
        self.assertEqual(answer["note"], periods.NOTE)
        found = periods.periods("stances", "voice", home=self.home.root, now=NOW)
        self.assertEqual((found[0]["overlaps"], found[0]["source"]), (3, "none"))

    def test_no_boundary_answers_one_period_per_item_with_the_note(self):
        self.home.usage([usage_row(days_ago(n)) for n in (12, 9, 2)])
        self.home.config({"voice": "concise", "cost": "balanced", "testing": "required"})
        answer = self.answer()
        self.assertEqual(sorted(r["item_id"] for r in answer["rows"]), ["cost", "testing", "voice"])
        self.assertEqual(answer["note"], periods.NOTE)
        self.assertTrue(all(r["period"]["end"] is None for r in answer["rows"]))
        self.assertEqual({r["status"] for r in answer["rows"]}, {"measured"})

    def test_a_completed_apply_is_a_boundary_with_its_prior_value(self):
        self.home.config({"testing": "pragmatic"})
        self.home.usage([usage_row(days_ago(n)) for n in (15, 5)])
        prior = base64.b64encode(json.dumps({"stances": {"testing": "required"}}).encode()).decode()
        intent = {"apply_id": "a1", "phase": "intent", "ts": stamp(days_ago(6)),
                  "prior_config": prior, "config": [{"key": "stances.testing", "action": "set",
                                                     "value": "pragmatic", "applied": "pragmatic"}]}
        self.home.applies([intent, {"apply_id": "a1", "phase": "completed",
                                    "ts": stamp(days_ago(6)), "doctor": "ok"},
                           {"apply_id": "a2", "phase": "intent", "ts": stamp(days_ago(4)),
                            "config": [{"key": "stances.testing", "action": "set",
                                        "value": "off", "applied": "off"}]}])
        found = periods.periods("stances", "testing", home=self.home.root, now=NOW)
        self.assertEqual([(p["value"], p["source"]) for p in found],
                         [("required", "applies"), ("pragmatic", "applies")])
        self.assertEqual(found[1]["start"], periods.iso(days_ago(6)))
        answer = self.answer(unit="testing")
        self.assertNotIn("note", answer)
        self.assertEqual(answer["rows"][1]["measuring_day"], 7)

    def test_a_config_commit_is_a_boundary(self):
        repo = self.home.config_dir
        env = dict({k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t")

        def git(*argv, when=None):
            extra = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {}
            subprocess.run(("git", "-C", str(repo), "-c", "commit.gpgsign=false") + argv,
                           check=True, capture_output=True,
                           env=dict(env, **extra))
        git("init", "-q")
        self.home.config({"autonomy": "execute"})
        git("add", "config.json")
        git("commit", "-q", "-m", "one", when=periods.iso(days_ago(40)))
        self.home.config({"autonomy": "propose"})
        git("commit", "-q", "-am", "two", when=periods.iso(days_ago(9)))
        self.home.usage([usage_row(days_ago(n)) for n in (20, 2)])
        found = periods.periods("stances", "autonomy", home=self.home.root, now=NOW)
        self.assertEqual([(p["value"], p["source"]) for p in found],
                         [("execute", "config-git"), ("propose", "config-git")])
        self.assertEqual(found[1]["start"], periods.iso(days_ago(9)))

    def test_harness_versions_are_the_sources_surface(self):
        self.home.usage([usage_row(days_ago(n), version="0.14.2") for n in (12, 10)]
                        + [usage_row(days_ago(n), version="0.15.0") for n in (8, 1)])
        answer = self.answer("sources")
        self.assertEqual([(r["item_id"], r["setting_label"], r["status"]) for r in answer["rows"]],
                         [("core", "0.14.2", "measured"), ("core", "0.15.0", "measured")])

    def test_rescanned_rows_are_not_read_as_the_session_profile(self):
        rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 10)]
        rows.append(usage_row(days_ago(5), voice="concise", stances_source="rescan"))
        self.home.usage(rows)
        self.assertEqual(len(periods.periods("stances", "voice", home=self.home.root, now=NOW)), 1)

    def test_an_unknown_surface_raises(self):
        with self.assertRaises(periods.UnknownSurface):
            periods.efficacy_rows("nonesuch", home=self.home.root, now=NOW)

    def test_a_registered_provider_is_served_without_touching_periods(self):
        def items(ctx):
            return {"thing": {"label": "Thing", "current": "on",
                              "observations": [(r["_ts"], r["kind"], "s") for r in ctx.usage]}}
        periods.register_surface("fake", items, lambda rows, unit, value: {"n": len(rows)})
        self.addCleanup(periods._REGISTRY.pop, "fake", None)
        self.home.usage([usage_row(days_ago(9), kind="a"), usage_row(days_ago(2), kind="b")])
        self.assertIn("fake", periods.surfaces())
        answer = self.answer("fake")
        self.assertEqual([(r["setting_label"], r["metrics"]) for r in answer["rows"]],
                         [("a", {"n": 1}), ("b", {"n": 1})])

    def test_reads_are_cached_until_the_log_changes(self):
        self.home.usage([usage_row(days_ago(3))])
        first = self.answer()
        self.assertIs(periods._jsonl(self.home.state / "usage.jsonl", "ended"),
                      periods._jsonl(self.home.state / "usage.jsonl", "ended"))
        self.home.usage([usage_row(days_ago(3)), usage_row(days_ago(2))])
        os.utime(self.home.state / "usage.jsonl", ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        self.assertNotEqual(first, self.answer())

    def test_thirty_days_of_rows_answer_under_two_seconds(self):
        rows = [usage_row(NOW - dt.timedelta(minutes=10 * i),
                          voice="concise" if i < 2000 else "answer-card") for i in range(4300)]
        self.home.usage(rows)
        started = time.perf_counter()
        for surface in ("stances", "sources"):
            self.answer(surface)
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_state_files_are_only_read(self):
        self.home.usage([usage_row(days_ago(3))])
        self.home.config({"voice": "concise"})
        before = {p: p.stat().st_mtime_ns for p in self.home.root.rglob("*") if p.is_file()}
        self.answer()
        after = {p: p.stat().st_mtime_ns for p in self.home.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class PeriodsRouteTests(studio_security.StudioSecurityFixture):
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

    def test_the_route_answers_period_rows(self):
        status, headers, body = self.get(ROUTE + "?surface=stances&days=30")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual(set(document), {"surface", "rows"})
        voice = [r for r in document["rows"] if r["item_id"] == "voice"]
        self.assertEqual([(r["setting_label"], r["status"]) for r in voice],
                         [("answer-card", "measured"), ("concise", "measuring")])

    def test_a_surface_with_no_change_carries_the_note(self):
        status, _headers, body = self.get(ROUTE + "?surface=sources")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["note"], periods.NOTE)

    def test_an_unknown_surface_or_bad_query_is_400(self):
        for query, error in (("?surface=nonesuch", "unknown_surface"),
                             ("", "invalid_request"), ("?days=30", "invalid_request"),
                             ("?surface=stances&days=0", "invalid_request"),
                             ("?surface=stances&days=x", "invalid_request"),
                             ("?surface=stances&surface=sources", "invalid_request"),
                             ("?surface=stances&other=1", "invalid_request")):
            status, _headers, body = self.get(ROUTE + query)
            self.assertEqual((status, json.loads(body)), (400, {"error": error}), query)

    def test_anonymous_is_refused_like_its_neighbours(self):
        for path in (ROUTE + "?surface=stances", ROUTE, "/api/overview"):
            status, _headers, body = self.get(path, cookie=False)
            self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}), path)
        status, _headers, _body = self.request("GET", ROUTE + "?surface=stances",
                                               {"Cookie": self.cookie_value}, host="evil.localhost")
        self.assertEqual(status, 403)

    def test_other_routes_still_refuse_a_query_string(self):
        status, _headers, _body = self.get("/api/overview?surface=stances")
        self.assertEqual(status, 404)

    def test_the_route_is_registered_read_only_with_its_query(self):
        route = {(r.method, r.path): r for r in server.ROUTES.entries}[("GET", ROUTE)]
        self.assertEqual((route.query, route.parity_exemption), (("surface", "days"), "page-read"))
        self.assertIsNone(route.request_media_type)


if __name__ == "__main__":
    unittest.main()
