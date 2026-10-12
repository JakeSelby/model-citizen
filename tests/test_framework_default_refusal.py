# SPDX-License-Identifier: MIT
"""A declared layer prompt file in an unnamed spawn's brief is refused by default (#739).

Recognising the adoption by its verbs leaked: of forty plain rewordings that tell a subagent to take
on the layer, thirty-five ran. A brief that edits the file, or only summarises, counts, copies,
explains or compares it, still runs. The directive rule stays for its precision, so its gap limit
and the qualified "those <words> instructions" branch are pinned here too.
Run: python3 -m unittest discover tests
"""
import unittest

from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from harness_core import frameworks

P = "_bmad/review-prompts/edge-case-hunter.md"
VALUE = "review-prompts/edge-case-hunter.md"
# Rewordings that adopt the file; before the default refusal, only five of them were refused.
REWORDED = [
    "Use the instructions in " + P + " to review calc.py.",
    "Using the checklist kept at " + P + ", review calc.py.",
    "Review calc.py using " + P + " as your guide.",
    "Load " + P + " and do what it says for calc.py.",
    "Read " + P + " and do exactly what it says.",
    "Read " + P + " and execute its instructions against calc.py.",
    "Read " + P + ". Then review calc.py the way it describes.",
    "Read " + P + " and review calc.py accordingly.",
    "Read " + P + " and conduct the review it describes on calc.py.",
    "Read " + P + ", which defines your task, then review calc.py.",
    "Your instructions are in " + P + ".",
    "The review prompt for your layer is " + P + "; read it and act on it.",
    "Read " + P + " first. Then review calc.py as it instructs.",
    "Read " + P + " and stick to it when reviewing calc.py.",
    "Read " + P + " carefully and then perform that review on calc.py.",
    "Read the edge-case-hunter prompt at " + P + " and run that review on calc.py.",
    "Read " + P + " and follow its instructions exactly.",
    "Read " + P + " and follow the steps it lays out.",
    "Read " + P + ". Follow its guidance when reviewing calc.py.",
    "Read " + P + " (your review instructions) before reviewing calc.py.",
    "Read " + P + ". Then review calc.py. Follow those instructions exactly.",
    "Read " + P + " then follow every instruction in it.",
    "Read " + P + " and adopt it as your methodology.",
    "Read " + P + " and treat it as your instructions.",
    "Read " + P + " and use it to review calc.py.",
    "Read " + P + " and apply its methodology to calc.py.",
    "Read " + P + " and follow the methodology it describes.",
    "Read " + P + ". It is your review prompt. Follow it.",
    "Read " + P + ". Then read calc.py. Then follow the instructions.",
    "First, read " + P + ". Second, read calc.py. Third, review calc.py following that prompt.",
    "Follow the instructions in the file " + P + ".",
    "Follow the review instructions from the BMad layer prompt " + P + ".",
    "Read " + P + " and do the review it asks for on calc.py.",
    "Run the edge-case-hunter review on calc.py, as defined in " + P + ".",
    "Perform the review defined in " + P + " on calc.py.",
    "Review calc.py using the methodology in " + P + ".",
    "Review calc.py with the edge-case-hunter rules in " + P + ".",
    "Read " + P + " and carry them out on calc.py.",
    "Read " + P + "; those are your instructions.",
    "Read " + P + " and act as the reviewer it describes.",
]
# The look has to be at the file, and at every mention of it.
NOT_A_LOOK = {
    "summarise something else": "Review calc.py using " + P + " and summarise your findings.",
    "a look then an adoption": "Summarise " + P + ". Then read " + P + " and do what it says.",
    "a negated look": "Do not summarise " + P + "; review calc.py with it.",
    "a look then do what it says": "Summarise " + P + " and do what it says.",
    "a look then do exactly what it says": "Summarise " + P + ". Then do exactly what it says.",
    "a read, a look, then do what it says": "Read " + P + ". Summarise it and do what it says.",
    "a read, a look, then a later adoption": "Read " + P + ". Summarise it. Then apply it.",
    "a read then a look at something else": "Read " + P + ". Summarise calc.py in three bullets.",
    "a read, a look, then another clause": "Read " + P + ". Summarise it, then review calc.py.",
    "a read then a look carried on": "Read " + P + ". Summarise it for calc.py accordingly.",
}
LOOKS = {
    "summarise it": "Read " + P + " and summarise it in three bullets.",
    "count": "Count the headings in " + P + " and reply with the number.",
    "copy": "Copy " + P + " to /tmp/edge.md and reply DONE.",
    "explain": "Explain to me what " + P + " asks a reviewer to look for.",
    "compare": "Compare " + P + " with review-prompts/blind-hunter.md and list the differences.",
    "edit": "Rewrite " + P + " so its headings are sentence case.",
    "a read then a look at it": "Read " + P + ". Summarise it in three bullets.",
}


class DefaultRefusalTests(unittest.TestCase):
    def classify(self, text):
        return frameworks.classify(text, None)

    def test_every_reworded_adoption_is_the_review_layer(self):
        for brief in REWORDED:
            with self.subTest(brief=brief):
                match = self.classify(brief)
                self.assertIsNotNone(match, msg=brief)
                self.assertEqual((match["spawn"], match["role"]), ("code-review-layer", "reviewer"))

    def test_a_look_at_the_file_or_an_edit_of_it_runs(self):
        for name, brief in LOOKS.items():
            with self.subTest(name=name):
                self.assertIsNone(self.classify(brief), msg=brief)

    def test_a_look_must_govern_every_mention_of_the_file(self):
        for name, brief in NOT_A_LOOK.items():
            with self.subTest(name=name):
                self.assertIsNotNone(self.classify(brief), msg=brief)

    def test_a_brief_naming_no_declared_prompt_file_runs(self):
        self.assertIsNone(self.classify("Read docs/review.md and do exactly what it says."))


class QualifierBindingTests(unittest.TestCase):
    """A qualified "the <words> instructions" means the declared file only when its words do."""

    def directed(self, text):
        return frameworks._directed(VALUE, frameworks.normalise(text))

    def test_a_qualified_pointer_back_with_the_file_s_words_is_a_directive(self):
        self.assertTrue(self.directed("Read " + P + " and apply those edge-case-hunter "
                                      "instructions to calc.py."))

    def test_a_qualified_pointer_back_with_other_words_is_not(self):
        self.assertFalse(self.directed("Read " + P + " and apply the house-style instructions "
                                       "to calc.py."))

    def test_a_qualified_anaphor_with_the_file_s_words_carries_the_chain(self):
        self.assertTrue(self.directed("Read " + P + ". Those edge-case-hunter review "
                                      "instructions are long. Follow them."))

    def test_a_qualified_anaphor_with_other_words_breaks_the_chain(self):
        self.assertFalse(self.directed("Read " + P + ". The commit instructions are strict. "
                                       "Follow them precisely."))

    def test_the_two_reported_false_positives_run(self):
        # From the #1043 review: an unrelated qualifier satisfied the pointer back. The second is
        # given a purpose here, since a bare read of the file is now refused by default.
        for brief in ("Summarise " + P + " in three bullets. Then follow the house-style "
                      "instructions for the reply.",
                      "Copy " + P + " to /tmp/x.md. The commit instructions are strict. Follow "
                      "them precisely."):
            with self.subTest(brief=brief):
                self.assertIsNone(frameworks.classify(brief, None), msg=brief)


class ReviewFollowUpTests(unittest.TestCase):
    """Reference chains and path boundaries, from the review of the default refusal."""

    def test_a_sentence_naming_another_file_breaks_the_chain(self):
        self.assertIsNone(frameworks.classify(
            "Summarise " + P + ". The file docs/other.md is authoritative. Follow it.", None))

    def test_an_unnamed_file_anaphor_still_carries_the_chain(self):
        self.assertIsNotNone(frameworks.classify(
            "Summarise " + P + ". The file is authoritative. Follow it.", None))

    def test_the_declared_path_glued_to_a_longer_directory_is_another_file(self):
        self.assertIsNone(frameworks.classify(
            "Review calc.py with the rules in my" + VALUE + ".", None))

    def test_the_declared_path_after_a_directory_separator_still_matches(self):
        for brief in ("Review calc.py with the rules in " + VALUE + ".",
                      "Review calc.py with the rules in x/" + VALUE + "."):
            with self.subTest(brief=brief):
                self.assertIsNotNone(frameworks.classify(brief, None), msg=brief)


class LaterAdoptionTests(unittest.TestCase):
    """A look at or an edit of the file does not cover a later clause that takes it on."""

    def test_a_look_then_use_it_to_review_is_the_review_layer(self):
        for brief in ("Summarise " + P + ", then use it to review calc.py.",
                      "Summarise " + P + ". Then use it to review calc.py."):
            with self.subTest(brief=brief):
                match = frameworks.classify(brief, None)
                self.assertIsNotNone(match, msg=brief)
                self.assertEqual(match["role"], "reviewer")

    def test_an_edit_then_follow_it_in_another_clause_is_the_review_layer(self):
        for brief in ("Edit " + P + ", then follow it when reviewing calc.py.",
                      "Rewrite " + P + " and then follow it when reviewing calc.py."):
            with self.subTest(brief=brief):
                match = frameworks.classify(brief, None)
                self.assertIsNotNone(match, msg=brief)
                self.assertEqual(match["role"], "reviewer")

    def test_an_edit_whose_own_clause_mentions_following_it_still_runs(self):
        self.assertIsNone(frameworks.classify(
            "Update the wording of " + P + " so reviewers follow it more easily.", None))


class LeadGapTests(unittest.TestCase):
    def directed(self, words):
        # The bare declared path, so a `_bmad/` prefix is not counted as a ninth word.
        filler = " ".join("w%d" % at for at in range(words))
        return frameworks._directed(VALUE, frameworks.normalise("Follow " + filler + " " + VALUE
                                                                + "."))

    def test_a_leading_directive_governs_the_file_across_eight_words(self):
        self.assertEqual(frameworks.LEAD_GAP, 8)
        self.assertTrue(self.directed(8))

    def test_nine_words_is_past_the_gap(self):
        self.assertFalse(self.directed(9))


if __name__ == "__main__":
    unittest.main()
