# SPDX-License-Identifier: MIT
"""Operator tokenization in the read-only grammar, and the grades that inherit it.

The property under test: an operator directly after a closing parenthesis or brace, or
directly before an opening parenthesis, is recognized exactly as it is with a space around it,
so `command_ok` and `grade-bash` give the same answer for either spelling. The tests only pass
strings to the parser; nothing is executed.

Run: python3 -m unittest discover tests
"""
import importlib.util
import random
import shlex
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, REPO / "claude" / "hooks" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ro = _load("allow-readonly-bash.py", "operator_spacing_readonly")
grader = _load("grade-bash.py", "operator_spacing_grader")

CLOSERS = {")": "(true)", "}": "{ true; }"}
SEPARATORS = [";", "&&", "||", "|", "&"]
READ_ONLY_AFTER = ["ls", "git status"]
WRITING_AFTER = ["touch x", "git push origin HEAD"]


def grade(command):
    return grader.grade_text(command, "/work/repo")[0]


def shlex_tokens(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.commenters = ""
    lex.whitespace_split = True
    return list(lex)


def spellings(closer, separator, after):
    """The spaced form first, then the forms with no space before the separator."""
    lead = CLOSERS[closer]
    return ("%s %s %s" % (lead, separator, after),
            "%s%s %s" % (lead, separator, after),
            "%s%s%s" % (lead, separator, after))


class SeparatorSweepTests(unittest.TestCase):
    def test_verdict_and_grade_do_not_depend_on_spacing(self):
        for closer in CLOSERS:
            for separator in SEPARATORS:
                for after in READ_ONLY_AFTER + WRITING_AFTER:
                    spaced, *tight = spellings(closer, separator, after)
                    for line in tight:
                        with self.subTest(line=line):
                            self.assertEqual(ro.command_ok(line), ro.command_ok(spaced))
                            self.assertEqual(grade(line), grade(spaced))

    def test_a_read_only_command_after_the_separator_stays_approved(self):
        for closer in CLOSERS:
            for separator in SEPARATORS:
                for after in READ_ONLY_AFTER:
                    for line in spellings(closer, separator, after):
                        with self.subTest(line=line):
                            self.assertTrue(ro.command_ok(line))
                            self.assertEqual(grade(line), 0)

    def test_a_writing_command_after_the_separator_is_graded_on_its_own(self):
        for closer in CLOSERS:
            for separator in SEPARATORS:
                for after in WRITING_AFTER:
                    expected = grade(after)
                    for line in spellings(closer, separator, after):
                        with self.subTest(line=line):
                            self.assertFalse(ro.command_ok(line))
                            self.assertEqual(grade(line), expected)

    def test_the_reported_line_is_rejected_and_graded_as_a_write(self):
        self.assertFalse(ro.command_ok("(true); touch x"))
        self.assertEqual(grade("(true); touch x"), 1)


class AdjacentOperatorTests(unittest.TestCase):
    def test_a_separator_directly_before_an_opening_parenthesis_is_recognized(self):
        for separator in SEPARATORS:
            for line in ("true%s(touch x)" % separator, "true %s (touch x)" % separator):
                with self.subTest(line=line):
                    self.assertFalse(ro.command_ok(line))
                    self.assertEqual(grade(line), 1)
            self.assertTrue(ro.command_ok("true%s(ls)" % separator))

    def test_a_redirect_directly_after_a_closing_parenthesis_is_seen(self):
        self.assertFalse(ro.command_ok("(ls)>out.txt"))
        self.assertFalse(ro.command_ok("(ls)>>out.txt"))
        for tight, spaced in (("(ls)>/dev/null", "(ls) > /dev/null"),
                              ("(ls)<in.txt", "(ls) < in.txt")):
            with self.subTest(line=tight):
                self.assertEqual(ro.command_ok(tight), ro.command_ok(spaced))
                self.assertEqual(grade(tight), grade(spaced))

    def test_nested_closers_and_a_separator_split(self):
        self.assertFalse(ro.command_ok("( (true));touch x"))
        self.assertTrue(ro.command_ok("( (git status));ls"))

    def test_genuinely_read_only_compound_lines_stay_approved(self):
        for line in ("(git status);ls", "{ ls; }&&git log -1", "(cat a)|head -5",
                     "(ls)||(pwd)", "{ echo a; };{ echo b; }"):
            with self.subTest(line=line):
                self.assertTrue(ro.command_ok(line))
                self.assertEqual(grade(line), 0)


class TokenizeTests(unittest.TestCase):
    def test_an_unquoted_run_splits_into_the_longest_operators(self):
        self.assertEqual(ro.tokenize("(true);touch x"), ["(", "true", ")", ";", "touch", "x"])
        self.assertEqual(ro.tokenize("a)&&(b"), ["a", ")", "&&", "(", "b"])
        self.assertEqual(ro.tokenize("a|&b;;c"), ["a", "|&", "b", ";;", "c"])
        self.assertEqual(ro.tokenize("ls 2>&1;x"), ["ls", "2", ">&", "1", ";", "x"])
        self.assertEqual(ro.tokenize("a&>>f"), ["a", "&>>", "f"])

    def test_case_terminators_split_into_delimiters(self):
        self.assertEqual(ro.tokenize(";&"), [";", "&"])
        self.assertEqual(ro.tokenize(";;&"), [";;", "&"])

    def test_a_quoted_or_escaped_operator_is_part_of_a_word(self):
        self.assertEqual(ro.tokenize("echo ');' x"), ["echo", ");", "x"])
        self.assertEqual(ro.tokenize('echo ");"x'), ["echo", ");x"])
        self.assertEqual(ro.tokenize("echo \\);x"), ["echo", ")", ";", "x"])
        self.assertEqual(ro.tokenize("echo ''"), ["echo", ""])

    def test_an_unclosed_quote_or_trailing_backslash_raises(self):
        for text in ("echo 'a", 'echo "a', "echo a\\", 'echo "a\\'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    ro.tokenize(text)

    def test_words_match_shlex_wherever_shlex_splits_operators_correctly(self):
        """A seeded sweep of short strings: wherever shlex returns no multi-operator run, the
        tokens are identical, and both raise on the same inputs. A string with `$'` or `$"` is
        skipped: bash reads an ANSI-C or locale string there, which shlex does not know."""
        rng = random.Random(896)
        alphabet = list("ab ;()|&<>'\"\\\t$#}{-=\n")
        compared = 0
        for _ in range(20000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
            if "$'" in text or '$"' in text:
                continue
            try:
                expected = shlex_tokens(text)
            except ValueError:
                expected = None
            try:
                actual = ro.tokenize(text)
            except ValueError:
                actual = None
            if expected is None or actual is None:
                self.assertEqual(expected, actual, text)
                continue
            if any(t and all(c in ro.OPERATOR_CHARS for c in t) and len(ro._operators(t)) > 1
                   for t in expected):
                continue
            compared += 1
            self.assertEqual(actual, expected, text)
        self.assertGreater(compared, 5000)


if __name__ == "__main__":
    unittest.main()
