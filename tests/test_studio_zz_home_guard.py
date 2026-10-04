"""No Studio test reads the user's real home (#1211).

`isolation` installs an audit hook that records every path a Studio test (any frame from a
`test_studio*.py` file) opens or lists under the account's real home, outside this checkout, its
Git directory and the interpreter. A registration test once discovered evaluator packs beside the
checkout and so read `~/repos/model-citizen-evals`, failing whenever that repository was broken.
`unittest discover` loads modules in name order, so this module runs after every other Studio
test in the same process and fails on any record they left; run alone, it still proves the hook
catches a real-home read and that a fixture read is not one.

Run: python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolation  # noqa: E402
from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from harness_core.studio import packs  # noqa: E402


class StudioHomeGuardTests(unittest.TestCase):
    def test_no_studio_test_read_the_real_home(self):
        touches = sorted(set(isolation.STUDIO_HOME_TOUCHES))
        self.assertEqual(touches, [], "Studio tests read the real home outside their fixtures; "
                                      "build a fixture instead")

    def test_the_hook_catches_a_real_home_read_and_ignores_a_fixture(self):
        before = len(isolation.STUDIO_HOME_TOUCHES)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.listdir(tmp)
                (Path(tmp) / "fixture.json").write_text("{}", encoding="utf-8")
            self.assertEqual(isolation.STUDIO_HOME_TOUCHES[before:], [])
            os.listdir(isolation.REAL_HOME)
            self.assertEqual(isolation.STUDIO_HOME_TOUCHES[before:],
                             [("test_studio_zz_home_guard.py", "os.listdir", isolation.REAL_HOME)])
        finally:
            del isolation.STUDIO_HOME_TOUCHES[before:]

    def test_pack_discovery_beside_a_checkout_under_the_real_home_is_caught(self):
        # The shape of the original leak: discovery lists the folders beside the checkout.
        parent = os.path.dirname(isolation.CHECKOUT)
        if not isolation.outside_fixtures(parent):
            self.skipTest("this checkout's parent is outside the real home")
        before = len(isolation.STUDIO_HOME_TOUCHES)
        try:
            packs.candidates(Path(isolation.CHECKOUT))
            self.assertIn(parent, [os.path.abspath(path) for _test, _event, path
                                   in isolation.STUDIO_HOME_TOUCHES[before:]])
        finally:
            del isolation.STUDIO_HOME_TOUCHES[before:]


if __name__ == "__main__":
    unittest.main()
