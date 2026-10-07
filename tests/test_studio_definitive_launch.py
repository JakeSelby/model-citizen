"""The definitive launch behind the engine's registered budget (AH-S345 AC 4), and the spend guard's
estimate re-checked at start: in the library, and through the real replay routes. Fake supervisors,
a mocked launcher and an injected budget provider; nothing spends."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402  adds lib/ to the import path
from harness_core.studio import auth, definitive_launch, headless, replay, runs, server, spend_guard  # noqa: E402
from harness_core.studio.state import Store  # noqa: E402
from studio_target_support import FixtureTargetService  # noqa: E402
from test_studio_replay import fixture_tasks  # noqa: E402

BUDGET = {"per_run_usd": "2", "whole_run_cap_usd": "40"}


def target(kind, ref, revision):
    return {"kind": kind, "ref": ref, "revision": revision, "version": None,
            "draft": ref if kind == "draft" else None, "config_digest": None}


class FakeSupervisor:
    def __init__(self, estimate, refusal=None):
        self.estimate = estimate
        self.refusal = refusal
        self.started = []

    def spend_preview(self, *_args, **_kwargs):
        return {"estimate": {"amount_usd": self.estimate, "basis": "median_same_suite_scale",
                             "sample_count": 3}, "caps": {}, "confirmation_token": "token"}

    def start(self, *args, **kwargs):
        self.started.append((args, kwargs))
        if self.refusal is not None:
            try:
                raise self.refusal
            except spend_guard.SpendGuardError as exc:
                raise runs.RunError("worded any way at all") from exc
        return {"run_id": "definitive-run", "status": "queued"}


class DefinitiveLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "benchmarks").mkdir()
        (self.root / "benchmarks" / "tasks.json").write_text(json.dumps(
            {"schema_version": 1, "tasks": [{"id": "one"}, {"id": "two"}]}), encoding="utf-8")

    def launch(self, budget=BUDGET, estimate=10.0, refusal=None):
        self.supervisor = FakeSupervisor(estimate, refusal)
        admission = replay.ReplayAdmission(self.root, self.root / "state", self.supervisor, None)
        admission._resolve = lambda kind, ref: target(kind, ref, ("a" if ref == "a" else "b") * 40)
        admission._confirm_resolved = lambda request: None  # the fixed targets cannot move
        provided = []

        def provider(repository, request):
            provided.append((repository, request.as_dict()))
            return budget
        self.provided = provided
        return definitive_launch.DefinitiveLaunch(admission, provider)

    def form(self, maximum="2", cap="40"):
        return {"targets": [{"kind": "draft", "ref": "a"}, {"kind": "draft", "ref": "b"}],
                "model": "m", "repetitions": 1, "tasks": ["one", "two"], "max_budget_usd": maximum,
                "spend_cap_usd": cap, "pre_registration": None, "definitive": True}

    def resolved(self, maximum="2", cap="40"):
        return dict(self.form(maximum, cap), targets=[target("draft", "a", "a" * 40),
                                                      target("draft", "b", "b" * 40)],
                    evidence="exploratory")

    def test_within_budget_it_goes_through_confirm_to_start_with_the_exact_caps(self):
        launch = self.launch()
        preview = launch.preview(self.form("1.5", "30"))
        self.assertEqual(preview["sampling"]["registered_budget"], BUDGET)
        self.assertIs(preview["request"]["definitive"], True)
        started = launch.start(preview["request"], "f" * 64)
        self.assertEqual(started["run_id"], "definitive-run")
        self.assertEqual(len(self.supervisor.started), 1)
        _args, kwargs = self.supervisor.started[0]
        self.assertEqual((kwargs["max_budget_usd"], kwargs["spend_cap_usd"], kwargs["pricing_source"],
                          kwargs["confirmed"], kwargs["estimate_ceiling_usd"]),
                         ("1.5", "30", "api_credit", "f" * 64, "40"))
        self.assertEqual(self.provided[0][0], self.root)

    def test_over_budget_it_is_refused_at_preview_and_at_confirm(self):
        for maximum, cap, named in (("2.5", "40", "per-run budget 2.5"), ("2", "41", "spend cap 41")):
            launch = self.launch()
            for step in (lambda: launch.preview(self.form(maximum, cap)),
                         lambda: launch.start(self.resolved(maximum, cap), "f" * 64)):
                with self.assertRaises(replay.ReplayRefusal) as caught:
                    step()
                self.assertEqual(caught.exception.code, definitive_launch.EXCEEDED)
                self.assertIn(named, str(caught.exception))
            self.assertEqual(self.supervisor.started, [])
        launch = self.launch(estimate=40.01)
        with self.assertRaises(replay.ReplayRefusal) as caught:
            launch.preview(self.form())
        self.assertIn("estimated spend 40.01", str(caught.exception))

    def test_with_no_registered_budget_it_is_refused_with_the_named_reason(self):
        launch = self.launch(budget=None)
        for step in (lambda: launch.preview(self.form()),
                     lambda: launch.start(self.resolved(), "f" * 64)):
            with self.assertRaises(replay.ReplayRefusal) as caught:
                step()
            self.assertEqual(caught.exception.code, definitive_launch.UNREGISTERED)
            self.assertIn("the engine has no registered budget for this run", str(caught.exception))
        self.assertEqual(self.supervisor.started, [])

    def test_the_production_provider_has_no_engine_reader_so_it_registers_nothing(self):
        self.assertIsNone(definitive_launch.engine_budget(self.root, replay.ReplayRequest.parse(self.resolved())))
        launch = definitive_launch.DefinitiveLaunch(self.launch().admission)
        with self.assertRaises(replay.ReplayRefusal) as caught:
            launch.start(self.resolved(), "f" * 64)
        self.assertEqual(caught.exception.code, definitive_launch.UNREGISTERED)

    def test_no_estimate_is_refused_at_preview_with_its_reason(self):
        launch = self.launch(estimate=None)
        with self.assertRaises(replay.ReplayRefusal) as caught:
            launch.preview(self.form())
        self.assertEqual(caught.exception.code, definitive_launch.UNESTIMATED)
        self.assertIn("no estimate for this run", str(caught.exception))

    def test_the_start_time_ceiling_refusal_is_mapped_by_its_type_and_code(self):
        for code, mapped in ((spend_guard.CeilingRefusal.ABOVE, definitive_launch.EXCEEDED),
                             (spend_guard.CeilingRefusal.UNESTIMATED, definitive_launch.UNESTIMATED)):
            launch = self.launch(refusal=spend_guard.CeilingRefusal(code, "any words"))
            with self.assertRaises(replay.ReplayRefusal) as caught:
                launch.start(self.resolved(), "f" * 64)
            self.assertEqual(caught.exception.code, mapped)
        launch = self.launch(refusal=spend_guard.SpendGuardError("above the ceiling, but untyped"))
        with self.assertRaises(replay.ReplayError) as caught:
            launch.start(self.resolved(), "f" * 64)
        self.assertNotIsInstance(caught.exception, replay.ReplayRefusal)

    def test_the_plain_path_refuses_a_flagged_request_and_this_one_an_unflagged_one(self):
        launch = self.launch()
        flagged = replay.ReplayRequest.parse(self.resolved())
        with self.assertRaises(replay.ReplayRefusal) as caught:
            launch.admission.preview_resolved(flagged)
        self.assertEqual(caught.exception.code, "definitive_launch_required")
        with self.assertRaises(replay.ReplayRefusal):
            launch.admission.start_confirmed(flagged, "f" * 64)
        plain = dict(self.resolved())
        plain.pop("definitive")
        with self.assertRaises(replay.ReplayRefusal) as caught:
            launch.start(plain, "f" * 64)
        self.assertEqual(caught.exception.code, definitive_launch.NOT_DEFINITIVE)
        self.assertEqual(self.supervisor.started, [])
        # Without the flag the request's JSON, and so its confirmation digest, is what it was.
        self.assertNotIn("definitive", replay.ReplayRequest.parse(plain).as_dict())

    def test_a_malformed_budget_from_the_provider_is_refused(self):
        for budget in ({"per_run_usd": "2"}, {"per_run_usd": "two", "whole_run_cap_usd": "40"}):
            with self.assertRaisesRegex(replay.ReplayError, "registered budget is malformed"):
                self.launch(budget=budget).confirm(self.resolved())


class StartCeilingTests(unittest.TestCase):
    """The supervisor re-estimates at start under its lock; a caller's ceiling is checked there."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        catalog = root / "suites.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "paid-suite", "version": 1, "argv": [sys.executable, "-c", "pass"], "parameters": {},
            "cost_class": "spends_usage", "expected_duration_seconds": 1, "timeout_seconds": 20,
            "targets": ["installed"], "cases": ["case-one"]}]}), encoding="utf-8")
        self.supervisor = runs.RunSupervisor(root / "studio", catalog, 1, target_service=FixtureTargetService())
        self.addCleanup(self.supervisor.close)

    def estimate(self, amount):
        return mock.patch.object(spend_guard, "estimate", lambda _records, suite, cases: {
            "amount_usd": amount, "basis": "median_same_suite_scale", "sample_count": 2,
            "suite_id": suite, "case_count": len(cases)})

    def preview_token(self):
        return self.supervisor.spend_preview("paid-suite", {}, "installed", "current", "1", "10",
                                             "api_credit")["confirmation_token"]

    def start(self, token, ceiling):
        return self.supervisor.start("paid-suite", {}, "installed", "current", confirmed=token,
                                     max_budget_usd="1", spend_cap_usd="10", pricing_source="api_credit",
                                     estimate_ceiling_usd=ceiling)

    def test_an_estimate_over_the_ceiling_is_refused_at_start(self):
        with self.estimate(5.0):
            token = self.preview_token()
            with mock.patch.object(runs.subprocess, "Popen") as launch, \
                    self.assertRaises(runs.RunError) as caught:
                self.start(token, "4")
            launch.assert_not_called()
            self.assertEqual(caught.exception.__cause__.code, spend_guard.CeilingRefusal.ABOVE)
            with mock.patch.object(self.supervisor, "_admit_locked"):
                self.assertEqual(self.start(token, "6")["status"], "queued")

    def test_no_estimate_is_refused_at_start_against_a_ceiling(self):
        with self.estimate(None):
            token = self.preview_token()
            with mock.patch.object(runs.subprocess, "Popen") as launch, \
                    self.assertRaises(runs.RunError) as caught:
                self.start(token, "6")
            launch.assert_not_called()
        self.assertIsInstance(caught.exception.__cause__, spend_guard.CeilingRefusal)
        self.assertEqual(caught.exception.__cause__.code, spend_guard.CeilingRefusal.UNESTIMATED)

    def test_an_estimate_that_grew_after_preview_fails_the_confirmation_at_start(self):
        with self.estimate(1.0):
            token = self.preview_token()
        with self.estimate(9.0), self.assertRaisesRegex(runs.RunError, "confirmation"):
            self.start(token, None)

    def test_with_no_ceiling_every_plan_passes_as_before(self):
        spend_guard.check_ceiling({"estimate": {"amount_usd": 99.0}}, None)
        spend_guard.check_ceiling({"estimate": {"amount_usd": None}}, None)


class DefinitiveRouteTests(unittest.TestCase):
    """The replay routes, served for real: a flagged request is held to the engine's budget, and
    the flag cannot carry a token across to the other path. The launcher is mocked."""

    BODY = {"targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": "my-draft"}],
            "model": "claude-test", "repetitions": 1, "tasks": ["link-alias"],
            "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(os.path.realpath(self.temporary.name))
        (self.base / "state").mkdir()
        self.estimate_usd = 5.0
        patches = [
            fixture_tasks("link-alias"),
            mock.patch.object(headless.targets, "TargetService", lambda _repository: FixtureTargetService()),
            mock.patch.object(headless.runs.RunSupervisor, "_admit_locked"),
            mock.patch.object(spend_guard, "estimate", lambda _records, suite, cases: {
                "amount_usd": self.estimate_usd, "basis": "median_same_suite_scale", "sample_count": 2,
                "suite_id": suite, "case_count": len(cases)}),
            mock.patch.dict(os.environ, {"HOME": str(self.base), "HARNESS_HOME": str(self.base)}),
        ]
        for patch in patches:
            patch.__enter__()
            self.addCleanup(patch.__exit__, None, None, None)
        self.store = Store(self.base / "state" / "studio")
        self.store.__enter__()
        self.addCleanup(self.store.__exit__, None, None, None)
        self.server, _fallback = server.bind(REPO / "studio" / "dist", "credential", self.store, 0)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        token, _form = self.server.sessions.issue()
        session, _form = self.server.sessions.consume(token)
        self.headers = {"Host": self.server.host, "Origin": "http://" + self.server.host,
                        "Cookie": "%s=%s" % (auth.SESSION_COOKIE, session.cookie),
                        "X-Studio-CSRF": session.csrf, "Content-Type": "application/json"}

    def http(self, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=60)
        try:
            connection.request("POST", path, json.dumps(body), self.headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def budget(self, value):
        patch = mock.patch.object(definitive_launch, "engine_budget", lambda _repository, _request: value)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def preview(self, definitive=True, **changes):
        body = dict(self.BODY, **changes)
        if definitive:
            body["definitive"] = True
        return self.http("/api/runs/replay/preview", {"request": body})

    def start(self, request, token):
        return self.http("/api/runs/replay/start", {"request": request, "confirmation_token": token})

    def test_in_production_the_engine_registers_no_budget_and_the_client_hears_why(self):
        self.assertEqual(self.preview(), (400, {"error": definitive_launch.UNREGISTERED}))

    def test_within_an_injected_budget_it_previews_and_starts(self):
        self.budget(BUDGET)
        status, preview = self.preview()
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["sampling"]["registered_budget"], BUDGET)
        self.assertIs(preview["request"]["definitive"], True)
        status, started = self.start(preview["request"], preview["confirmation_token"])
        self.assertEqual(status, 200, started)
        self.assertTrue(started["run_id"])

    def test_over_an_injected_budget_it_is_refused(self):
        self.budget(BUDGET)
        self.assertEqual(self.preview(spend_cap_usd="41"), (400, {"error": definitive_launch.EXCEEDED}))
        self.assertEqual(self.preview(max_budget_usd="3", spend_cap_usd="30"),
                         (400, {"error": definitive_launch.EXCEEDED}))
        self.estimate_usd = 40.5
        self.assertEqual(self.preview(), (400, {"error": definitive_launch.EXCEEDED}))
        self.estimate_usd = None
        self.assertEqual(self.preview(), (400, {"error": definitive_launch.UNESTIMATED}))

    def test_the_flag_cannot_carry_a_token_across_paths_or_be_anything_but_true(self):
        self.budget(BUDGET)
        status, definitive = self.preview()
        self.assertEqual(status, 200, definitive)
        unflagged = {key: value for key, value in definitive["request"].items() if key != "definitive"}
        # A definitive preview's token does not start a plain replay of the same request.
        self.assertEqual(self.start(unflagged, definitive["confirmation_token"]),
                         (400, {"error": "replay_refused"}))
        status, plain = self.preview(definitive=False)
        self.assertEqual(status, 200, plain)
        self.assertNotIn("definitive", plain["request"])
        # Nor does a plain preview's token start a definitive one.
        self.assertEqual(self.start(dict(plain["request"], definitive=True), plain["confirmation_token"]),
                         (400, {"error": "replay_refused"}))
        self.assertEqual(self.http("/api/runs/replay/preview", {"request": dict(self.BODY, definitive="yes")}),
                         (400, {"error": "replay_refused"}))


if __name__ == "__main__":
    unittest.main()
