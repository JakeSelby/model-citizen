"""The definitive launch behind the engine's registered budget (AH-S345 AC 4), and the spend guard's
estimate re-checked at start. Fake supervisors and an injected budget provider; nothing spends."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402,F401  adds lib/ to the import path
from harness_core.studio import definitive_launch, replay, runs, spend_guard  # noqa: E402
from studio_target_support import FixtureTargetService  # noqa: E402

BUDGET = {"per_run_usd": "2", "whole_run_cap_usd": "40"}


def target(kind, ref, revision):
    return {"kind": kind, "ref": ref, "revision": revision, "version": None,
            "draft": ref if kind == "draft" else None, "config_digest": None}


class FakeSupervisor:
    def __init__(self, estimate):
        self.estimate = estimate
        self.started = []

    def spend_preview(self, *_args, **_kwargs):
        return {"estimate": {"amount_usd": self.estimate, "basis": "median_same_suite_scale",
                             "sample_count": 3}, "caps": {}, "confirmation_token": "token"}

    def start(self, *args, **kwargs):
        self.started.append((args, kwargs))
        return {"run_id": "definitive-run", "status": "queued"}


class DefinitiveLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "benchmarks").mkdir()
        (self.root / "benchmarks" / "tasks.json").write_text(json.dumps(
            {"schema_version": 1, "tasks": [{"id": "one"}, {"id": "two"}]}), encoding="utf-8")

    def launch(self, budget=BUDGET, estimate=10.0):
        self.supervisor = FakeSupervisor(estimate)
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
                "spend_cap_usd": cap, "pre_registration": None}

    def resolved(self, maximum="2", cap="40"):
        return dict(self.form(maximum, cap), targets=[target("draft", "a", "a" * 40),
                                                      target("draft", "b", "b" * 40)],
                    evidence="exploratory")

    def test_within_budget_it_goes_through_confirm_to_start_with_the_exact_caps(self):
        launch = self.launch()
        preview = launch.preview(self.form("1.5", "30"))
        self.assertEqual(preview["registered_budget"], BUDGET)
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

    def start(self, token, ceiling):
        return self.supervisor.start("paid-suite", {}, "installed", "current", confirmed=token,
                                     max_budget_usd="1", spend_cap_usd="10", pricing_source="api_credit",
                                     estimate_ceiling_usd=ceiling)

    def test_an_estimate_over_the_ceiling_is_refused_at_start(self):
        with self.estimate(5.0):
            token = self.supervisor.spend_preview("paid-suite", {}, "installed", "current", "1", "10",
                                                  "api_credit")["confirmation_token"]
            with mock.patch.object(runs.subprocess, "Popen") as launch, \
                    self.assertRaisesRegex(runs.RunError, "estimate 5.0 USD is above the ceiling 4 USD"):
                self.start(token, "4")
            launch.assert_not_called()
            with mock.patch.object(self.supervisor, "_admit_locked"):
                self.assertEqual(self.start(token, "6")["status"], "queued")

    def test_an_estimate_that_grew_after_preview_fails_the_confirmation_at_start(self):
        with self.estimate(1.0):
            token = self.supervisor.spend_preview("paid-suite", {}, "installed", "current", "1", "10",
                                                  "api_credit")["confirmation_token"]
        with self.estimate(9.0), self.assertRaisesRegex(runs.RunError, "confirmation"):
            self.start(token, None)

    def test_no_ceiling_and_no_estimate_pass(self):
        spend_guard.check_ceiling({"estimate": {"amount_usd": 99.0}}, None)
        spend_guard.check_ceiling({"estimate": {"amount_usd": None}}, "1")


if __name__ == "__main__":
    unittest.main()
