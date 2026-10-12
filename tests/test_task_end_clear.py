# SPDX-License-Identifier: MIT
"""`/land` and `/close-out` refresh the handoff at task end, then clear only an idle desktop session."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / "primitives" / "workflows"
COMMANDS = REPO / "claude" / "commands"
OPEN_ITEMS = ("review thread", "pending approval", "release card", "unmerged pull request",
              "refused or dirty worktree", "stale checkout", "parked decision", "unanswered question",
              "mid-task user work")


def body(path):
    return path.read_text(encoding="utf-8").partition("\n---\n")[2]


def flat(text):
    return re.sub(r"\s+", " ", text)


def step(text, number):
    """The flattened text of one numbered step."""
    match = re.search(rf"^{number}\. (.+?)(?=^\d+\. |^\S|\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"no step {number}")
    return flat(match.group(1))


def clause(text, start, end):
    """The text from `start` up to, not including, `end`."""
    i = text.index(start)
    return text[i:text.index(end, i)]


class TaskEndClearTest(unittest.TestCase):
    def task_end(self):
        return step(body(WORKFLOWS / "handoff.md"), 4)

    def test_handoff_task_end_is_skipped_inside_close_out_before_any_write_or_clear(self):
        text = self.task_end()
        skip = text.index("Skip it in a `/land` run by `/close-out`, from any step.")
        self.assertLess(skip, text.index("Run steps 1 and 2"))
        self.assertLess(skip, text.index("call `clear_session`"))

    def test_handoff_task_end_clears_only_after_successful_writes_and_zero_open_items(self):
        text = self.task_end()
        gate = clause(text, "then clear only if all hold:", "Say in one line")
        self.assertLess(text.index("Run steps 1 and 2"), text.index(gate))
        self.assertIn("a Claude desktop session (`clear_session` available) inside a repository", gate)
        self.assertIn("both writes succeeded (`task save` exited 0)", gate)
        self.assertIn("zero open items", gate)
        for item in OPEN_ITEMS + ("`/close-out` archive",):
            self.assertIn(item, gate, msg=item)
        self.assertLess(text.index(gate), text.index("call `clear_session`"))
        self.assertIn("the context will be cleared and the handoff reseeds the next prompt, then call "
                      "`clear_session` with session_id `self` as the turn's final action", text)
        self.assertIn("nothing from before carries over", text)
        self.assertIn("a queued user message cancels it", text)
        self.assertIn("unless auto or bypass mode is on", text)
        self.assertIn("Otherwise, CLI and Codex included, say why in one line and stop", text)
        self.assertIn("before any step 4 clear", flat(body(WORKFLOWS / "handoff.md")))

    def test_only_handoff_holds_the_clear_mechanics(self):
        for name in ("land.md", "close-out.md"):
            self.assertNotIn("clear_session", body(WORKFLOWS / name), msg=name)

    def test_land_ends_with_the_task_end_step_and_skips_it_inside_close_out(self):
        text = step(body(WORKFLOWS / "land.md"), 8)
        self.assertIn("End with the `handoff` workflow's task-end step", text)
        self.assertIn("clear only if nothing is open", text)
        self.assertIn("Skip it when `/close-out` runs this command, from any of its steps", text)

    def test_land_keeps_its_removal_audit_and_release_detail(self):
        text = body(WORKFLOWS / "land.md")
        self.assertIn("(the default branch) probes", flat(text))
        self.assertIn("`citizen usage --conflicts`", text)
        self.assertIn("on its default branch with `git pull --ff-only`", flat(text))
        removal = step(text, 5)
        self.assertIn("stop the workflow with the reason and the path, so the work can be read first", removal)
        self.assertIn("`--also-clear <name>` for a regenerable top-level directory", removal)
        self.assertIn("what each still holds", step(text, 6))
        self.assertIn("the version, what landed since the last one, the surfaces", step(text, 7))

    def test_close_out_owns_the_handoff_for_every_land_it_runs(self):
        text = step(body(WORKFLOWS / "close-out.md"), 5)
        self.assertIn("with the `handoff` workflow", text)
        self.assertIn("every `land` run in steps 3 and 4 skips its own handoff and task-end step", text)

    def test_close_out_reaches_the_task_end_step_only_on_the_nothing_open_path(self):
        text = step(body(WORKFLOWS / "close-out.md"), 7)
        archive = clause(text, "Archive when", "With anything open")
        wait = clause(text, "With anything open", "With nothing open")
        self.assertIn("end on the checklist and wait.", wait)
        for branch in (archive, wait):
            self.assertNotIn("task-end", branch)
        path = clause(text, "With nothing open and no archive, inside a repository,", "Outside a repository")
        self.assertIn("end with the `handoff` workflow's task-end step", path)
        self.assertIn("Outside a repository there is no clear and no reseed", text)

    def test_the_command_projections_carry_the_same_steps(self):
        for name, number in (("land.md", 8), ("close-out.md", 7), ("handoff.md", 4)):
            self.assertEqual(step(body(COMMANDS / name), number), step(body(WORKFLOWS / name), number))

    def test_the_projections_match_the_workflows(self):
        result = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "generate", "--check"],
                                cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        result = subprocess.run([sys.executable, str(REPO / "scripts" / "sync_directory_plugin.py"), "--check"],
                                cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
