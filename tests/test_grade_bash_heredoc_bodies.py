# SPDX-License-Identifier: MIT
"""Here-document bodies that a shell runs, graded as the commands they hold.

A body is data only while nothing runs it. The shell runs the `$(…)` and backtick substitutions
of a body whose delimiter is unquoted, even when `cat` only prints it, and a body read by a shell
(`bash <<EOF`, `cat <<EOF | sh`, `eval "$(cat <<EOF …)"`, `source /dev/stdin`) runs line by
line. Each template's `{cmd}` is `git push` for the hook and a harmless `echo` for bash, which
the test runs where bash is installed to show the line really runs.

Run: python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from test_grade_bash import CWD, grade, grader, pre_tool_use_timeout

ro = grader.ro
BASH = shutil.which("bash")
PROBE = "touch ran.txt"


ZSH = shutil.which("zsh")
CSH = shutil.which("tcsh") and shutil.which("csh")


def ran(template, shell=None):
    """Whether `bash -c`, or `shell -c`, runs the `{cmd}` of `template`: the probe leaves a file
    behind."""
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run([shell or BASH, "-c", template.format(cmd=PROBE)], cwd=scratch,
                       capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        return os.path.exists(os.path.join(scratch, "ran.txt"))


# Templates whose `{cmd}` bash runs from a here-document body.
RUN = [
    # An unquoted body runs its substitutions, whatever quotes surround them.
    "cat <<EOF\n$({cmd})\nEOF",
    "cat <<EOF\n`{cmd}`\nEOF",
    "cat <<EOF\n'$({cmd})'\nEOF",
    "cat <<EOF\n${{x:-$({cmd})}}\nEOF",
    "cat <<EOF\n$\\\n({cmd})\nEOF",
    "cat > out.txt <<EOF\nnotes $({cmd})\nEOF",
    # A `$((` that does not close as arithmetic is a substitution.
    "cat <<EOF\n$(({cmd}) )\nEOF",
    "cat <<EOF\n$(( $({cmd}) ))\nEOF",
    # A body a shell reads is a script, quoted delimiter or not.
    "bash <<EOF\n{cmd}\nEOF",
    "bash <<'EOF'\n{cmd}\nEOF",
    "sh <<EOF\n{cmd}\nEOF",
    "bash -s <<EOF\n{cmd}\nEOF",
    "cat <<EOF | sh\n{cmd}\nEOF",
    "cat <<'EOF' | bash\n{cmd}\nEOF",
    "eval \"$(cat <<EOF\n{cmd}\nEOF\n)\"",
    "eval \"$(cat <<'EOF'\n{cmd}\nEOF\n)\"",
    "source /dev/stdin <<EOF\n{cmd}\nEOF",
    ". /dev/stdin <<EOF\n{cmd}\nEOF",
    "env FOO=1 bash <<'EOF'\n{cmd}\nEOF",
    "x=$(bash <<'EOF'\n{cmd}\nEOF\n)",
    # A comment inside a substitution hides its `)` from bash 3.2 and zsh, not from the matcher.
    "cat <<EOF\n$(echo a # )\n{cmd}\n)\nEOF",
    # An escaped backtick inside a backtick substitution opens a nested one.
    "cat <<EOF\n`echo \\`{cmd}\\``\nEOF",
    # A command word that expands to a shell's name, or env's split string naming one.
    "{{bash,-s}} <<'EOF'\n{cmd}\nEOF",
    "x=; /bin/sh$x <<'EOF'\n{cmd}\nEOF",
    "env -S 'sh -e' <<'EOF'\n{cmd}\nEOF",
    "env -iS'sh -e' <<'EOF'\n{cmd}\nEOF",
]
ZSH_RUN = ["zsh <<EOF\n{cmd}\nEOF"]
# Globs that name /bin/bash, which bash expands before it runs the word.
GLOB_RUN = [
    "/bin/ba?h <<'EOF'\n{cmd}\nEOF",
    "/bin/[b]ash <<'EOF'\n{cmd}\nEOF",
    "/bin/b*sh <<'EOF'\n{cmd}\nEOF",
]
# Run by zsh itself, which closes these substitutions later than bash 3.2 does.
ZSH_ONLY_RUN = [
    "cat <<EOF\n$(case x in x) {cmd};; esac)\nEOF",
    "cat <<EOF\n$(cat <<'X'\n)\nX\n{cmd})\nEOF",
    "=bash <<'EOF'\n{cmd}\nEOF",
]
CSH_RUN = [
    "tcsh <<'EOF'\n{cmd}\nEOF",
    "csh -s <<'EOF'\n{cmd}\nEOF",
    "cat <<'EOF' | tcsh\n{cmd}\nEOF",
]
GRADED = RUN + ZSH_RUN + GLOB_RUN + ZSH_ONLY_RUN + CSH_RUN
# A shell reading an unquoted body runs what bash left once it removed the escapes, so an escaped
# substitution reaches it unescaped. The hook grades a substitution, not the command it holds,
# below 3, so these are graded with a forced push only.
ESCAPED_RUN = [
    "bash <<EOF\necho \"\\$({cmd})\"\nEOF",
    "bash <<EOF\necho \"\\`{cmd}\\`\"\nEOF",
]

# Templates whose body is data: nothing runs `{cmd}`.
DATA = [
    "cat <<'EOF'\n{cmd}\nEOF",
    "cat <<'EOF'\n$({cmd})\nEOF",
    "cat <<\\EOF\n`{cmd}`\nEOF",
    "cat <<EOF\n{cmd}\nEOF",
    "cat <<EOF\n\\$({cmd})\nEOF",
    "cat > notes.md <<'EOF'\n$({cmd})\nEOF",
    "printf '%s\\n' \"$(cat <<'EOF'\nfix: $({cmd})\nEOF\n)\"",
    # `$$` is the shell's process ID, and a quoted body reaches the shell with its escapes.
    "cat <<EOF\n$$({cmd})\nEOF",
    "bash <<'EOF'\necho \"\\$({cmd})\"\nEOF",
]


class BodyRunsTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_every_body_line(self):
        templates = RUN + (ZSH_RUN if shutil.which("zsh") else [])
        self.assertEqual([t for t in templates if not ran(t)], [])

    @unittest.skipUnless(BASH and os.path.exists("/bin/bash"), "/bin/bash is not installed")
    def test_bash_expands_a_glob_to_a_shell(self):
        self.assertEqual([t for t in GLOB_RUN if not ran(t)], [])

    @unittest.skipUnless(ZSH, "zsh is not installed")
    def test_zsh_runs_every_body_line(self):
        self.assertEqual([t for t in ZSH_ONLY_RUN if not ran(t, ZSH)], [])

    @unittest.skipUnless(BASH and CSH, "csh and tcsh are not installed")
    def test_csh_runs_every_body_line(self):
        self.assertEqual([t for t in CSH_RUN if not ran(t)], [])

    def test_a_push_in_a_body_that_runs_grades_a_push(self):
        commands = [t.format(cmd="git push") for t in GRADED]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) != 2], [])

    def test_a_forced_push_in_a_body_that_runs_grades_three(self):
        commands = [t.format(cmd="git push --force") for t in GRADED]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) != 3], [])

    def test_no_body_that_runs_passes_the_read_only_check(self):
        for template in GRADED:
            command = template.format(cmd="git push")
            with self.subTest(command=command):
                self.assertFalse(ro.command_ok(command))

    @unittest.skipUnless(BASH, "bash is not installed")
    def test_a_shell_runs_an_escaped_substitution_of_an_unquoted_body(self):
        self.assertEqual([t for t in ESCAPED_RUN if not ran(t)], [])

    def test_an_escaped_substitution_a_shell_reads_is_graded(self):
        commands = [t.format(cmd="git push --force") for t in ESCAPED_RUN]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) != 3], [])

    def test_a_harmless_script_body_grades_as_the_shell_does(self):
        self.assertEqual(grade("bash <<'EOF'\necho hi\nEOF"), 1)


class BodyDataTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_no_data_body(self):
        self.assertEqual([t for t in DATA if ran(t)], [])

    def test_a_push_in_a_data_body_is_not_graded(self):
        self.assertEqual(len(DATA), 9)
        for template, expected in zip(DATA, (0, 0, 0, 0, 0, 1, 1, 0, 1)):
            command = template.format(cmd="git push --force")
            with self.subTest(command=command):
                self.assertEqual(grade(command), expected)

    def test_bodies_remember_whether_their_delimiter_was_quoted(self):
        _text, bodies, _certain = grader._split_heredocs("cat <<'A' <<B\n1\nA\n2\nB")
        self.assertEqual(bodies, ["1", "2"])
        self.assertEqual([body.quoted for body in bodies], [True, False])


class UnreadableBodyTests(unittest.TestCase):
    def test_a_substitution_that_never_closes_is_graded_unknown_or_higher(self):
        self.assertEqual(grader._body_substitutions("$(git push"), None)
        self.assertEqual(grade("cat <<EOF\n$(git push\nEOF"), 2)
        self.assertEqual(grade("cat <<EOF\n`touch x\nEOF"), 1)

    def test_the_walk_reads_substitutions_as_bash_does(self):
        read = grader._body_substitutions
        self.assertEqual(read("a $(b) `c` \\$(d) $((1+2)) $\\\n(e)"), ["b", "c", "e"])
        self.assertEqual(read("$(( $(f) ))"), ["f"])
        self.assertEqual(read("plain text, $HOME and 'quotes'"), [])
        self.assertEqual(read("$$(a) $$$(b) $\\\n$(c)"), ["b"])

    def test_a_substitution_the_shells_may_close_later_is_also_read_to_the_end(self):
        read = grader._body_substitutions
        self.assertEqual(read("$(a # )\nb\n)"), ["a # ", "a # )\nb\n)"])
        self.assertEqual(read("$(cat <<X\n)\nX\nb)"), ["cat <<X\n", "cat <<X\n)\nX\nb)"])
        self.assertEqual(read("x $(a#b) y"), ["a#b"])

    def test_a_backtick_substitution_loses_its_escapes(self):
        self.assertEqual(grader._body_substitutions("`a \\`b\\` \\$c`"), ["a `b` $c"])

    def test_a_command_word_that_may_expand_to_a_shell_counts_as_one(self):
        may = grader._may_name_shell
        self.assertEqual([w for w in ("ba?h", "/bin/[b]ash", "b*sh", "{bash,-s}", "sh$x", "=zsh",
                                      "tcsh", "csh", "fish", "mksh") if not may(w)], [])
        self.assertEqual([w for w in ("cat", "[", "[[", "*.txt", "/usr/bin/python3", "tee")
                          if may(w)], [])

    def test_env_split_string_is_read_as_env_reads_it(self):
        split = grader._env_split
        self.assertEqual(split(["-S", "sh -e", "x"]), ["sh", "-e", "x"])
        self.assertEqual(split(["-iSsh -e"]), ["sh", "-e"])
        self.assertEqual(split(["--split-string=sh -e"]), ["sh", "-e"])
        self.assertEqual(split(["-u", "S", "ls"]), ["-u", "S", "ls"])
        self.assertIsNone(split(["-S", "sh ${X}"]))

    def test_too_many_substitutions_are_unreadable(self):
        self.assertIsNone(grader._body_substitutions("$(a) " * (grader.BODY_SUB_CHECKS + 1)))
        self.assertEqual(grade("cat <<EOF\n" + "$(a) " * 100 + "git push --force\nEOF"), 3)


class BodyTimingTests(unittest.TestCase):
    def best(self, text):
        times = []
        for _ in range(3):
            start = time.perf_counter()
            grader.grade_text(text, CWD)
            times.append(time.perf_counter() - start)
        return min(times)

    def test_body_substitutions_are_read_in_bounded_time(self):
        budget = pre_tool_use_timeout() / 10
        cases = [
            lambda k: "cat <<EOF\n" + "$((" * 300 * k + "\nEOF",
            lambda k: "cat <<EOF\n" + "$(a) x " * 2000 * k + "\nEOF",
            lambda k: "cat <<EOF\n" + "`a` $(b " * 2000 * k + "\nEOF",
            lambda k: "bash <<EOF\n" + "echo a\n" * 500 * k + "EOF",
            lambda k: "bash <<EOF\n" + "echo \\$(a) \\` $$(\n" * 500 * k + "EOF",
        ]
        for make in cases:
            with self.subTest(sample=make(1)[:30]):
                full = self.best(make(4))
                quarter = self.best(make(1))
                self.assertLess(full, budget)
                self.assertLess(full / max(quarter, 1e-4), 8)


if __name__ == "__main__":
    unittest.main()
