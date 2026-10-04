# SPDX-License-Identifier: MIT
"""Every trigger phrase a listed description existed to match survives a trim of that
description. Run: python3 -m unittest discover tests"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "primitives" / "skills"
ROLES = REPO / "primitives" / "roles"

# Frozen from the descriptions as they stood at 0.12.0, before #430 shortened them. A description
# is the whole of what a session sees until the skill or agent is invoked, so these are the
# substrings the selection is actually made on: the conditions and the literal user phrasings.
# Shorten the prose around them freely; removing one is a behaviour change, not an edit.
SKILL_TRIGGERS = {
    "api-verification": ("unfamiliar API", "suspiciously clean results", "swallowed",
                         "briefing research agents"),
    "architecture-viewer": ("inspect architecture interactively", "diagram"),
    "code-quality-instruments": ("Measure", "Use when", "adding or reviewing tests",
                                 "a bug still shipped", "coverage is high and confidence is not",
                                 "well tested", "mutation score", "branch coverage"),
    "delegation-tiering": ("planning a fan-out", "choosing a subagent model", "opts.model",
                           "authoring an agent definition", "cost posture",
                           "delegation decision is non-obvious"),
    "design-loop": ("look dramatically better, more polished or more professional",
                    "visual design, look, styling or visual quality",
                    "reference, mockup or screenshot", "user-facing surface",
                    "UI, page, HTML doc, dashboard, game scene or 3D asset",
                    "Not for deciding what screens exist"),
    "harness-authoring": ("add, change or remove a rule, skill, instruction, hook, setting or "
                          "CLAUDE.md line", "\"remember\"", "persist beyond this session",
                          "apply to future sessions"),
    "licensing-review": ("third-party library, asset, model, font, dataset or copied snippet",
                         "adding or upgrading any dependency", "downloading any asset",
                         "before a release"),
    "migration-safety": ("database schema migration", "Alembic, Prisma, Django, Rails, Flyway, "
                         "raw SQL", "shared environment"),
    "plan-authoring": ("plan file, proposal, design doc or handoff", "Review Card",
                       ".agent-harness/plans/", "go/no-go"),
    "sandbox": ("Use ", "unattended loop", "`execute` autonomy", "untrusted input",
                "network off", "container"),
    "spike-contract": ("\"spike\"", "\"prototype to find out\"", "\"de-risk\"",
                       "\"check whether X is feasible\"", "numeric exit criterion",
                       "spikes section of a plan or README"),
    "studio-loop": ("tune, try out or measure a rule, stance, setting or module change",
                    "roll an apply back", "wants to see a draft, a review or a comparison"),
    "transcript-hygiene": ("briefing a subagent", "reading large output"),
    "upstream-contribution": ("repository you do not own", "`upstream` remote",
                              "\"open a PR against <someone else's repo>\"",
                              "first commit in any repo the user is a guest in"),
    "workflow-status": ("workflow progress", "\"/workflows doesn't work\"",
                        "\"is the workflow done\"", "\"how's the workflow going\"",
                        "\"check the workflow\"", "multi-agent orchestration run"),
    "worktree-per-agent": ("git worktree", "start of any implementation task",
                           "\"work in a worktree\""),
}

ROLE_TRIGGERS = {
    "builder": ("one issue or approved plan", "git worktree", "Never pushes",
                "never opens a pull request"),
    "design-judge": ("render or screenshot", "rubric", "Never edits", "each round of the design "
                     "loop", "did not build the thing"),
    "designer": ("locked target", "capture paths", "Never scores its own work",
                 "build and fix steps of the design loop"),
    "gatherer": ("Read-only", "Offline", "400 words", "grep fan-outs",
                 "bulk read-and-summarize", "doc lookups"),
    "log-compressor": ("test, build or CI log", "150 words", "exit"),
    "planner": ("Review Card", "Never implements anything"),
    "reviewer": ("Fresh-context adversarial review", "Findings only",
                 "never saw the work being written"),
    "spec-reviewer": ("the issue, the plan or the pull request body", "scope deviations only",
                      "work nobody asked for", "acceptance criteria the diff does not prove",
                      "`reviewer`"),
    "worker-a": ("Band A", "grep fan-out", "log compression", "worker-b or worker-c"),
    "worker-b": ("Band B", "two-tool chain", "already-decided plan",
                 "steps are known in advance", "worker-c"),
    "worker-c": ("Band C", "branching on intermediate results", "multi-source synthesis",
                 "long-horizon coding", "never safe to down-class"),
}


def description(path):
    """The frontmatter description, folded lines joined, as a session would list it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    end = lines.index("---", 1)
    out, taking = [], False
    for line in lines[1:end]:
        if line.startswith("description:"):
            out.append(line.split(":", 1)[1].strip())
            taking = True
        elif taking and line[:1] in (" ", "\t"):
            out.append(line.strip())
        else:
            taking = False
    return " ".join(out)


class TriggerPhraseTests(unittest.TestCase):
    def test_every_skill_description_keeps_its_trigger_phrases(self):
        for name, phrases in SKILL_TRIGGERS.items():
            text = description(SKILLS / name / "SKILL.md")
            for phrase in phrases:
                with self.subTest(skill=name, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_every_role_description_keeps_its_trigger_phrases(self):
        for name, phrases in ROLE_TRIGGERS.items():
            text = description(ROLES / (name + ".md"))
            for phrase in phrases:
                with self.subTest(role=name, phrase=phrase):
                    self.assertIn(phrase, text)

    def test_the_frozen_set_covers_every_skill_and_role(self):
        # A new primitive declares the phrases it is selected on, or this check protects nothing.
        self.assertEqual(sorted(p.name for p in SKILLS.iterdir() if p.is_dir()),
                         sorted(SKILL_TRIGGERS))
        self.assertEqual(sorted(p.stem for p in ROLES.glob("*.md")), sorted(ROLE_TRIGGERS))

    def test_a_trimmed_description_still_says_when_to_use_it(self):
        for folder, names, suffix in ((SKILLS, SKILL_TRIGGERS, "/SKILL.md"),
                                      (ROLES, ROLE_TRIGGERS, ".md")):
            for name in names:
                path = folder / (name + suffix) if suffix == ".md" else folder / name / "SKILL.md"
                with self.subTest(primitive=name):
                    self.assertTrue(description(path))
                    self.assertLessEqual(len(description(path)), 620)


if __name__ == "__main__":
    unittest.main()
