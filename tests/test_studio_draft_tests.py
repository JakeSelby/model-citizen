"""Test a draft against its base and keep a verdict per checkpoint (AH-S308, #991).

A draft test is one live replay whose target 1 is the draft's base commit and target 2 its
checkpoint. The finished pair is read through #990's comparison; the verdict rule and the power
check are `draft_tests`'. The replays here go through `replay.execute` with the native benchmark
faked, as `test_studio_compare` does, and the committed fixture
`studio/tests/fixtures/draft-test.json`, which `studio/tests/draft-test.test.ts` feeds through the
page, is the verdicts route's payload. Regenerate it with:

    python3 tests/test_studio_draft_tests.py --write
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO, harness  # noqa: E402
from harness_core.studio import compare, draft_tests, drafts, replay, runs, server  # noqa: E402
from test_studio_compare import BASE, CHEAPER, NOISY, Runs, request, target  # noqa: E402
import test_studio_security as studio_security  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "draft-test.json"
BASE_REV, FIRST_REV, SECOND_REV = "a" * 40, "b" * 40, "d" * 40
DRAFT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ONE = "00000000-0000-4000-8000-000000000011"
RUN_TWO = "00000000-0000-4000-8000-000000000012"
RUN_THREE = "00000000-0000-4000-8000-000000000013"
PLAN = {"effect": 0.15, "cv": 0.25}
FORM = {"model": "claude-test", "repetitions": 2, "tasks": ["a", "b", "c"],
        "max_budget_usd": "2", "spend_cap_usd": "200", "pack": None}


def state(revision=FIRST_REV, draft_id=DRAFT_ID):
    return {"name": "tuned", "draft_id": draft_id, "base_ref": "installed",
            "base_revision": BASE_REV, "revision": revision}


@contextlib.contextmanager
def draft_at(revision=FIRST_REV, draft_id=DRAFT_ID, exists=True):
    """The draft `tuned` at `revision`, as both the snapshot read and the locked read see it."""
    def snapshot(_repo, name, operation):
        if not exists or name != "tuned":
            raise drafts.DraftError("not-found", "draft does not exist: " + name)
        return operation(Path("/nonexistent"), state(revision, draft_id), {})

    def config(_repo, name):
        if not exists or name != "tuned":
            raise drafts.DraftError("not-found", "draft does not exist: " + name)
        return {"draft": {"revision": revision}, "config": {}}

    with mock.patch.object(drafts, "read_snapshot", side_effect=snapshot), \
            mock.patch.object(drafts, "read_config", side_effect=config):
        yield


def draft_request(revision=FIRST_REV, **changes):
    return request([target("branch", BASE_REV, BASE_REV), target("draft", "tuned", revision)],
                   **changes)


def tested(root, specs):
    """Finished draft tests: `(run_id, revision, candidate spec, created_at)` each, recorded."""
    supervisor = Runs(Path(root) / "runs")
    supervisor.root.mkdir()
    supervisor.state_root = Path(root) / "state"
    for run_id, revision, candidate, created in specs:
        selected = draft_request(revision)
        supervisor.add(run_id, selected, {BASE_REV: BASE, revision: candidate})
        with draft_at(revision):
            planned = draft_tests.power(REPO, 3, 2, PLAN)
            value = draft_tests.record(supervisor.state_root, run_id,
                                       draft_tests.identity(REPO, "tuned"), selected, planned)
        value["created_at"] = created
        path = supervisor.state_root / draft_tests.RECORDS_DIR / (run_id + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
    return supervisor


class Handler:
    def __init__(self, supervisor, body):
        self.server = SimpleNamespace(run_supervisor=supervisor, repo_root=REPO,
                                      mutations=SimpleNamespace(call=lambda action: action()))
        self.request_json = body
        self.response = None

    def _json(self, code, payload):
        self.response = (code, payload)

    def _error(self, code, name):
        self.response = (code, {"error": name})


def route_call(supervisor, path, body, admission=None):
    route = next(item for item in server.ROUTES.entries if item.path == path)
    handler = Handler(supervisor, body)
    with mock.patch.object(server, "_replay_admission", return_value=admission):
        route.handler(handler, route)
    return handler.response


class Admission:
    """The replay admission surface the draft-test routes call, with the target builds faked."""

    def __init__(self, revision=FIRST_REV, run_id=RUN_THREE):
        self.revision, self.run_id = revision, run_id
        self.forms, self.started = [], []

    def _resolve(self, kind, ref):
        return target(kind, ref, BASE_REV if kind == "branch" else self.revision)

    def resolve(self, value):
        self.forms.append(copy.deepcopy(value))
        return replay.resolve_request(value, self._resolve)

    def preview_resolved(self, selected):
        return {"valid": True, "request": selected.as_dict(), "confirmation_token": "token",
                "estimate": {"amount_usd": None}}

    def confirm(self, value):
        return replay.ReplayRequest.parse(value)

    def start_confirmed(self, selected, token):
        self.started.append((selected, token))
        return {"run_id": self.run_id, "status": "queued",
                "targets": [item.as_dict() for item in selected.targets]}


class PowerTests(unittest.TestCase):
    def test_enough_trials_reports_the_engines_detectable_effect(self):
        value = draft_tests.power(REPO, 6, 10, {"effect": 0.5, "cv": 0.25})
        expected = round(compare._engine().minimum_detectable_effect(60, 0.25), 4)
        self.assertEqual(value["minimum_detectable_effect"], expected)
        self.assertTrue(value["enough"])
        self.assertEqual((value["reasons"], value["needed_trials"]), ([], None))
        self.assertTrue(draft_tests.power_line(value).startswith("Enough trials: 60 attempt(s)"))

    def test_too_few_trials_is_said_before_the_run_with_the_number_needed(self):
        value = draft_tests.power(REPO, 3, 2, PLAN)
        engine = compare._engine()
        self.assertFalse(value["enough"])
        self.assertIn("fewer than %d trials per task" % engine.MIN_TRIALS, value["reasons"][0])
        self.assertIn("more than the 15.0% you want to detect", value["reasons"][1])
        # The fewest trials, from the engine alone, whose detectable effect reaches 15%.
        needed = next(count for count in range(engine.MIN_TRIALS, 21)
                      if round(engine.minimum_detectable_effect(3 * count, 0.25), 4) <= 0.15)
        self.assertEqual(value["needed_trials"], needed)
        self.assertIn("run %d trials per task instead" % needed, draft_tests.power_line(value))

    def test_no_trial_count_up_to_the_limit_is_said_plainly(self):
        value = draft_tests.power(REPO, 1, 5, {"effect": 0.01, "cv": 0.25})
        self.assertIsNone(value["needed_trials"])
        self.assertIn("no trial count up to 20 detects it on 1 task(s)", draft_tests.power_line(value))

    def test_the_declared_planning_assumption_stands_in_for_an_unstated_cv(self):
        declared = json.loads((REPO / "benchmarks" / "ablations.json").read_text())["planning"]
        value = draft_tests.power(REPO, 3, 5, {"effect": 0.15, "cv": None})
        self.assertEqual(value["cv"], declared["cv"])
        self.assertEqual(value["cv_source"], "benchmarks/ablations.json: " + declared["source"])
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(draft_tests.DraftTestError) as caught:
                draft_tests.power(Path(empty), 3, 5, {"effect": 0.15, "cv": None})
        self.assertEqual(caught.exception.code, "draft_test_planning_unavailable")

    def test_an_effect_or_cv_out_of_range_is_refused(self):
        for effect, cv in ((0, None), (1, None), (True, None), ("0.1", None), (0.1, 0), (0.1, "x")):
            with self.assertRaises(draft_tests.DraftTestError):
                draft_tests.parse_plan(effect, cv)
        self.assertEqual(draft_tests.parse_plan(0.1, None), {"effect": 0.1, "cv": None})


class PairTests(unittest.TestCase):
    def test_the_base_commit_and_the_draft_run_as_one_pair_with_one_set_of_parameters(self):
        with draft_at():
            draft = draft_tests.identity(REPO, "tuned")
        form = draft_tests.replay_form(draft, FORM)
        self.assertEqual(form["targets"], [{"kind": "branch", "ref": BASE_REV},
                                           {"kind": "draft", "ref": "tuned"}])
        self.assertIsNone(form["pre_registration"])
        selected = replay.resolve_request(form, Admission()._resolve)
        draft_tests.check_request(draft, selected)
        self.assertEqual((selected.model, selected.repetitions, selected.tasks),
                         ("claude-test", 2, ("a", "b", "c")))

    def test_a_request_for_another_draft_checkpoint_or_base_is_refused(self):
        with draft_at():
            draft = draft_tests.identity(REPO, "tuned")
        for selected in (draft_request(SECOND_REV),
                         request([target("branch", "main", FIRST_REV), target("draft", "tuned", FIRST_REV)]),
                         request([target("branch", BASE_REV, BASE_REV), target("draft", "other", FIRST_REV)])):
            with self.assertRaises(draft_tests.DraftTestError) as caught:
                draft_tests.check_request(draft, selected)
            self.assertEqual(caught.exception.code, "draft_test_mismatch")
        with self.assertRaises(draft_tests.DraftTestError):
            draft_tests.replay_form(draft, dict(FORM, pre_registration="x"))

    def test_a_draft_target_is_never_registered_so_no_draft_test_can_be_cited(self):
        registered = replay.ReplayRequest.parse(dict(
            draft_request().as_dict(), pre_registration="docs/x.md", evidence="pre-registered",
            targets=[target("release", "v1.0.0", BASE_REV), target("draft", "tuned", FIRST_REV)]))
        self.assertEqual(replay.target_evidence(registered, registered.targets[1]), "exploratory")


class ClaimTests(unittest.TestCase):
    @staticmethod
    def comparison(cost="lower", passing="higher", withheld=(), comparable=True, error=None):
        return {"comparable": comparable, "refusals": [] if comparable else ["different models"],
                "error": error, "direction_withheld": list(withheld),
                "preferred": dict(compare.PREFERRED),
                "result": {"arms": [{"measures": {"cost_per_passed": {"reading": cost},
                                                  "pass_rate": {"reading": passing}}}]}}

    def test_helped_only_when_citable_and_both_judged_measures_read_their_preferred_way(self):
        self.assertEqual(draft_tests.claim(self.comparison())["verdict"], "helped")
        self.assertEqual(draft_tests.claim(self.comparison(passing="inconclusive"))["verdict"],
                         "inconclusive")
        self.assertEqual(draft_tests.claim(self.comparison(cost="higher"))["verdict"], "worse")
        self.assertEqual(draft_tests.claim(self.comparison(passing="lower"))["verdict"], "worse")

    def test_a_withheld_direction_refusal_or_engine_error_never_reads_helped(self):
        value = draft_tests.claim(self.comparison(withheld=["the candidate is exploratory"]))
        self.assertEqual(value, {"verdict": "exploratory", "reasons": ["the candidate is exploratory"]})
        self.assertEqual(draft_tests.claim(self.comparison(comparable=False)),
                         {"verdict": "not_compared", "reasons": ["different models"]})
        self.assertEqual(draft_tests.claim(self.comparison(error="no rows"))["verdict"],
                         "engine_refused")


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_finished_test_shows_its_verdict_readings_spend_and_comparison(self):
        supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        with draft_at():
            payload = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")
            expected = compare.compare_runs(supervisor, REPO, {"base": (RUN_ONE, 1),
                                                               "candidate": (RUN_ONE, 2)})
        test = payload["tests"][0]
        self.assertEqual(test["verdict"], "exploratory")
        self.assertEqual(test["reasons"], expected["direction_withheld"])
        self.assertTrue(test["headline"].startswith("Exploratory: no claim."))
        measures = expected["result"]["arms"][0]["measures"]
        self.assertEqual(test["readings"], {key: measures[key] for key in compare.PREFERRED})
        self.assertEqual(test["comparison"], {
            "base": {"run_id": RUN_ONE, "target": 1}, "candidate": {"run_id": RUN_ONE, "target": 2},
            "command": "citizen runs compare %s:1 %s:2" % (RUN_ONE, RUN_ONE)})
        summary = replay.read_summary(supervisor.root / RUN_ONE / "replay" / replay.SUMMARY_NAME)
        self.assertEqual(test["spend_usd"], summary["spend_usd"])
        self.assertEqual((test["stale"], test["stale_copy"]), (False, None))
        self.assertFalse(test["power"]["enough"])
        server.DRAFT_TEST_VERDICTS.validate(payload)

    def test_a_running_test_says_so_and_an_unknown_run_is_unavailable(self):
        supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        supervisor.records[RUN_ONE] = (supervisor.records[RUN_ONE][0], "running")
        with draft_at():
            test = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")["tests"][0]
        self.assertEqual((test["verdict"], test["spend_usd"]), ("running", None))
        del supervisor.records[RUN_ONE]
        with draft_at():
            test = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")["tests"][0]
        self.assertEqual((test["verdict"], test["reasons"]),
                         ("unavailable", ["the run is no longer known"]))

    def test_a_verdict_is_kept_for_each_checkpoint_and_marked_stale_once_the_draft_changes(self):
        supervisor = tested(self.tmp.name, [
            (RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00"),
            (RUN_TWO, SECOND_REV, NOISY, "2026-10-02T00:00:00+00:00"),
            (RUN_THREE, SECOND_REV, CHEAPER, "2026-10-03T00:00:00+00:00")])
        with draft_at(SECOND_REV):
            payload = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")
        self.assertEqual([test["run_id"] for test in payload["tests"]], [RUN_THREE, RUN_TWO, RUN_ONE])
        self.assertEqual([(item["revision"], item["current"], item["tests"], item["latest"]["run_id"])
                          for item in payload["checkpoints"]],
                         [(SECOND_REV, True, 2, RUN_THREE), (FIRST_REV, False, 1, RUN_ONE)])
        stale = payload["checkpoints"][1]["latest"]
        self.assertEqual((stale["stale"], stale["stale_reason"], stale["stale_copy"]),
                         (True, "the draft has a newer checkpoint",
                          "Stale: the draft changed after this comparison."))
        self.assertFalse(payload["checkpoints"][0]["latest"]["stale"])

    def test_another_drafts_tests_are_left_out_and_unreadable_records_counted(self):
        supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        (supervisor.state_root / draft_tests.RECORDS_DIR / (RUN_TWO + ".json")).write_text("{")
        with draft_at(draft_id="22222222-2222-4222-8222-222222222222"):
            payload = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")
        self.assertEqual((payload["tests"], payload["checkpoints"], payload["unreadable_records"]),
                         ([], [], 1))

    def test_an_unreadable_recorded_power_check_is_said_and_the_verdict_still_read(self):
        supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        path = supervisor.state_root / draft_tests.RECORDS_DIR / (RUN_ONE + ".json")
        path.write_text(json.dumps(dict(json.loads(path.read_text()), power={})), encoding="utf-8")
        with draft_at():
            test = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")["tests"][0]
        self.assertEqual(test["power_line"], "The power check recorded with this test is unreadable.")
        self.assertEqual(test["verdict"], "exploratory")

    def test_a_record_is_written_once(self):
        supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        with draft_at():
            with self.assertRaises(FileExistsError):
                draft_tests.record(supervisor.state_root, RUN_ONE, draft_tests.identity(REPO, "tuned"),
                                   draft_request(), {})


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.supervisor = tested(self.tmp.name, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])

    def test_plan_pairs_the_base_and_the_draft_and_reports_power_before_anything_starts(self):
        admission = Admission()
        with draft_at():
            status, payload = route_call(self.supervisor, "/api/configure/test/plan",
                                         dict(draft="tuned", request=FORM, **PLAN), admission)
        self.assertEqual(status, 200, payload)
        server.DRAFT_TEST_PLAN.validate(payload)
        self.assertEqual(admission.forms[0]["targets"], [{"kind": "branch", "ref": BASE_REV},
                                                          {"kind": "draft", "ref": "tuned"}])
        self.assertEqual(admission.started, [])
        self.assertEqual(payload["power"], draft_tests.power(REPO, 3, 2, PLAN))
        self.assertTrue(payload["power_line"].startswith("Too few trials"))
        self.assertEqual([item["revision"] for item in payload["preview"]["request"]["targets"]],
                         [BASE_REV, FIRST_REV])

    def test_start_runs_the_confirmed_pair_and_records_it_against_the_checkpoint(self):
        admission = Admission()
        resolved = draft_request().as_dict()
        with draft_at():
            status, payload = route_call(self.supervisor, "/api/configure/test/start", dict(
                draft="tuned", request=resolved, confirmation_token="token", **PLAN), admission)
        self.assertEqual(status, 200, payload)
        server.DRAFT_TEST_RUN.validate(payload)
        self.assertEqual(admission.started[0][1], "token")
        self.assertEqual((payload["record"]["run_id"], payload["record"]["revision"],
                          payload["record"]["base_revision"], payload["record"]["draft_id"]),
                         (RUN_THREE, FIRST_REV, BASE_REV, DRAFT_ID))
        self.assertTrue((self.supervisor.state_root / draft_tests.RECORDS_DIR
                         / (RUN_THREE + ".json")).is_file())

    def test_start_refuses_a_request_for_an_older_checkpoint(self):
        admission = Admission()
        with draft_at(SECOND_REV):
            status, payload = route_call(self.supervisor, "/api/configure/test/start", dict(
                draft="tuned", request=draft_request().as_dict(), confirmation_token="token",
                **PLAN), admission)
        self.assertEqual((status, payload), (409, {"error": "draft_test_mismatch"}))
        self.assertEqual(admission.started, [])

    def test_bad_input_an_unknown_draft_and_a_replay_refusal_have_status_codes(self):
        admission = Admission()
        with draft_at():
            cases = (
                ("/api/configure/test/plan", {"draft": "tuned"}, (400, "invalid_request")),
                ("/api/configure/test/plan", dict(draft="tuned", request=FORM, effect=2, cv=None),
                 (400, "invalid_request")),
                ("/api/configure/test/plan", dict(draft="missing", request=FORM, **PLAN),
                 (404, "draft_not_found")),
                ("/api/configure/test/plan", dict(draft="tuned", request=dict(FORM, repetitions=0),
                                                  **PLAN), (400, "replay_refused")),
                ("/api/configure/test/start", dict(draft="tuned", request={}, confirmation_token=1,
                                                   **PLAN), (400, "invalid_request")),
                ("/api/configure/test/verdicts", {"draft": "missing"}, (404, "draft_not_found")),
                ("/api/configure/test/verdicts", {"draft": 7}, (400, "invalid_request")),
            )
            for path, body, (code, name) in cases:
                status, payload = route_call(self.supervisor, path, body, admission)
                self.assertEqual((status, payload), (code, {"error": name}), (path, body))

    def test_the_routes_name_their_citizen_commands(self):
        commands = {item.path: item.cli_command for item in server.ROUTES.entries}
        self.assertEqual(commands["/api/configure/test/plan"], ("citizen", "runs", "spend-preview"))
        self.assertEqual(commands["/api/configure/test/start"], ("citizen", "runs", "start"))
        self.assertEqual(commands["/api/configure/test/verdicts"], ("citizen", "draft", "test"))

    def test_the_verdicts_route_and_citizen_draft_test_agree(self):
        with draft_at(SECOND_REV):
            status, payload = route_call(self.supervisor, "/api/configure/test/verdicts",
                                         {"draft": "tuned"})
            self.assertEqual(status, 200, payload)
            outputs = []
            for extra in (["--json"], []):
                output = io.StringIO()
                with mock.patch.object(runs, "RunSupervisor", return_value=self.supervisor), \
                        mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(harness.main(["draft", "test", "tuned"] + extra), 0)
                outputs.append(output.getvalue())
        self.assertEqual(json.loads(outputs[0]), json.loads(json.dumps(payload)))
        self.assertIn("checkpoint %s: Exploratory: no claim." % FIRST_REV[:12], outputs[1])
        self.assertIn("Stale: the draft changed after this comparison.", outputs[1])
        self.assertIn("comparison: citizen runs compare %s:1 %s:2" % (RUN_ONE, RUN_ONE), outputs[1])
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture, json.loads(json.dumps(payload)))


def fixture_payload():
    with tempfile.TemporaryDirectory() as tmp:
        supervisor = tested(tmp, [(RUN_ONE, FIRST_REV, CHEAPER, "2026-10-01T00:00:00+00:00")])
        with draft_at(SECOND_REV):
            return route_call(supervisor, "/api/configure/test/verdicts", {"draft": "tuned"})[1]


class DraftTestRouteSecurityTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, path, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", path, supplied, body)

    def test_each_route_requires_a_session_csrf_and_origin_then_validates_its_input(self):
        for path, valid in (
                ("/api/configure/test/plan", dict(draft="no-such-draft-991", request=FORM, **PLAN)),
                ("/api/configure/test/start", dict(draft="no-such-draft-991", request={},
                                                   confirmation_token="t", **PLAN)),
                ("/api/configure/test/verdicts", {"draft": "no-such-draft-991"})):
            body = json.dumps(valid).encode()
            status, _headers, _body = self.request("POST", path, {
                "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
            self.assertEqual(status, 401, path)
            status, _headers, _body = self.post(path, valid, {"X-Studio-CSRF": "wrong"})
            self.assertEqual(status, 403, path)
            status, _headers, _body = self.post(path, valid, {"Origin": "http://evil.example"})
            self.assertEqual(status, 403, path)
            status, _headers, body = self.post(path, {"draft": "no-such-draft-991", "extra": 1})
            self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}), path)
            status, _headers, body = self.post(path, valid)
            self.assertEqual((status, json.loads(body)), (404, {"error": "draft_not_found"}), path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        FIXTURE.write_text(json.dumps(fixture_payload(), indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
        print("wrote " + str(FIXTURE))
    else:
        unittest.main()
