# SPDX-License-Identifier: MIT
"""The suite's presence stub (`isolation.stub_presence`) outlives a test's `mock.patch.stopall`.

Several suites clean up with `patch.stopall`; when the stub was a started patch, that call
switched it off, and every later Studio spend or apply test was refused with a 403.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import isolation  # noqa: E402
from harness_core import presence  # noqa: E402


class PresenceStubTests(unittest.TestCase):
    def test_stopall_leaves_the_suite_stub_in_place(self):
        unrelated = mock.patch.object(presence, "REASON_LIMIT", 1)
        unrelated.start()
        mock.patch.stopall()
        self.assertEqual(presence.REASON_LIMIT, 480)
        self.assertIsInstance(presence.confirm, mock.MagicMock)
        self.assertTrue(callable(isolation.PRESENCE.temp_original))
        self.assertTrue(presence.confirm("a paid run"))


if __name__ == "__main__":
    unittest.main()
