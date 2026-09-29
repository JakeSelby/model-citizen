# SPDX-License-Identifier: MIT
"""Shared lock files open even when two openers race to create them."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import test_harness  # noqa: F401  loads the repository library path
from harness_core.studio import state
from harness_core.studio.state import CREATE_RACE_ATTEMPTS, open_shared

FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW


class SharedOpenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.directory)

    def test_a_lost_create_race_is_retried(self):
        real_open = os.open
        attempts = []

        def losing_once(name, flags, mode=0o777, *, dir_fd=None):
            attempts.append(name)
            if len(attempts) == 1:
                raise FileNotFoundError(2, "No such file or directory", name)
            return real_open(name, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(state.os, "open", losing_once):
            descriptor = open_shared("shared.lock", FLAGS, 0o600, self.directory)
        os.close(descriptor)
        self.assertEqual(len(attempts), 2)
        self.assertTrue((self.root / "shared.lock").is_file())

    def test_a_missing_directory_still_fails(self):
        gone = self.root / "gone"
        gone.mkdir()
        descriptor = os.open(str(gone), os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        gone.rmdir()
        with mock.patch.object(state.os, "open", wraps=os.open) as opened:
            with self.assertRaises(FileNotFoundError):
                open_shared("shared.lock", FLAGS, 0o600, descriptor)
        self.assertEqual(opened.call_count, CREATE_RACE_ATTEMPTS)

    def test_open_without_create_is_not_retried(self):
        with mock.patch.object(state.os, "open", wraps=os.open) as opened:
            with self.assertRaises(FileNotFoundError):
                open_shared("absent", os.O_RDONLY, 0o600, self.directory)
        self.assertEqual(opened.call_count, 1)

    def test_retries_back_off_within_a_bounded_total(self):
        with mock.patch.object(state.os, "open",
                               side_effect=FileNotFoundError(2, "No such file or directory")), \
                mock.patch.object(state.time, "sleep") as slept:
            with self.assertRaises(FileNotFoundError):
                open_shared("shared.lock", FLAGS, 0o600, self.directory)
        delays = [call.args[0] for call in slept.call_args_list]
        self.assertEqual(len(delays), CREATE_RACE_ATTEMPTS - 1)
        self.assertEqual(delays, [delays[0] * 2 ** index for index in range(len(delays))])
        self.assertLess(sum(delays), 0.5)

    def test_the_losing_creator_retries_and_both_threads_open_the_file(self):
        """Two real threads race; the loser sees the macOS ENOENT once and must retry."""
        real_open = os.open
        guard = threading.Lock()
        calls = {}
        barrier = threading.Barrier(2)
        winner_created = threading.Event()
        errors = []

        def instrumented(name, flags, mode=0o777, *, dir_fd=None):
            me = threading.current_thread().name
            with guard:
                calls[me] = calls.get(me, 0) + 1
                first_caller = len(calls) == 1 and calls[me] == 1
            if first_caller:
                try:
                    return real_open(name, flags, mode, dir_fd=dir_fd)
                finally:
                    winner_created.set()
            winner_created.wait(5)
            if calls[me] == 1:
                raise FileNotFoundError(2, "No such file or directory", name)
            return real_open(name, flags, mode, dir_fd=dir_fd)

        def create():
            barrier.wait()
            try:
                os.close(open_shared("race.lock", FLAGS, 0o600, self.directory))
            except OSError as exc:
                errors.append(exc)

        with mock.patch.object(state.os, "open", instrumented):
            threads = [threading.Thread(target=create, name="creator-%d" % index)
                       for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(calls.values()), [1, 2])

    def test_concurrent_creators_all_open_the_file(self):
        """Supplementary stress on the real kernel; the losing-creator test proves the retry."""
        errors = []
        for trial in range(100):
            name = "race-%d.lock" % trial
            barrier = threading.Barrier(2)

            def create():
                barrier.wait()
                try:
                    os.close(open_shared(name, FLAGS, 0o600, self.directory))
                except OSError as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=create) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
