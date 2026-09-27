"""The Studio run index is rebuildable from authenticated, bounded source files."""
import json
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import run_store, runs


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.state = self.root / "state"
        self.catalog = self.root / "suites.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1,
            "argv": [sys.executable, "-c", "print('done')"], "parameters": {},
            "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["installed"],
        }]}))

    def supervisor(self, state=None):
        return runs.RunSupervisor(state or self.state, self.catalog)

    def queued_run(self, supervisor):
        with mock.patch.object(supervisor, "_admit_locked"):
            return supervisor.start("fixture", {}, "installed", "current")

    def forge_chain(self, run_dir, records, terminal_override=object(), write_metadata=True):
        creation = run_store._immutable_digest(records[0])
        rows = []
        previous = "0" * 64
        for sequence, record in enumerate(records, 1):
            record_digest = run_store._digest(record)
            terminal = (run_store._terminal_digest(sequence, previous, creation, record_digest)
                        if run_store._is_final(record) else None)
            if sequence == len(records) and terminal_override.__class__ is not object:
                terminal = terminal_override
            unsigned = {
                "schema_version": run_store.SIDECAR_SCHEMA_VERSION,
                "sequence": sequence,
                "event_type": "creation" if sequence == 1 else "transition",
                "previous_hash": previous,
                "creation_digest": creation,
                "record_digest": record_digest,
                "terminal_digest": terminal,
                "record": record,
            }
            envelope = dict(unsigned, hash=run_store._digest(unsigned))
            rows.append(envelope)
            previous = envelope["hash"]
        sidecar = run_dir / run_store.SIDECAR_NAME
        sidecar.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        sidecar.chmod(0o600)
        if write_metadata:
            metadata = run_store._history_metadata(rows)
            (run_dir / run_store.SIDECAR_META_NAME).write_text(json.dumps(metadata) + "\n")
            (run_dir / run_store.SIDECAR_META_NAME).chmod(0o600)
        return rows

    def write_sources(self):
        benchmark = self.repository / "benchmarks"
        results = benchmark / "v0.1"
        evidence = self.repository / "compatibility" / "evidence"
        results.mkdir(parents=True)
        evidence.mkdir(parents=True)
        (benchmark / "static.json").write_text(json.dumps({
            "schema_version": 2, "harness_version": "0.1.0",
            "total": {"files": 1, "lines": 2, "chars": 400, "est_tokens": 100},
            "usd": {"model": {"session_start": .01, "later_turn": .002}},
            "files": {},
        }))
        (benchmark / "history.jsonl").write_text(json.dumps({
            "date": "2026-01-01", "series": "fixture", "harness_version": "0.1.0",
            "harness_sha": "a" * 40, "tag": "v0.1.0", "model": "model",
            "status": "passed", "reps": 2,
            "bare": {"runs": 2, "errors": 0, "passed": 1, "cost_per_passed": .5},
            "harness": {"runs": 2, "errors": 0, "passed": 1, "cost_per_passed": .25},
            "ratio": .5, "ratio_cache_normalised": .4, "per_task": {},
        }) + "\n")
        (results / "results.jsonl").write_text(json.dumps({
            "task": "case", "arm": "harness", "rep": 1, "harness_sha": "a" * 40,
            "tag": "v0.1.0", "model": "model", "cli_version": "1.0", "passed": True,
            "error": False, "cost_usd": .125, "cost_normalised_usd": .25,
            "input_tokens": 10, "output_tokens": 4,
        }) + "\n")
        (evidence / "client.json").write_text(json.dumps({
            "kind": "native", "client": "fixture-client", "client_version": "1.0",
            "harness_version": "0.1.0", "source_commit": "a" * 40,
            "model_run": "model", "cases": {"install": "passed"},
        }))

    def test_studio_sidecar_rebuild_restores_the_same_full_record(self):
        self.write_sources()
        supervisor = self.supervisor()
        with mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("fixture", {}, "installed", "current")
        with supervisor.lock():
            record = supervisor._read(created["run_id"])
            record["status"] = "cancelled"
            record["completed_at"] = runs.utc_now()
            supervisor._write(record)
        supervisor.reindex(self.repository)
        indexed_records = {row["run_id"]: row for row in supervisor.history.list(limit=20)}
        indexed = indexed_records[created["run_id"]]
        self.assertEqual(indexed["raw"]["argv"][0], sys.executable)
        self.assertEqual(indexed["source"]["kind"], "studio")
        supervisor.history.close()
        (self.state / run_store.DATABASE_NAME).unlink()
        supervisor.history = run_store.RunStore(self.state)
        report = supervisor.reindex(self.repository)
        rebuilt_records = {row["run_id"]: row for row in supervisor.history.list(limit=20)}
        self.assertEqual(report, {"studio_runs": 1, "imported_runs": 4,
                                  "total_runs": 5, "skipped": []})
        self.assertEqual(rebuilt_records, indexed_records)
        run_dir = self.state / "runs" / created["run_id"]
        rows = [json.loads(line) for line in (run_dir / run_store.SIDECAR_NAME).read_text().splitlines()]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[-1]["previous_hash"], rows[-2]["hash"])

    def test_reindex_bootstraps_a_pre_sidecar_supervisor_record(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        run_dir = self.state / "runs" / created["run_id"]
        (run_dir / run_store.SIDECAR_NAME).unlink()
        (run_dir / run_store.SIDECAR_META_NAME).unlink()
        (self.state / run_store.AUTHORITY_NAME).unlink()
        (self.state / run_store.AUTHORITY_HEAD_NAME).unlink()
        report = supervisor.reindex(self.repository)
        self.assertEqual(report["studio_runs"], 1)
        self.assertTrue((run_dir / run_store.SIDECAR_NAME).is_file())
        self.assertEqual(supervisor.history.get(created["run_id"])["run_id"], created["run_id"])

    def test_studio_creation_records_and_freezes_complete_ad30_identity(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        run_id = created["run_id"]
        record = supervisor._read(run_id)
        self.assertEqual(record["target"], {
            "kind": "installed", "ref": "current", "revision": None,
            "draft": None, "config_digest": None,
        })
        self.assertIsNone(record["case_identities"])
        self.assertIsNone(record["spend_estimate"])
        self.assertIsNone(record["spend_cap"])
        self.assertIsNone(record["pricing_identity"])
        self.assertEqual(record["canonical_run_digest"],
                         run_store._canonical_run_digest(record))
        record["case_identities"] = ["forged-case"]
        record["canonical_run_digest"] = run_store._canonical_run_digest(record)
        with self.assertRaisesRegex(runs.RunError, "immutable creation fields changed"):
            supervisor._write(record)

    def test_studio_revision_survives_append_index_rebuild_and_json_output(self):
        supervisor = self.supervisor()
        run_id = str(uuid.uuid4())
        revision = "a1b2c3d4e5f6"
        record = {
            "schema_version": runs.SCHEMA_VERSION,
            "run_id": run_id,
            "suite_id": "fixture",
            "suite_version": 1,
            "parameters": {},
            "target": {"kind": "branch", "ref": "feature/revision", "revision": revision,
                       "draft": None, "config_digest": None},
            "argv": [sys.executable, "-c", "print('done')"],
            "cost_class": "free",
            "expected_duration_seconds": 1,
            "timeout_seconds": 10,
            "case_identities": None,
            "spend_estimate": None,
            "spend_cap": None,
            "pricing_identity": None,
            "status": "queued",
            "queue_sequence": 1,
            "created_at": runs.utc_now(),
        }
        record["canonical_run_digest"] = run_store._canonical_run_digest(record)
        supervisor._write(record)
        indexed = supervisor.history.get(run_id)
        self.assertEqual(indexed["raw"]["target"]["revision"], revision)
        self.assertEqual(indexed["target"]["commit"], revision)
        self.assertEqual(json.loads(json.dumps(indexed))["target"]["commit"], revision)
        supervisor.history.close()
        (self.state / run_store.DATABASE_NAME).unlink()
        supervisor.history = run_store.RunStore(self.state)
        report = supervisor.reindex(self.repository)
        rebuilt = supervisor.history.get(run_id)
        self.assertEqual(report["studio_runs"], 1)
        self.assertEqual(rebuilt, indexed)
        self.assertEqual(json.loads(json.dumps(rebuilt))["target"]["commit"], revision)

    def test_reindex_imports_recognized_sources_and_is_idempotent(self):
        self.write_sources()
        supervisor = self.supervisor()
        first = supervisor.reindex(self.repository)
        records = supervisor.history.list(limit=20)
        second = supervisor.reindex(self.repository)
        again = supervisor.history.list(limit=20)
        self.assertEqual(first["imported_runs"], 4)
        self.assertEqual(first["skipped"], [])
        self.assertEqual(records, again)
        result = next(row for row in records if row["source"]["kind"] == "benchmark-result")
        self.assertEqual(result["cost"], {
            "amount_usd": .125, "cache_normalized_amount_usd": .25,
            "basis": run_store.COST_BASIS,
        })
        self.assertEqual(result["tokens"], {"input_tokens": 10, "output_tokens": 4})
        static = next(row for row in records if row["source"]["kind"] == "benchmark-static")
        self.assertEqual(static["cost"]["basis"], run_store.COST_BASIS)
        history = next(row for row in records if row["source"]["kind"] == "benchmark-history")
        self.assertEqual(history["cost"], {
            "basis": run_store.COST_BASIS,
            "by_arm_usd_per_pass": {"bare": .5, "harness": .25},
            "ratio": .5, "cache_normalized_ratio": .4,
        })
        self.assertEqual(second["total_runs"], 4)

    def test_unknown_source_schema_is_reported_and_never_guessed(self):
        benchmark = self.repository / "benchmarks" / "v-next"
        benchmark.mkdir(parents=True)
        source = benchmark / "results.jsonl"
        source.write_text(json.dumps({
            "schema_version": 99, "task": "case", "arm": "harness", "rep": 1,
            "harness_sha": "a" * 40, "model": "model",
        }) + "\n")
        supervisor = self.supervisor()
        report = supervisor.reindex(self.repository)
        self.assertEqual(report["imported_runs"], 0)
        self.assertEqual(report["skipped"], [{
            "path": "benchmarks/v-next/results.jsonl",
            "reason": "unsupported benchmark result schema version: 99",
        }])
        self.assertEqual(supervisor.history.list(), [])

    def test_corrupt_or_tampered_sidecar_aborts_without_replacing_index(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        before = supervisor.history.get(created["run_id"])
        sidecar = self.state / "runs" / created["run_id"] / run_store.SIDECAR_NAME
        rows = sidecar.read_text().splitlines()
        envelope = json.loads(rows[-1])
        envelope["record"]["status"] = "failed"
        rows[-1] = json.dumps(envelope)
        sidecar.write_text("\n".join(rows) + "\n")
        sidecar.chmod(0o600)
        with self.assertRaisesRegex(runs.RunError, "hash chain is invalid"):
            supervisor.reindex(self.repository)
        self.assertEqual(supervisor.history.get(created["run_id"]), before)

    def test_incomplete_sidecar_is_rejected_instead_of_repaired_or_ignored(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        sidecar = self.state / "runs" / created["run_id"] / run_store.SIDECAR_NAME
        sidecar.write_bytes(sidecar.read_bytes()[:-1])
        with self.assertRaisesRegex(runs.RunError, "incomplete record"):
            supervisor.show(created["run_id"])

    def test_clean_prefix_truncation_conflicts_with_the_persisted_chain_head(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        with supervisor.lock():
            record = supervisor._read(created["run_id"])
            record["status"] = "cancelled"
            record["completed_at"] = runs.utc_now()
            supervisor._write(record)
        run_dir = self.state / "runs" / created["run_id"]
        sidecar = run_dir / run_store.SIDECAR_NAME
        sidecar.write_text(sidecar.read_text().splitlines()[0] + "\n")
        sidecar.chmod(0o600)
        with self.assertRaisesRegex(runs.RunError, "metadata conflicts"):
            supervisor.show(created["run_id"])

    def test_truncating_sidecar_metadata_and_ledger_conflicts_with_external_head(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        with supervisor.lock():
            record = supervisor._read(created["run_id"])
            record["status"] = "cancelled"
            record["completed_at"] = runs.utc_now()
            supervisor._write(record)
        run_dir = self.state / "runs" / created["run_id"]
        rows = [json.loads(line) for line in
                (run_dir / run_store.SIDECAR_NAME).read_text().splitlines()]
        (run_dir / run_store.SIDECAR_NAME).write_text(json.dumps(rows[0]) + "\n")
        (run_dir / run_store.SIDECAR_NAME).chmod(0o600)
        metadata = run_store._history_metadata(rows[:1])
        (run_dir / run_store.SIDECAR_META_NAME).write_text(json.dumps(metadata) + "\n")
        (run_dir / run_store.SIDECAR_META_NAME).chmod(0o600)
        ledger = self.state / run_store.AUTHORITY_NAME
        ledger.write_text(ledger.read_text().splitlines()[0] + "\n")
        ledger.chmod(0o600)
        supervisor.history.close()
        (self.state / run_store.DATABASE_NAME).unlink()
        supervisor.history = run_store.RunStore(self.state)
        with self.assertRaisesRegex(runs.RunError, "authority head conflicts"):
            supervisor.reindex(self.repository)

    def test_durable_append_intent_recovers_at_every_fsync_boundary(self):
        for failure_at in range(1, 12):
            with self.subTest(failure_at=failure_at):
                state = self.root / ("fault-" + str(failure_at))
                supervisor = self.supervisor(state)
                created = self.queued_run(supervisor)
                record = supervisor._read(created["run_id"])
                record["status"] = "cancelled"
                record["completed_at"] = runs.utc_now()
                calls = 0

                def fail_boundary(descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == failure_at:
                        raise OSError("injected fsync failure")
                    os.fsync(descriptor)

                with mock.patch.object(run_store, "_sync", side_effect=fail_boundary):
                    with self.assertRaises(runs.RunError):
                        supervisor._write(record)
                recovered = supervisor._read(created["run_id"])
                self.assertIn(recovered["status"], {"queued", "cancelled"})
                self.assertFalse((state / run_store.APPEND_INTENT_NAME).exists())
                run_dir = state / "runs" / created["run_id"]
                descriptor = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    verified, _ = supervisor.history.read_studio(descriptor, created["run_id"])
                finally:
                    os.close(descriptor)
                self.assertEqual(verified["status"], recovered["status"])

    def test_worker_store_construction_waits_for_parent_append_intent(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        record = supervisor._read(created["run_id"])
        record["status"] = "cancelled"
        record["completed_at"] = runs.utc_now()
        original = run_store._apply_append_intent
        marker = self.root / "worker-opened"
        code = (
            "import sys; from pathlib import Path; "
            "from harness_core.studio.run_store import RunStore; "
            "store=RunStore(Path(sys.argv[1])); Path(sys.argv[2]).write_text('opened'); store.close()"
        )
        workers = []

        def paused_apply(state_fd, intent):
            worker = subprocess.Popen(
                [sys.executable, "-c", code, str(self.state), str(marker)],
                env=dict(os.environ, PYTHONPATH=str(REPO / "lib")),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            workers.append(worker)
            with self.assertRaises(subprocess.TimeoutExpired):
                worker.wait(timeout=.25)
            self.assertFalse(marker.exists())
            self.assertTrue((self.state / run_store.APPEND_INTENT_NAME).exists())
            return original(state_fd, intent)

        with mock.patch.object(run_store, "_apply_append_intent", side_effect=paused_apply):
            supervisor._write(record)
        output, errors = workers[0].communicate(timeout=5)
        worker = workers[0]
        self.assertEqual((worker.returncode, output, errors), (0, "", ""))
        self.assertTrue(marker.is_file())
        self.assertEqual(supervisor._read(created["run_id"])["status"], "cancelled")

    def test_append_intent_rejects_noncanonical_run_id_before_path_access(self):
        self.state.mkdir(mode=0o700)
        (self.state / "runs").mkdir(mode=0o700)
        intent = {
            "schema_version": run_store.APPEND_INTENT_SCHEMA_VERSION,
            "run_id": "",
            "sidecar_old_size": 0,
            "sidecar_line": "{}\n",
            "metadata": {},
            "authority_old_size": 0,
            "authority_line": "{}\n",
            "authority_head": {},
        }
        outside = self.root / run_store.SIDECAR_NAME
        path = self.state / run_store.APPEND_INTENT_NAME
        for run_id in ("..", str(uuid.uuid4()).upper()):
            with self.subTest(run_id=run_id):
                intent["run_id"] = run_id
                path.write_text(json.dumps(intent) + "\n")
                path.chmod(0o600)
                with self.assertRaisesRegex(run_store.RunStoreError,
                                            "invalid run id|non-canonical run id"):
                    run_store.RunStore(self.state)
                self.assertFalse(outside.exists())
                self.assertEqual(json.loads(path.read_text()), intent)

    def test_missing_sidecar_with_existing_anchors_never_bootstraps_legacy_history(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        run_dir = self.state / "runs" / created["run_id"]
        (run_dir / run_store.SIDECAR_NAME).unlink()
        with self.assertRaisesRegex(runs.RunError, "metadata exists without history"):
            supervisor.show(created["run_id"])
        self.assertFalse((run_dir / run_store.SIDECAR_NAME).exists())

    def test_rehashed_sidecar_and_metadata_conflict_with_independent_authority(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        run_dir = self.state / "runs" / created["run_id"]
        record = supervisor._read(created["run_id"])
        record["suite_id"] = "mutated"
        record["canonical_run_digest"] = run_store._canonical_run_digest(record)
        self.forge_chain(run_dir, [record])
        with self.assertRaisesRegex(runs.RunError, "authority ledger conflicts"):
            supervisor.show(created["run_id"])

    def test_external_source_collision_rolls_back_every_row_from_that_source(self):
        benchmark = self.repository / "benchmarks" / "v0.1"
        benchmark.mkdir(parents=True)
        source = benchmark / "results.jsonl"
        base = {
            "task": "case", "arm": "harness", "rep": 1, "harness_sha": "a" * 40,
            "tag": "v0.1.0", "model": "model", "passed": True, "error": False,
        }
        source.write_text(json.dumps(base) + "\n" + json.dumps(dict(base, cost_usd=.5)) + "\n")
        supervisor = self.supervisor()
        report = supervisor.reindex(self.repository)
        self.assertEqual(report["imported_runs"], 0)
        self.assertEqual(report["skipped"][0]["path"], "benchmarks/v0.1/results.jsonl")
        self.assertIn("source identity collision", report["skipped"][0]["reason"])
        self.assertEqual(supervisor.history.list(), [])

    def test_validly_hashed_illegal_lifecycle_transition_is_rejected(self):
        supervisor = self.supervisor()
        created = self.queued_run(supervisor)
        run_dir = self.state / "runs" / created["run_id"]
        queued = supervisor._read(created["run_id"])
        running = dict(queued, status="running", runner_pid=os.getpid(),
                       runner_identity=runs.process_identity(os.getpid()),
                       command_pid=os.getpid(), command_identity=runs.process_identity(os.getpid()),
                       started_at=runs.utc_now())
        self.forge_chain(run_dir, [queued, running])
        with self.assertRaisesRegex(runs.RunError, "illegal lifecycle transition"):
            supervisor.show(created["run_id"])

    def test_terminal_digest_is_required_and_verified(self):
        for terminal in (None, "f" * 64):
            with self.subTest(terminal=terminal):
                state = self.root / ("terminal-" + ("missing" if terminal is None else "wrong"))
                supervisor = self.supervisor(state)
                created = self.queued_run(supervisor)
                run_dir = state / "runs" / created["run_id"]
                queued = supervisor._read(created["run_id"])
                cancelled = dict(queued, status="cancelled", completed_at=runs.utc_now())
                self.forge_chain(run_dir, [queued, cancelled], terminal_override=terminal)
                with self.assertRaisesRegex(runs.RunError, "terminal digest"):
                    supervisor.show(created["run_id"])

    def test_external_source_collision_is_visible_and_preserves_the_prior_index(self):
        self.write_sources()
        supervisor = self.supervisor()
        supervisor.reindex(self.repository)
        before = next(row for row in supervisor.history.list(20)
                      if row["source"]["kind"] == "benchmark-result")
        source = self.repository / "benchmarks" / "v0.1" / "results.jsonl"
        changed = dict(before["raw"], cost_usd=.999)
        source.write_text(json.dumps(changed) + "\n")
        with self.assertRaisesRegex(runs.RunError, "source identity collision"):
            supervisor.reindex(self.repository)
        self.assertEqual(supervisor.history.get(before["run_id"]), before)

    def test_reindex_keeps_other_live_store_connections_on_the_current_database(self):
        supervisor = self.supervisor()
        observer = run_store.RunStore(self.state)
        self.addCleanup(observer.close)
        self.write_sources()
        report = supervisor.reindex(self.repository)
        self.assertEqual(report["imported_runs"], 4)
        self.assertEqual(len(observer.list(limit=20)), 4)

    def test_malformed_nested_external_schemas_are_reported_and_skipped(self):
        benchmark = self.repository / "benchmarks"
        results = benchmark / "bad"
        evidence = self.repository / "compatibility" / "evidence"
        results.mkdir(parents=True)
        evidence.mkdir(parents=True)
        (results / "results.jsonl").write_text(json.dumps({
            "task": [], "arm": "harness", "rep": 1, "harness_sha": "a" * 40,
            "model": "model",
        }) + "\n")
        (benchmark / "history.jsonl").write_text(json.dumps({
            "date": "2026-01-01", "series": "bad", "harness_version": "0.1.0",
            "harness_sha": "a" * 40, "model": "model", "status": [],
        }) + "\n")
        (benchmark / "static.json").write_text(json.dumps({
            "schema_version": 2, "harness_version": "0.1.0",
            "total": [], "usd": {}, "files": {},
        }))
        (evidence / "bad.json").write_text(json.dumps({
            "kind": "native", "client": "client", "harness_version": "0.1.0",
            "source_commit": "a" * 40, "cases": {"case": []},
        }))
        supervisor = self.supervisor()
        report = supervisor.reindex(self.repository)
        self.assertEqual(report["imported_runs"], 0)
        self.assertEqual(len(report["skipped"]), 4)
        self.assertEqual(supervisor.history.list(), [])

    def test_every_malformed_benchmark_history_container_is_skipped(self):
        base = {
            "date": "2026-01-01", "series": "fixture", "harness_version": "0.1.0",
            "harness_sha": "a" * 40, "model": "model", "status": "passed", "reps": 2,
            "bare": {"runs": 2, "errors": 0, "passed": 1, "cost_per_passed": .5},
            "harness": {"runs": 2, "errors": 0, "passed": 1, "cost_per_passed": .25},
            "ratio": .5, "ratio_cache_normalised": .4, "per_task": {},
        }
        mutations = {
            "bare": [],
            "harness": {"cost_per_passed": "wrong"},
            "ratio": {},
            "reps": [],
            "per_task": [],
            "per_task_cell": {"case": {"bare": [], "harness": .5, "ratio": .5,
                                               "bare_spread": 1, "harness_spread": 1, "n": 1}},
            "summary_missing": None,
            "per_task_missing": None,
            "bucket": [],
            "cache_miss": [],
        }
        for name, malformed in mutations.items():
            with self.subTest(name=name):
                repository = self.root / ("history-" + name)
                benchmark = repository / "benchmarks"
                benchmark.mkdir(parents=True)
                row = dict(base)
                if name == "summary_missing":
                    row["bare"] = dict(base["bare"])
                    del row["bare"]["runs"]
                elif name == "per_task_missing":
                    row["per_task"] = {"case": {"bare": .5, "harness": .25, "ratio": .5,
                                                        "bare_spread": 1, "harness_spread": 1}}
                else:
                    row["per_task" if name == "per_task_cell" else name] = malformed
                (benchmark / "history.jsonl").write_text(json.dumps(row) + "\n")
                supervisor = self.supervisor(self.root / ("history-state-" + name))
                report = supervisor.reindex(repository)
                self.assertEqual(report["imported_runs"], 0)
                self.assertEqual(len(report["skipped"]), 1)
                self.assertEqual(supervisor.history.list(), [])

    def test_every_malformed_benchmark_result_scalar_skips_the_entire_source(self):
        base = {
            "task": "case", "arm": "harness", "rep": 1, "harness_sha": "a" * 40,
            "tag": "v0.1.0", "model": "model", "cli_version": "1.0", "passed": True,
            "error": False, "error_kind": "", "input_tokens": 10, "output_tokens": 4,
            "cost_usd": .1, "cost_normalised_usd": .2, "date": "2026-01-01",
        }
        mutations = {
            "arm": "control", "rep_negative": -1, "rep_bool": True,
            "token_negative": -1, "token_bool": True, "passed": "yes", "error": {},
            "error_kind": [], "cost": {}, "tag": [], "cli_version": [], "date": [],
        }
        for name, malformed in mutations.items():
            with self.subTest(name=name):
                repository = self.root / ("result-" + name)
                results = repository / "benchmarks" / "v0.1"
                results.mkdir(parents=True)
                row = dict(base)
                field = {
                    "rep_negative": "rep", "rep_bool": "rep", "token_negative": "input_tokens",
                    "token_bool": "input_tokens", "cost": "cost_usd",
                }.get(name, name)
                row[field] = malformed
                (results / "results.jsonl").write_text(
                    json.dumps(base) + "\n" + json.dumps(row) + "\n")
                supervisor = self.supervisor(self.root / ("result-state-" + name))
                report = supervisor.reindex(repository)
                self.assertEqual(report["imported_runs"], 0)
                self.assertEqual(len(report["skipped"]), 1)
                self.assertEqual(supervisor.history.list(), [])

    def test_every_malformed_static_nested_shape_skips_the_entire_source(self):
        total = {"files": 1, "lines": 2, "chars": 400, "est_tokens": 100}
        prices = {"model": {"session_start": .01, "later_turn": .002}}
        file_row = {"group": "always_loaded", "chars": 400, "est_tokens": 100,
                    "usd": prices}
        base = {"schema_version": 2, "harness_version": "0.1.0", "total": total,
                "usd": prices, "files": {"claude/CLAUDE.md": file_row}}
        mutations = ("total_missing", "total_bool", "usd_missing", "usd_bool", "files_path",
                     "files_missing", "files_bool")
        for name in mutations:
            with self.subTest(name=name):
                repository = self.root / ("static-" + name)
                benchmark = repository / "benchmarks"
                benchmark.mkdir(parents=True)
                row = json.loads(json.dumps(base))
                if name == "total_missing":
                    del row["total"]["lines"]
                elif name == "total_bool":
                    row["total"]["files"] = True
                elif name == "usd_missing":
                    del row["usd"]["model"]["later_turn"]
                elif name == "usd_bool":
                    row["usd"]["model"]["session_start"] = True
                elif name == "files_path":
                    row["files"] = {"../escape": file_row}
                elif name == "files_missing":
                    del row["files"]["claude/CLAUDE.md"]["group"]
                else:
                    row["files"]["claude/CLAUDE.md"]["chars"] = False
                (benchmark / "static.json").write_text(json.dumps(row))
                supervisor = self.supervisor(self.root / ("static-state-" + name))
                report = supervisor.reindex(repository)
                self.assertEqual(report["imported_runs"], 0)
                self.assertEqual(len(report["skipped"]), 1)
                self.assertEqual(supervisor.history.list(), [])

    def test_schema_one_index_migrates_without_losing_a_record(self):
        self.state.mkdir(mode=0o700)
        path = self.state / run_store.DATABASE_NAME
        connection = sqlite3.connect(str(path))
        connection.execute(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, "
            "source_path TEXT NOT NULL, suite_id TEXT NOT NULL, status TEXT NOT NULL, "
            "record_json TEXT NOT NULL)")
        record = {
            "schema_version": 1, "run_id": "legacy", "source": {"kind": "legacy", "path": "old"},
            "suite": {"id": "legacy", "version": 1}, "status": "succeeded",
        }
        connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                           ("legacy", "legacy", "old", "legacy", "succeeded", json.dumps(record)))
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        connection.close()
        store = run_store.RunStore(self.state)
        self.addCleanup(store.close)
        self.assertEqual(store.get("legacy"), record)
        self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_store_refuses_symlinked_database_and_bounds_queries(self):
        self.state.mkdir(mode=0o700)
        outside = self.root / "outside.sqlite3"
        outside.write_bytes(b"")
        (self.state / run_store.DATABASE_NAME).symlink_to(outside)
        with self.assertRaises(run_store.RunStoreError):
            run_store.RunStore(self.state)
        (self.state / run_store.DATABASE_NAME).unlink()
        store = run_store.RunStore(self.state)
        self.addCleanup(store.close)
        for limit, offset in ((0, 0), (1001, 0), (1, -1)):
            with self.subTest(limit=limit, offset=offset):
                with self.assertRaises(run_store.RunStoreError):
                    store.list(limit, offset)

    def test_cli_reindex_reports_imports_and_unknown_schemas_as_json(self):
        benchmark = REPO / "benchmarks"
        self.assertTrue((benchmark / "static.json").is_file())
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.state), \
             mock.patch.object(runs, "default_catalog_path", return_value=self.catalog), \
             mock.patch("sys.stdout", output):
            code = harness.main(["runs", "reindex", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertGreaterEqual(result["imported_runs"], 1)
        self.assertEqual(result["total_runs"], result["studio_runs"] + result["imported_runs"])
        self.assertIsInstance(result["skipped"], list)


if __name__ == "__main__":
    unittest.main()
