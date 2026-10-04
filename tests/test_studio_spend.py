# SPDX-License-Identifier: MIT
"""The Studio's spend report is `citizen usage --json`, unchanged, for every grouping it shows."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_core.studio import server, spend  # noqa: E402

import test_studio_security as studio_security  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "usage" / "studio-spend.jsonl"
# The fixture's rows are dated 2026; a ten-year window keeps them in it for as long as it lasts.
WINDOW = 3660
# The documents the frontend tests render, written by `python3 tests/test_studio_spend.py --write`.
FRONTEND_FIXTURE = ROOT / "studio" / "tests" / "fixtures" / "spend.json"


def fixture_as_of():
    """The oldest shipped date among the two models the fixture ledger's priced rows ran on."""
    models = json.loads((ROOT / "policy" / "prices.json").read_text())["models"]
    return min(models[name]["as_of"] for name in ("claude-opus-5", "claude-sonnet-5"))


def write_year(path):
    """A year of sessions, 20 a day with one subagent each: 14,600 ledger rows."""
    now = time.time()
    with Path(path).open("w", encoding="utf-8") as stream:
        for day in range(365):
            for index in range(20):
                ended = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(now - day * 86400 - index * 60))
                stream.write(json.dumps({
                    "kind": "session", "runtime": "claude-code",
                    "session_id": "s-%d-%d" % (day, index), "repo": "repo-%d" % (index % 7),
                    "models": ["claude-opus-5"], "started": ended, "ended": ended,
                    "input": 100, "output": 2000, "cache_read": 50000, "cache_write": 3000,
                    "days": {ended[:10]: {"input": 100, "output": 2000, "cache_read": 50000,
                                          "cache_write": 3000}},
                    "raw_vs_deduped": 1.2, "schema_version": 1}) + "\n")
                stream.write(json.dumps({
                    "kind": "subagent", "runtime": "claude-code",
                    "session_id": "s-%d-%d" % (day, index), "agent_type": "gatherer",
                    "model": "claude-sonnet-5", "started": ended, "ended": ended,
                    "input": 10, "output": 500, "cache_read": 1000, "cache_write": 100,
                    "tool_calls": 5, "schema_version": 1}) + "\n")


def environment(home):
    base = {key: value for key, value in os.environ.items()
            if key != "HARNESS_QUIET" and not key.startswith("HARNESS_")
            and key != "CLAUDE_CONFIG_DIR"}
    return dict(base, HOME=str(home), HARNESS_HOME=str(home), PYTHONDONTWRITEBYTECODE="1")


def install_ledger(home, source=FIXTURE):
    state = Path(home) / ".local" / "state" / "agent-harness"
    state.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(state / "usage.jsonl"))


def frontend_documents():
    """The Studio's answers for the fixture ledger, in a home of their own."""
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(os.path.realpath(temporary)) / "home"
        home.mkdir()
        install_ledger(home)
        with mock.patch.dict(os.environ, environment(home), clear=True):
            return {by: spend.report(ROOT, by, WINDOW) for by in ("session", "role")}


def cli_document(env, by, days):
    done = subprocess.run([sys.executable, str(ROOT / "bin" / "harness"), "usage", "--json",
                           "--by", by, "--days", str(days)], env=env, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


class SpendReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(os.path.realpath(temporary.name)) / "home"
        self.home.mkdir()
        install_ledger(self.home)
        self.env = environment(self.home)
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_ledger_grouping_is_the_cli_document_unchanged(self):
        for by in spend.LEDGER_GROUPINGS:
            with self.subTest(by=by):
                payload = spend.report(ROOT, by, WINDOW)
                expected = cli_document(self.env, by, WINDOW)
                self.assertEqual(payload["ledger"], expected)
                self.assertEqual(payload["command"],
                                 "citizen usage --json --by %s --days %d" % (by, WINDOW))
                if by != "role":
                    self.assertEqual(payload["ledger"]["totals"], expected["totals"])

    def test_the_fixture_exercises_sessions_subagents_codex_workers_and_studio_runs(self):
        sessions = spend.report(ROOT, "session", WINDOW)["ledger"]
        names = {group["name"]: group for group in sessions["groups"]}
        self.assertEqual(set(names), {"sess-alpha", "sess-beta", "worker-1", "codex-parent",
                                      "run-0001"})
        self.assertEqual(names["codex-parent"]["runs"], 2)
        self.assertIsNone(names["codex-parent"]["usd"])
        self.assertEqual(sessions["totals"]["runs"], 6)
        self.assertEqual(sessions["unpriced"], 2)
        roles = spend.report(ROOT, "role", WINDOW)["ledger"]
        self.assertEqual([group["name"] for group in roles["groups"]],
                         ["builder", "gatherer", "reviewer"])

    def test_every_figure_carries_the_list_price_label_and_pricing_date(self):
        as_of = fixture_as_of()
        for by in spend.LEDGER_GROUPINGS:
            with self.subTest(by=by):
                payload = spend.report(ROOT, by, WINDOW)
                self.assertEqual(payload["basis"], {"label": "list-price equivalent",
                                                    "cost_basis": "list_price_equivalent",
                                                    "price_as_of": as_of})
                self.assertEqual(payload["ledger"]["price_as_of"], as_of)

    def test_rebuild_attribution_is_the_cli_document_with_its_pricing_date(self):
        payload = spend.report(ROOT, "rebuild", 7)
        expected = cli_document(self.env, "rebuild", 7)
        self.assertEqual(payload["ledger"], expected)
        self.assertEqual(payload["ledger"]["report"], "rebuild")
        self.assertEqual((payload["ledger"]["unpriced"], payload["ledger"]["unpriced_calls"]),
                         (0, 0))
        # No transcript in this home, so nothing was priced and no date is claimed.
        self.assertEqual(payload["basis"], {"label": "list-price equivalent",
                                            "cost_basis": "list_price_equivalent",
                                            "price_as_of": None})

    def test_the_frontend_fixture_is_what_the_studio_answers_for_the_fixture_ledger(self):
        committed = json.loads(FRONTEND_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(committed, frontend_documents(),
                         "regenerate with: python3 tests/test_studio_spend.py --write")

    def test_an_empty_ledger_answers_with_empty_groups(self):
        (self.home / ".local" / "state" / "agent-harness" / "usage.jsonl").unlink()
        payload = spend.report(ROOT, "day", 30)
        self.assertEqual(payload["ledger"]["groups"], [])
        self.assertEqual(payload["ledger"]["totals"]["runs"], 0)

class SpendContractTests(unittest.TestCase):
    def test_requests_name_one_grouping_and_a_bounded_window(self):
        self.assertEqual(spend.parse({"by": "session", "days": 30}), ("session", 30))
        for request in ({"by": "stance", "days": 30}, {"by": "day", "days": 0},
                        {"by": "day", "days": True}, {"by": "day", "days": "30"},
                        {"by": "day", "days": spend.MAX_DAYS + 1},
                        {"by": "day", "days": 30, "rescan": True}, {"by": "day"}, []):
            with self.subTest(request=request), self.assertRaises(spend.SpendError):
                spend.parse(request)

    def answer(self, document=None, code=0, stdout=None):
        captured = []

        def run(argv, repo_root, environment, timeout, cancelled):
            captured.append({"argv": argv, "env": environment, "timeout": timeout})
            return code, json.dumps(document) if stdout is None else stdout

        return captured, mock.patch.object(spend, "_run", side_effect=run)

    @staticmethod
    def usage(**extra):
        document = {"schema_version": 1, "report": "usage", "by": "day", "days": 30,
                    "groups": [], "totals": {}, "price_as_of": "2026-09-01",
                    "cost_basis": "list_price_equivalent"}
        document.update(extra)
        return document

    def test_the_command_reads_the_ledger_and_never_exports_or_rescans(self):
        captured, patch = self.answer(self.usage())
        with mock.patch.dict(os.environ, {"HARNESS_QUIET": "1"}), patch:
            payload = spend.report(ROOT, "day", 30)
        argv = captured[0]["argv"]
        self.assertEqual(argv[1:], [str(ROOT / "bin" / "harness"), "usage", "--json", "--by",
                                    "day", "--days", "30"])
        self.assertNotIn("export", argv)
        self.assertNotIn("--rescan", argv)
        self.assertNotIn("HARNESS_QUIET", captured[0]["env"])
        self.assertEqual(payload["basis"], {"label": "list-price equivalent",
                                            "cost_basis": "list_price_equivalent",
                                            "price_as_of": "2026-09-01"})

    def test_the_label_comes_from_the_cost_basis_or_says_it_is_unknown(self):
        for document, label in ((self.usage(cost_basis="invoice"), "unknown basis"),
                                ({key: value for key, value in self.usage().items()
                                  if key != "cost_basis"}, "unknown basis"),
                                (self.usage(price_as_of="unknown"), "list-price equivalent")):
            _captured, patch = self.answer(document)
            with self.subTest(document=document), patch:
                basis = spend.report(ROOT, "day", 30)["basis"]
            self.assertEqual(basis["label"], label)
        _captured, patch = self.answer(self.usage(price_as_of="unknown"))
        with patch:
            self.assertEqual(spend.report(ROOT, "day", 30)["basis"]["price_as_of"], "unknown")

    def test_a_failed_or_foreign_answer_is_unavailable_not_empty(self):
        answers = [(2, ""), (0, "not json"), (0, '{"value": NaN}'),
                   (0, json.dumps({"schema_version": 1, "report": "roles", "by": "role",
                                   "days": 30, "groups": []}))]
        for code, stdout in answers:
            _captured, patch = self.answer(code=code, stdout=stdout)
            with self.subTest(stdout=stdout), patch, self.assertRaises(spend.SpendUnavailable):
                spend.report(ROOT, "day", 30)

    def test_a_child_past_its_deadline_or_abandoned_by_its_client_is_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name, timeout, cancelled, error in (
                    ("deadline", 0.5, lambda: False, spend.SpendUnavailable),
                    ("cancel", 60, lambda: True, spend.SpendCancelled)):
                pid_file = Path(temporary) / name
                script = ("import os, pathlib, time; pathlib.Path(%r).write_text(str(os.getpid()));"
                          " time.sleep(60)" % str(pid_file))
                started = time.monotonic()
                with self.subTest(name=name), self.assertRaises(error):
                    spend._run([sys.executable, "-c", script], ROOT, dict(os.environ), timeout,
                               lambda: pid_file.exists() and cancelled())
                self.assertLess(time.monotonic() - started, 10)
                pid = int(pid_file.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_one_child_per_grouping_and_a_busy_grouping_is_refused(self):
        _captured, patch = self.answer(self.usage())
        slot = spend._SLOTS["day"]
        self.assertTrue(slot.acquire(blocking=False))
        try:
            with patch, mock.patch.object(spend, "SLOT_WAIT", 0.05), \
                    self.assertRaises(spend.SpendBusy):
                spend.report(ROOT, "day", 30)
            # Another grouping has its own slot.
            _captured, other = self.answer(self.usage(by="model"))
            with other:
                self.assertEqual(spend.report(ROOT, "model", 30)["by"], "model")
        finally:
            slot.release()

    def test_a_closed_client_connection_is_noticed(self):
        import socket
        ours, theirs = socket.socketpair()
        try:
            self.assertFalse(server._client_gone(ours))
            theirs.sendall(b"x")
            self.assertFalse(server._client_gone(ours))
            theirs.close()
            ours.recv(1)
            self.assertTrue(server._client_gone(ours))
        finally:
            ours.close()

    def test_the_route_names_its_citizen_command(self):
        route = next(item for item in server.ROUTES.entries if item.path == "/api/reports/spend")
        self.assertEqual((route.method, route.cli_command),
                         ("POST", ("citizen", "usage", "--json")))


class SpendRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        # The Studio reads the ledger on each request, so it is installed after the server starts.
        install_ledger(self.home)
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session",
                                               {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", "/api/reports/spend", supplied, body)

    def test_the_route_requires_a_session_csrf_and_origin_then_validates_its_input(self):
        valid = {"by": "day", "days": 30}
        body = json.dumps(valid).encode()
        status, _headers, _body = self.request("POST", "/api/reports/spend", {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)
        status, _headers, _body = self.post(valid, {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.post(valid, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        for invalid in ({"by": "export", "days": 30}, {"by": "day", "days": -1},
                        {"by": "day", "days": 30, "rescan": True}):
            status, _headers, body = self.post(invalid)
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body), {"error": "invalid_request"})

    def test_the_route_answers_with_the_cli_document_for_the_same_window(self):
        for by in ("day", "session", "role"):
            with self.subTest(by=by):
                status, _headers, body = self.post({"by": by, "days": WINDOW})
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertEqual(payload["ledger"], cli_document(self.env, by, WINDOW))
                self.assertEqual(payload["basis"]["label"], "list-price equivalent")
                self.assertEqual(payload["basis"]["price_as_of"], fixture_as_of())


class SpendRoutePerformanceTests(studio_security.StudioSecurityFixture):
    """AC3, end to end: the authenticated route answers a year-sized ledger within budget.

    The criterion is one second, which a developer machine meets with room to spare (about
    0.5 s per grouping, most of it the CLI's start-up). The ceiling here is three seconds so a
    shared, loaded CI runner does not fail an unchanged tree; a regression that scales with the
    ledger, such as a quadratic grouping, still breaks it.
    """

    CEILING = 3.0

    def setUp(self):
        super().setUp()
        write_year(self.home / "year.jsonl")
        install_ledger(self.home, self.home / "year.jsonl")
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session",
                                               {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def test_a_year_of_sessions_loads_through_the_route_within_the_ceiling(self):
        for by in ("day", "session"):
            with self.subTest(by=by):
                body = json.dumps({"by": by, "days": 365}).encode()
                headers = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                           "Content-Length": str(len(body)),
                           "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
                started = time.monotonic()
                status, _headers, payload = self.request("POST", "/api/reports/spend",
                                                         headers, body)
                elapsed = time.monotonic() - started
                self.assertEqual(status, 200)
                ledger = json.loads(payload)["ledger"]
                self.assertEqual(ledger["totals"]["runs"], 365 * 20)
                if by == "session":
                    self.assertEqual(len(ledger["groups"]), 365 * 20)
                self.assertLess(elapsed, self.CEILING, "%s took %.2f s" % (by, elapsed))


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        FRONTEND_FIXTURE.write_text(json.dumps(frontend_documents(), indent=2, sort_keys=True)
                                    + "\n", encoding="utf-8")
        print("wrote " + str(FRONTEND_FIXTURE))
    else:
        unittest.main()
