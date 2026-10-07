"""The Studio live replay binds two targets, spend, native rows and table metrics."""
import contextlib
import json
import os
import shlex
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_harness import REPO
from test_replay_pack import make_pack  # noqa: E402
from harness_core.studio import replay, replay_runner, run_store, runs, server, spend_guard
from studio_target_support import FixtureTargetService


@contextlib.contextmanager
def fixture_tasks(*identities):
    """Read the task catalog from a fixture, since the repository's own tasks.json retires every
    in-repository task in favour of evaluator packs."""
    with tempfile.TemporaryDirectory() as root:
        (Path(root) / "benchmarks").mkdir()
        (Path(root) / "benchmarks" / "tasks.json").write_text(json.dumps(
            {"schema_version": 1, "tasks": [{"id": item} for item in identities]}), encoding="utf-8")
        tasks = replay._repository_tasks
        with mock.patch.object(replay, "_repository_tasks", lambda _repository: tasks(root)), \
                mock.patch.object(replay.packs, "discover", lambda _repository: {
                    "packs": [], "default_digest": None, "skipped": []}), \
                mock.patch.object(replay, "registered_sample", lambda _repository, _plan: {
                    "tasks": len(identities), "long": None, "trials": 1,
                    "power_calculation": None, "have": None, "min_trials": 1}), \
                mock.patch.object(replay, "registered_budget", lambda _repository, plan: {
                    "per_run_usd": "2", "whole_run_cap_usd": "20", "spend": "fixture",
                    "pre_registration": plan}):
            yield Path(root)


def target(kind, ref, revision, digest=None):
    return {"kind": kind, "ref": ref, "revision": revision,
            "version": ref.removeprefix("v") if kind == "release" else None,
            "draft": ref if kind == "draft" else None, "config_digest": digest}


def request(**changes):
    value = {
        "targets": [
            target("release", "v0.17.0", "a" * 40),
            target("draft", "cost-pass", "b" * 40, replay.DEFAULT_CONFIG_DIGEST),
        ],
        "model": "claude-test", "repetitions": 2, "tasks": ["one", "two"],
        "max_budget_usd": "2", "spend_cap_usd": "20",
        "pre_registration": "docs/pre-registration-template.md",
    }
    value.update(changes)
    return value


def row(tag, task, arm, rep, passed, cost):
    return {"schema_version": 1, "tag": tag, "task": task, "arm": arm, "rep": rep,
            "passed": passed, "error": False, "cost_usd": cost,
            "harness_sha": ("a" * 40 if tag == "v0.17.0" else tag), "model": "claude-test"}


def write_native_result(command, records, preflight=0.0, stopped=False):
    ref = command[command.index("--tag") + 1]
    run_cap = float(command[command.index("--run-cap") + 1])
    spend_cap = float(command[command.index("--spend-cap") + 1])
    target_output = Path(command[command.index("--out") + 1]) / ref
    target_output.mkdir(parents=True)
    if records:
        (target_output / replay.RESULTS_NAME).write_text(
            "".join(json.dumps(item) + "\n" for item in records))
    scored = sum(run_cap if item["cost_usd"] is None else item["cost_usd"]
                 for item in records)
    spend_path = target_output / replay.SPEND_NAME
    spend_path.write_text(json.dumps({
        "schema_version": 1, "tag": ref, "run_cap_usd": run_cap,
        "spend_cap_usd": spend_cap, "preflight_spend_usd": preflight,
        "scored_spend_usd": round(scored, 6),
        "charged_spend_usd": round(preflight + scored, 6),
        "stopped_at_cap": stopped,
    }) + "\n")
    spend_path.chmod(0o600)


class StudioReplayTests(unittest.TestCase):
    def test_two_explicit_targets_each_launch_their_own_resolved_profile(self):
        parsed = replay.ReplayRequest.parse(request())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            calls = []

            def launch(command, **kwargs):
                calls.append((command, kwargs))
                ref = command[command.index("--tag") + 1]
                records = [row(ref, task, arm, rep, True, 0.25)
                           for task in ("one", "two") for rep in (1, 2)
                           for arm in replay.ARM_NAMES]
                write_native_result(command, records)
                return SimpleNamespace(returncode=0)

            summary = replay.execute(parsed, REPO, output, launch)

        self.assertEqual([call[0][call[0].index("--tag") + 1] for call in calls],
                         ["a" * 40, "b" * 40])
        self.assertEqual(summary["targets"], request()["targets"])
        self.assertEqual(summary["measures"], "source")
        self.assertEqual(len(summary["table"]), 8)
        self.assertEqual(len(summary["cases"]), 18)
        self.assertTrue(all(item["status"] == "completed" for item in summary["cases"]))
        self.assertEqual({item["target"]["ref"] for item in summary["table"]},
                         {"v0.17.0", "cost-pass"})

    def test_missing_or_implicit_targets_are_refused_before_launch(self):
        for supplied in ([], [target("release", "v0.17.0", "a" * 40)],
                         [target("release", "v0.17.0", "") for _ in range(2)]):
            with self.subTest(targets=supplied), self.assertRaises(replay.ReplayError):
                replay.ReplayRequest.parse(request(targets=supplied))

    def test_caps_must_round_trip_through_the_native_float_before_confirmation(self):
        precise = "0.1234567890123456789"
        with self.assertRaisesRegex(replay.ReplayError, "more precision"):
            replay.ReplayRequest.parse(request(
                max_budget_usd="0.1", spend_cap_usd=precise))
        parsed = replay.ReplayRequest.parse(request(
            max_budget_usd="0.1", spend_cap_usd="0.12345678901234568"))
        self.assertEqual(parsed.spend_cap_usd, "0.12345678901234568")

    def test_ui_target_refs_are_resolved_before_preview_and_launch(self):
        unresolved = request(targets=[{"kind": "release", "ref": "v0.17.0"},
                                      {"kind": "draft", "ref": "cost-pass"}])
        calls = []

        def resolve(kind, ref):
            calls.append((kind, ref))
            return target(kind, ref, "a" * 40 if kind == "release" else "b" * 40,
                          replay.DEFAULT_CONFIG_DIGEST if kind == "draft" else None)

        parsed = replay.resolve_request(unresolved, resolve)
        preview = replay.preview_payload([], parsed)
        self.assertEqual(calls, [("release", "v0.17.0"), ("draft", "cost-pass")])
        self.assertEqual([item["revision"] for item in preview["request"]["targets"]],
                         ["a" * 40, "b" * 40])
        self.assertEqual(preview["request"]["targets"][1]["config_digest"], replay.DEFAULT_CONFIG_DIGEST)
        self.assertEqual(len(replay.case_identities(parsed)), 18)
        launch = replay.launch_payload(parsed, "one-use-token")
        self.assertEqual(launch["targets"], preview["request"]["targets"])
        self.assertEqual(launch["case_identities"], replay.case_identities(parsed))
        self.assertEqual(json.loads(launch["parameters"]["request_json"]), parsed.as_dict())

    def test_empty_optional_pre_registration_normalizes_before_validation(self):
        unresolved = request(
            targets=[{"kind": "branch", "ref": "main"},
                     {"kind": "draft", "ref": "cost-pass"}],
            pre_registration="")
        parsed = replay.resolve_request(
            unresolved, lambda kind, ref: target(
                kind, ref, "a" * 40 if kind == "branch" else "b" * 40,
                replay.DEFAULT_CONFIG_DIGEST if kind == "draft" else None))
        self.assertIsNone(parsed.pre_registration)

    def test_release_writes_project_history_while_draft_is_exploratory(self):
        parsed = replay.ReplayRequest.parse(dict(request(), evidence="pre-registered"))
        with tempfile.TemporaryDirectory() as temporary:
            release = replay.command_for_target(parsed, parsed.targets[0], REPO,
                                                Path(temporary) / "release")
            draft = replay.command_for_target(parsed, parsed.targets[1], REPO,
                                              Path(temporary) / "draft")
        self.assertEqual(release[release.index("--history-dir") + 1], str(REPO / "benchmarks"))
        self.assertEqual(release[release.index("--tag") + 1], "a" * 40)
        self.assertIn("--pre-registration", release)
        self.assertNotIn("--exploratory", release)
        self.assertIn("--exploratory", draft)
        self.assertNotIn("--history-dir", draft)

    def test_release_history_transactions_serialize_concurrent_pair_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            history = repository / "benchmarks"
            history.mkdir()
            (history / "history.jsonl").write_text("", encoding="utf-8")
            (history / "history.md").write_text("start\n", encoding="utf-8")
            start = threading.Barrier(2)
            errors = []

            def worker(label):
                try:
                    start.wait()

                    def update(stage):
                        rows = (stage / "history.jsonl").read_text(encoding="utf-8")
                        time.sleep(0.03)
                        (stage / "history.jsonl").write_text(
                            rows + json.dumps({"label": label}) + "\n", encoding="utf-8")
                        (stage / "history.md").write_text(
                            "labels: " + ",".join(
                                item["label"] for item in map(json.loads, (rows +
                                    json.dumps({"label": label}) + "\n").splitlines())) + "\n",
                            encoding="utf-8")
                        return SimpleNamespace(returncode=0)

                    replay.release_history_transaction(repository, update)
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(label,)) for label in ("one", "two")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            rows = [json.loads(line) for line in
                    (history / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            markdown = (history / "history.md").read_text(encoding="utf-8")
        self.assertEqual(errors, [])
        self.assertEqual({item["label"] for item in rows}, {"one", "two"})
        self.assertEqual({item["label"] for item in rows}, set(markdown.removeprefix(
            "labels: ").strip().split(",")))

    def test_release_history_rolls_back_both_files_when_second_publish_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            history = repository / "benchmarks"
            history.mkdir()
            old = {"history.jsonl": b'{"old": true}\n', "history.md": b"old\n"}
            for name, content in old.items():
                (history / name).write_bytes(content)

            def update(stage):
                (stage / "history.jsonl").write_text('{"new": true}\n', encoding="utf-8")
                (stage / "history.md").write_text("new\n", encoding="utf-8")
                return SimpleNamespace(returncode=0)

            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second-write failure")
                os.replace(source, destination)

            with mock.patch.object(replay, "_replace_history_file", side_effect=fail_second), \
                    self.assertRaisesRegex(replay.ReplayError, "rolled back"):
                replay.release_history_transaction(repository, update)

            restored = {name: (history / name).read_bytes() for name in old}
            transaction_exists = (history / replay.HISTORY_TRANSACTION).exists()
        self.assertEqual(restored, old)
        self.assertFalse(transaction_exists)

    def test_release_history_recovers_a_crash_between_pair_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            history = repository / "benchmarks"
            transaction = history / replay.HISTORY_TRANSACTION
            transaction.mkdir(parents=True)
            old = {"history.jsonl": b'{"old": true}\n', "history.md": b"old\n"}
            for name, content in old.items():
                (transaction / name).write_bytes(content)
            (transaction / "manifest.json").write_text(json.dumps(
                {name: True for name in old}) + "\n", encoding="utf-8")
            (history / "history.jsonl").write_text('{"new": true}\n', encoding="utf-8")
            (history / "history.md").write_bytes(old["history.md"])

            replay.release_history_transaction(
                repository, lambda _stage: SimpleNamespace(returncode=1))

            restored = {name: (history / name).read_bytes() for name in old}
            transaction_exists = transaction.exists()
        self.assertEqual(restored, old)
        self.assertFalse(transaction_exists)

    def test_spend_guard_caps_are_applied_to_each_run_and_the_whole_set(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            caps = []

            def launch(command, **_kwargs):
                caps.append(command[command.index("--spend-cap") + 1])
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [row(ref, "one", arm, 1, True, 1.75)
                                               for arm in replay.ARM_NAMES],
                                    preflight=1.0)
                return SimpleNamespace(returncode=0)

            replay.execute(parsed, REPO, output, launch)
        self.assertEqual(caps, ["20", "15.5"])

    def test_unreadable_cost_consumes_the_per_run_cap_before_target_two(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        with tempfile.TemporaryDirectory() as temporary:
            caps = []

            def launch(command, **_kwargs):
                caps.append(command[command.index("--spend-cap") + 1])
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [row(ref, "one", arm, 1, False, None)
                                               for arm in replay.ARM_NAMES],
                                    preflight=0.5)
                return SimpleNamespace(returncode=0)

            summary = replay.execute(parsed, REPO, Path(temporary) / "out", launch)
        self.assertEqual(caps, ["20", "15.5"])
        self.assertEqual(summary["spend_usd"], 9.0)
        self.assertEqual(summary["reported_spend_usd"], 0.0)
        self.assertEqual([item["spend_usd"] for item in summary["cases"]
                          if item["status"] == "completed"], [0.5, 2.0, 2.0,
                                                               0.5, 2.0, 2.0])

    def test_authoritative_spend_sidecar_is_required_and_reconciled_with_rows(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                replay.ReplayExecutionError, "sidecar is missing") as caught:
            def missing_sidecar(command, **_kwargs):
                ref = command[command.index("--tag") + 1]
                target_output = Path(command[command.index("--out") + 1]) / ref
                target_output.mkdir(parents=True)
                (target_output / replay.RESULTS_NAME).write_text(
                    "".join(json.dumps(row(ref, "one", arm, 1, True, 1.0)) + "\n"
                            for arm in replay.ARM_NAMES))
                return SimpleNamespace(returncode=0)

            replay.execute(parsed, REPO, Path(temporary) / "out", missing_sidecar)
        self.assertEqual(caught.exception.summary["spend_usd"], 20.0)
        self.assertEqual(caught.exception.summary["cases"][0]["spend_usd"], 20.0)

        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                replay.ReplayError, "does not match its native rows"):
            def mismatched_sidecar(command, **_kwargs):
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [row(ref, "one", arm, 1, True, 1.0)
                                               for arm in replay.ARM_NAMES])
                spend_path = (Path(command[command.index("--out") + 1]) / ref
                              / replay.SPEND_NAME)
                spend = json.loads(spend_path.read_text())
                spend["scored_spend_usd"] = 0.5
                spend["charged_spend_usd"] = 0.5
                spend_path.write_text(json.dumps(spend) + "\n")
                spend_path.chmod(0o600)
                return SimpleNamespace(returncode=0)

            replay.execute(parsed, REPO, Path(temporary) / "out", mismatched_sidecar)

    def test_failed_native_target_keeps_authoritative_spend_for_the_studio_protocol(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        for outcome in (SimpleNamespace(returncode=2), RuntimeError("native launch failed")):
            with self.subTest(outcome=type(outcome).__name__), tempfile.TemporaryDirectory() as temporary:
                def launch(command, **_kwargs):
                    write_native_result(command, [], preflight=0.3)
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome

                with self.assertRaises(replay.ReplayExecutionError) as caught:
                    replay.execute(parsed, REPO, Path(temporary) / "out", launch)
                self.assertEqual(caught.exception.summary["spend_usd"], 0.3)
                completed = [item for item in caught.exception.summary["cases"]
                             if item["status"] == "completed"]
                self.assertEqual([item["id"] for item in completed], ["target-1-preflight"])

    def test_runner_publishes_paid_spend_before_returning_a_native_failure(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        summary = {
            "spend_usd": 0.3, "stopped_at_cap": False,
            "cases": [{"id": "target-1-preflight", "status": "completed", "spend_usd": 0.3}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "spend-result.json"
            environment = {"CITIZEN_STUDIO_RESULT": str(result),
                           "CITIZEN_STUDIO_RUN_ID": "run-failed"}
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(replay_runner.replay, "execute", side_effect=
                                      replay.ReplayExecutionError("native failed", summary)):
                code = replay_runner.main([
                    "--request-json", json.dumps(parsed.as_dict()), "--out", temporary,
                    "--max-budget-usd", "2", "--spend-cap", "20",
                ])
            descriptor = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            try:
                recorded = spend_guard.read_result(
                    descriptor, "run-failed", replay.case_identities(parsed))
            finally:
                os.close(descriptor)
        self.assertEqual(code, 2)
        self.assertEqual((recorded["stop_reason"], recorded["spend_usd"]),
                         ("runner_failure", 0.3))
        self.assertEqual(recorded["cases"][0]["status"], "completed")
        self.assertTrue(all(item["status"] == "not_run" for item in recorded["cases"][1:]))

    def test_preflight_only_cap_stop_is_accounted_without_admitting_target_two(self):
        parsed = replay.ReplayRequest.parse(request(spend_cap_usd="2.5"))
        with tempfile.TemporaryDirectory() as temporary:
            calls = []

            def launch(command, **_kwargs):
                calls.append(command)
                write_native_result(command, [], preflight=1.0, stopped=True)
                return SimpleNamespace(returncode=1)

            summary = replay.execute(parsed, REPO, Path(temporary) / "out", launch)

        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["spend_usd"], 1.0)
        self.assertTrue(summary["stopped_at_cap"])
        self.assertEqual(summary["result_files"], [])
        completed = [item for item in summary["cases"] if item["status"] == "completed"]
        self.assertEqual(completed, [{
            "id": "target-1-preflight", "target": request()["targets"][0],
            "status": "completed", "spend_usd": 1.0,
        }])

    def test_target_two_receives_only_the_remaining_cap_and_can_stop_before_preflight(self):
        parsed = replay.ReplayRequest.parse(request(
            tasks=["one"], repetitions=1, max_budget_usd="0.5", spend_cap_usd="1.2"))
        with tempfile.TemporaryDirectory() as temporary:
            caps = []

            def launch(command, **_kwargs):
                cap = command[command.index("--spend-cap") + 1]
                caps.append(cap)
                ref = command[command.index("--tag") + 1]
                if len(caps) == 1:
                    write_native_result(command, [row(ref, "one", arm, 1, True, 0.25)
                                                   for arm in replay.ARM_NAMES], preflight=0.5)
                    return SimpleNamespace(returncode=0)
                write_native_result(command, [], stopped=True)
                return SimpleNamespace(returncode=1)

            summary = replay.execute(parsed, REPO, Path(temporary) / "out", launch)

        self.assertEqual(caps, ["1.2", "0.2"])
        self.assertEqual(summary["spend_usd"], 1.0)
        self.assertTrue(summary["stopped_at_cap"])

    def test_authoritative_overshoot_is_recorded_instead_of_clamped_to_remaining_cap(self):
        parsed = replay.ReplayRequest.parse(request(
            tasks=["one"], repetitions=1, max_budget_usd="0.5", spend_cap_usd="1.2"))
        with tempfile.TemporaryDirectory() as temporary:
            calls = []

            def launch(command, **_kwargs):
                calls.append(command)
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [row(ref, "one", arm, 1, True, 0.6)
                                               for arm in replay.ARM_NAMES],
                                    preflight=0.1, stopped=True)
                return SimpleNamespace(returncode=1)

            summary = replay.execute(parsed, REPO, Path(temporary) / "out", launch)

        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["spend_usd"], 1.3)
        self.assertEqual(sum(item["spend_usd"] for item in summary["cases"]), 1.3)
        self.assertTrue(summary["stopped_at_cap"])

    def test_per_task_table_reports_cost_per_passed_and_pass_rate_for_every_arm(self):
        parsed = replay.ReplayRequest.parse(request())
        rows = [
            row("v0.17.0", "one", "bare", 1, True, 2.0),
            row("v0.17.0", "one", "bare", 2, False, 1.0),
            row("v0.17.0", "one", "harness", 1, True, 0.5),
            row("v0.17.0", "one", "harness", 2, True, 0.5),
        ]
        table = replay.table_rows([(parsed.targets[0], rows)])
        bare = next(item for item in table if item["arm"] == "bare")
        harness = next(item for item in table if item["arm"] == "harness")
        self.assertEqual((bare["pass_rate"], bare["cost_per_passed"]), (0.5, 3.0))
        self.assertEqual((harness["pass_rate"], harness["cost_per_passed"]), (1.0, 0.5))

    def test_rows_from_a_different_resolved_revision_are_refused(self):
        parsed = replay.ReplayRequest.parse(request())
        with self.assertRaisesRegex(replay.ReplayError, "resolved target revision"):
            replay._verify_target_rows(parsed.targets[0], [dict(
                row("v0.17.0", "one", "bare", 1, True, 1.0), harness_sha="f" * 40)])

    def test_native_rows_must_match_the_exact_requested_task_rep_arm_matrix(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=2))
        exact = [row("v0.17.0", "one", arm, repetition, True, 1.0)
                 for repetition in (1, 2) for arm in replay.ARM_NAMES]
        replay._reconcile_target_rows(parsed, exact, complete=True)
        failures = [
            exact + [exact[0]],
            exact + [dict(exact[0], task="unknown")],
            exact[:-1],
            exact + [dict(exact[0], rep=3)],
        ]
        for rows in failures:
            with self.subTest(rows=rows), self.assertRaises(replay.ReplayError):
                replay._reconcile_target_rows(parsed, rows, complete=True)
        replay._reconcile_target_rows(parsed, exact[:-1], complete=False)

    def test_a_zero_exit_without_native_rows_is_not_reported_as_an_empty_success(self):
        parsed = replay.ReplayRequest.parse(request())
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                replay.ReplayError, "without every requested"):
            replay.execute(parsed, REPO, Path(temporary) / "out",
                           lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    def test_preserved_native_rows_are_indexed_by_the_rebuildable_run_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            results = root / "results.jsonl"
            results.write_text(json.dumps(row("v0.17.0", "one", "bare", 1, True, 1.0)) + "\n")
            store = run_store.RunStore(root / "state")
            count = replay.index_native_rows(store, root, {"result_files": [str(results)]})
            indexed = store.list()
        self.assertEqual(count, 1)
        self.assertEqual(indexed[0]["source"]["kind"], "benchmark-result")
        self.assertEqual(indexed[0]["suite"]["id"], "cost-benchmark")

    def test_runner_emits_the_spend_result_protocol_for_selected_target_tasks(self):
        parsed = replay.ReplayRequest.parse(request())
        summary = {"spend_usd": 2.5, "stopped_at_cap": False, "cases": [
            {"id": "target-1-one-1-bare", "status": "completed", "spend_usd": 1.0},
            {"id": "target-1-one-1-harness", "status": "not_run", "spend_usd": 0.0},
            {"id": "target-2-one-1-bare", "status": "completed", "spend_usd": 1.5},
            {"id": "target-2-one-1-harness", "status": "not_run", "spend_usd": 0.0},
        ]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            replay_runner.write_spend_result(path, "run-id", summary)
            value = json.loads(path.read_text())
        self.assertEqual(value["run_id"], "run-id")
        self.assertEqual(value["spend_usd"], 2.5)
        self.assertEqual([item["status"] for item in value["cases"]],
                         ["completed", "not_run", "completed", "not_run"])

    def test_prepared_catalog_entry_is_standard_and_matches_the_fragment(self):
        fragment = json.loads((REPO / "policy" / "studio" / "replay-suite.json").read_text())
        self.assertEqual(fragment["suites"], [replay.catalog_entry()])
        catalog = runs.SuiteCatalog.load(REPO / "policy" / "studio" / "replay-suite.json")
        self.assertEqual(catalog.get("live-replay").cost_class, "spends_usage")

    def test_replay_admission_resolves_both_targets_and_binds_dynamic_paid_cases(self):
        class Supervisor:
            def spend_preview(self, *args, **kwargs):
                self.preview = (args, kwargs)
                cases = kwargs["case_identities"]
                return {
                    "estimate": {"amount_usd": None, "basis": "no_history",
                                 "sample_count": 0, "suite_id": "live-replay",
                                 "case_count": len(cases)},
                    "caps": {"max_budget_usd": "2", "spend_cap_usd": "20"},
                    "pricing": {"source": "api_credit", "basis": "money charged"},
                    "confirmation_required": True, "confirmation_token": "f" * 64,
                    "cost_class": "spends_usage", "case_identities": cases,
                }

            def start(self, *args, **kwargs):
                self.started = (args, kwargs)
                return {"run_id": "replay-run", "status": "queued"}

        unresolved = request(targets=[{"kind": "release", "ref": "v0.17.0"},
                                      {"kind": "draft", "ref": "cost-pass"}],
                             tasks=["link-alias"], repetitions=1)
        with tempfile.TemporaryDirectory() as temporary, fixture_tasks("link-alias"):
            supervisor = Supervisor()
            admission = replay.ReplayAdmission(
                REPO, Path(temporary), supervisor, FixtureTargetService())
            preview = admission.preview(unresolved)
            started = admission.start(preview["request"], preview["confirmation_token"])
        self.assertEqual(len(preview["request"]["targets"]), 2)
        self.assertRegex(preview["request"]["targets"][0]["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(supervisor.preview[1]["case_identities"],
                         replay.case_identities(replay.ReplayRequest.parse(preview["request"])))
        self.assertEqual(supervisor.started[1]["case_identities"],
                         supervisor.preview[1]["case_identities"])
        self.assertEqual(started["run_id"], "replay-run")
        server.REPLAY_PREVIEW.validate(preview)
        server.REPLAY_RUN.validate(started)

    def test_replay_routes_are_registered_and_catalog_exposes_benchmark_tasks(self):
        routes = {(item.method, item.path) for item in server.ROUTES.entries}
        self.assertTrue({
            ("GET", "/api/runs/replay/catalog"),
            ("POST", "/api/runs/replay/preview"),
            ("POST", "/api/runs/replay/start"),
            ("POST", "/api/runs/replay/result"),
        }.issubset(routes))
        with fixture_tasks("link-alias") as root:
            catalog = replay.task_catalog(root)
        self.assertEqual(catalog["tasks"], [{"id": "link-alias", "label": "Link Alias", "long": False}])
        server.REPLAY_CATALOG.validate(catalog)
        self.assertIn("release", catalog["target_kinds"])

    def test_unknown_benchmark_task_is_refused_before_spend_preview(self):
        supervisor = mock.Mock()
        unresolved = request(
            targets=[{"kind": "branch", "ref": "main"},
                     {"kind": "draft", "ref": "cost-pass"}],
            tasks=["not-in-the-catalog"], pre_registration="")
        with tempfile.TemporaryDirectory() as temporary:
            admission = replay.ReplayAdmission(
                REPO, Path(temporary), supervisor, FixtureTargetService())
            with self.assertRaisesRegex(replay.ReplayError, "unknown benchmark tasks"):
                admission.preview(unresolved)
        supervisor.spend_preview.assert_not_called()

    def test_result_route_streams_task_arm_progress_and_indexes_inside_mutation_owner(self):
        selected = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(os.path.realpath(temporary)) / "run"
            progress_file = (run_root / "replay" / "target-1"
                             / selected.targets[0].execution_ref / replay.RESULTS_NAME)
            progress_file.parent.mkdir(parents=True)
            progress_file.write_text(json.dumps(row(
                selected.targets[0].execution_ref, "one", "bare", 1, True, 0.25)) + "\n")

            class Mutations:
                active = False

                def call(self, callback):
                    self.active = True
                    try:
                        return callback()
                    finally:
                        self.active = False

            mutations = Mutations()

            class History:
                indexed = 0

                def upsert(self, _value):
                    self.assert_owner()
                    self.indexed += 1

                @staticmethod
                def assert_owner():
                    if not mutations.active:
                        raise AssertionError("SQLite indexing escaped MutationExecutor")

            class Supervisor:
                status = "running"
                history = History()

                def show(self, run_id):
                    return {"run_id": run_id, "suite_id": "live-replay",
                            "status": self.status}

                @staticmethod
                def lock():
                    return contextlib.nullcontext()

                @staticmethod
                def _read(_run_id):
                    return {"parameters": {
                        "request_json": json.dumps(selected.as_dict(), sort_keys=True)}}

                @staticmethod
                def _run_path(_run_id):
                    return run_root / "run.json"

            class Handler:
                request_json = {"run_id": "replay-run"}
                response = None
                error = None

                def __init__(self):
                    self.server = mock.Mock(run_supervisor=Supervisor(), mutations=mutations)

                def _json(self, code, payload):
                    self.response = (code, payload)

                def _error(self, code, name):
                    self.error = (code, name)

            handler = Handler()
            route = server.Route("POST", "/result", "application/json", server.REPLAY_RESULT,
                                 server._replay_result, None, "application/json", ("replay",))
            server._replay_result(handler, route)
            completed = [item for item in handler.response[1]["progress"]
                         if item["status"] == "completed"]
            self.assertEqual([(item["task"], item["arm"]) for item in completed],
                             [("one", "bare")])

            shutil.rmtree(run_root / "replay")

            def launch(command, **_kwargs):
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [row(ref, "one", arm, 1, True, 0.25)
                                               for arm in replay.ARM_NAMES])
                return SimpleNamespace(returncode=0)

            replay.execute(selected, REPO, run_root / "replay", launch)
            handler.server.run_supervisor.status = "succeeded"
            server._replay_result(handler, route)
            self.assertIsNotNone(handler.response[1]["result"])
            self.assertEqual(handler.server.run_supervisor.history.indexed, 4)

    def test_real_supervisor_confirmation_binds_the_dynamic_replay_case_matrix(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        launch = replay.launch_payload(parsed, "unused", REPO)
        cases = replay.case_identities(parsed)
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = runs.RunSupervisor(
                Path(os.path.realpath(temporary)),
                REPO / "policy" / "studio" / "replay-suite.json",
                target_service=FixtureTargetService())
            try:
                preview = supervisor.spend_preview(
                    "live-replay", launch["parameters"], launch["target_kind"],
                    launch["target_ref"], parsed.max_budget_usd, parsed.spend_cap_usd,
                    "api_credit", case_identities=cases)
                with mock.patch.object(supervisor, "_admit_locked"):
                    started = supervisor.start(
                        "live-replay", launch["parameters"], launch["target_kind"],
                        launch["target_ref"], confirmed=preview["confirmation_token"],
                        max_budget_usd=parsed.max_budget_usd,
                        spend_cap_usd=parsed.spend_cap_usd, pricing_source="api_credit",
                        case_identities=cases)
                record = supervisor._read(started["run_id"])
            finally:
                supervisor.close()
        self.assertEqual(record["case_identities"], cases)



def load_cost_bench():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cost_bench_for_replay_tests", REPO / "scripts" / "cost_bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SnapshotTargetService(FixtureTargetService):
    def build(self, kind, ref, destination):
        return dict(super().build(kind, ref, destination), snapshot=kind == "worktree")


class RecordingMutations:
    """A mutation owner that records whether work ran inside it."""
    active = False

    def call(self, callback):
        self.active = True
        try:
            return callback()
        finally:
            self.active = False


class ReplayReviewFixTests(unittest.TestCase):
    def test_any_configuration_digest_parses_because_the_replay_measures_source_only(self):
        parsed = replay.ReplayRequest.parse(request(targets=[
            target("release", "v0.17.0", "a" * 40),
            target("draft", "cost-pass", "a" * 40, "f" * 64)]))
        self.assertEqual(parsed.targets[1].config_digest, "f" * 64)

    def test_a_dirty_worktree_target_is_refused_before_spend_preview(self):
        supervisor = mock.Mock()
        with tempfile.TemporaryDirectory() as temporary:
            admission = replay.ReplayAdmission(
                REPO, Path(temporary), supervisor, SnapshotTargetService())
            with self.assertRaises(replay.ReplayRefusal) as caught:
                admission.preview(request(targets=[{"kind": "branch", "ref": "main"},
                                                   {"kind": "worktree", "ref": "/tmp/wt"}],
                                          tasks=["link-alias"], pre_registration=""))
        self.assertEqual(caught.exception.code, "replay_worktree_dirty")
        supervisor.spend_preview.assert_not_called()

    def test_a_refusal_before_the_native_output_exists_charges_nothing(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        for outcome in (SimpleNamespace(returncode=2), SimpleNamespace(returncode=1),
                        RuntimeError("arm build failed")):
            with self.subTest(outcome=repr(outcome)), tempfile.TemporaryDirectory() as temporary:
                def launch(_command, **_kwargs):
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome

                with self.assertRaisesRegex(replay.ReplayExecutionError,
                                            "before any spend") as caught:
                    replay.execute(parsed, REPO, Path(temporary) / "out", launch)
                summary = caught.exception.summary
                self.assertEqual(summary["spend_usd"], 0.0)
                self.assertFalse(summary["stopped_at_cap"])
                self.assertTrue(all(item["spend_usd"] == 0.0 for item in summary["cases"]))
                path = Path(temporary) / "result.json"
                replay_runner.write_spend_result(path, "run-id", summary, "runner_failure")
                self.assertEqual(json.loads(path.read_text())["spend_usd"], 0.0)

    def test_independently_rounded_sidecar_and_row_sums_reconcile_within_one_unit(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        preflight, costs = 0.3932551, {"bare": 0.4896935, "harness": 0.029575}

        def launch(command, **_kwargs):
            ref = command[command.index("--tag") + 1]
            native = Path(command[command.index("--out") + 1]) / ref
            native.mkdir(parents=True)
            (native / replay.RESULTS_NAME).write_text("".join(
                json.dumps(row(ref, "one", arm, 1, True, costs[arm])) + "\n"
                for arm in replay.ARM_NAMES))
            spent = 0.0 + preflight  # cost_bench's own float accumulation
            for arm in replay.ARM_NAMES:
                spent += costs[arm]
            spend = native / replay.SPEND_NAME
            spend.write_text(json.dumps({
                "schema_version": 1, "tag": ref,
                "run_cap_usd": float(command[command.index("--run-cap") + 1]),
                "spend_cap_usd": float(command[command.index("--spend-cap") + 1]),
                "preflight_spend_usd": round(preflight, 6),
                "scored_spend_usd": round(spent - preflight, 6),
                "charged_spend_usd": round(spent, 6), "stopped_at_cap": False}) + "\n")
            spend.chmod(0o600)
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temporary:
            summary = replay.execute(parsed, REPO, Path(temporary) / "out", launch)
        self.assertAlmostEqual(summary["spend_usd"], 2 * 0.912524, places=5)

    def test_release_history_is_published_only_after_native_output_verifies(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "docs").mkdir()
            (repository / "docs" / "pre-registration-template.md").write_text("plan\n")
            history = repository / "benchmarks"
            history.mkdir()
            (history / "history.jsonl").write_text("")
            (history / "history.md").write_text("before\n")
            stale = history / (replay.HISTORY_STAGE_PREFIX + "killed")
            stale.mkdir()

            def launch(command, **_kwargs):
                if "--history-dir" in command:
                    stage = Path(command[command.index("--history-dir") + 1])
                    (stage / "history.jsonl").write_text(json.dumps({"row": 1}) + "\n")
                    (stage / "history.md").write_text("after\n")
                ref = command[command.index("--tag") + 1]
                # Rows claim another revision, so verification must refuse them.
                write_native_result(command, [dict(row(ref, "one", arm, 1, True, 0.5),
                                                   harness_sha="f" * 40)
                                              for arm in replay.ARM_NAMES])
                return SimpleNamespace(returncode=0)

            with self.assertRaisesRegex(replay.ReplayExecutionError, "resolved target revision"):
                replay.execute(parsed, repository, repository / "out", launch)
            self.assertEqual((history / "history.md").read_text(), "before\n")
            self.assertEqual((history / "history.jsonl").read_text(), "")
            self.assertFalse(stale.exists())

    def test_errored_native_rows_are_reported_as_errored_progress(self):
        parsed = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))
        errored = dict(row("a" * 40, "one", "bare", 1, None, None), error="timeout")
        progress = replay.progress_payload(parsed, {1: [errored]})
        bare = next(item for item in progress if item["arm"] == "bare"
                    and item["target"]["ref"] == "v0.17.0")
        self.assertEqual((bare["status"], bare["passed"]), ("errored", None))

    def test_indexed_rows_are_unique_per_run_and_rebuilt_from_the_run_folder(self):
        selected = replay.ReplayRequest.parse(request(tasks=["one"], repetitions=1))

        def launch(command, **_kwargs):
            ref = command[command.index("--tag") + 1]
            write_native_result(command, [row(ref, "one", arm, 1, True, 0.25)
                                           for arm in replay.ARM_NAMES])
            return SimpleNamespace(returncode=0)

        launched = replay.launch_payload(selected, "unused", REPO)
        cases = replay.case_identities(selected)
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = runs.RunSupervisor(
                Path(os.path.realpath(temporary)),
                REPO / "policy" / "studio" / "replay-suite.json",
                target_service=FixtureTargetService())
            try:
                run_ids = []
                for _ in range(2):
                    preview = supervisor.spend_preview(
                        "live-replay", launched["parameters"], launched["target_kind"],
                        launched["target_ref"], selected.max_budget_usd,
                        selected.spend_cap_usd, "api_credit", case_identities=cases)
                    with mock.patch.object(supervisor, "_admit_locked"):
                        started = supervisor.start(
                            "live-replay", launched["parameters"], launched["target_kind"],
                            launched["target_ref"], confirmed=preview["confirmation_token"],
                            max_budget_usd=selected.max_budget_usd,
                            spend_cap_usd=selected.spend_cap_usd,
                            pricing_source="api_credit", case_identities=cases)
                    run_ids.append(started["run_id"])
                    run_root = supervisor._run_path(started["run_id"]).parent
                    summary = replay.execute(selected, REPO, run_root / "replay", launch)
                    replay.index_native_rows(supervisor.history, run_root, summary)

                def replay_rows():
                    return sorted(run_id for (run_id,) in supervisor.history.connection.execute(
                        "SELECT run_id FROM runs WHERE source_kind='benchmark-result' "
                        "AND source_path LIKE 'runs/%'"))

                indexed = replay_rows()
                self.assertEqual(len(indexed), 8)
                self.assertEqual(len(set(indexed)), 8)
                with supervisor.history.connection:
                    supervisor.history.connection.execute("DELETE FROM runs")
                supervisor.reindex(REPO)
                self.assertEqual(replay_rows(), indexed)
            finally:
                supervisor.close()

    def test_preview_and_start_build_targets_outside_the_mutation_owner(self):
        mutations = RecordingMutations()
        builds = []

        class Service(FixtureTargetService):
            def build(self, kind, ref, destination):
                builds.append(mutations.active)
                return super().build(kind, ref, destination)

        class Supervisor:
            def spend_preview(self, *_args, **kwargs):
                assert mutations.active
                cases = kwargs["case_identities"]
                return {"estimate": {"amount_usd": None, "basis": "no_history",
                                     "sample_count": 0, "suite_id": "live-replay",
                                     "case_count": len(cases)},
                        "caps": {"max_budget_usd": "2", "spend_cap_usd": "20"},
                        "pricing": {"source": "api_credit", "basis": "money charged"},
                        "confirmation_required": True, "confirmation_token": "f" * 64,
                        "cost_class": "spends_usage", "case_identities": cases}

            def start(self, *_args, **_kwargs):
                assert mutations.active
                return {"run_id": "replay-run", "status": "queued"}

        with tempfile.TemporaryDirectory() as temporary, fixture_tasks("link-alias"):
            class Handler:
                response = None
                error = None

                def __init__(self, body):
                    self.request_json = body
                    self.server = SimpleNamespace(
                        repo_root=REPO, store=SimpleNamespace(path=Path(temporary)),
                        run_supervisor=Supervisor(), target_service=Service(),
                        mutations=mutations)

                def _json(self, code, payload):
                    self.response = (code, payload)

                def _error(self, code, name):
                    self.error = (code, name)

            unresolved = request(targets=[{"kind": "release", "ref": "v0.17.0"},
                                          {"kind": "draft", "ref": "cost-pass"}],
                                 tasks=["link-alias"], repetitions=1)
            previewed = Handler({"request": unresolved})
            server._replay_preview(previewed, server.Route(
                "POST", "/p", "application/json", server.REPLAY_PREVIEW,
                server._replay_preview, None, "application/json", ("p",)))
            self.assertIsNone(previewed.error)
            started = Handler({"request": previewed.response[1]["request"],
                               "confirmation_token": "f" * 64})
            server._replay_start(started, server.Route(
                "POST", "/s", "application/json", server.REPLAY_RUN,
                server._replay_start, None, "application/json", ("s",)))
            self.assertIsNone(started.error)
        self.assertEqual(builds, [False] * 4)

    def test_refusal_codes_reach_the_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = SimpleNamespace(
                request_json={"request": request(
                    targets=[{"kind": "branch", "ref": "main"},
                             {"kind": "worktree", "ref": "/tmp/wt"}],
                    tasks=["link-alias"], pre_registration="")},
                server=SimpleNamespace(
                    repo_root=REPO, store=SimpleNamespace(path=Path(temporary)),
                    run_supervisor=mock.Mock(), target_service=SnapshotTargetService(),
                    mutations=RecordingMutations()),
                _json=mock.Mock(), _error=mock.Mock())
            server._replay_preview(handler, None)
        handler._error.assert_called_once_with(400, "replay_worktree_dirty")

    def test_advertised_commands_exist_and_parse_as_the_native_replay(self):
        route = next(item for item in server.ROUTES.entries
                     if item.path == "/api/runs/replay/catalog")
        self.assertEqual(route.cli_command, ("citizen", "runs", "replay", "catalog", "--json"))
        self.assertTrue((REPO / replay.NATIVE_COMMAND[1]).is_file())
        # Real discovery, pointed at a fixture packs folder rather than beside this checkout.
        with tempfile.TemporaryDirectory() as folder:
            make_pack(Path(folder) / "fixture-pack")
            with mock.patch.dict(os.environ, {replay.packs.PACKS_ENV: folder}):
                catalog = replay.task_catalog(REPO)
        self.assertEqual(catalog["commands"]["run"], "python3 scripts/cost_bench.py replay")
        self.assertEqual([item["name"] for item in catalog["packs"]], ["test-pack"])
        parsed = replay.ReplayRequest.parse(dict(request(), evidence="pre-registered"))
        text = replay.preview_payload([], parsed)["command"]
        self.assertNotIn("citizen", text)
        bench = load_cost_bench()
        seen = []
        with mock.patch.object(bench, "cmd_replay", lambda args: seen.append(args) or 0):
            for line in text.splitlines():
                argv = shlex.split(line)
                self.assertEqual(argv[:3], ["python3", "scripts/cost_bench.py", "replay"])
                self.assertEqual(bench.main(argv[2:]), 0)
        self.assertEqual([args.tag for args in seen], [["a" * 40], ["b" * 40]])
        self.assertEqual([args.exploratory for args in seen], [False, True])


if __name__ == "__main__":
    unittest.main()
