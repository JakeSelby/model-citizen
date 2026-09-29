# SPDX-License-Identifier: MIT
"""A brief that tells the subagent to follow a framework's layer prompt file is that layer's work,
however the client worded it (#739). The 0.13.0 qualification observed a model write such a brief
itself: it kept the prompt file and quoted none of the descriptor's sentences, and it ran unconfined.
A brief that edits the file, or reads it for another reason, still runs.
Run: python3 -m unittest discover tests
"""
import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from test_harness import REPO  # also puts lib/ on the path
from harness_core import frameworks, lifecycle

PROMPT = "_bmad/review-prompts/edge-case-hunter.md"
# The brief the model wrote in the 0.13.0 round, from the macOS evidence, trimmed at its tail.
OBSERVED = (
    "We're running a BMad Method multi-layer code review on a file called calc.py, located in the "
    "current project directory. You are acting as the \"edge-case-hunter\" review layer of that "
    "process. Before doing anything else, read the review instructions for this layer from "
    + PROMPT + " (in the current project directory) and follow them exactly — that file "
    "defines your review methodology, focus areas, and expected output format for this layer. Then "
    "read calc.py in the current project directory and perform the edge-case-hunter review on it "
    "per those instructions. Report back your findings."
)
DIRECTED = {
    "apply": "Read the review-layer instructions at " + PROMPT + " and apply them to calc.py.",
    "next sentence": "The instructions live at " + PROMPT + ". Read them and follow them exactly.",
    "per": "Review calc.py per the checklist in " + PROMPT + ".",
    "as instructed": "Go over calc.py as instructed in " + PROMPT + ", then report.",
    "your instructions": "Your review instructions are in " + PROMPT + ": read that file first.",
    "adhere": "Check calc.py, adhering to " + PROMPT + " throughout.",
    "negated edit": "Follow " + PROMPT + " on calc.py, and do not edit it.",
    "another layer": "Apply review-prompts/blind-hunter.md to the diff in /tmp/change.patch.",
    "as your instructions": "Use " + PROMPT + " as your review instructions for calc.py.",
    # An edit verb that governs something else leaves the directive standing.
    "update your findings": "Follow " + PROMPT + " on calc.py and update your findings as you go.",
    "edit calc.py": "Read " + PROMPT + " and apply it; then edit calc.py to fix what you find.",
    "at the sentence end": "Here is your task. Review calc.py and follow " + PROMPT + ".",
    "as an absolute path": "Follow /work/project/" + PROMPT + " on calc.py.",
    "as your instructions in": "Use " + PROMPT + " as your instructions in this review of calc.py.",
    "two sentences on": ("Read " + PROMPT + ". These instructions define the layer's scope. "
                         "Follow them precisely."),
    "qualified anaphor": (
        "Review calc.py following the BMad Method edge-case-hunter layer instructions. First, "
        "read the review instructions at " + PROMPT + " to understand how to conduct it. Then, "
        "read calc.py and apply those edge-case-hunter review instructions to it."
    ),
    "qualified directive before prefixed path": (
        "Review calc.py following the edge-case-hunter review layer instructions in _bmad/"
        "review-prompts/edge-case-hunter.md."
    ),
    "em dash explanation": (
        "Before doing anything else, read the file `" + PROMPT + "` in the current working "
        "directory — it contains this layer's actual review instructions (what to look for, "
        "how to prioritize, and expected output format). Follow those instructions exactly."
    ),
    "long explanation": ("Read " + PROMPT + " — it contains " + "detailed " * 30
                         + "review instructions. Follow them precisely."),
    "directions": ("Read " + PROMPT + " — it contains the directions for this review. "
                   "Follow them precisely."),
}
UNDIRECTED = {
    "edit": ("Edit templates/bmad/custom/bmad-code-review.user.toml so the edge-case layer points "
             "at review-prompts/edge-case-hunter.md rather than the old path, then run harness "
             "bmad check."),
    "update that mentions follow": ("Update " + PROMPT + " so it tells the reviewer to follow the "
                                    "house style."),
    "summarise": "Read " + PROMPT + " and summarise what it asks for in three bullets.",
    "unrelated next sentence": ("Summarise " + PROMPT + " in three bullets. Follow the "
                                "repository's commit convention."),
    "edit then apply": "Reword " + PROMPT + ". Then apply it to the other layers as well.",
    "third person": "Tell me whether a reviewer who follows " + PROMPT + " would miss overflow.",
    "follow elsewhere": ("Count the lines of " + PROMPT + ". Then run the gate and report the "
                         "count."),
    # A directive that is negated, or that governs something other than the file, is not one.
    "negated next sentence": "Read " + PROMPT + ". Do not follow it; just list its headings.",
    "according to something else": ("Check whether " + PROMPT + " is formatted according to "
                                    "the house template."),
    "your checklist after the file": ("Read " + PROMPT + " and tell me if it matches your review "
                                      "checklist."),
    "use it as a fixture": "Copy " + PROMPT + " to /tmp/x.md. Then use it as a fixture in a test.",
    "per something else": "Explain what " + PROMPT + " does per the docs.",
    "directive in another clause": ("Follow the commit convention, and summarise " + PROMPT
                                    + "."),
    "a chain that changes subject": ("Read " + PROMPT + ". The gate is slow today. Follow them "
                                     "anyway."),
    "an unrelated it clause": ("Read " + PROMPT + " — it is used as a fixture in this test. "
                                "Follow those instructions."),
    "negated guidance": ("Read " + PROMPT + " — it contains no review instructions. Follow "
                         "those instructions."),
    "another instruction owner": ("Read " + PROMPT + " — when processing it, our review "
                                    "instructions require logging. Follow those instructions."),
    "guidance after another clause": ("Read " + PROMPT + " — it is used as a fixture, and our "
                                       "review instructions require logging. Follow those "
                                       "instructions."),
    "edit that governs the file": ("Update the wording of " + PROMPT + " so reviewers follow it "
                                   "more easily."),
    # A longer file name that starts with the declared path is another file.
    "a longer file name": "Follow " + PROMPT + ".bak on calc.py.",
    "a longer directory name": "Follow " + PROMPT + "-notes/checklist on calc.py.",
    # "The instructions" that say where they live are another file's.
    "another file's instructions": ("Read " + PROMPT + " and follow the instructions in "
                                    "docs/other.md."),
    "another file's instructions next": ("Read " + PROMPT + ". Then follow the instructions in "
                                         "docs/other.md."),
}


class DirectedIdentifierTests(unittest.TestCase):
    def classify(self, text):
        return frameworks.classify(text, None)

    def test_the_brief_the_model_wrote_live_is_the_review_layer(self):
        match = self.classify(OBSERVED)
        self.assertIsNotNone(match)
        self.assertEqual((match["spawn"], match["role"]), ("code-review-layer", "reviewer"))

    def test_it_carries_none_of_the_descriptor_sentences(self):
        # What makes this the regression: the classifier had only the prompt file to go on.
        spawn = frameworks.descriptors()[0]["spawns"][0]
        text = frameworks.normalise(OBSERVED)
        self.assertEqual(frameworks._score(spawn, text, "")[3], 0)
        self.assertEqual(frameworks._score(spawn, text, "")[1], 1)

    def test_every_brief_a_model_wrote_in_a_qualification_round_is_the_review_layer(self):
        # The 0.13.x rounds recorded four model-written briefs, one per platform and release. The
        # macOS 0.13.1 one puts its "follow them precisely" two sentences after the prompt file.
        briefs = {}
        for name in ("linux-0.13.0", "linux-0.13.1", "macos-0.13.0", "macos-0.13.1"):
            path = os.path.join(REPO, "compatibility", "evidence", "claude-code-cli-%s.json" % name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            found = re.search(r'the model wrote its own brief \\"(.*?)\\"; it was', text)
            self.assertIsNotNone(found, msg=name)
            briefs[name] = json.loads('"%s"' % found.group(1))
        for name, brief in briefs.items():
            with self.subTest(name=name):
                match = self.classify(brief)
                self.assertIsNotNone(match, msg=brief)
                self.assertEqual(match["spawn"], "code-review-layer")

    def test_a_directive_in_any_wording_is_recognised(self):
        for name, brief in DIRECTED.items():
            with self.subTest(name=name):
                match = self.classify(brief)
                self.assertIsNotNone(match, msg=brief)
                self.assertEqual(match["role"], "reviewer")

    def test_a_brief_that_edits_or_merely_reads_the_file_is_not(self):
        for name, brief in UNDIRECTED.items():
            with self.subTest(name=name):
                self.assertIsNone(self.classify(brief), msg=brief)

    def test_a_directive_with_no_declared_prompt_file_is_not(self):
        self.assertIsNone(self.classify("Follow the review checklist in docs/review.md on calc.py."))


class DirectedRefusalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HOME": temp.name, "PATH": os.environ["PATH"],
                                "HARNESS_STANCE_DELEGATION": "tiered"}, clear=True).start()

    def spawn(self, prompt, session):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": session,
                   "tool_input": {"prompt": prompt}}
        return lifecycle.dispatch("claude-code", payload).get("hookSpecificOutput", {})

    def test_the_refusal_is_the_one_the_framework_s_own_text_gets(self):
        spawn = frameworks.descriptors()[0]["spawns"][0]
        own = self.spawn(" ".join(spawn["phrases"]), "own")
        reworded = self.spawn(OBSERVED, "reworded")
        self.assertEqual(reworded.get("permissionDecision"), "deny")
        self.assertEqual(reworded.get("permissionDecisionReason"),
                         own.get("permissionDecisionReason"))
        self.assertIn("harness role run reviewer", reworded["permissionDecisionReason"])

    def test_an_edit_of_the_prompt_file_still_runs(self):
        answer = self.spawn(UNDIRECTED["update that mentions follow"], "edit")
        self.assertNotEqual(answer.get("permissionDecision"), "deny")


if __name__ == "__main__":
    unittest.main()
