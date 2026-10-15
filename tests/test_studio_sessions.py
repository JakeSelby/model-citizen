# SPDX-License-Identifier: MIT
"""The Sessions page's routes: cost per turn, context per call against the 160k and 400k lines,
reset events and sessions by cost, read from the transcript index (AH-S377, #1329).

Every transcript here is synthetic, written into a temporary home.
Run: python3 -m unittest discover -s tests -p test_studio_sessions.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_periods as base  # noqa: E402
import test_studio_security as studio_security  # noqa: E402
import test_studio_transcripts as tx  # noqa: E402
from harness_core.studio import server, sessions, transcripts  # noqa: E402

NOW = tx.NOW
CLI = Path(__file__).resolve().parents[1] / "bin" / "harness"
LIST = "/api/sessions"
SHOW = "/api/sessions/session"
UNPRICED = "not-a-priced-model"


def busy_session():
    """Three turns: 100k, 200k and 450k of context, a compaction then a reseed before the last."""
    return [tx.prompt(0), tx.reply(1, "m1", input=10, read=99990, output=50),
            tx.prompt(2), tx.reply(3, "m2", input=10, read=199990, output=50),
            tx.prompt(4), tx.compact(5, pre=210000, post=9000), tx.reseed(6),
            tx.reply(7, "m3", input=10, read=449990, output=50)]


def join(home):
    thread = transcripts._WARMING.get(str(home))
    if thread is not None:
        thread.join(20)


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)
        self.files = tx.Transcripts(self.home)
        self.addCleanup(join, self.home.root)

    def overview(self, days=30):
        transcripts.sessions(self.home.root, days=days, now=NOW)  # warm the cache first
        return sessions.overview(self.home.root, days=days, now=NOW)

    def test_the_list_counts_calls_over_each_line_and_ranks_sessions_by_cost(self):
        self.files.write("busy", busy_session())
        self.files.write("quiet", [tx.prompt(10), tx.reply(11, "q1", input=50000, model=UNPRICED)])
        found = self.overview()
        self.assertEqual(set(found), {"window_days", "indexing", "pending", "lines", "basis",
                                      "summary", "histogram", "sessions", "events"})
        self.assertEqual(found["lines"], {"warn": 160000, "limit": 400000})
        self.assertFalse(found["indexing"])
        summary = found["summary"]
        self.assertEqual((summary["sessions"], summary["calls"]), (2, 4))
        self.assertEqual(summary["median_context"], 150000)
        self.assertEqual((summary["over_warn"], summary["over_limit"]), (2, 1))
        self.assertEqual(summary["unpriced_calls"], 1)
        self.assertEqual((summary["compacts"], summary["auto_compacts"], summary["reseeds"]), (1, 1, 1))
        self.assertEqual([s["session_id"] for s in found["sessions"]], ["busy", "quiet"])
        busy = found["sessions"][0]
        self.assertEqual((busy["calls"], busy["turns"], busy["max_context"], busy["over_limit"]),
                         (3, 3, 450000, 1))
        self.assertAlmostEqual(busy["cost_per_turn"], busy["cost"] / 3, places=6)
        self.assertIsNone(found["sessions"][1]["cost"])
        self.assertEqual(summary["turns"], 3, "an unpriced turn has no cost to count")
        self.assertEqual([e["kind"] for e in found["events"]], ["reseed", "compact"])

    def test_the_histogram_has_50k_buckets_and_an_open_last_one(self):
        self.files.write("busy", busy_session())
        buckets = self.overview()["histogram"]
        self.assertEqual(len(buckets), 13)
        self.assertEqual((buckets[0]["from"], buckets[0]["to"]), (0, 50000))
        self.assertEqual((buckets[-1]["from"], buckets[-1]["to"]), (600000, None))
        self.assertEqual({b["from"]: b["calls"] for b in buckets if b["calls"]},
                         {100000: 1, 200000: 1, 450000: 1})

    def test_subagent_calls_stay_out_of_the_context_figures(self):
        side = [tx.reply(2, "a1", input=10, read=700000, sidechain=True)]
        self.files.write("main", [tx.prompt(0), tx.reply(1, "m1", read=1000)], subagents={"x": side})
        found = self.overview()
        self.assertEqual((found["summary"]["calls"], found["summary"]["over_limit"]), (1, 0))
        self.assertEqual(found["sessions"][0]["subagent_calls"], 1)

    def test_calls_before_the_window_are_not_counted_though_their_session_is_listed(self):
        old = dict(tx.reply(1, "m0", read=300000), timestamp=base.stamp(NOW - dt.timedelta(days=9)))
        self.files.write("long", [old, tx.prompt(0), tx.reply(1, "m1", read=1000)])
        found = self.overview(days=7)
        self.assertEqual((found["summary"]["calls"], found["summary"]["over_warn"]), (1, 0))
        self.assertEqual(found["sessions"][0]["max_context"], 300010)

    def test_a_cold_index_answers_at_once_and_says_it_is_still_indexing(self):
        self.files.write("busy", busy_session())
        with mock.patch.object(transcripts, "warm") as warm:
            cold = sessions.overview(self.home.root, days=30, now=NOW)
        warm.assert_called_once()
        self.assertEqual((cold["indexing"], cold["pending"], cold["sessions"], cold["summary"]["calls"]),
                         (True, 1, [], 0))
        sessions.overview(self.home.root, days=30, now=NOW)
        join(self.home.root)
        warmed = sessions.overview(self.home.root, days=30, now=NOW)
        self.assertEqual((warmed["indexing"], warmed["pending"], len(warmed["sessions"])), (False, 0, 1))

    def test_an_empty_home_answers_with_no_sessions(self):
        found = sessions.overview(self.home.root, days=30, now=NOW)
        self.assertEqual((found["sessions"], found["summary"]["median_context"], found["indexing"]),
                         ([], 0, False))

    def test_the_basis_calls_the_figures_observed_never_evidence(self):
        self.assertIn("Observed in your transcripts", sessions.BASIS)
        self.assertNotIn("evidence", sessions.BASIS.lower())


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)
        self.files = tx.Transcripts(self.home)
        self.addCleanup(join, self.home.root)

    def test_a_bad_window_is_refused(self):
        for days in ("0", "366", "-1", "abc", "", "3.5", "٣"):
            with self.subTest(days=days):
                self.assertEqual(sessions.answer_list(days, home=self.home.root),
                                 (400, {"error": "invalid_request"}))
        status, found = sessions.answer_list(None, home=self.home.root)
        self.assertEqual((status, found["window_days"]), (200, 30))

    def test_a_malformed_id_is_400_and_an_unknown_one_404(self):
        for bad in (None, "", "../busy", "a/b", "-dash", "x" * 129):
            with self.subTest(id=bad):
                self.assertEqual(sessions.answer_show(bad, home=self.home.root),
                                 (400, {"error": "invalid_request"}))
        self.assertEqual(sessions.answer_show("absent", home=self.home.root),
                         (404, {"error": "unknown_session"}))

    def test_one_session_has_context_per_call_with_its_reset_marked(self):
        self.files.write("busy", busy_session())
        status, found = sessions.answer_show("busy", home=self.home.root)
        self.assertEqual(status, 200)
        self.assertEqual(set(found), {"session", "lines", "basis", "calls", "turns", "events"})
        server.SESSIONS_SHOW.validate(found)
        self.assertEqual([c["context_tokens"] for c in found["calls"]], [100000, 200000, 450000])
        self.assertEqual([c["event"] for c in found["calls"]], [None, None, "reseed"])
        self.assertEqual([c["index"] for c in found["calls"]], [0, 1, 2])
        self.assertEqual([c["turn"] for c in found["calls"]], [0, 1, 2])
        self.assertEqual([e["kind"] for e in found["events"]], ["compact", "reseed"])
        self.assertEqual(found["session"]["session_id"], "busy")


class SessionsRouteTests(studio_security.StudioSecurityFixture):
    """The two routes over HTTP, against a Studio started on a temporary home."""

    def setUp(self):
        super().setUp()
        self.project = self.home / ".claude" / "projects" / "-work-proj"
        self.project.mkdir(parents=True)
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def write(self, session_id, entries):
        # The fixture's stamps are moved to the real clock, since the Studio windows on it.
        shift = dt.datetime.now(dt.timezone.utc) - NOW
        lines = []
        for entry in entries:
            entry = dict(entry)
            entry["timestamp"] = base.stamp(base.periods.parse_ts(entry["timestamp"]) + shift)
            lines.append(json.dumps(entry))
        path = self.project / (session_id + ".jsonl")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def get(self, path, cookie=True):
        status, headers, body = self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})
        return status, headers, json.loads(body)

    def settled(self, path=LIST + "?days=30"):
        for _ in range(100):
            status, _headers, document = self.get(path)
            if status != 200 or not document["indexing"]:
                return status, document
            time.sleep(0.1)
        self.fail("the index never finished warming")

    def test_the_list_route_answers_the_page_shape(self):
        self.write("busy", busy_session())
        status, document = self.settled()
        self.assertEqual(status, 200, document)
        server.SESSIONS_LIST.validate(document)
        self.assertEqual(document["summary"]["calls"], 3)
        self.assertEqual(document["sessions"][0]["session_id"], "busy")

    def test_a_cold_index_is_answered_as_still_indexing(self):
        self.write("busy", busy_session())
        status, _headers, cold = self.get(LIST)
        self.assertEqual((status, cold["indexing"], cold["pending"]), (200, True, 1))
        _status, warmed = self.settled(LIST)
        self.assertEqual((warmed["pending"], len(warmed["sessions"])), (0, 1))

    def test_one_session_is_read_by_id(self):
        self.write("busy", busy_session())
        status, _headers, document = self.get(SHOW + "?id=busy")
        self.assertEqual(status, 200, document)
        self.assertEqual(len(document["calls"]), 3)

    def test_bad_parameters_are_refused(self):
        for path, expected in ((LIST + "?days=0", (400, "invalid_request")),
                               (LIST + "?days=many", (400, "invalid_request")),
                               (LIST + "?window=7", (400, "invalid_request")),
                               (SHOW, (400, "invalid_request")),
                               (SHOW + "?id=..%2Fbusy", (400, "invalid_request")),
                               (SHOW + "?id=absent", (404, "unknown_session"))):
            with self.subTest(path=path):
                status, _headers, document = self.get(path)
                self.assertEqual((status, document), (expected[0], {"error": expected[1]}))

    def test_anonymous_is_refused(self):
        for path in (LIST, SHOW + "?id=busy"):
            with self.subTest(path=path):
                status, _headers, document = self.get(path, cookie=False)
                self.assertEqual((status, document), (401, {"error": "unauthorized"}))


class SessionsCliTests(unittest.TestCase):
    def setUp(self):
        self.home = base.Home(self)
        self.files = tx.Transcripts(self.home)
        env = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
        self.env = dict(env, HOME=str(self.home.root), HARNESS_HOME=str(self.home.root))

    def cli(self, *argv):
        return subprocess.run([sys.executable, str(CLI), "sessions", *argv], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_show_prints_each_call_with_its_marker(self):
        self.files.write("busy", busy_session())
        done = self.cli("show", "busy")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("450000\treseed", done.stdout)

    def test_a_refusal_exits_2_and_names_the_error(self):
        done = self.cli("list", "--days", "0")
        self.assertEqual((done.returncode, done.stderr.strip()), (2, "citizen sessions list: invalid_request"))


if __name__ == "__main__":
    unittest.main()
