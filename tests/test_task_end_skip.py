# SPDX-License-Identifier: MIT
"""The handoff's task-end clear runs only where `/land` or `/close-out` step 7 reach it."""
import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / "primitives" / "workflows"


def step(name, number):
    text = (WORKFLOWS / name).read_text(encoding="utf-8").partition("\n---\n")[2]
    match = re.search(rf"^{number}\. (.+?)(?=^\d+\. |^\S|\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"no step {number} in {name}")
    return re.sub(r"\s+", " ", match.group(1))


class TaskEndSkipTest(unittest.TestCase):
    def test_close_out_step_5_and_a_plain_handoff_never_reach_the_clear(self):
        text = step("handoff.md", 4)
        skip = text.index("Skip it too in a plain `/handoff` and in `/close-out` step 5.")
        self.assertLess(skip, text.index("Run steps 1 and 2"))
        self.assertLess(skip, text.index("call `clear_session`"))
        self.assertTrue(text.startswith("**Task end, from `/land` or `/close-out` step 7.**"))

    def test_close_out_runs_the_full_handoff_at_step_5_and_the_task_end_step_only_at_step_7(self):
        self.assertIn("**Hand off** with the `handoff` workflow", step("close-out.md", 5))
        self.assertIn("task-end step, which skips the writes step 5 just ran",
                      step("close-out.md", 7))


if __name__ == "__main__":
    unittest.main()
