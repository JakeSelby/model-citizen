# SPDX-License-Identifier: MIT
"""The OTLP exporter backs off a collector that is not listening.

Every session end tried `localhost:4318` and logged a failure per batch, hundreds of times with
no collector running. With a backoff file the session-end hook tries once per window, the window
doubles up to a cap, and a collector that answers clears it. A replay from the CLI passes no
backoff file and is never held back.

Run: python3 -m unittest discover tests
"""
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import isolation  # noqa: F401  (moves the process onto a disposable home)

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


telemetry = _load("telemetry_backoff", REPO / "policy" / "hooks" / "telemetry.py")
CONFIG = {"export": "otlp", "endpoint": "http://localhost:4318", "headers_env": "",
          "headers_file": "", "labels": {}}
ROWS = [{"session_id": "s-%d" % n, "ended": "2026-10-01T00:00:00Z"} for n in range(3)]


class Response(object):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b""


class Opener(object):
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Response()


class BackoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backoff = Path(tmp.name) / "export.backoff.json"
        self.errors = Path(tmp.name) / "usage.errors.jsonl"

    def export(self, opener, now, backoff=True):
        return telemetry.export_rows(ROWS, config=CONFIG, errors_path=self.errors, opener=opener,
                                     prices={}, now=now,
                                     backoff_path=self.backoff if backoff else None)

    def failures(self):
        if not self.errors.exists():
            return []
        return [json.loads(line) for line in self.errors.read_text().splitlines()]

    def test_a_refused_connection_opens_a_window_and_nothing_is_tried_inside_it(self):
        refused = Opener(urllib.error.URLError(ConnectionRefusedError(61, "refused")))
        self.assertEqual(self.export(refused, now=1000), (0, 3))
        self.assertEqual(refused.calls, 1)
        self.assertEqual(json.loads(self.backoff.read_text()),
                         {"failures": 1, "until": 1000 + telemetry.BACKOFF_BASE})
        self.assertEqual(self.export(refused, now=1030), (0, 3))
        self.assertEqual(refused.calls, 1)
        self.assertEqual(len(self.failures()), 1)

    def test_the_window_doubles_and_stops_at_the_cap(self):
        refused = Opener(urllib.error.URLError(OSError("unreachable")))
        now = 0
        for _ in range(12):
            now = json.loads(self.backoff.read_text())["until"] if self.backoff.exists() else 0
            self.export(refused, now=now)
        state = json.loads(self.backoff.read_text())
        self.assertEqual(state["failures"], 12)
        self.assertEqual(state["until"] - now, telemetry.BACKOFF_MAX)

    def test_a_collector_that_answers_clears_the_backoff(self):
        self.export(Opener(urllib.error.URLError(ConnectionRefusedError())), now=0)
        self.assertTrue(self.backoff.exists())
        self.assertEqual(self.export(Opener(), now=telemetry.BACKOFF_BASE + 1), (3, 0))
        self.assertFalse(self.backoff.exists())

    def test_an_error_status_is_a_listener_and_opens_no_window(self):
        rejected = urllib.error.HTTPError("http://localhost:4318/v1/logs", 500, "no", None,
                                          io.BytesIO(b""))
        self.assertEqual(self.export(Opener(rejected), now=0), (0, 3))
        self.assertFalse(self.backoff.exists())

    def test_a_replay_without_a_backoff_file_is_never_held_back(self):
        self.export(Opener(urllib.error.URLError(ConnectionRefusedError())), now=0)
        working = Opener()
        self.assertEqual(self.export(working, now=1, backoff=False), (3, 0))
        self.assertEqual(working.calls, 1)

    def test_an_unreadable_or_far_future_backoff_is_bounded(self):
        self.backoff.write_text("not json")
        self.assertEqual(telemetry.backoff_until(self.backoff, now=5), 0)
        self.backoff.write_text(json.dumps({"failures": 1, "until": 10 ** 12}))
        self.assertEqual(telemetry.backoff_until(self.backoff, now=5), 5 + telemetry.BACKOFF_MAX)


if __name__ == "__main__":
    unittest.main()
