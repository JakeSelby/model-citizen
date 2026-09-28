"""Run-history indexing, paging and commit-scoped flaky classification."""
import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from test_harness import REPO  # noqa: F401 - adds lib/ to the test import path
from harness_core.studio import run_store


def indexed(run_id, created, outcome=None, commit="a" * 40, suite="unit-tests",
            cost=None, duration=True):
    times = {"created_at": created}
    if duration:
        times.update({"started_at": created, "completed_at": created})
    cases = [] if outcome is None else [{"id": "test_a.C.test_one", "status": outcome,
                                         "detail": ""}]
    return {
        "schema_version": 1, "run_id": run_id,
        "source": {"kind": "fixture", "path": "fixture", "record_identity": run_id},
        "suite": {"id": suite, "version": 1},
        "target": {"kind": "commit", "ref": commit, "commit": commit,
                   "draft": None, "config_digest": None},
        "runtime": None, "model": None, "arms": [], "trials": None,
        "parameters": {}, "argv": [], "status": "succeeded", "times": times,
        "tokens": {}, "cost": ({"amount_usd": cost} if cost is not None else None),
        "cases": cases, "artifacts": [], "raw": {},
    }


class RunStoreHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = run_store.RunStore(Path(self.temporary.name).resolve())
        self.addCleanup(self.store.close)

    def test_flaky_marks_require_pass_and_fail_at_the_same_known_commit(self):
        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        other = str(uuid.uuid4())
        self.store.upsert(indexed(first, "2026-01-03T00:00:00Z", "passed"))
        self.store.upsert(indexed(second, "2026-01-02T00:00:00Z", "failed"))
        self.store.upsert(indexed(other, "2026-01-01T00:00:00Z", "passed", commit="b" * 40))
        self.assertEqual(self.store.detail(first)["cases"][0]["flaky"], True)
        self.assertEqual(self.store.detail(second)["cases"][0]["flaky"], True)
        self.assertEqual(self.store.detail(other)["cases"][0]["flaky"], False)
        history = self.store.case_history("test_a.C.test_one")
        self.assertEqual([item["case"]["flaky"] for item in history["items"]],
                         [True, True, False])

    def test_keyset_pages_do_not_shift_when_a_new_run_is_inserted(self):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for number, run_id in enumerate(ids):
            self.store.upsert(indexed(run_id, "2026-01-0%dT00:00:00Z" % (number + 1)))
        first = self.store.history(limit=2)
        self.store.upsert(indexed(str(uuid.uuid4()), "2026-02-01T00:00:00Z"))
        second = self.store.history(limit=2, cursor=first["next_cursor"])
        self.assertEqual({item["run_id"] for item in first["items"]}
                         & {item["run_id"] for item in second["items"]}, set())
        self.assertEqual(len(second["items"]), 1)

    def test_unknown_cost_and_duration_do_not_match_numeric_filters(self):
        unknown = str(uuid.uuid4())
        known = str(uuid.uuid4())
        self.store.upsert(indexed(unknown, "2026-01-01T00:00:00Z", duration=False))
        self.store.upsert(indexed(known, "2026-01-02T00:00:00Z", cost=1.25))
        self.assertEqual([item["run_id"] for item in self.store.history(min_cost_usd=1)["items"]],
                         [known])
        self.assertEqual([item["run_id"] for item in self.store.history(min_duration_ms=0)["items"]],
                         [known])
        self.assertEqual([item["run_id"] for item in self.store.history(
            created_from="2026-01-02", created_to="2026-01-02")["items"]], [known])
        self.assertEqual([item["run_id"] for item in self.store.history(target="a" * 40)["items"]],
                         [known, unknown])

    def test_schema_two_migration_backfills_history_detail_and_cases(self):
        self.store.close()
        path = Path(self.temporary.name) / run_store.DATABASE_NAME
        path.unlink()
        record = indexed(str(uuid.uuid4()), "2026-01-02T01:02:03-05:00", "passed", cost=1.25)
        sealed = run_store._seal_index_record(record)
        connection = sqlite3.connect(str(path))
        connection.execute(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, "
            "source_path TEXT NOT NULL, suite_id TEXT NOT NULL, status TEXT NOT NULL, "
            "record_json TEXT NOT NULL, source_digest TEXT, indexed_at TEXT)")
        connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)", (
            sealed["run_id"], "fixture", "fixture", "unit-tests", "succeeded",
            json.dumps(sealed), "digest", "2026-01-01T00:00:00Z"))
        connection.execute("PRAGMA user_version=2")
        connection.commit()
        connection.close()

        self.store = run_store.RunStore(Path(self.temporary.name).resolve())
        self.addCleanup(self.store.close)
        history = self.store.history()
        detail = self.store.detail(sealed["run_id"])
        self.assertEqual(history["items"][0]["created_at"], "2026-01-02T06:02:03.000000Z")
        self.assertEqual(history["items"][0]["case_count"], 1)
        self.assertEqual(detail["cases"][0]["outcome"], "passed")
        self.assertEqual(detail["cost_usd"], 1.25)

    def test_unknown_paid_case_outcomes_keep_identity_without_becoming_flaky(self):
        run_id = str(uuid.uuid4())
        record = indexed(run_id, "2026-01-01T00:00:00Z")
        record["cases"] = [{"id": "test_a.C.test_one", "status": "completed",
                            "spend_usd": 0.1},
                           {"id": "test_a.C.test_two", "status": "not_run",
                            "spend_usd": 0.0}]
        self.store.upsert(record)
        cases = self.store.detail(run_id)["cases"]
        self.assertEqual([(item["id"], item["outcome"], item["flaky"]) for item in cases],
                         [("test_a.C.test_one", "unknown", False),
                          ("test_a.C.test_two", "unknown", False)])

    def test_timestamp_offsets_sort_chronologically_and_naive_filters_are_rejected(self):
        early, late = str(uuid.uuid4()), str(uuid.uuid4())
        self.store.upsert(indexed(early, "2026-01-01T23:30:00-05:00"))
        self.store.upsert(indexed(late, "2026-01-02T05:00:00+00:00"))
        self.assertEqual([item["run_id"] for item in self.store.history()["items"]],
                         [late, early])
        with self.assertRaisesRegex(run_store.RunStoreError, "filter"):
            self.store.history(created_from="2026-01-02T00:00:00")

    def test_lineage_is_keyset_paged_and_case_sql_errors_are_translated(self):
        parent = str(uuid.uuid4())
        self.store.upsert(indexed(parent, "2026-01-01T00:00:00Z"))
        for day in range(2, 5):
            child = indexed(str(uuid.uuid4()), "2026-01-0%dT00:00:00Z" % day)
            child["rerun_of"] = parent
            self.store.upsert(child)
        first = self.store.detail(parent, lineage_limit=2)["reruns"]
        second = self.store.detail(parent, lineage_limit=2,
                                   lineage_cursor=first["next_cursor"])["reruns"]
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 1)
        self.store.connection.execute("DROP TABLE run_cases")
        with self.assertRaisesRegex(run_store.RunStoreError, "case history read failed"):
            self.store.case_history("test_a.C.test_one")

    def test_filtered_first_page_over_ten_thousand_rows_is_under_200_ms(self):
        with self.store.connection:
            for number in range(10000):
                record = indexed(str(uuid.uuid4()), "2026-01-%02dT00:00:00Z" % (number % 28 + 1),
                                 suite="unit-tests" if number % 2 else "lint")
                self.store._upsert(record)
        started = time.perf_counter()
        page = self.store.history(limit=50, suite_id="unit-tests", status="succeeded")
        json.dumps(page, sort_keys=True)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(page["items"]), 50)
        self.assertLess(elapsed, .2, "filtered history took %.3fs" % elapsed)


if __name__ == "__main__":
    unittest.main()
