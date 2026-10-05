"""The guard that keeps Studio tests out of the user's real home works (#1211).

`isolation` fails any Studio test that opens, lists or stats a path under the account's real home
outside this checkout, its Git directory and the interpreter, on the test's own thread or one it
started, in any test order. A registration test once discovered evaluator packs beside the
checkout and so read `~/repos/model-citizen-evals`. Subprocess reads are not covered. These tests
prove each part of the guard bites, and clear their own deliberate records so they pass.

Run: python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolation  # noqa: E402
from test_harness import REPO  # noqa: E402,F401  (puts lib/ on the path)
from harness_core.studio import packs  # noqa: E402

HOME = os.path.realpath(isolation.REAL_HOME)
ME = os.path.basename(__file__)


class StudioHomeGuardTests(unittest.TestCase):
    def setUp(self):
        self.before = len(isolation.STUDIO_HOME_TOUCHES)

    def recorded(self):
        return isolation.STUDIO_HOME_TOUCHES[self.before:]

    def tearDown(self):
        del isolation.STUDIO_HOME_TOUCHES[self.before:]  # the deliberate reads, cleared

    def test_a_fixture_read_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.listdir(tmp)
            (Path(tmp) / "fixture.json").write_text("{}", encoding="utf-8")
            os.path.exists(os.path.join(tmp, "fixture.json"))
        os.path.isfile(os.path.join(isolation.CHECKOUT, "AGENTS.md"))
        self.assertEqual(self.recorded(), [])

    def test_a_listing_and_a_stat_probe_of_the_real_home_are_recorded(self):
        os.listdir(isolation.REAL_HOME)
        probe = os.path.join(isolation.REAL_HOME, ".studio-guard-probe")
        for check in (os.path.exists, os.path.isdir, os.path.isfile):
            check(probe)
        Path(probe).exists()
        events = {(test, event, path) for test, event, path in self.recorded()}
        self.assertIn((ME, "os.listdir", HOME), events)
        for event in ("os.path.exists", "os.path.isdir", "os.path.isfile"):
            self.assertIn((ME, event, os.path.join(HOME, ".studio-guard-probe")), events)

    def test_a_read_on_a_thread_the_test_started_is_attributed_to_it(self):
        # No frame from this file runs on either thread: the outer one only starts the inner
        # one, which only lists the home, so attribution must pass from thread to thread.
        inner = threading.Timer(0, os.listdir, args=(isolation.REAL_HOME,))
        outer = threading.Thread(target=inner.start)
        outer.start()
        outer.join()
        inner.join()
        self.assertIn((ME, "os.listdir", HOME), self.recorded())

    def test_pack_discovery_beside_a_checkout_under_the_real_home_is_recorded(self):
        parent = os.path.realpath(os.path.dirname(isolation.CHECKOUT))
        if not isolation.outside_fixtures(parent):
            self.skipTest("this checkout's parent is outside the real home")
        with mock.patch.dict(os.environ, {packs.PACKS_ENV: ""}):
            packs.candidates(Path(isolation.CHECKOUT))
        self.assertIn(parent, [path for _test, _event, path in self.recorded()])

    def test_a_studio_test_that_reads_the_real_home_fails_in_any_order(self):
        class Leaky(unittest.TestCase):
            def test_leak(self):
                os.listdir(isolation.REAL_HOME)

        Leaky.__module__ = __name__  # a Studio test module, as the guard reads it
        result = unittest.TestResult()
        Leaky("test_leak").run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("read the real home outside its fixtures", result.failures[0][1])
        self.assertIn(HOME, result.failures[0][1])

    def test_a_stat_of_the_environment_passes_but_reading_beside_the_checkout_does_not(self):
        parent = os.path.dirname(isolation.CHECKOUT)
        main = os.path.dirname(isolation._git_common_dir(isolation.CHECKOUT) or isolation.CHECKOUT)
        os.path.isdir(main)  # a registered working tree: an existence probe only
        os.lstat(parent)  # an ancestor of the checkout
        self.assertEqual(self.recorded(), [])
        absent = os.path.join(parent, ".studio-guard-absent")
        with self.assertRaises(OSError):
            open(absent, "rb").close()
        self.assertIn((ME, "open", os.path.realpath(absent)), self.recorded())

    def test_no_allowed_root_holds_the_home_itself(self):
        for root in isolation.ALLOWED_ROOTS:
            self.assertFalse(HOME == root or HOME.startswith(root.rstrip(os.sep) + os.sep), root)


if __name__ == "__main__":
    unittest.main()
