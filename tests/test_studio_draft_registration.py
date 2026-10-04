"""Pre-register a draft test so it can say whether the change helped (AH-S346, #1211).

A registration is written before the run, in the engine's pre-registration format, committed to
the Studio's private registry and checked by `experiment_protocol.check`; the run it names may read
helped or worse only when it matches the registration exactly. The replays go through
`replay.execute` with the native benchmark faked, as `test_studio_draft_tests` does.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO, harness  # noqa: E402
from harness_core.studio import draft_registration, draft_tests, replay, runs, server  # noqa: E402
from test_studio_compare import Runs, spec  # noqa: E402
from test_studio_draft_tests import (  # noqa: E402
    BASE_REV, FIRST_REV, SECOND_REV, RUN_ONE, RUN_TWO, Admission, draft_at, draft_request,
    route_call)
import test_studio_security as studio_security  # noqa: E402

PROTOCOL = replay._engine_module("experiment_protocol")
TASKS = [{"id": task, "label": task.upper(), "long": task == "c"} for task in ("a", "b", "c")]
SPEC = {"model": "claude-test", "repetitions": 5, "tasks": ["c", "a", "b"], "pack": None}
EFFECT, CV = 0.15, 0.1
BASE5 = spec({task: [1.0] * 5 for task in "abc"})
CHEAPER5 = spec({"a": [0.5, 0.52, 0.51, 0.5, 0.53], "b": [0.55, 0.5, 0.52, 0.54, 0.5],
                 "c": [0.6, 0.58, 0.59, 0.6, 0.57]})
DEARER5 = spec({task: [1.6, 1.55, 1.62, 1.58, 1.6] for task in "abc"})
NOISY5 = spec({"a": [0.4, 0.5, 0.45, 0.42, 0.48], "b": [1.5, 1.4, 1.45, 1.6, 1.5],
               "c": [0.6, 0.7, 0.65, 0.62, 0.68]})


def sized(trials, selected):
    """`trials` cut or padded to the request's tasks and trials per task."""
    return {task: {arm: (values * 2)[:selected.repetitions] for arm, values in trials[task].items()}
            for task in selected.tasks}


@contextlib.contextmanager
def catalog():
    with mock.patch.object(replay, "_repository_tasks", return_value=TASKS):
        yield


def register(root, revision=FIRST_REV, spec_value=None, effect=EFFECT, cv=CV):
    with draft_at(revision), catalog():
        return draft_registration.register(root, REPO, "tuned", dict(spec_value or SPEC),
                                           effect, cv)


def registered_run(tmp, candidate, registration=True, run_revision=FIRST_REV, **changes):
    """One finished draft test at five trials per task, started under a fresh registration
    unless `registration` is false; `changes` alter the started request."""
    supervisor = Runs(Path(tmp) / "runs")
    supervisor.root.mkdir()
    supervisor.state_root = Path(tmp) / "state"
    supervisor.state_root.mkdir(mode=0o700)
    found = register(supervisor.state_root) if registration else None
    selected = draft_request(run_revision, repetitions=changes.pop("repetitions", 5), **changes)
    if selected.repetitions != 5 or list(selected.tasks) != ["a", "b", "c"]:
        base = sized(BASE5, selected)
        candidate = sized(candidate, selected)
    else:
        base = BASE5
    supervisor.add(RUN_ONE, selected, {BASE_REV: base, run_revision: candidate})
    with draft_at(run_revision):
        draft = draft_tests.identity(REPO, "tuned")
        identity, deviations = draft_tests.start_registration(
            supervisor.state_root, draft, selected,
            found["registration_id"] if found else None)
        draft_tests.record(supervisor.state_root, RUN_ONE, draft, selected,
                           draft_tests.power(REPO, len(selected.tasks), selected.repetitions,
                                             {"effect": EFFECT, "cv": CV}),
                           identity, deviations)
    return supervisor, found, deviations


def latest(supervisor, revision=FIRST_REV):
    with draft_at(revision):
        payload = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")
    return payload, payload["checkpoints"][0]["latest"]


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state"
        self.root.mkdir(mode=0o700)

    def test_registration_names_the_checkpoint_base_sample_and_effect_in_the_engines_format(self):
        found = register(self.root)
        self.assertEqual((found["revision"], found["base_revision"], found["tasks"],
                          found["repetitions"], found["model"], found["pack"], found["effect"]),
                         (FIRST_REV, BASE_REV, ["a", "b", "c"], 5, "claude-test", None, EFFECT))
        self.assertEqual((found["stale"], found["problems"]), (False, []))
        registry = self.root / draft_tests.RECORDS_DIR / draft_registration.REGISTRY_DIR
        self.assertTrue(found["plan"].startswith("benchmarks/preregistrations/"))
        errors, checked = PROTOCOL.check(found["plan"], str(registry), cwd=str(registry))
        self.assertEqual(errors, [])
        self.assertEqual(checked["pre_registration_commit"], found["plan_commit"])
        text = (registry / found["plan"]).read_text(encoding="utf-8")
        self.assertEqual(PROTOCOL.missing_fields(text), [])
        sample = PROTOCOL.fields(PROTOCOL.sections(text)["Sample size"])
        self.assertTrue(sample["Tasks"].startswith("3, of which 1 are long"))
        self.assertEqual(sample["Trials per task and arm"], "5")
        self.assertIn(FIRST_REV, text)
        self.assertIn(BASE_REV, text)
        self.assertEqual(found["power"]["minimum_detectable_effect"],
                         draft_tests.power(REPO, 3, 5, {"effect": EFFECT, "cv": CV})[
                             "minimum_detectable_effect"])

    def test_an_underpowered_registration_is_refused_with_the_detectable_effect_and_trials(self):
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, cv=0.25)
        power = draft_tests.power(REPO, 3, 5, {"effect": EFFECT, "cv": 0.25})
        self.assertEqual(caught.exception.code, "draft_test_underpowered")
        self.assertIn("%.1f%%" % (100 * power["minimum_detectable_effect"]), str(caught.exception))
        self.assertIn("run %d trials per task instead" % power["needed_trials"], str(caught.exception))
        self.assertFalse((self.root / draft_tests.RECORDS_DIR / "registrations").exists())

    def test_fewer_than_the_engines_minimum_trials_and_an_effect_above_sm2s_cap_are_refused(self):
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, spec_value=dict(SPEC, repetitions=2), cv=0.01)
        self.assertEqual(caught.exception.code, "draft_test_underpowered")
        self.assertIn("fewer than 5 trials per task", str(caught.exception))
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, effect=0.2)
        self.assertEqual(caught.exception.code, "draft_test_effect_too_large")

    def test_bad_specs_unknown_tasks_and_a_draft_at_its_base_are_refused(self):
        for bad in ({"model": "m"}, dict(SPEC, tasks=[]), dict(SPEC, tasks=["a", "a"]),
                    dict(SPEC, model="-x"), dict(SPEC, repetitions=21),
                    dict(SPEC, pack={"name": "x"})):
            with self.assertRaises(draft_tests.DraftTestError):
                register(self.root, spec_value=bad)
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, spec_value=dict(SPEC, tasks=["a", "zz"]))
        self.assertIn("zz", str(caught.exception))
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, revision=BASE_REV)
        self.assertEqual(caught.exception.code, "draft_test_unchanged")

    def test_a_registration_cannot_be_edited_afterwards_without_being_found(self):
        found = register(self.root)
        registry = self.root / draft_tests.RECORDS_DIR / draft_registration.REGISTRY_DIR
        plan = registry / found["plan"]
        self.assertEqual(plan.stat().st_mode & 0o777, 0o400)
        record = self.root / draft_tests.RECORDS_DIR / "registrations" / (
            found["registration_id"] + ".json")
        with self.assertRaises(FileExistsError):
            fd = os.open(str(record), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)
        plan.chmod(0o600)
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "- **Trials per task and arm:** 5", "- **Trials per task and arm:** 9"), encoding="utf-8")
        problems = draft_registration.load(self.root, found["registration_id"])["problems"]
        self.assertTrue(any("uncommitted changes" in item for item in problems))
        self.assertIn("the plan differs from the text registered", problems)

    def test_an_edit_to_the_draft_makes_the_registration_stale_and_it_stays_listed(self):
        found = register(self.root)
        newer = register(self.root, revision=SECOND_REV)
        with draft_at(SECOND_REV):
            listed = draft_registration.listed(self.root, draft_tests.identity(REPO, "tuned"))
        self.assertEqual([item["registration_id"] for item in listed],
                         [newer["registration_id"], found["registration_id"]])
        self.assertEqual([(item["stale"], item["stale_reason"]) for item in listed],
                         [(False, None), (True, "the draft has a newer checkpoint")])
        with draft_at(SECOND_REV):
            draft = draft_tests.identity(REPO, "tuned")
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            draft_tests.start_registration(self.root, draft, draft_request(SECOND_REV, repetitions=5),
                                           found["registration_id"])
        self.assertEqual(caught.exception.code, "draft_test_registration_stale")

    def test_a_pack_is_registered_by_its_digest_and_another_pack_or_commit_is_a_deviation(self):
        pack = {"name": "suite", "version": "1.0.0", "commit": "e" * 40, "digest": "f" * 64,
                "source": "/packs/suite", "tasks": TASKS}
        with mock.patch.object(draft_registration.packs, "select", return_value=pack):
            found = register(self.root, spec_value=dict(
                SPEC, pack={"name": "suite", "digest": "f" * 64}))
        self.assertEqual(found["pack"], {"name": "suite", "version": "1.0.0",
                                         "commit": "e" * 40, "digest": "f" * 64})
        registry = self.root / draft_tests.RECORDS_DIR / draft_registration.REGISTRY_DIR
        self.assertIn("digest `%s`" % ("f" * 64), (registry / found["plan"]).read_text(
            encoding="utf-8"))
        exact = draft_registration.measured(BASE_REV, FIRST_REV, found["config_digest"],
                                            ["b", "a", "c"], 5, "claude-test", "f" * 64)
        self.assertEqual(draft_registration.deviations(found, exact), [])
        self.assertEqual(draft_registration.deviations(found, dict(exact, pack_digest=None)),
                         ["the run's evaluator pack differs from the registration's"])
        self.assertEqual(draft_registration.deviations(found, dict(exact, revision=SECOND_REV)),
                         ["the run's draft commit differs from the registration's"])
        self.assertEqual(draft_registration.deviations(found, dict(exact, base_revision="c" * 40)),
                         ["the run's base commit differs from the registration's"])

    def test_an_unknown_or_malformed_registration_id_is_refused(self):
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            draft_registration.load(self.root, "11111111-1111-4111-8111-111111111112")
        self.assertEqual(caught.exception.code, "draft_test_registration_not_found")
        with self.assertRaises(draft_tests.DraftTestError):
            draft_registration.load(self.root, "../x")


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_run_matching_its_registration_reads_helped_by_the_decision_rule(self):
        supervisor, found, deviations = registered_run(self.tmp.name, CHEAPER5)
        self.assertEqual(deviations, [])
        payload, test = latest(supervisor)
        self.assertEqual((test["evidence"], test["registration"], test["deviations"]),
                         (replay.PREREGISTERED, found["registration_id"], []))
        self.assertEqual(test["verdict"], "helped")
        self.assertEqual(payload["registrations"][0]["registration_id"], found["registration_id"])
        server.DRAFT_TEST_VERDICTS.validate(payload)

    def test_its_mirror_reads_worse_and_a_spanning_interval_inconclusive(self):
        supervisor, _found, _ = registered_run(self.tmp.name, DEARER5)
        self.assertEqual(latest(supervisor)[1]["verdict"], "worse")
        with tempfile.TemporaryDirectory() as other:
            supervisor, _found, _ = registered_run(other, NOISY5)
            self.assertEqual(latest(supervisor)[1]["verdict"], "inconclusive")

    def test_the_same_run_unregistered_stays_exploratory(self):
        supervisor, _found, _ = registered_run(self.tmp.name, CHEAPER5, registration=False)
        _payload, test = latest(supervisor)
        self.assertEqual((test["verdict"], test["evidence"], test["registration"]),
                         ("exploratory", replay.EXPLORATORY, None))

    def test_any_deviation_from_the_registration_is_exploratory_with_no_direction(self):
        for changes, named in (({"tasks": ["a", "b"]}, "task set"),
                               ({"repetitions": 6}, "trials per task"),
                               ({"model": "claude-other"}, "model")):
            with tempfile.TemporaryDirectory() as tmp:
                supervisor, _found, deviations = registered_run(tmp, CHEAPER5, **changes)
                self.assertTrue(any(named in item for item in deviations), deviations)
                _payload, test = latest(supervisor)
                self.assertEqual((test["verdict"], test["evidence"]),
                                 ("exploratory", replay.EXPLORATORY), changes)
                self.assertTrue(any(named in item for item in test["reasons"]))

    def test_an_edited_registration_turns_a_matching_run_exploratory(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        self.assertEqual(latest(supervisor)[1]["verdict"], "helped")
        plan = (supervisor.state_root / draft_tests.RECORDS_DIR / draft_registration.REGISTRY_DIR
                / found["plan"])
        plan.chmod(0o600)
        plan.write_text(plan.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        _payload, test = latest(supervisor)
        self.assertEqual((test["verdict"], test["evidence"]), ("exploratory", replay.EXPLORATORY))
        self.assertTrue(any("not intact" in item for item in test["reasons"]))

    def test_a_registration_written_after_the_run_started_does_not_count(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        shown = {"run_id": RUN_ONE, "suite_id": "live-replay", "status": "succeeded", "created_at": "2000-01-01T00:00:00+00:00"}
        with mock.patch.object(supervisor, "show", return_value=shown):
            _payload, test = latest(supervisor)
        self.assertEqual(test["verdict"], "exploratory")
        self.assertIn("the registration was written after the run started", test["reasons"])
        # The supervisor stamps "Z"; a later start in that form still counts.
        with mock.patch.object(supervisor, "show", return_value=dict(
                shown, created_at="2999-01-01T00:00:00.000001Z")):
            self.assertEqual(latest(supervisor)[1]["verdict"], "helped")

    def test_the_comparison_is_the_engines_and_both_sides_are_registered_only_for_the_claim(self):
        supervisor, _found, _ = registered_run(self.tmp.name, CHEAPER5)
        from harness_core.studio import compare
        comparison = compare.compare_runs(supervisor, REPO, {"base": (RUN_ONE, 1),
                                                             "candidate": (RUN_ONE, 2)})
        self.assertTrue(comparison["direction_withheld"])
        self.assertEqual(draft_tests.claim(comparison)["verdict"], "exploratory")
        self.assertEqual(draft_tests.registered_claim(comparison)["verdict"], "helped")


def register_body(**changes):
    value = {"draft": "tuned", "request": dict(SPEC), "effect": EFFECT, "cv": CV}
    value.update(changes)
    return value


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.supervisor = Runs(Path(self.tmp.name) / "runs")
        self.supervisor.state_root = Path(self.tmp.name) / "state"
        self.supervisor.state_root.mkdir(mode=0o700)

    def call(self, path, body, admission=None, revision=FIRST_REV):
        with draft_at(revision), catalog():
            return route_call(self.supervisor, path, body, admission)

    def test_register_writes_a_registration_and_refuses_bad_input_with_status_codes(self):
        code, payload = self.call("/api/configure/test/register", register_body())
        self.assertEqual(code, 200)
        server.DRAFT_TEST_REGISTER.validate(payload)
        self.assertEqual(payload["registration"]["revision"], FIRST_REV)
        self.assertEqual(self.call("/api/configure/test/register", {"draft": "tuned"}),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(cv=0.25)),
                         (409, {"error": "draft_test_underpowered"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(effect=0.3)),
                         (400, {"error": "draft_test_effect_too_large"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(draft="gone")),
                         (404, {"error": "draft_not_found"}))

    def test_start_records_the_registration_and_its_deviations_and_refuses_a_stale_one(self):
        _code, payload = self.call("/api/configure/test/register", register_body())
        identity = payload["registration"]["registration_id"]
        selected = draft_request(FIRST_REV, repetitions=5)
        body = {"draft": "tuned", "request": selected.as_dict(), "confirmation_token": "token",
                "effect": EFFECT, "cv": CV, "registration": identity}
        code, started = self.call("/api/configure/test/start", body, Admission())
        self.assertEqual(code, 200, started)
        self.assertEqual((started["record"]["registration"], started["record"]["deviations"]),
                         (identity, []))
        deviating = dict(body, request=draft_request(FIRST_REV, repetitions=6).as_dict())
        code, started = self.call("/api/configure/test/start", deviating,
                                  Admission(run_id=RUN_TWO))
        self.assertEqual(code, 200, started)
        self.assertEqual(started["record"]["deviations"],
                         ["the run's trials per task differs from the registration's"])
        stale = dict(body, request=draft_request(SECOND_REV, repetitions=5).as_dict())
        self.assertEqual(self.call("/api/configure/test/start", stale,
                                   Admission(revision=SECOND_REV), revision=SECOND_REV),
                         (409, {"error": "draft_test_registration_stale"}))
        unknown = dict(body, registration="11111111-1111-4111-8111-111111111112")
        self.assertEqual(self.call("/api/configure/test/start", unknown, Admission()),
                         (404, {"error": "draft_test_registration_not_found"}))

    def test_the_register_route_names_its_citizen_command(self):
        commands = {item.path: item.cli_command for item in server.ROUTES.entries}
        self.assertEqual(commands["/api/configure/test/register"],
                         ("citizen", "draft", "test", "--register"))


class CliTests(unittest.TestCase):
    def run_cli(self, arguments, root):
        output = io.StringIO()
        supervisor = mock.Mock(state_root=root)
        with mock.patch.object(runs, "RunSupervisor", return_value=supervisor), \
                mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                contextlib.redirect_stdout(output):
            code = harness.main(["draft", "test", "tuned"] + arguments)
        return code, output.getvalue()

    def test_citizen_draft_test_register_writes_through_the_routes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arguments = ["--register", "--model", "claude-test", "--repetitions", "5",
                         "--task", "c", "--task", "a", "--task", "b", "--effect", "0.15",
                         "--cv", "0.1"]
            with draft_at(), catalog(), mock.patch.object(
                    draft_registration, "register", wraps=draft_registration.register) as spy:
                code, out = self.run_cli(arguments + ["--json"], root)
                self.assertEqual(code, 0, out)
                code, text = self.run_cli(arguments, root)
            self.assertEqual(code, 0, text)
            self.assertEqual(spy.call_args.args[2:], ("tuned", SPEC, 0.15, 0.1))
            payload = json.loads(out)
            self.assertEqual((payload["revision"], payload["tasks"]), (FIRST_REV, ["a", "b", "c"]))
            self.assertIn("registered ", text)
            self.assertIn("plan: benchmarks/preregistrations/", text)
            with draft_at():
                listed = draft_registration.listed(root, draft_tests.identity(REPO, "tuned"))
            self.assertEqual(len(listed), 2)

    def test_register_without_its_sample_or_underpowered_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_cli(["--register", "--model", "m"], Path(tmp))
            self.assertEqual(code, 2)
            self.assertIn("--register needs", out)
            with draft_at(), catalog():
                code, out = self.run_cli(["--register", "--model", "m", "--repetitions", "5",
                                          "--task", "a", "--effect", "0.15", "--cv", "0.25",
                                          "--json"], Path(tmp))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out)["error"]["code"], "draft_test_underpowered")


class RegisterRouteSecurityTests(studio_security.StudioSecurityFixture):
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

    def test_register_requires_a_session_csrf_and_origin_then_validates_its_input(self):
        path = "/api/configure/test/register"
        valid = register_body(draft="no-such-draft-1211")
        body = json.dumps(valid).encode()
        status, _headers, _body = self.request("POST", path, {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)
        status, _headers, _body = self.post(path, valid, {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.post(path, valid, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _headers, body = self.post(path, {"draft": "no-such-draft-1211", "extra": 1})
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))
        status, _headers, body = self.post(path, valid)
        self.assertEqual((status, json.loads(body)), (404, {"error": "draft_not_found"}))


if __name__ == "__main__":
    unittest.main()
