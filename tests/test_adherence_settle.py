# SPDX-License-Identifier: MIT
"""A live session settles due adherence emissions: the session-start hook answers each one once.

Driven through the Claude Code and Codex lifecycle entry points as their own processes, the way
the runtime runs them, so the test covers the wiring and not only `adherence.settle`. The answer
is observation, so the start-up output must be the same with a due emission, with one not yet
due, and with no ledger at all.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
RUNTIMES = ("claude-code", "codex")
DAY = 86400


def load_adherence():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence_settle", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adherence = load_adherence()


def emitted(ident, age):
    return {"kind": "emitted", "adherence_id": ident, "recommendation": "fresh-session",
            "module": "hooks/usage-feed", "session_id": "s-old", "turn": 2,
            "ts": adherence.now_ts(time.time() - age), "profile_fingerprint": "p",
            "schema_version": 1}


class LiveSettleTests(unittest.TestCase):
    def home(self, rows=None):
        home = Path(tempfile.mkdtemp(prefix="adherence-settle-"))
        self.addCleanup(shutil.rmtree, str(home), ignore_errors=True)
        (home / "work").mkdir()
        if rows is not None:
            target = adherence.path({"HOME": str(home)})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return home

    def start(self, home, runtime="claude-code", session="s-new"):
        """The session-start entry's stdout, run as the runtime runs it."""
        env = without_harness_vars()
        env["HOME"] = str(home)
        payload = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup",
                   "cwd": str(home / "work")}
        done = subprocess.run([sys.executable, str(REPO / "adapters" / runtime / "hook.py")],
                              input=json.dumps(payload), capture_output=True, text=True,
                              env=env, cwd=str(home / "work"), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def ledger(self, home):
        return adherence.read_rows(adherence.path({"HOME": str(home)}))

    def test_a_due_emission_gets_exactly_one_response_and_a_second_start_adds_none(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                home = self.home([emitted("due", 2 * DAY)])
                self.start(home, runtime)
                responses = [row for row in self.ledger(home) if row["kind"] == "response"]
                self.assertEqual(len(responses), 1)
                answer = responses[0]
                self.assertEqual((answer["adherence_id"], answer["outcome"], answer["reason"]),
                                 ("due", "unknown", "unobserved"))
                self.assertEqual(sorted(answer), sorted([
                    "adherence_id", "kind", "module", "outcome", "profile_fingerprint",
                    "reason", "recommendation", "schema_version", "session_id", "ts",
                    "turn", "turns_after", "window"]))
                self.start(home, runtime, session="s-later")
                self.assertEqual(len(self.ledger(home)), 2)

    def test_settling_adds_nothing_to_what_session_start_says(self):
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                bare = self.start(self.home(), runtime)
                self.assertEqual(self.start(self.home([emitted("due", 2 * DAY)]), runtime), bare)
                self.assertEqual(self.start(self.home([emitted("open", 60)]), runtime), bare)

    def test_nothing_due_writes_nothing(self):
        home = self.home([emitted("open", 60)])
        target = adherence.path({"HOME": str(home)})
        before = target.read_bytes()
        self.start(home)
        self.assertEqual(target.read_bytes(), before)
        empty = self.home()
        self.start(empty)
        self.assertFalse(adherence.path({"HOME": str(empty)}).exists())

    def test_a_ledger_it_cannot_read_leaves_session_start_unchanged(self):
        bare = self.start(self.home())
        home = self.home()
        adherence.path({"HOME": str(home)}).mkdir(parents=True)
        self.assertEqual(self.start(home), bare)


class ConcurrentSettleTests(unittest.TestCase):
    """Two sessions starting together: the read, the check and the append are one locked step."""

    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="adherence-race-"))
        self.addCleanup(shutil.rmtree, str(home), ignore_errors=True)
        self.env = {"HOME": str(home)}
        target = adherence.path(self.env)
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(emitted("due", 2 * DAY)) + "\n")

    def race(self, wait):
        """Settle from two threads, each held inside its reading until both arrive or `wait` ends.

        Unlocked, both have read the ledger before either appends, which is the race.
        """
        barrier = threading.Barrier(2, timeout=wait)
        real = adherence.respond

        def held(row, observed):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return real(row, observed)

        with patch.object(adherence, "respond", held):
            threads = [threading.Thread(target=adherence.settle, args=(self.env,)) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        return self.rows()

    def rows(self):
        return [row for row in adherence.read_rows(adherence.path(self.env))
                if row["kind"] == "response"]

    def test_a_settler_that_tries_while_another_holds_the_lock_writes_nothing(self):
        # The first settler waits inside its reading until the second has tried the lock and
        # been refused, so the one row below is the lock's doing, not the threads' timing.
        inside, refused = threading.Event(), threading.Event()
        real_respond, real_fcntl = adherence.respond, adherence.fcntl

        def held(row, observed):
            inside.set()
            refused.wait(10)
            return real_respond(row, observed)

        def flock(stream, operation):
            try:
                return real_fcntl.flock(stream, operation)
            except BlockingIOError:
                refused.set()
                raise

        fake = types.SimpleNamespace(flock=flock, LOCK_EX=real_fcntl.LOCK_EX,
                                     LOCK_NB=real_fcntl.LOCK_NB)
        with patch.object(adherence, "respond", held), patch.object(adherence, "fcntl", fake):
            first = threading.Thread(target=adherence.settle, args=(self.env,))
            first.start()
            self.assertTrue(inside.wait(10))
            second = threading.Thread(target=adherence.settle, args=(self.env,))
            second.start()
            first.join(30)
            second.join(30)
        self.assertTrue(refused.is_set())
        self.assertEqual(len(self.rows()), 1)

    def test_no_advisory_locking_leaves_the_emission_for_a_later_start(self):
        with patch.object(adherence, "fcntl", None):
            self.assertEqual(adherence.settle(self.env), [])
        self.assertEqual(self.rows(), [])

    def test_the_race_writes_two_without_the_lock(self):
        # Proof the race is real: with the lock replaced by no lock, both settlers answer.
        @contextmanager
        def unlocked(target):
            yield True

        with patch.object(adherence, "ledger_lock", unlocked):
            self.assertEqual(len(self.race(wait=10)), 2)

    def test_a_holder_that_never_lets_go_leaves_the_answer_for_later(self):
        with open(str(adherence.path(self.env)), "rb") as stream, \
                patch.object(adherence, "LOCK_BUDGET", 0.05):
            adherence.fcntl.flock(stream, adherence.fcntl.LOCK_EX)
            started = time.monotonic()
            self.assertEqual(adherence.settle(self.env), [])
            self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(adherence.settle(self.env)), 1)


if __name__ == "__main__":
    unittest.main()
