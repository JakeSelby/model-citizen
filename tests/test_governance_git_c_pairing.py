# SPDX-License-Identifier: MIT
"""Each unresolved `git -C` operand stays with its own push through governance.

`govern()` pairs operands with push entries in walk order before it drops grade-0 entries, so a
push the walk grades 0 still consumes its operand and never lends it to a later push.

Run: python3 -m unittest discover tests
"""
import unittest
from unittest import mock

from test_governance_binding import Home, grader


class GradeZeroPushPairing(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")
        self.user_policy({"defaults": {"coding.git_push": 2}})

    def test_a_grade_zero_push_keeps_its_operand_from_the_next_push(self):
        def walk(command, cwd, causes=None):
            causes.extend(["$ZERO", "$SECOND"])
            return [(grader.PUSH, 0, None, []), (grader.PUSH, 2, None, [])]

        with mock.patch.object(grader, "governed_text", walk):
            outcome, sentence = grader.govern("git -C $ZERO push; git -C $SECOND push",
                                              str(self.repo), 2, self.stance)
        self.assertEqual(outcome, "ask")
        self.assertIn("Git -C operand `$SECOND` could not be resolved", sentence)
        self.assertNotIn("$ZERO", sentence)


if __name__ == "__main__":
    unittest.main()
