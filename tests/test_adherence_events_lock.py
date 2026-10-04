# SPDX-License-Identifier: MIT
"""The session events file is rotated, appended and read under one shared lock.

Two hook processes that both find the events file full used to rotate it twice: the second move
replaced the first one's `.1`, and the prompts in it were gone, so `settle` answered from a
history with a hole in it. Each case drives the locked sections in one process, holding the lock
or running the second writer at the exact point the first has seen a full file, so no case
depends on timing.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence_events_lock", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adherence = _load()


class EventsLockTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        self.target = adherence.events_path(self.env)
        # A lock the test holds is never released inside a case, so polling would only wait.
        for name, value in (("EVENTS_MAX_BYTES", 1), ("LOCK_BUDGET", 0)):
            self.addCleanup(setattr, adherence, name, getattr(adherence, name))
            setattr(adherence, name, value)

    def note(self, session, now):
        adherence.note_event("UserPromptSubmit", session, env=self.env, now=now)

    def sessions(self):
        return sorted(row["session_id"] for row in adherence.observed_rows(self.env))

    def test_a_second_rotation_after_the_first_saw_a_full_file_loses_no_prompt(self):
        self.note("s-0", 100)
        self.assertGreaterEqual(self.target.stat().st_size, adherence.EVENTS_MAX_BYTES)
        real = os.path.getsize
        calls = []

        def stale(name):
            # Writer A has measured a full file; writer B now runs in full before A acts on it.
            size = real(name)
            if not calls and name == str(self.target):
                calls.append(name)
                self.note("s-b", 101)
            return size

        with patch("os.path.getsize", stale):
            self.note("s-a", 102)
        self.assertEqual(calls, [str(self.target)])
        self.assertEqual(self.sessions(), ["s-0", "s-a", "s-b"])

    def test_a_writer_without_the_lock_appends_and_leaves_the_rotation(self):
        self.note("s-0", 100)
        with adherence.events_lock(self.env) as held:
            self.assertTrue(held)
            self.note("s-1", 101)
        self.assertFalse(Path(str(self.target) + ".1").exists())
        rows = adherence.read_rows(self.target)
        self.assertEqual([r["session_id"] for r in rows], ["s-0", "s-1"])

    def test_the_two_file_read_waits_for_the_lock_and_settle_answers_nothing_meanwhile(self):
        self.note("s-0", 100)
        ledger = adherence.path(self.env)
        row = adherence.emission("fresh-session", "s-0", 1, 100)
        ledger.write_text(json.dumps(row) + "\n")
        with adherence.events_lock(self.env) as held:
            self.assertTrue(held)
            self.assertIsNone(adherence.observed_rows(self.env))
            self.assertEqual(adherence.settle(self.env, now=100 + adherence.UNKNOWN_AFTER), [])
        self.assertEqual(self.sessions(), ["s-0"])


if __name__ == "__main__":
    unittest.main()
