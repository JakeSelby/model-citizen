# SPDX-License-Identifier: MIT
"""Plan mode owns the review surface: the pane renders the plan and its approval is the gate.

Three things have to hold together. The validator keys off the plans directory and not the
filename, because plan mode names the file itself — a slug of the opening words plus two random
words, fixed before any content exists. The workflows say so: `plan` asks to enter plan mode,
ends at `ExitPlanMode`, and only then renames the file and hands `build` the path, while `build`
never goes looking for one. And the stance, which is always-loaded, cannot grow while saying it.

Run: python3 -m unittest discover tests
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "validate-plan-card.py"
STANCE = REPO / "primitives" / "stances" / "plan-ceremony" / "review-card.md"
PLAN = REPO / "claude" / "commands" / "plan.md"
BUILD = REPO / "claude" / "commands" / "build.md"
SKILL = REPO / "claude" / "skills" / "plan-authoring" / "SKILL.md"

# What a live `claude --permission-mode plan` run produced: kebab-case opening words, two random
# words, and no relation to the plan's own title.
RUNTIME_NAME = "two-facts-for-a-quiet-willow.md"

# The stance is resident in every session, so its replacement may not be longer than the text it
# replaced — in characters, since a line count hides a line that grew. 742 is what the whole
# stance measured when the gate was still a typed reply.
STANCE_CHARS = 742

CARD = """# A title

> One sentence on what this builds, and one on the mechanism.
> Effort: an hour · Risk: low · Blast radius: one file.

## At a glance

- **Outcome** — the thing exists.

## System design

```text
  a ──▶ b
```

The drawing.

## Steps

1. **Do the thing** — `some/file.py`.
   *Exit:* the test passes.

## Decisions for the reviewer

None — approve to proceed.

## Risks

- **It breaks** — revert it.

---

# Addendum

Detail nobody reviewing needs.
"""


def flat(path):
    """The file as one whitespace-normalised line, so a wrapped sentence still matches."""
    return " ".join(path.read_text(encoding="utf-8").split())


def validate(name, directory, card=CARD):
    """Run the hook over a plan file and return its problem list, empty when it stayed silent."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(card, encoding="utf-8")
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path)},
                   "tool_response": {"filePath": str(path)}}
        out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                             capture_output=True, text=True).stdout.strip()
    if not out:
        return []
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    return [line[2:] for line in context.splitlines() if line.startswith("- ")]


class ValidatorIgnoresTheFilenameTests(unittest.TestCase):
    def test_a_runtime_named_card_that_meets_the_contract_passes(self):
        for directory in (".agent-harness/plans", ".claude/plans"):
            self.assertEqual(validate(RUNTIME_NAME, directory), [], msg=directory)

    def test_a_runtime_named_card_is_still_checked(self):
        """Filename-insensitive means unchecked nowhere, not unchecked here."""
        broken = CARD.replace("## At a glance", "## Context").replace("*Exit:*", "| Exit |")
        problems = validate(RUNTIME_NAME, ".agent-harness/plans", card=broken)
        self.assertTrue(any("## At a glance" in p for p in problems), problems)
        self.assertTrue(any("## Context" in p for p in problems), problems)
        self.assertTrue(any("table lines" in p for p in problems), problems)

    def test_the_same_names_outside_a_plans_directory_are_not_plan_files(self):
        broken = CARD.replace("## Steps", "## Context")
        for directory in ("docs", ".agent-harness"):
            self.assertEqual(validate(RUNTIME_NAME, directory, card=broken), [], msg=directory)
            self.assertEqual(validate("plan.md", directory, card=broken), [], msg=directory)


class StanceStaysWithinWhatItReplacedTests(unittest.TestCase):
    def test_the_stance_is_no_longer_than_the_text_it_replaced(self):
        text = STANCE.read_text(encoding="utf-8")
        self.assertLessEqual(len(text), STANCE_CHARS)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 100, msg=line)

    def test_the_stance_names_the_native_gate_and_the_fallback(self):
        text = STANCE.read_text(encoding="utf-8")
        self.assertIn("ExitPlanMode", text)
        self.assertIn("rename to a topic slug", text)
        self.assertIn("Reply **build** to proceed", text)


class WorkflowTextTests(unittest.TestCase):
    def test_plan_asks_before_entering_plan_mode(self):
        """Entering plan mode is the user's call, so the command prompts before it does anything."""
        body = flat(PLAN)
        self.assertIn("Ask before entering plan mode", body)
        self.assertLess(body.index("Ask before entering plan mode"), body.index("ExitPlanMode"))

    def test_plan_ends_at_the_native_approval_and_asks_for_no_typed_reply(self):
        body = flat(PLAN)
        self.assertIn("ExitPlanMode", body)
        self.assertNotIn("Reply **build**", body)

    def test_plan_names_the_file_after_approval_and_refuses_to_overwrite(self):
        """The agent that wrote the plan knows its path, so nothing downstream has to find it."""
        body = flat(PLAN)
        approval = body.index("Once approved")
        self.assertLess(body.index("ExitPlanMode"), approval)
        self.assertIn("never over an existing name", body[approval:])
        self.assertIn("`-2`", body[approval:])
        self.assertIn("/build <absolute path>", body[approval:])

    def test_plan_falls_through_to_the_written_file_when_plan_mode_is_declined(self):
        body = flat(PLAN)
        self.assertIn("No plan mode, or the user declines it", body)

    def test_the_planner_worker_runs_before_plan_mode_starts(self):
        """It writes an artifact, and plan mode permits no write but its own plan file."""
        body = flat(PLAN)
        self.assertLess(body.index("citizen role run planner"), body.index("entering plan mode"))

    def test_plan_keeps_the_no_plan_mode_branch_and_names_the_file_only_there(self):
        body = flat(PLAN)
        branch = body.index("**No plan mode:**")
        self.assertIn("named for the topic", body[branch:])
        self.assertNotIn("named for the topic", body[:branch])

    def test_build_is_given_the_plan_and_never_searches_for_one(self):
        body = flat(BUILD)
        self.assertIn("Work from the plan path or the issue number you were given", body)
        self.assertIn("Never search for a plan", body)
        self.assertIn("With neither path nor issue, ask for one", body)
        self.assertNotIn("newest", body)

    def test_the_builder_is_what_commits_the_plan_file(self):
        """A worktree carries no untracked file, so the plan reaches the PR only this way."""
        self.assertIn("copy the file into its worktree and commit it", flat(BUILD))
        self.assertIn("The builder commits it", flat(PLAN))

    def test_the_skill_offers_the_typed_build_line_only_without_plan_mode(self):
        text = flat(SKILL)
        start = 0
        seen = 0
        while True:
            at = text.find("Reply **build**", start)
            if at < 0:
                break
            seen += 1
            self.assertIn("no plan mode", text[max(0, at - 300):at + 200])
            start = at + 1
        # Two, and one of them wraps across a line break in the source — the reason this test
        # normalises whitespace before it searches.
        self.assertEqual(seen, 2)


if __name__ == "__main__":
    unittest.main()
