"""Read-only run routes read the real run index on the thread that owns it (#990).

The served Studio builds its `RunSupervisor` on the mutation executor's thread, and the run index
is a SQLite connection only that thread may use. A route that reads the supervisor from its own
request thread gets "run index update failed", which `compare` reported as an unknown run. Every
test here uses a real supervisor, run store and executor built as `server.Server` builds them, with
a finished live replay on disk; only the HTTP handler object is a fixture.
"""
import json
import os
import sys
import tempfile
import threading
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import compare, draft_tests, replay, runs, server  # noqa: E402
from harness_core.studio.mutations import MutationExecutor  # noqa: E402
from studio_target_support import FixtureTargetService  # noqa: E402
from test_replay_stats import rows_for  # noqa: E402
from test_studio_compare import BASE, CHEAPER  # noqa: E402
from test_studio_draft_tests import BASE_REV, FIRST_REV, PLAN, draft_at, draft_request  # noqa: E402
from test_studio_replay import write_native_result  # noqa: E402


class Handler:
    def __init__(self, studio, body):
        self.server = studio
        self.request_json = body
        self.response = None

    def _json(self, code, payload):
        self.response = (code, payload)

    def _error(self, code, name):
        self.response = (code, {"error": name})


def call_route(studio, path, body):
    route = next(item for item in server.ROUTES.entries if item.path == path)
    handler = Handler(studio, body)
    route.handler(handler, route)
    return handler.response


class RouteThreadOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mutations = MutationExecutor()
        self.addCleanup(self.mutations.close)
        root = Path(self.tmp.name).resolve()
        self.supervisor = self.mutations.call(lambda: runs.RunSupervisor(
            root / "state", REPO / "policy" / "studio" / "replay-suite.json",
            target_service=FixtureTargetService()))
        self.addCleanup(self.close_supervisor)
        self.selected = draft_request(FIRST_REV)
        self.run_id = self.mutations.call(self.finished_replay)
        self.studio = SimpleNamespace(run_supervisor=self.supervisor, repo_root=REPO,
                                      mutations=self.mutations)

    def close_supervisor(self):
        try:
            self.mutations.call(self.supervisor.close)
        except RuntimeError:
            pass  # a test closed the executor first, and the supervisor with it

    def finished_replay(self):
        """A live replay admitted by the real supervisor, its native rows written, and marked
        succeeded, all on the owning thread."""
        launch = replay.launch_payload(self.selected, "unused", REPO)
        cases = replay.case_identities(self.selected)
        preview = self.supervisor.spend_preview(
            "live-replay", launch["parameters"], launch["target_kind"], launch["target_ref"],
            self.selected.max_budget_usd, self.selected.spend_cap_usd, "api_credit",
            case_identities=cases)
        with mock.patch.object(self.supervisor, "_admit_locked"):
            started = self.supervisor.start(
                "live-replay", launch["parameters"], launch["target_kind"], launch["target_ref"],
                confirmed=preview["confirmation_token"],
                max_budget_usd=self.selected.max_budget_usd,
                spend_cap_usd=self.selected.spend_cap_usd, pricing_source="api_credit",
                case_identities=cases)
        run_id = started["run_id"]
        specs = {BASE_REV: BASE, FIRST_REV: CHEAPER}

        def launch_native(command, **_kwargs):
            ref = command[command.index("--tag") + 1]
            write_native_result(command, [
                dict(row, tag=ref, harness_sha=ref, model=self.selected.model, schema_version=1)
                for row in rows_for(specs[ref])])
            return SimpleNamespace(returncode=0)

        replay.execute(self.selected, REPO,
                       self.supervisor._run_path(run_id).parent / "replay", launch_native)
        # The legal path to a finished run, as the run worker walks it.
        identity = {"runner_pid": os.getpid(), "runner_identity": runs.process_identity(os.getpid())}
        steps = (("admitted", {"admission_token": "a" * 32}), ("starting", identity),
                 ("running", {"started_at": runs.utc_now(), "command_pid": os.getpid(),
                              "command_identity": runs.process_identity(os.getpid())}),
                 ("succeeded", {"completed_at": runs.utc_now()}))
        with self.supervisor.lock():
            record = self.supervisor._read(run_id)
            for status, fields in steps:
                record.update(fields, status=status)
                self.supervisor._write(record)
        return run_id

    def compare_body(self):
        return {"base": {"run_id": self.run_id, "target": 1},
                "candidate": {"run_id": self.run_id, "target": 2}}

    def test_a_read_off_the_owning_thread_fails_on_the_real_run_index(self):
        # The control: the same read without the executor is the reported bug.
        bypass = SimpleNamespace(run_supervisor=self.supervisor, repo_root=REPO,
                                 mutations=SimpleNamespace(call=lambda action: action()))
        # The real failure is a read that failed, reported as retryable, never a missing run.
        self.assertEqual(call_route(bypass, "/api/runs/compare", self.compare_body()),
                         (503, {"error": "compare_run_unreadable"}))

    def test_an_unknown_run_is_still_not_found(self):
        body = {"base": {"run_id": str(uuid.uuid4()), "target": 1},
                "candidate": {"run_id": self.run_id, "target": 2}}
        self.assertEqual(call_route(self.studio, "/api/runs/compare", body),
                         (404, {"error": "compare_not_found"}))

    def test_the_sides_load_on_the_owning_thread_and_the_engine_runs_off_it(self):
        threads = {"load": [], "engine": []}
        load_side, engine = compare.load_side, compare.compare

        def recorded_load(*args, **kwargs):
            threads["load"].append(threading.current_thread())
            return load_side(*args, **kwargs)

        def recorded_engine(*args, **kwargs):
            threads["engine"].append(threading.current_thread())
            return engine(*args, **kwargs)

        with mock.patch.object(compare, "load_side", recorded_load), \
                mock.patch.object(compare, "compare", recorded_engine):
            status, _payload = call_route(self.studio, "/api/runs/compare", self.compare_body())
            with draft_at(FIRST_REV):
                self.record_draft_test()
                verdicts = call_route(self.studio, "/api/configure/test/verdicts",
                                      {"draft": "tuned"})
        self.assertEqual((status, verdicts[0]), (200, 200))
        owner = self.mutations._thread
        self.assertEqual(len(threads["load"]), 4)
        self.assertEqual(len(threads["engine"]), 2)
        self.assertTrue(all(thread is owner for thread in threads["load"]))
        self.assertTrue(all(thread is not owner for thread in threads["engine"]))

    def test_each_draft_test_costs_one_executor_trip(self):
        trips = []
        call = self.mutations.call

        def counted(operation):
            trips.append(operation)
            return call(operation)

        counting = SimpleNamespace(run_supervisor=self.supervisor, repo_root=REPO,
                                   mutations=SimpleNamespace(call=counted))
        with draft_at(FIRST_REV):
            self.record_draft_test()
            status, payload = call_route(counting, "/api/configure/test/verdicts",
                                         {"draft": "tuned"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload["checkpoints"]), 1)
        self.assertEqual(len(trips), 1)

    def test_a_closed_executor_is_a_retryable_503_on_both_routes(self):
        self.mutations.call(self.supervisor.close)
        self.mutations.close()
        self.assertEqual(call_route(self.studio, "/api/runs/compare", self.compare_body()),
                         (503, {"error": "studio_unavailable"}))
        with draft_at(FIRST_REV):
            self.record_draft_test()
            self.assertEqual(call_route(self.studio, "/api/configure/test/verdicts",
                                        {"draft": "tuned"}),
                             (503, {"error": "studio_unavailable"}))

    def record_draft_test(self):
        draft_tests.record(self.supervisor.state_root, self.run_id,
                           draft_tests.identity(REPO, "tuned"), self.selected,
                           draft_tests.power(REPO, 3, 2, PLAN))

    def test_compare_reads_the_real_supervisor_through_its_owning_thread(self):
        status, payload = call_route(self.studio, "/api/runs/compare", self.compare_body())
        self.assertEqual(status, 200, payload)
        server.RUNS_COMPARE.validate(payload)
        self.assertTrue(payload["comparable"], payload["refusals"])
        self.assertEqual((payload["base"]["run_id"], payload["candidate"]["target"]),
                         (self.run_id, 2))
        self.assertIsNotNone(payload["result"])

    def test_draft_test_verdicts_read_the_real_supervisor_through_its_owning_thread(self):
        with draft_at(FIRST_REV):
            self.record_draft_test()
            status, payload = call_route(self.studio, "/api/configure/test/verdicts",
                                         {"draft": "tuned"})
        self.assertEqual(status, 200, payload)
        latest = payload["checkpoints"][0]["latest"]
        self.assertEqual(latest["status"], "succeeded", json.dumps(latest))
        self.assertNotEqual(latest["verdict"], "unavailable", latest["reasons"])
        self.assertIn("cost_per_passed", latest["readings"])


if __name__ == "__main__":
    unittest.main()
