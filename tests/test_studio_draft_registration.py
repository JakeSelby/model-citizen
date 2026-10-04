"""Pre-register a draft test so it can say whether the change helped (AH-S346, #1211).

A registration is written before the run from the engine's pre-registration template, committed to
the Studio's private registry and checked by `experiment_protocol.check`; the one run that claims it
may read helped or worse only when it matches the committed plan exactly. The replays go through
`replay.execute` with the native benchmark faked, as `test_studio_draft_tests` does.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO, harness  # noqa: E402
from harness_core.studio import draft_registration, draft_tests, packs, replay, runs, server  # noqa: E402
from test_studio_compare import Runs, request, spec, target  # noqa: E402
from test_studio_draft_tests import (  # noqa: E402
    BASE_REV, FIRST_REV, SECOND_REV, RUN_ONE, RUN_TWO, Admission, Handler, draft_at,
    draft_request, route_call)
import test_studio_security as studio_security  # noqa: E402

PROTOCOL = replay._engine_module("experiment_protocol")
TASKS = [{"id": task, "label": task.upper(), "long": task == "c"} for task in ("a", "b", "c")]
SPEC = {"model": "claude-test", "repetitions": 5, "tasks": ["c", "a", "b"], "pack": None}
EFFECT, CV = 0.15, 0.1
OTHER_BASE = "c" * 40
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
def catalog(cv=CV):
    """The three-task repository catalog, and a declared planning variance of `cv`."""
    declared = {"cv": cv, "source": "benchmarks/ablations.json: declared for this test"}
    with mock.patch.object(replay, "_repository_tasks", return_value=TASKS), \
            mock.patch.object(draft_tests, "declared_planning", return_value=declared):
        yield


def register(root, revision=FIRST_REV, spec_value=None, effect=EFFECT, cv=None, declared=CV):
    with draft_at(revision), catalog(declared):
        return draft_registration.register(root, REPO, "tuned", dict(spec_value or SPEC),
                                           effect, cv)


def registered_run(tmp, candidate, registration=True, selected=None, run_id=RUN_ONE,
                   supervisor=None, found=None, stamp=None, **changes):
    """One finished draft test at five trials per task, started and claimed under a fresh
    registration (or `found`) unless `registration` is false; `changes` alter the request."""
    if supervisor is None:
        supervisor = Runs(Path(tmp) / "runs")
        supervisor.root.mkdir()
        supervisor.state_root = Path(tmp) / "state"
        supervisor.state_root.mkdir(mode=0o700)
    if registration and found is None:
        found = register(supervisor.state_root)
    if selected is None:
        selected = draft_request(FIRST_REV, repetitions=changes.pop("repetitions", 5), **changes)
    base, revision = selected.targets[0].revision, selected.targets[1].revision
    with draft_at(revision):
        draft = draft_tests.identity(REPO, "tuned")
        identity, deviations = draft_tests.start_registration(
            supervisor.state_root, REPO, draft, selected,
            found["registration_id"] if registration else None)
    # The server's order: claim, admit the run, then record the run the registration backs.
    if identity is not None:
        draft_registration.claim(supervisor.state_root, identity)
    if selected.repetitions != 5 or list(selected.tasks) != ["a", "b", "c"]:
        supervisor.add(run_id, selected, {base: sized(BASE5, selected),
                                          revision: sized(candidate, selected)}, stamp=stamp)
    else:
        supervisor.add(run_id, selected, {base: BASE5, revision: candidate}, stamp=stamp)
    if identity is not None:
        draft_registration.bind(supervisor.state_root, identity, run_id)
    with draft_at(revision):
        draft_tests.record(supervisor.state_root, run_id, draft, selected,
                           draft_tests.power(REPO, len(selected.tasks), selected.repetitions,
                                             {"effect": EFFECT, "cv": CV}),
                           identity, deviations)
    return supervisor, found, deviations


def latest(supervisor, revision=FIRST_REV):
    with draft_at(revision):
        payload = draft_tests.verdicts(supervisor, REPO, supervisor.state_root, "tuned")
    return payload, payload["checkpoints"][0]["latest"]


def registry(root):
    return Path(root) / draft_tests.RECORDS_DIR / draft_registration.REGISTRY_DIR


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
        self.assertEqual((found["stale"], found["problems"], found["used_by"]), (False, [], None))
        self.assertTrue(found["plan"].startswith("benchmarks/preregistrations/"))
        errors, checked = PROTOCOL.check(found["plan"], str(registry(self.root)),
                                         cwd=str(registry(self.root)))
        self.assertEqual(errors, [])
        self.assertEqual(checked["pre_registration_commit"], found["plan_commit"])
        text = (registry(self.root) / found["plan"]).read_text(encoding="utf-8")
        self.assertEqual(PROTOCOL.missing_fields(text), [])
        sample = PROTOCOL.fields(PROTOCOL.sections(text)["Sample size"])
        self.assertTrue(sample["Tasks"].startswith("3, of which 1 are long"))
        self.assertEqual(sample["Trials per task and arm"], "5")
        self.assertEqual(found["committed"], {
            "base_revision": BASE_REV, "revision": FIRST_REV,
            "config_digest": found["config_digest"], "tasks": ["a", "b", "c"], "trials": 5,
            "model": "claude-test", "pack_digest": None,
            "manifest_digest": found["manifest_digest"]})
        self.assertEqual(found["power"]["minimum_detectable_effect"],
                         draft_tests.power(REPO, 3, 5, {"effect": EFFECT, "cv": CV})[
                             "minimum_detectable_effect"])
        self.assertEqual(found["power"]["cv_source"],
                         "benchmarks/ablations.json: declared for this test")

    def test_the_plan_is_the_template_section_by_section_with_its_own_text_kept(self):
        found = register(self.root)
        text = (registry(self.root) / found["plan"]).read_text(encoding="utf-8")
        template = draft_registration.TEMPLATE.read_text(encoding="utf-8")
        body = template.split("\n---\n", 1)[1]
        self.assertEqual(list(PROTOCOL.sections(text)), list(PROTOCOL.sections(body)))
        kept = PROTOCOL.fields(PROTOCOL.sections(body)["Guardrails"])["Contamination control"]
        self.assertEqual(PROTOCOL.fields(PROTOCOL.sections(text)["Guardrails"])[
            "Contamination control"], kept)
        self.assertIn("the installed checkout's `benchmarks/tasks.json` as read at registration",
                      PROTOCOL.fields(PROTOCOL.sections(text)["Run"])["Task manifest"])
        rule = PROTOCOL.sections(text)["Decision rule"]
        self.assertIn("The result is published with its intervals, whatever it shows.", rule)
        self.assertIn("(draft\n  minus base)", rule)
        self.assertNotIn("bare", rule)
        log = PROTOCOL.sections(text)["Deviation log"]
        self.assertIn("Append a dated entry for every change after the first trial", log)
        self.assertNotIn("<", log)

    def test_a_template_field_left_unfilled_or_an_override_it_lacks_fails_registration(self):
        template = draft_registration.TEMPLATE.read_text(encoding="utf-8")
        grown = template.replace("- **Other:** <each further guardrail metric and its bound, or "
                                 "\"none\">", "- **Other:** none.\n- **Novel:** <a new field>")
        self.assertNotEqual(grown, template)
        with mock.patch.object(draft_registration.TEMPLATE.__class__, "read_text",
                               return_value=grown):
            with self.assertRaises(draft_tests.DraftTestError) as caught:
                register(self.root)
        self.assertIn("Novel", str(caught.exception))
        shrunk = template.replace("- **Fallback rate:**", "- **Fallback:**")
        with mock.patch.object(draft_registration.TEMPLATE.__class__, "read_text",
                               return_value=shrunk):
            with self.assertRaises(draft_tests.DraftTestError) as caught:
                register(self.root)
        self.assertIn("Fallback rate", str(caught.exception))

    def test_a_real_evaluator_pack_is_registered_by_its_digest_against_the_declared_variance(self):
        chosen = packs.discover(REPO)["packs"][0]
        tasks = [item["id"] for item in chosen["tasks"]]
        with draft_at():
            found = draft_registration.register(
                self.root, REPO, "tuned", {"model": "claude-test", "repetitions": 8,
                                           "tasks": tasks, "pack": {"name": chosen["name"],
                                                                    "digest": chosen["digest"]}},
                EFFECT, None)
        self.assertEqual((found["pack"]["digest"], found["committed"]["pack_digest"],
                          found["manifest_digest"]), (chosen["digest"],) * 3)
        self.assertEqual(found["power"]["cv"], draft_tests.declared_planning(REPO)["cv"])
        with draft_at(), self.assertRaises(draft_tests.DraftTestError):
            draft_registration.register(
                self.root, REPO, "tuned", {"model": "claude-test", "repetitions": 8,
                                           "tasks": tasks, "pack": {"name": chosen["name"],
                                                                    "digest": "0" * 64}},
                EFFECT, None)

    def test_an_underpowered_registration_is_refused_with_the_detectable_effect_and_trials(self):
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, declared=0.25)
        power = draft_tests.power(REPO, 3, 5, {"effect": EFFECT, "cv": 0.25})
        self.assertEqual(caught.exception.code, "draft_test_underpowered")
        self.assertIn("%.1f%%" % (100 * power["minimum_detectable_effect"]), str(caught.exception))
        self.assertIn("run %d trials per task instead" % power["needed_trials"], str(caught.exception))
        self.assertFalse((self.root / draft_tests.RECORDS_DIR / "registrations").exists())

    def test_a_stated_cv_a_task_subset_too_few_trials_and_a_large_effect_are_refused(self):
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, cv=0.01)
        self.assertEqual(caught.exception.code, "draft_test_cv_not_declared")
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, spec_value=dict(SPEC, tasks=["a", "b"]))
        self.assertEqual(caught.exception.code, "draft_test_registration_subset")
        self.assertIn("leaves out c", str(caught.exception))
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, spec_value=dict(SPEC, repetitions=2), declared=0.01)
        self.assertEqual(caught.exception.code, "draft_test_underpowered")
        self.assertIn("fewer than 5 trials per task", str(caught.exception))
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, effect=0.2)
        self.assertEqual(caught.exception.code, "draft_test_effect_too_large")

    def test_no_declared_variance_is_refused_with_the_way_to_declare_one(self):
        missing = draft_tests.DraftTestError("no planning assumption is declared",
                                             "draft_test_planning_unavailable")
        with draft_at(), catalog(), mock.patch.object(draft_tests, "declared_planning",
                                                      side_effect=missing):
            with self.assertRaises(draft_tests.DraftTestError) as caught:
                draft_registration.register(self.root, REPO, "tuned", dict(SPEC), EFFECT, None)
        self.assertEqual(caught.exception.code, "draft_test_variance_undeclared")
        for named in ("scripts/replay_power.py --pilot", "benchmarks/ablations.json",
                      "cost_bench.py replay --exploratory"):
            self.assertIn(named, str(caught.exception))
        self.assertNotIn("state a coefficient", str(caught.exception))

    def test_the_model_is_stripped_so_the_record_matches_its_plan(self):
        found = register(self.root, spec_value=dict(SPEC, model="  claude-test "))
        self.assertEqual((found["model"], found["committed"]["model"], found["problems"]),
                         ("claude-test", "claude-test", []))

    def test_bad_specs_unknown_tasks_and_a_draft_at_its_base_are_refused(self):
        for bad in ({"model": "m"}, dict(SPEC, tasks=[]), dict(SPEC, tasks=["a", "a"]),
                    dict(SPEC, model="-x"), dict(SPEC, model="a`b"), dict(SPEC, repetitions=21),
                    dict(SPEC, pack={"name": "x"})):
            with self.assertRaises(draft_tests.DraftTestError):
                register(self.root, spec_value=bad)
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, spec_value=dict(SPEC, tasks=["a", "zz"]))
        self.assertIn("zz", str(caught.exception))
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            register(self.root, revision=BASE_REV)
        self.assertEqual(caught.exception.code, "draft_test_unchanged")

    def test_a_registration_cannot_be_written_over_and_an_edit_is_found(self):
        found = register(self.root)
        plan = registry(self.root) / found["plan"]
        self.assertEqual(plan.stat().st_mode & 0o777, 0o400)
        with self.assertRaises(PermissionError):  # the gate never runs as root
            plan.write_text("over", encoding="utf-8")
        records = os.open(str(self.root / draft_tests.RECORDS_DIR / "registrations"),
                          os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(FileExistsError):
                draft_registration._write_once(records, found["registration_id"] + ".json", b"{}")
        finally:
            os.close(records)
        plan.chmod(0o600)
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "- **Trials per task and arm:** 5", "- **Trials per task and arm:** 9"), encoding="utf-8")
        problems = draft_registration.load(self.root, found["registration_id"])["problems"]
        self.assertTrue(any("uncommitted changes" in item for item in problems))
        self.assertIn("the plan differs from the text registered", problems)

    def test_a_rewritten_record_is_compared_through_the_committed_plan(self):
        found = register(self.root)
        path = self.root / draft_tests.RECORDS_DIR / "registrations" / (
            found["registration_id"] + ".json")
        value = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        path.write_text(json.dumps(dict(value, model="claude-other", tasks=["a"])), encoding="utf-8")
        loaded = draft_registration.load(self.root, found["registration_id"])
        self.assertIn("the record differs from the committed plan", loaded["problems"])
        self.assertEqual(loaded["committed"]["model"], "claude-test")
        run = draft_registration.measured(BASE_REV, FIRST_REV, value["config_digest"], ["a"], 5,
                                          "claude-other", None)
        self.assertEqual(draft_registration.deviations(loaded["committed"], run), [
            "the run's task set differs from the registration's",
            "the run's model differs from the registration's"])

    def test_the_registry_check_ignores_a_git_dir_in_the_environment(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": str(Path(self.tmp.name) / "elsewhere")}):
            found = register(self.root)
            self.assertEqual(draft_registration.load(self.root, found["registration_id"])[
                "problems"], [])

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
            draft_tests.start_registration(self.root, REPO, draft,
                                           draft_request(SECOND_REV, repetitions=5),
                                           found["registration_id"])
        self.assertEqual(caught.exception.code, "draft_test_registration_stale")

    def test_a_registration_backs_one_run_and_a_reuse_is_refused(self):
        found = register(self.root)
        identity = found["registration_id"]
        with draft_at():
            draft = draft_tests.identity(REPO, "tuned")
        selected = draft_request(FIRST_REV, repetitions=5)
        self.assertEqual(draft_tests.start_registration(self.root, REPO, draft, selected,
                                                        identity), (identity, []))
        draft_registration.claim(self.root, identity)
        self.assertEqual(draft_registration.used_by(self.root, identity), draft_registration.PENDING)
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            draft_registration.claim(self.root, identity)
        self.assertEqual(caught.exception.code, "draft_test_registration_used")
        draft_registration.bind(self.root, identity, RUN_ONE)
        draft_registration.release(self.root, identity)  # a bound claim is never undone
        self.assertEqual(draft_registration.used_by(self.root, identity), RUN_ONE)
        with self.assertRaises(draft_tests.DraftTestError) as caught:
            draft_tests.start_registration(self.root, REPO, draft, selected, identity)
        self.assertEqual(caught.exception.code, "draft_test_registration_used")

    def test_a_changed_task_manifest_at_start_is_a_deviation(self):
        found = register(self.root)
        with draft_at():
            draft = draft_tests.identity(REPO, "tuned")
        with mock.patch.object(draft_registration, "manifest",
                               return_value={"source": "x", "digest": "0" * 64}):
            _identity, deviations = draft_tests.start_registration(
                self.root, REPO, draft, draft_request(FIRST_REV, repetitions=5),
                found["registration_id"])
        self.assertEqual(deviations, ["the run's task manifest differs from the registration's"])

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
        self.assertEqual(payload["registrations"][0]["used_by"], RUN_ONE)
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
        other_base = request([target("branch", OTHER_BASE, OTHER_BASE),
                              target("draft", "tuned", FIRST_REV)], repetitions=5)
        for changes, named in (({"tasks": ["a", "b"]}, "task set"),
                               ({"repetitions": 6}, "trials per task"),
                               ({"model": "claude-other"}, "model"),
                               ({"selected": other_base}, "base commit")):
            with tempfile.TemporaryDirectory() as tmp:
                supervisor, _found, deviations = registered_run(tmp, CHEAPER5, **changes)
                self.assertTrue(any(named in item for item in deviations), deviations)
                _payload, test = latest(supervisor)
                self.assertEqual((test["verdict"], test["evidence"]),
                                 ("exploratory", replay.EXPLORATORY), changes)
                self.assertTrue(any(named in item for item in test["reasons"]))

    def test_another_pack_or_draft_commit_in_the_measured_run_is_exploratory(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        candidate = {"ref": {"revision": FIRST_REV, "config_digest": found["config_digest"]},
                     "tasks": ["a", "b", "c"], "trials": 5, "model": "claude-test",
                     "pack_digest": None}
        comparison = {"base": {"ref": {"revision": BASE_REV}}, "candidate": candidate}
        root = supervisor.state_root
        self.assertEqual(draft_registration.evidence(root, found["registration_id"], [], RUN_ONE,
                                                     comparison, None),
                         (replay.PREREGISTERED, []))
        for changed, named in (({"pack_digest": "f" * 64}, "evaluator pack"),
                               ({"ref": {"revision": SECOND_REV,
                                         "config_digest": found["config_digest"]}},
                                "draft commit")):
            label, reasons = draft_registration.evidence(
                root, found["registration_id"], [], RUN_ONE,
                dict(comparison, candidate=dict(candidate, **changed)), None)
            self.assertEqual(label, replay.EXPLORATORY)
            self.assertTrue(any(named in item for item in reasons), reasons)

    def test_another_pack_in_the_run_reads_exploratory_through_the_full_verdict(self):
        pack = {"name": "suite", "version": "1.0.0", "commit": "e" * 40, "digest": "f" * 64,
                "source": "/packs/suite"}
        supervisor, _found, deviations = registered_run(
            self.tmp.name, CHEAPER5, selected=draft_request(FIRST_REV, repetitions=5, pack=pack),
            stamp={"pack_digest": pack["digest"], "pack_commit": pack["commit"]})
        self.assertIn("the run's evaluator pack differs from the registration's", deviations)
        _payload, test = latest(supervisor)
        self.assertEqual((test["verdict"], test["evidence"]), ("exploratory", replay.EXPLORATORY))
        self.assertIn("the run's evaluator pack differs from the registration's", test["reasons"])

    def test_a_run_of_another_draft_commit_reads_exploratory_through_the_full_verdict(self):
        # Registered at the first checkpoint; a run recorded against it measured the second.
        supervisor = Runs(Path(self.tmp.name) / "runs")
        supervisor.root.mkdir()
        supervisor.state_root = Path(self.tmp.name) / "state"
        supervisor.state_root.mkdir(mode=0o700)
        found = register(supervisor.state_root)
        selected = draft_request(SECOND_REV, repetitions=5)
        draft_registration.claim(supervisor.state_root, found["registration_id"])
        supervisor.add(RUN_ONE, selected, {BASE_REV: BASE5, SECOND_REV: CHEAPER5})
        draft_registration.bind(supervisor.state_root, found["registration_id"], RUN_ONE)
        with draft_at(SECOND_REV):
            draft_tests.record(supervisor.state_root, RUN_ONE,
                               draft_tests.identity(REPO, "tuned"), selected,
                               draft_tests.power(REPO, 3, 5, {"effect": EFFECT, "cv": CV}),
                               found["registration_id"], [])
        _payload, test = latest(supervisor, SECOND_REV)
        self.assertEqual((test["verdict"], test["evidence"]), ("exploratory", replay.EXPLORATORY))
        self.assertIn("the run's draft commit differs from the registration's", test["reasons"])

    def test_a_verdicts_request_reads_each_registration_once(self):
        supervisor, _found, _ = registered_run(self.tmp.name, CHEAPER5)
        register(supervisor.state_root)
        with mock.patch.object(draft_registration, "_annotated",
                               wraps=draft_registration._annotated) as spy:
            payload, test = latest(supervisor)
        self.assertEqual(test["verdict"], "helped")
        self.assertEqual(len(payload["registrations"]), 2)
        self.assertEqual(spy.call_count, 2)

    def test_a_second_run_under_a_used_registration_never_counts(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        label, reasons = draft_registration.evidence(
            supervisor.state_root, found["registration_id"], [], RUN_TWO, {}, None)
        self.assertEqual(label, replay.EXPLORATORY)
        self.assertIn("the registration backs run %s, not this run" % RUN_ONE, reasons)

    def test_an_edited_registration_turns_a_matching_run_exploratory(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        self.assertEqual(latest(supervisor)[1]["verdict"], "helped")
        plan = registry(supervisor.state_root) / found["plan"]
        plan.chmod(0o600)
        plan.write_text(plan.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
        _payload, test = latest(supervisor)
        self.assertEqual((test["verdict"], test["evidence"]), ("exploratory", replay.EXPLORATORY))
        self.assertTrue(any("not intact" in item for item in test["reasons"]))

    def test_a_registration_written_after_the_run_started_does_not_count(self):
        supervisor, found, _ = registered_run(self.tmp.name, CHEAPER5)
        shown = {"run_id": RUN_ONE, "suite_id": "live-replay", "status": "succeeded",
                 "created_at": "2000-01-01T00:00:00+00:00"}
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
    value = {"draft": "tuned", "request": dict(SPEC), "effect": EFFECT, "cv": None}
    value.update(changes)
    return value


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.supervisor = Runs(Path(self.tmp.name) / "runs")
        self.supervisor.state_root = Path(self.tmp.name) / "state"
        self.supervisor.state_root.mkdir(mode=0o700)

    def call(self, path, body, admission=None, revision=FIRST_REV, declared=CV):
        with draft_at(revision), catalog(declared):
            return route_call(self.supervisor, path, body, admission)

    def registered(self):
        _code, payload = self.call("/api/configure/test/register", register_body())
        return payload["registration"]["registration_id"]

    def test_register_writes_a_registration_and_refuses_bad_input_with_status_codes(self):
        code, payload = self.call("/api/configure/test/register", register_body())
        self.assertEqual(code, 200)
        server.DRAFT_TEST_REGISTER.validate(payload)
        self.assertEqual(payload["registration"]["revision"], FIRST_REV)
        self.assertEqual(self.call("/api/configure/test/register", {"draft": "tuned"}),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(), declared=0.25),
                         (409, {"error": "draft_test_underpowered"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(cv=0.1)),
                         (400, {"error": "draft_test_cv_not_declared"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(
            request=dict(SPEC, tasks=["a"]))), (400, {"error": "draft_test_registration_subset"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(effect=0.3)),
                         (400, {"error": "draft_test_effect_too_large"}))
        self.assertEqual(self.call("/api/configure/test/register", register_body(draft="gone")),
                         (404, {"error": "draft_not_found"}))

    def test_plan_returns_the_servers_deviations_from_the_named_registration(self):
        identity = self.registered()
        form = {"model": "claude-test", "repetitions": 6, "tasks": ["a", "b", "c"],
                "max_budget_usd": "2", "spend_cap_usd": "200", "pack": None}
        code, payload = self.call("/api/configure/test/plan", {
            "draft": "tuned", "request": form, "effect": EFFECT, "cv": CV,
            "registration": identity}, Admission())
        self.assertEqual(code, 200, payload)
        server.DRAFT_TEST_PLAN.validate(payload)
        self.assertEqual(payload["registration"], {
            "registration_id": identity,
            "deviations": ["the run's trials per task differs from the registration's"]})
        code, payload = self.call("/api/configure/test/plan", {
            "draft": "tuned", "request": form, "effect": EFFECT, "cv": CV}, Admission())
        self.assertEqual((code, payload["registration"]), (200, None))

    def test_start_claims_the_registration_once_and_refuses_a_reuse_or_a_stale_one(self):
        identity = self.registered()
        selected = draft_request(FIRST_REV, repetitions=5)
        body = {"draft": "tuned", "request": selected.as_dict(), "confirmation_token": "token",
                "effect": EFFECT, "cv": CV, "registration": identity}
        code, started = self.call("/api/configure/test/start", body, Admission())
        self.assertEqual(code, 200, started)
        self.assertEqual((started["record"]["registration"], started["record"]["deviations"]),
                         (identity, []))
        self.assertEqual(draft_registration.used_by(self.supervisor.state_root, identity),
                         started["run_id"])
        self.assertEqual(self.call("/api/configure/test/start", body, Admission(run_id=RUN_TWO)),
                         (409, {"error": "draft_test_registration_used"}))
        fresh = self.registered()
        deviating = dict(body, registration=fresh,
                         request=draft_request(FIRST_REV, repetitions=6).as_dict())
        code, started = self.call("/api/configure/test/start", deviating,
                                  Admission(run_id=RUN_TWO))
        self.assertEqual(code, 200, started)
        self.assertEqual(started["record"]["deviations"],
                         ["the run's trials per task differs from the registration's"])
        stale = dict(body, registration=self.registered(),
                     request=draft_request(SECOND_REV, repetitions=5).as_dict())
        self.assertEqual(self.call("/api/configure/test/start", stale,
                                   Admission(revision=SECOND_REV), revision=SECOND_REV),
                         (409, {"error": "draft_test_registration_stale"}))
        unknown = dict(body, registration="11111111-1111-4111-8111-111111111112")
        self.assertEqual(self.call("/api/configure/test/start", unknown, Admission()),
                         (404, {"error": "draft_test_registration_not_found"}))

    def start_body(self, identity):
        return {"draft": "tuned", "request": draft_request(FIRST_REV, repetitions=5).as_dict(),
                "confirmation_token": "token", "effect": EFFECT, "cv": CV,
                "registration": identity}

    def test_two_starts_racing_for_one_registration_start_one_run(self):
        identity = self.registered()
        barrier = threading.Barrier(2)
        admissions = [Admission(run_id=RUN_ONE), Admission(run_id=RUN_TWO)]
        for admission in admissions:
            confirm = admission.confirm

            def synchronised(value, confirm=confirm):
                barrier.wait(timeout=10)  # both starts reach the claim together
                return confirm(value)
            admission.confirm = synchronised
        route = next(item for item in server.ROUTES.entries
                     if item.path == "/api/configure/test/start")
        handlers = [Handler(self.supervisor, self.start_body(identity)) for _ in admissions]
        with draft_at(), catalog(), mock.patch.object(server, "_replay_admission",
                                                      side_effect=admissions):
            threads = [threading.Thread(target=route.handler, args=(handler, route))
                       for handler in handlers]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        codes = sorted(handler.response[0] for handler in handlers)
        self.assertEqual(codes, [200, 409], [handler.response for handler in handlers])
        self.assertEqual(sum(len(admission.started) for admission in admissions), 1)
        winner = next(handler for handler in handlers if handler.response[0] == 200)
        self.assertEqual(winner.response[1]["record"]["deviations"], [])
        self.assertEqual(draft_registration.used_by(self.supervisor.state_root, identity),
                         winner.response[1]["run_id"])
        loser = next(handler for handler in handlers if handler.response[0] == 409)
        self.assertEqual(loser.response[1], {"error": "draft_test_registration_used"})

    def test_a_claim_that_cannot_be_written_refuses_the_start(self):
        identity = self.registered()
        admission = Admission()
        with mock.patch.object(draft_registration, "_write_once", side_effect=OSError("disk")):
            response = self.call("/api/configure/test/start", self.start_body(identity), admission)
        self.assertEqual(response, (500, {"error": "draft_test_registration_failed"}))
        self.assertEqual(admission.started, [])
        self.assertIsNone(draft_registration.used_by(self.supervisor.state_root, identity))

    def test_a_start_that_is_not_admitted_releases_its_claim(self):
        identity = self.registered()
        admission = Admission()

        def refuse(_selected, _token):
            raise replay.ReplayError("refused")
        admission.start_confirmed = refuse
        code, _payload = self.call("/api/configure/test/start", self.start_body(identity), admission)
        self.assertEqual(code, 400)
        self.assertIsNone(draft_registration.used_by(self.supervisor.state_root, identity))
        code, payload = self.call("/api/configure/test/start", self.start_body(identity),
                                  Admission())
        self.assertEqual((code, payload["record"]["deviations"]), (200, []))

    def test_the_register_route_names_its_citizen_command(self):
        commands = {item.path: item.cli_command for item in server.ROUTES.entries}
        self.assertEqual(commands["/api/configure/test/register"],
                         ("citizen", "draft", "test", "{draft}", "--register", "--model", "{model}",
                          "--repetitions", "{repetitions}", "--task", "{task}", "--pack", "{pack}",
                          "--pack-digest", "{pack_digest}", "--effect", "{effect}", "--json"))


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
                         "--task", "c", "--task", "a", "--task", "b", "--effect", "0.15"]
            with draft_at(), catalog(), mock.patch.object(
                    draft_registration, "register", wraps=draft_registration.register) as spy:
                code, out = self.run_cli(arguments + ["--json"], root)
                self.assertEqual(code, 0, out)
                code, text = self.run_cli(arguments, root)
            self.assertEqual(code, 0, text)
            self.assertEqual(spy.call_args.args[2:], ("tuned", SPEC, 0.15, None))
            payload = json.loads(out)
            # The register route's own object: the registration and its power line.
            self.assertEqual(sorted(payload), ["power_line", "registration"])
            registered = payload["registration"]
            self.assertEqual((registered["revision"], registered["tasks"]),
                             (FIRST_REV, ["a", "b", "c"]))
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
            with draft_at(), catalog(0.25):
                code, out = self.run_cli(["--register", "--model", "m", "--repetitions", "5",
                                          "--task", "a", "--task", "b", "--task", "c",
                                          "--effect", "0.15", "--json"], Path(tmp))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out), {"error": "draft_test_underpowered"})


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
