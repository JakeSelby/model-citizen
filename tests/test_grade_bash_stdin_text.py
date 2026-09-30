# SPDX-License-Identifier: MIT
"""Text a shell or another interpreter reads from its standard input, graded as what it holds.

A here-string (`sh <<<'…'`), text piped to a shell (`echo … | sh`, `printf … | xargs -0 sh -c`),
a program fed to Python, Perl, Ruby, Node, awk, make or osascript, and a script written and then
run all run the commands in them. Each template's `{cmd}` is `git push` for the hook and a
harmless `touch` for bash, which the test runs where the program is installed, to show the text
really runs. Plain data passed to a command that runs nothing keeps its grade.

Run: python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from test_grade_bash import CWD, cpu_seconds, grade, grader, pre_tool_use_timeout

ro = grader.ro
BASH = shutil.which("bash")
PROBE = "touch ran.txt"


def ran(template):
    """Whether `bash -c` runs the `{cmd}` of `template`: the probe leaves a file behind."""
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run([BASH, "-c", template.format(cmd=PROBE)], cwd=scratch,
                       capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        return os.path.exists(os.path.join(scratch, "ran.txt"))


# Shell text the hook can read, graded as the commands it holds.
SHELL = [
    "sh <<<'{cmd}'",
    "bash <<< \"{cmd}\"",
    "echo '{cmd}' | sh",
    "printf '{cmd}\\0' | xargs -0 sh -c",
    "printf '%s\\n' '{cmd}' | bash -s",
    "cat <<<'{cmd}' | sh",
    "echo '{cmd}' | tee log.txt | bash",
    "{{ echo '{cmd}'; }} | sh",
    "cat > x.sh <<'EOF'\n{cmd}\nEOF\nchmod +x x.sh; ./x.sh",
    "cat > x.sh <<EOF\n{cmd}\nEOF\n. ./x.sh",
    "echo '{cmd}' > y.sh; sh y.sh",
]
# A program another interpreter reads, with the program each needs.
PROGRAM = [
    ("python3 - <<'EOF'\nimport os; os.system('{cmd}')\nEOF", "python3"),
    ("python3 <<<\"import os; os.system('{cmd}')\"", "python3"),
    ("echo \"import os; os.system('{cmd}')\" | python3", "python3"),
    ("cat > x.py <<'EOF'\nimport os; os.system('{cmd}')\nEOF\npython3 x.py", "python3"),
    ("perl <<'EOF'\nsystem('{cmd}')\nEOF", "perl"),
    ("ruby <<'EOF'\nsystem('{cmd}')\nEOF", "ruby"),
    ("node <<'EOF'\nrequire('child_process').execSync('{cmd}')\nEOF", "node"),
    ("awk -f /dev/stdin <<'EOF'\nBEGIN {{ system(\"{cmd}\") }}\nEOF", "awk"),
    ("make -f - <<'EOF'\nall:\n\t{cmd}\nEOF", "make"),
    ("osascript <<'EOF'\ndo shell script \"{cmd}\"\nEOF", "osascript"),
]
GRADED = SHELL + [template for template, _program in PROGRAM]
# Data: nothing runs `{cmd}`, and each keeps the grade it had.
DATA = [
    ("echo '{cmd}' > notes.txt", 1),
    ("cat <<'EOF'\n{cmd}\nEOF", 0),
    ("grep -c x <<<'{cmd}'", 0),
    ("echo '{cmd}' | grep -c x", 0),
    ("echo '{cmd}' > y.sh; cat y.sh", 1),
    ("cat > notes.md <<'EOF'\n{cmd}\nEOF", 1),
    ("python3 script.py <<'EOF'\n{cmd}\nEOF", 1),
    ("echo '{cmd}' | python3 -m json.tool", 1),
    ("awk '{{print}}' <<'EOF'\n{cmd}\nEOF", 0),
]


class StdinRunsTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_every_shell_text(self):
        self.assertEqual([t for t in SHELL if not ran(t)], [])

    @unittest.skipUnless(BASH, "bash is not installed")
    def test_each_installed_interpreter_runs_its_program(self):
        installed = [t for t, program in PROGRAM if shutil.which(program)]
        self.assertEqual([t for t in installed if not ran(t)], [])

    def test_a_push_the_text_holds_grades_a_push(self):
        commands = [t.format(cmd="git push") for t in GRADED]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) != 2], [])

    def test_a_forced_push_the_text_holds_grades_three(self):
        commands = [t.format(cmd="git push --force") for t in GRADED]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) != 3], [])

    def test_each_grades_at_least_as_the_same_command_passed_to_sh_c(self):
        for cmd in ("git push", "git push --force", "rm -rf ~"):
            floor = grade("sh -c '%s'" % cmd)
            commands = [t.format(cmd=cmd) for t in GRADED]
            self.assertEqual([(c, grade(c)) for c in commands if grade(c) < floor], [])

    def test_none_passes_the_read_only_check(self):
        for template in GRADED:
            command = template.format(cmd="git push")
            with self.subTest(command=command):
                self.assertFalse(ro.command_ok(command))

    def test_an_unreadable_program_is_graded_unknown_or_higher(self):
        self.assertEqual(grade("python3 - <<'EOF'\nprint(1)\nEOF"), 1)
        self.assertEqual(grade("python3 - <<'EOF'\nsubprocess.run(['git', 'push'])\nEOF"), 2)
        self.assertEqual(grade("echo \"import os; os.system('rm -rf ~')\" | python3"), 3)

    def test_words_piped_to_xargs_are_graded_as_its_command_arguments(self):
        self.assertEqual(grade("echo push | xargs git"), 2)
        self.assertEqual(grade("echo --force | xargs git push origin"), 3)


class StdinDataTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_no_data_text(self):
        self.assertEqual([t for t, _grade in DATA if ran(t)], [])

    def test_data_keeps_its_grade(self):
        for template, expected in DATA:
            for cmd in ("git push", "git push --force"):
                command = template.format(cmd=cmd)
                with self.subTest(command=command):
                    self.assertEqual(grade(command), expected)


class ReaderTests(unittest.TestCase):
    def test_interpreters_read_standard_input_only_without_a_program_named(self):
        reads = grader._interprets_input
        self.assertEqual([t for t in (["python3"], ["python3", "-"], ["python3", "-u", "-"],
                                      ["perl"], ["node", "-"], ["awk", "-f", "/dev/stdin"],
                                      ["make", "-f", "-"], ["deno", "run", "-"],
                                      ["xargs", "python3", "-c"], ["uv", "run", "python3"],
                                      ["env", "X=1", "ruby"], ["$PY", "-"])
                          if not reads(t)], [])
        self.assertEqual([t for t in (["python3", "script.py"], ["python3", "-m", "json.tool"],
                                      ["python3", "-c", "print(1)"], ["perl", "-ne", "print"],
                                      ["awk", "{print}"], ["make"], ["node", "-e", "1"],
                                      ["cat"], ["deno", "run", "main.ts"])
                          if reads(t)], [])

    def test_printf_output_is_read_as_printf_writes_it(self):
        self.assertEqual(grader._printf("git %s\\n", ["push", "--force"]),
                         "git push\ngit --force\n")
        self.assertEqual(grader._printf("%s\\0", ["a b"]), "a b\n")
        self.assertEqual(grader._printf("%b%%", ["x\\ty"]), "x\ty%")

    def test_pipes_are_linked_to_the_command_they_feed(self):
        linked = grader._linked_segments("echo a | sh; (echo b) | bash && echo c | (sh)")
        self.assertEqual([fed for _tokens, fed in linked],
                         [None, 0, None, grader.COMPOUND, None, 4])


class StdinTimingTests(unittest.TestCase):
    def test_programs_are_graded_in_bounded_time(self):
        budget = pre_tool_use_timeout() / 10
        line = "subprocess.run(['git', 'log', \"x\"])  # find kill\n"
        cases = [
            lambda k: "python3 - <<'EOF'\n" + line * 1000 * k + "EOF",
            lambda k: "python3 - <<'EOF'\n" + "x = 1 + 2  # comment\n" * 2500 * k + "EOF",
            lambda k: "python3 - <<'EOF'\n" + "'git' " * 10000 * k + "\nEOF",
            lambda k: "sh <<<'" + "git status; " * 300 * k + "'",
            lambda k: "printf '" + "%s " * 700 * k + "' " + "a " * 500 * k + "| sh",
        ]
        for make in cases:
            with self.subTest(sample=make(1)[:30]):
                full = cpu_seconds(lambda: grader.grade_text(make(4), CWD), batches=3)
                quarter = cpu_seconds(lambda: grader.grade_text(make(1), CWD), batches=3)
                self.assertLess(full, budget)
                self.assertLess(full / max(quarter, 1e-4), 8)


if __name__ == "__main__":
    unittest.main()
