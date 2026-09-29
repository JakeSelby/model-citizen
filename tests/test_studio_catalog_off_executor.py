# SPDX-License-Identifier: MIT
"""The slow free-suite catalog read never holds the serial mutation executor."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import free_suites, runs, server  # noqa: E402
from harness_core.studio.mutations import MutationExecutor  # noqa: E402


class CatalogOffExecutorTests(unittest.TestCase):
    def test_history_answers_while_catalog_discovery_is_still_running(self):
        executor = MutationExecutor()
        self.addCleanup(executor.close)
        entered = threading.Event()
        release = threading.Event()
        history_payload = {"schema_version": 1}

        def slow_catalog(_root):
            entered.set()
            release.wait(5)
            return {"suites": []}

        supervisor = mock.Mock()
        supervisor.catalog.side_effect = slow_catalog
        supervisor.history_page.return_value = history_payload
        catalog_handler = mock.Mock()
        catalog_handler.server.repo_root = ROOT
        catalog_handler.server.mutations = executor
        catalog_handler.server.run_supervisor = supervisor
        history_handler = mock.Mock()
        history_handler.server.mutations = executor
        history_handler.server.run_supervisor = supervisor
        history_route = mock.Mock()
        with mock.patch.object(server, "_required_request", return_value={"limit": 50}):
            catalog_thread = threading.Thread(target=server._runs_catalog, args=(
                catalog_handler, mock.Mock()))
            catalog_thread.start()
            self.assertTrue(entered.wait(1))
            history_thread = threading.Thread(target=server._run_history, args=(
                history_handler, history_route))
            history_thread.start()
            history_thread.join(1)
            answered = not history_thread.is_alive()
            release.set()
            catalog_thread.join(1)
            history_thread.join(1)
        self.assertTrue(answered, "history waited behind catalog discovery")
        history_handler._json.assert_called_once_with(200, history_payload)
        catalog_handler._json.assert_called_once_with(200, {"suites": []})

    def test_concurrent_catalog_requests_share_one_discovery_process(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        supervisor = runs.RunSupervisor(Path(temporary.name).resolve() / "state",
                                        ROOT / "policy" / "studio" / "suites.json")
        started = []
        entered = threading.Event()
        release = threading.Event()
        case = {"id": "test_sample.SampleTests.test_passes", "module": "test_sample",
                "class_name": "SampleTests", "method": "test_passes"}

        def discover(_root):
            started.append(threading.current_thread().name)
            entered.set()
            release.wait(5)
            return [case]

        results = []
        with mock.patch.object(free_suites, "discover_unit_tests", side_effect=discover):
            threads = [threading.Thread(target=lambda: results.append(supervisor.catalog(ROOT)))
                       for _ in range(5)]
            threads[0].start()
            self.assertTrue(entered.wait(1))
            for thread in threads[1:]:
                thread.start()
            release.set()
            for thread in threads:
                thread.join(5)
            self.assertEqual(len(started), 1)
            self.assertEqual(len(results), 5)
            self.assertTrue(all(result == results[0] for result in results))
            supervisor.catalog(ROOT)
        self.assertEqual(len(started), 2, "a request after the flight lands starts a fresh one")

    def test_a_failed_discovery_fails_every_waiting_request(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        supervisor = runs.RunSupervisor(Path(temporary.name).resolve() / "state",
                                        ROOT / "policy" / "studio" / "suites.json")
        with mock.patch.object(free_suites, "discover_unit_tests",
                               side_effect=free_suites.FreeSuiteError("unit-test discovery failed")):
            with self.assertRaises(runs.RunError):
                supervisor.catalog(ROOT)


if __name__ == "__main__":
    unittest.main()
