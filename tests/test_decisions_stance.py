# SPDX-License-Identifier: MIT
"""Unit tests for the decisions stance, the decision-page skill and what the decisions rule keeps.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

DECISIONS = REPO / "primitives" / "stances" / "decisions"
RULE = REPO / "primitives" / "rules" / "decisions-and-plans.md"
SKILL = REPO / "primitives" / "skills" / "decision-page"
PLAN_SKILL = REPO / "primitives" / "skills" / "plan-authoring" / "SKILL.md"
REVIEW_CARD = REPO / "primitives" / "stances" / "plan-ceremony" / "review-card.md"
CHOOSER_LINE = ("- **No chooser widget for substantive decisions.** Flag the ask in the reply's first "
                "two lines; the numbered decision block may still close the message. Batch them.")


def flat(text):
    return " ".join(text.split())


def load_posture():
    path = REPO / "policy" / "hooks" / "posture.py"
    mod_spec = importlib.util.spec_from_file_location("decisions_posture", path)
    module = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(module)
    return module


class TempHome(unittest.TestCase):
    """A home with no real config, so a machine's own selection cannot mask the default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def run_cli(self, *args):
        env = without_harness_vars()
        env["HOME"] = self.tmp.name
        return subprocess.run([sys.executable, str(REPO / "bin" / "harness")] + list(args),
                              capture_output=True, text=True, env=env)


class DecisionsStanceTests(TempHome):
    def test_dimension_is_registered_with_two_variants(self):
        self.assertIn("decisions", harness.STANCE_NAMES)
        self.assertEqual(sorted(p.stem for p in DECISIONS.glob("*.md")),
                         ["decision-page", "numbered-block"])

    def test_default_is_the_numbered_block_everywhere_a_default_is_named(self):
        cfg = harness.load_config(env={})
        self.assertEqual(harness.resolve_stances(cfg)["decisions"], DECISIONS / "numbered-block.md")
        example = json.loads((REPO / "config.example.json").read_text())
        self.assertEqual(example["stances"]["decisions"], "numbered-block")
        self.assertEqual(load_posture().DEFAULT_STANCES["decisions"], "numbered-block")

    def test_env_override_selects_the_decision_page(self):
        cfg = harness.load_config(env={"HARNESS_STANCE_DECISIONS": "decision-page"})
        self.assertEqual(harness.resolve_stances(cfg)["decisions"], DECISIONS / "decision-page.md")

    def test_config_set_accepts_the_decision_page_and_refuses_an_unknown_variant(self):
        ok = self.run_cli("config", "set", "stances.decisions", "decision-page")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        got = self.run_cli("config", "get", "stances.decisions")
        self.assertEqual(got.stdout.strip(), "decision-page", got.stderr)
        bad = self.run_cli("config", "set", "stances.decisions", "chooser")
        self.assertNotEqual(bad.returncode, 0)

    def test_both_adapters_declare_the_dimension(self):
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime):
                caps = json.loads((REPO / "adapters" / runtime / "capabilities.json").read_text())
                self.assertEqual(caps["stances"]["decisions"]["mode"], "instruction")


class RuleMoveTests(unittest.TestCase):
    def test_the_chooser_line_moved_word_for_word_into_the_default_variant(self):
        self.assertIn(CHOOSER_LINE, flat((DECISIONS / "numbered-block.md").read_text()))
        self.assertNotIn("chooser widget", RULE.read_text())

    def test_the_rule_keeps_its_other_lines(self):
        rule = flat(RULE.read_text())
        for line in ("**Pointing at an option is not approval** — put it in the review artifact; "
                     "build only on an explicit \"build\" or \"go with N\".",
                     "**Once the user has approved a multi-step plan, do not re-ask at each step**",
                     "**Re-surface for confirmation** when an error or unexpected state makes a "
                     "step unsafe, a step was not in the plan, or the blast radius grew."):
            with self.subTest(line=line[:40]):
                self.assertIn(line, rule)


class DecisionPageVariantTests(unittest.TestCase):
    def test_the_variant_carries_each_requirement(self):
        text = flat((DECISIONS / "decision-page.md").read_text())
        for phrase in ("`decision-page` skill", "never a typed-answer block", "first two lines",
                       "two to four options", "recommended one first", "free-text field",
                       "accepts every recommendation", "Never collect a secret",
                       "only when the user says so", "no go-ahead to build",
                       "No artifact tool: numbered blocks, and say so."):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class DecisionPageSkillTests(unittest.TestCase):
    def setUp(self):
        self.skill = (SKILL / "SKILL.md").read_text()
        self.template = (SKILL / "template.html").read_text()

    def test_the_skill_documents_the_store_contract(self):
        for phrase in ("`content/<doc id>`", "`answers/<item id>`", "\"choice\"", "\"text\"",
                       "\"read\": \"owner\", \"write\": \"owner\"", "Decision page: <url>",
                       ".agent-harness/progress.md"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_the_template_is_generic(self):
        self.assertEqual(re.search(r"<title>(.*?)</title>", self.template).group(1), "Decisions")
        for project_word in ("Gold", "Learning", "graph", "Highway", "fonts.googleapis"):
            with self.subTest(word=project_word):
                self.assertNotIn(project_word, self.template)

    def test_the_template_reads_and_writes_the_documented_paths(self):
        self.assertIn("db.collection('content')", self.template)
        self.assertIn("db.collection('answers')", self.template)
        self.assertIn("db.doc('answers/'+id).set({choice:", self.template)

    def test_the_template_never_renders_store_data_as_markup(self):
        self.assertNotIn("innerHTML", self.template)

    def test_a_secret_item_has_no_input(self):
        body = self.template.split("function secretCard", 1)[1].split("function choiceCard", 1)[0]
        self.assertNotIn("textarea", body)
        self.assertNotIn("input", body)


class PlanAnsweringPathTests(unittest.TestCase):
    def test_the_typed_reply_is_the_default_variants_path_only(self):
        text = flat(PLAN_SKILL.read_text())
        self.assertIn("Under the default `numbered-block` decisions stance the reviewer answers "
                      "them in chat by number (\"1 A, 2 your rec\"); under `decision-page` they "
                      "stay numbered here for reading and are answered on the decision page.", text)

    def test_the_review_card_stance_links_the_page_under_the_variant(self):
        self.assertIn("decisions verbatim or linked", flat(REVIEW_CARD.read_text()))
        self.assertIn("A Review Card keeps its decisions numbered; its message links the page.",
                      flat((DECISIONS / "decision-page.md").read_text()))


if __name__ == "__main__":
    unittest.main()
