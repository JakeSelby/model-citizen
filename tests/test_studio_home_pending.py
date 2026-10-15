# SPDX-License-Identifier: MIT
"""Home never waits on a slow read: `GET /api/home` answers from the cache and warms the rest (#1332).

Every home here is temporary; the slow reads are probes that block on an event the test holds.
Run: python3 -m unittest discover -s tests -p test_studio_home_pending.py
"""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_home as home_case  # noqa: E402
from harness_core.studio import attention, home as studio_home, server  # noqa: E402

PROBE = "slow-probe"
# Far below the probe's hold; a Home that waited on the probe would take the full hold.
FAST = 2.0
HOLD = 30.0


class SlowReadCase(home_case.HomeCase):
    """A registered read that blocks until `release` is set, and a source that reads it."""

    def setUp(self):
        super().setUp()
        self.release = threading.Event()
        self.answer = "first"
        self.calls = 0

        def compute(_home):
            self.calls += 1
            if not self.release.wait(HOLD):
                raise TimeoutError("the probe was never released")
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        studio_home.register_read(PROBE, compute)
        attention.register_attention(PROBE, lambda home: [] if studio_home.read(PROBE, home) else [])
        self.addCleanup(attention._REGISTRY.pop, PROBE, None)
        self.addCleanup(studio_home._READS.pop, PROBE, None)
        self.addCleanup(self.release.set)

    def timed_report(self):
        started = time.monotonic()
        result = studio_home.report(self.home.root, wait=False)
        return result, time.monotonic() - started

    def settle(self):
        """Let every warm finish, the probe's included."""
        self.release.set()
        for thread in list(studio_home._WARMING.values()):
            thread.join(HOLD)


class PendingTests(SlowReadCase):
    def test_a_slow_source_does_not_delay_the_report_and_is_pending(self):
        result, took = self.timed_report()
        self.assertLess(took, FAST)
        self.assertEqual(result["queue"]["sources"][PROBE], "pending")
        self.assertEqual(result["queue"]["sources"]["hooks"], "ok", "a fast source still answers")
        self.assertTrue(studio_home.warming(PROBE, self.home.root), "the read warms in the background")
        self.settle()
        self.assertEqual(self.timed_report()[0]["queue"]["sources"][PROBE], "ok")
        self.assertEqual(self.calls, 1, "one warm, reused by the next report")

    def test_a_cold_drift_read_is_a_pending_block_not_an_error(self):
        studio_home.register_read("drift", lambda home: studio_home.drift(home) if self.release.wait(HOLD) else {})
        self.addCleanup(studio_home.register_read, "drift", studio_home.drift)
        result, took = self.timed_report()
        self.assertLess(took, FAST)
        self.assertEqual(result["drift"], {"status": "pending", "items": [], "message": None})
        self.assertEqual(result["queue"]["sources"]["drift"], "pending")
        self.settle()
        self.assertEqual(self.timed_report()[0]["drift"]["status"], "current")

    def test_an_expired_value_answers_while_a_fresh_one_warms(self):
        self.release.set()
        self.assertEqual(studio_home.read(PROBE, self.home.root), "first")
        self.release.clear()
        self.answer = "second"
        later = time.monotonic() + studio_home.READ_TTL + 1
        with mock.patch.object(studio_home.time, "monotonic", return_value=later):
            result, took = self.timed_report()
        self.assertLess(took, FAST)
        self.assertEqual(result["queue"]["sources"][PROBE], "ok", "the expired value still answers")
        self.assertTrue(studio_home.warming(PROBE, self.home.root))
        self.settle()
        self.assertEqual(studio_home.read(PROBE, self.home.root), "second")

    def test_a_failed_warm_is_an_error_not_pending_forever(self):
        self.answer = OSError("engine failed")
        self.assertEqual(self.timed_report()[0]["queue"]["sources"][PROBE], "pending")
        self.settle()
        self.assertEqual(self.timed_report()[0]["queue"]["sources"][PROBE], "error")
        self.assertFalse(studio_home.warming(PROBE, self.home.root), "no retry inside the error window")

    def test_a_waiting_report_still_waits_for_every_read(self):
        self.release.set()
        result = studio_home.report(self.home.root)
        self.assertEqual(result["queue"]["sources"][PROBE], "ok")
        self.assertNotIn("pending", result["queue"]["sources"].values())


class RouteTests(unittest.TestCase):
    def test_the_route_asks_for_a_report_that_never_waits(self):
        handler = mock.Mock()
        route = mock.Mock()
        with mock.patch.object(studio_home, "report", return_value={"queue": {}}) as report:
            server._home(handler, route)
        self.assertEqual(report.call_args.kwargs, {"wait": False})
        handler._json.assert_called_once_with(200, {"queue": {}})


if __name__ == "__main__":
    unittest.main()
