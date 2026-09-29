# SPDX-License-Identifier: MIT
"""ANSI-C quotes, here-document markers and quoted tilde-prefixes, read as bash reads them.

Each case names a command whose later line or word bash runs while an older reading of the hooks
took it for quoted text or for a here-document body. The `{cmd}` in a template is `git push` for
the hooks and `echo RAN` for `bash -c`, which the test runs where bash is installed to show that
the line really runs. Every such bash expectation was also checked by hand with `bash -c`
(GNU bash 3.2), as were these, which differ between bash versions and so are not re-run here:
`cat <<$'E\\x4fF'` ends its body at `EOF` in 3.2, and in 3.2 a `)` inside a here-document inside
`$(…)` closes the substitution, so `x=$(cat <<'EOF'` then `1) echo RAN` runs `echo RAN`.
`cd "~/x"` and `cd ~"/x"` both reach `./~/x` in bash, while zsh expands `~"/x"`.

Run: python3 -m unittest discover tests
"""
import random
import re
import shutil
import subprocess
import tempfile
import time
import unittest

from test_grade_bash import CWD, grade, grader, pre_tool_use_timeout
from test_governance_binding import Home, make_repo

ro = grader.ro
BASH = shutil.which("bash")


def ran(template):
    """Whether `bash -c` runs the `{cmd}` line of `template`."""
    with tempfile.TemporaryDirectory() as scratch:
        out = subprocess.run([BASH, "-c", template.format(cmd="echo RAN")], cwd=scratch,
                             capture_output=True, text=True, timeout=10)
    return any(line.split()[:1] == ["RAN"] for line in out.stdout.splitlines())


# Templates whose `{cmd}` line bash runs, on every version.
HIDDEN = [
    # An ANSI-C string ends at its unescaped quote, not at `\'`.
    "echo $'a\\'b'; {cmd}",
    "echo $'it\\'s'; {cmd} origin main",
    "echo $'x\\'' ; {cmd} #'",
    # `$$` is one parameter: the quote after it is a plain one, so `\'` ends it.
    "echo $$'a\\' ; {cmd} #'",
    # A `<<` that is quoted, commented, a here-string or a shift is no here-document.
    "echo '<<EOF'\n{cmd}\nEOF",
    "echo x # <<EOF\n{cmd}\nEOF",
    "cat <<<EOF\n{cmd}\nEOF",
    "X=1; echo $((1<<X))\n{cmd}\nX",
    # The delimiter is the quote-removed word, and its line must match it exactly.
    "cat <<'E'OF\nEOF\n{cmd}\nE",
    "cat <<E\\OF\nEOF\n{cmd}\nE",
    "cat <<EOF\n EOF\ncat <<X\nEOF\n{cmd}\nX",
    # A continuation on the operator line does not start the body.
    "echo <<EOF \\\n; {cmd}\nEOF",
    # In an unquoted body `foo\` joins the next line, so the `X` after it ends nothing.
    "cat <<X\nfoo\\\nX\ncat <<Y\nX\n{cmd}\nY",
    # A body is not commands: its quote cannot swallow the lines after it.
    "cat <<EOF\necho '\nEOF\n{cmd}\n'",
    # A `#` on a later line of a quoted string is text, and so is the quote after it.
    "echo 'a\nx #'; {cmd}; echo '\n'",
]


class HiddenLineTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_every_hidden_line(self):
        self.assertEqual([t for t in HIDDEN if not ran(t)], [])

    def test_the_hidden_push_grades_at_least_a_push(self):
        commands = [t.format(cmd="git -C /tmp/x push") for t in HIDDEN]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) < 2], [])

    def test_no_hidden_line_passes_the_read_only_check(self):
        for template in HIDDEN:
            for cmd in ("git push", "touch x"):
                command = template.format(cmd=cmd)
                with self.subTest(command=command):
                    self.assertFalse(ro.command_ok(command))
                    self.assertGreater(grade(command), 0)

    def test_a_continuation_after_a_dollar_dollar_quote_is_joined(self):
        # `$$'\'` is `$$` and the single-quoted `\`, so the continuation after it is unquoted.
        command = "echo $$'\\' ; git pu\\\nsh #'"
        self.assertEqual(grader._join_continuations(command), ("echo $$'\\' ; git push #'", True))
        self.assertEqual(grade(command), 2)
        if BASH:
            self.assertTrue(ran("echo $$'\\' ; ec\\\nho RAN #'"))

    def test_a_forced_push_after_a_quoted_marker_grades_three(self):
        self.assertEqual(grade("echo '<<EOF'\ngit push --force\nEOF"), 3)
        self.assertEqual(grade("cat <<EOF\n EOF\ncat <<X\nEOF\ngit push --force\nX"), 3)

    def test_an_uncertain_delimiter_is_graded_with_its_body_as_well(self):
        # bash 3.2 decodes `$'E\x4fF'` to `EOF`; the hook cannot know which shell reads it.
        self.assertEqual(grade("cat <<$'E\\x4fF'\nbody\nEOF\ngit push\nE\\x4fF"), 2)
        self.assertEqual(grade("cat <<$x\nbody\ngit push\n$x"), 2)

    def test_a_close_parenthesis_in_a_body_inside_a_substitution_is_graded_both_ways(self):
        # bash 3.2 closes the substitution at the body's `)` and runs the rest of the line.
        command = "x=$(cat <<'EOF'\n1) git push\nEOF\n)"
        self.assertEqual(grade(command), 2)
        self.assertEqual(len(grader._readings(command)[0]), 2)


class AnsiCTests(unittest.TestCase):
    def test_the_tokenizer_decodes_ansi_c_and_locale_strings(self):
        self.assertEqual(ro.tokenize("printf $'a\\tb' $'it\\'s' $\"x y\""),
                         ["printf", "a\tb", "it's", "x y"])
        self.assertEqual(ro.tokenize("git $'pu\\x73h' $'\\160ush' $'\\u0070ush'"),
                         ["git", "push", "push", "push"])
        self.assertEqual(ro.tokenize("echo $$'a' \"$'\""), ["echo", "$$a", "$'"])
        # A NUL ends the string; the word goes on after it.
        self.assertEqual(ro.tokenize("git $'pu\\0x'sh $'push\\x00y' $'push\\c@z'"),
                         ["git", "push", "push", "push"])

    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_decodes_the_same_escapes(self):
        out = subprocess.run([BASH, "-c", "printf '%s\\n' $'pu\\x73h' $'\\160ush' $'it\\'s' "
                              "$\"-delete\" \"$'\" $'pu\\0x'sh $'push\\x00y' $'push\\c@z'"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(out.stdout.splitlines(),
                         ["push", "push", "it's", "-delete", "$'", "push", "push", "push"])

    def test_strict_tokenizing_refuses_escapes_and_locale_strings(self):
        self.assertEqual(ro.tokenize("echo $'plain'", strict=True), ["echo", "plain"])
        for text in ("echo $'a\\tb'", 'echo $"x"', "echo $'open"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ro.tokenize(text, strict=True)

    def test_an_ansi_c_or_locale_word_is_graded_as_the_word_bash_passes(self):
        for command in ("find . $'-delete'", 'find . $"-delete"', "find . $'\\x2ddelete'",
                        "find . $'-delete\\0'", "git $'pu\\0x'sh --force"):
            with self.subTest(command=command):
                self.assertFalse(ro.command_ok(command))
                self.assertEqual(grade(command), 3)
        self.assertEqual(grade("git $'pu\\x73h'"), 2)
        self.assertEqual(grade("git $'push\\0x'"), 2)
        self.assertEqual(grade('git $"push"'), 2)

    def test_a_plain_ansi_c_word_stays_read_only(self):
        self.assertTrue(ro.command_ok("grep $'needle' README.md"))
        self.assertTrue(ro.command_ok("grep 'end$' README.md"))

    def test_a_substitution_after_an_escaped_quote_is_extracted(self):
        stripped, inners = grader._extract_subs("echo $'x\\'' $(git push) #'")
        self.assertEqual(inners, ["git push"])
        self.assertEqual(grader._strip_comments("echo $'x\\'' y #'"), "echo $'x\\'' y ")

    def test_the_push_after_an_escaped_quote_is_governed(self):
        found = grader.governed_text("echo $'x\\'' ; git push #'", CWD)
        self.assertIn(("coding.git_push", 2), [entry[:2] for entry in found])


class HeredocSplitTests(unittest.TestCase):
    def split(self, text):
        return grader._split_heredocs(text)

    def test_the_delimiter_is_quote_removed(self):
        text, bodies, certain = self.split('cat <<E"OF"\nE\nx\nEOF\ny')
        self.assertEqual((bodies, certain), (["E\nx"], True))
        self.assertTrue(text.endswith("EOF\ny"))
        self.assertEqual(self.split("cat <<\\EOF\n$HOME\nEOF")[1], ["$HOME"])

    def test_every_operator_on_a_line_takes_its_own_body(self):
        self.assertEqual(self.split("cat <<A <<-B\n1\nA\n2\n\tB\necho after")[:2],
                         ("cat <<A <<-B\nA\n\tB\necho after", ["1", "2"]))

    def test_a_here_document_in_a_substitution_keeps_its_body_out(self):
        command = "git commit -m \"$(cat <<'EOF'\nfix(x): it's done (see #1)\nEOF\n)\""
        texts, bodies = grader._readings(command)
        self.assertEqual((len(texts), bodies), (1, ["fix(x): it's done (see #1)"]))
        self.assertEqual(grade(command), 1)

    def test_a_sql_body_inside_a_substitution_is_graded(self):
        self.assertEqual(grade("x=$(psql <<EOF\nDROP TABLE users;\nEOF\n)"), 3)
        self.assertEqual(grade("echo \"$(mongosh <<'EOF'\ndb.users.drop()\nEOF\n)\""), 3)

    def test_an_unplaced_marker_is_read_with_nothing_removed(self):
        # bash 3.2 reads `((echo a) <<X)` as a subshell with a here-document, yet runs the line
        # after `x=$((echo a) <<X)`; a `(( (1) <<X ))` is a shift.
        for text in ("echo ${x:-<<EOF}\na\nEOF", "((echo a) <<X)\na\nX", "x=$((echo a) <<X)\na\nX"):
            with self.subTest(text=text):
                self.assertFalse(self.split(text)[2])
        self.assertEqual(self.split("(( (1) <<X ))\na\nX"), ("(( (1) <<X ))\na\nX", [], True))


class MongoTests(unittest.TestCase):
    OLD = re.compile(r"\bdb(?:\.\w+)*\.(dropDatabase|drop|deleteMany|remove)\s*\(")

    def test_the_walk_finds_what_the_old_expression_found(self):
        rng = random.Random(1088)
        pieces = ["db", ".", "x", "drop", "remove", " ", "(", "_", "db.", "mydb", "..", "1"]
        for _ in range(20000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 10)))
            match = self.OLD.search(text)
            self.assertEqual(grader._mongo(text), match.group(1) if match else None, text)

    def test_destructive_calls_grade_three(self):
        for body in ("db.users.drop()", "db.dropDatabase ()", "x; db.a.b.deleteMany({})"):
            with self.subTest(body=body):
                self.assertEqual(grader._sql(body)[0], 3)
        self.assertIsNone(grader._sql("mydb.users.drop()"))


class ScalingTests(unittest.TestCase):
    """As `test_grading_a_hundred_kilobyte_command_stays_well_inside_the_hook_timeout`: the best
    of several runs, inside a tenth of the hook timeout, and a quarter-size run that bounds the
    growth, which a quadratic reading would push past eightfold."""

    def best(self, function, argument, runs=3):
        times = []
        for _ in range(runs):
            start = time.perf_counter()
            function(argument)
            times.append(time.perf_counter() - start)
        return min(times)

    def assert_linear(self, function, make):
        budget = pre_tool_use_timeout() / 10
        full = self.best(function, make(4))
        quarter = self.best(function, make(1))
        self.assertLess(full, budget)
        self.assertLess(full / max(quarter, 1e-4), 8)

    def test_a_body_of_repeated_db_is_scanned_in_linear_time(self):
        self.assert_linear(grader._sql, lambda k: "db." * 5000 * k)
        self.assert_linear(lambda c: grader.grade_text(c, CWD),
                           lambda k: "mongosh <<EOF\n" + "db." * 5000 * k + "\nEOF")

    def test_here_document_markers_are_split_in_linear_time(self):
        cases = [
            lambda k: "echo " + "'<<x' " * 500 * k + "\ngit push",
            lambda k: "cat <<EOF\n" + "a\\\n" * 5000 * k + "EOF\ngit push",
            lambda k: "x=$(cat <<'E'\n1)\nE\n)\n" * 100 * k,
            lambda k: "echo $'\\'" * 1000 * k,
        ]
        for make in cases:
            with self.subTest(sample=make(1)[:30]):
                self.assert_linear(lambda c: grader.grade_text(c, CWD), make)

    def test_the_read_only_check_is_capped_before_it_reads(self):
        long_text = "ls " + "a" * ro.MAX_LENGTH
        self.assertFalse(ro.command_ok(long_text))
        self.assert_linear(ro.command_ok, lambda k: "ls " + "$'a' " * 500 * k)


class QuotedTildeTests(Home):
    """bash keeps a quoted tilde-prefix literal, so `cd "~/x"` does not reach the home directory."""

    def setUp(self):
        super().setUp()
        self.configure("local")
        self.beta = make_repo(self.home / "beta", branch="trunk")

    def places(self, command):
        return [entry[2] for entry in grader.governed_text(command, str(self.repo))
                if entry[0] == "coding.git_push"]

    def test_a_quoted_tilde_prefix_leaves_the_directory_unknown(self):
        for command in ('cd "~/beta" && git push', "cd ~\"/beta\" && git push",
                        "cd '~'/beta && git push", "cd \\~/beta && git push",
                        "cd ~$''/beta && git push", 'cd ~$""/beta && git push',
                        "cd $'\\x7e/beta' && git push", "pushd $'\\x7e/beta' && git push",
                        "git -C $'\\x7e/beta' push"):
            with self.subTest(command=command):
                self.assertEqual(self.places(command), [None])

    def test_an_unquoted_tilde_still_reaches_home(self):
        self.assertEqual(self.places("cd ~/beta && git push"), [str(self.beta)])
        self.assertEqual(self.places('cd ~/"beta" && git push'), [str(self.beta)])

    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_does_not_expand_a_quoted_tilde_prefix(self):
        out = subprocess.run([BASH, "-c", "echo \"~/x\" ~\"/x\" '~'/x \\~/x ~$''/x ~$\"\"/x "
                              "$'\\x7e/x'"], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.stdout.split(), ["~/x"] * 7)


if __name__ == "__main__":
    unittest.main()
