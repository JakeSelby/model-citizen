# SPDX-License-Identifier: MIT
"""Text a shell or another interpreter reads from its standard input, graded as what it holds.

A here-string (`sh <<<'…'`), text piped to a shell (`echo … | sh`, `printf … | xargs -0 sh -c`),
a program fed to Python, Perl, Ruby, Node, awk, make or osascript, and a script written and then
run all run the commands in them. Each template's `{cmd}` is `git push` for the hook and a
harmless `touch` for bash, which the test runs where the program is installed, to show the text
really runs. Plain data passed to a command that runs nothing keeps its grade.

Run: python3 -m unittest discover tests
"""
import base64
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest

from test_grade_bash import CWD, cpu_growth, grade, grader, pre_tool_use_timeout

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
    "echo '{cmd}' | head -1 | sh",
    "builtin echo '{cmd}' | sh",
    "command echo '{cmd}' | sh",
    "printf '%*s\\n' -1 '{cmd}' | sh",
    "printf '%.*s\\n' 99 '{cmd}' | sh",
    "echo '{cmd}' > x.sh; sh < x.sh",
    "echo '{cmd}' > x; cat x | sh",
    "echo '{cmd}' > x.sh; chmod +x x.sh; env ./x.sh",
    "echo '{cmd}' > x.sh; chmod +x x.sh; nohup ./x.sh >/dev/null 2>&1",
    "echo '{cmd}' > a.sh; cp a.sh b.sh; sh b.sh",
    "echo '{cmd}' > x.sh; ln -s x.sh y; sh y",
    "echo '{cmd}' > x.sh; sh *.sh",
    "echo '{cmd}' > x.sh; f=x.sh; sh $f",
    "echo '{cmd}' > x.sh; bash -c 'sh x.sh'",
    "echo '{cmd}' > x.sh; xargs sh <<< x.sh",
    "echo '{cmd}' > x.sh; bash -x x.sh",
    "printf '%s\\n' " + "x " * 64 + "'{cmd}' | sh",
    "echo '{cmd}' > ./task.sh; sh task.sh",
    "mkdir -p notes; echo '{cmd}' > notes/task.sh; sh ./notes//task.sh",
    "mkdir -p notes; echo '{cmd}' > notes/task.sh; cd notes; sh task.sh; mv ran.txt ..",
    "mkdir -p notes; echo '{cmd}' > notes/task.sh; chmod +x notes/task.sh; "
    "PATH=notes:$PATH task.sh",
    "mkdir -p notes s; echo '{cmd}' > notes/task.sh; mv notes/task.sh s/; sh s/task.sh",
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
    ("make -sf - <<'EOF'\nall:\n\t{cmd}\nEOF", "make"),
    ("printf 'all:\\n\\t{cmd}\\n' > Makefile; make -s", "make"),
    ("echo \"import os; os.system('{cmd}')\" > x.py; python3 -m x", "python3"),
    ("echo \"import os; os.system('{cmd}')\" > x.py; python3 -W ignore x.py", "python3"),
    ("echo \"system('{cmd}')\" > x.pl; perl -I lib x.pl", "perl"),
    ("echo \"system('{cmd}')\" > x.rb; ruby -r json x.rb", "ruby"),
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
    ("echo '{cmd}' | grep push", 0),
    ("git log | tee out.txt", 1),
    ("echo '{cmd}' > in.txt; python3 lint.py in.txt", 1),
    ("echo '{cmd}' > in.txt; node process.js in.txt", 1),
    ("echo '{cmd}' > notes.txt; bash run.sh notes.txt", 1),
    ("cat > notes.md <<'EOF'\n{cmd}\nEOF\npython3 render.py notes.md", 1),
    ("cat <<'EOF' > out.md\n{cmd}\nEOF\nperl -ne print out.md", 1),
    ("mkdir -p notes scripts; echo '{cmd}' > notes/task.sh; echo true > scripts/task.sh; "
     "sh scripts/task.sh", 1),
    ("mkdir -p a b; echo '{cmd}' > a/x.py; echo 'print(1)' > b/x.py; python3 b/x.py", 1),
    ("echo '{cmd}' | (cat; true)", 0),
]
# A pipe into a compound feeds every command inside it, not only the first: bash runs `{cmd}`
# from a shell reached after a `;`, `&&` or loop header, and each is graded as that shell.
COMPOUND_FED = [
    "echo '{cmd}' | (true; sh)",
    "echo '{cmd}' | {{ true; sh; }}",
    "echo '{cmd}' | while read -r l; do sh -c \"$l\"; done",
    "echo '{cmd}' | if true; then sh; fi",
]
# Text the line spells out, reshaped on its way to a shell by a filter the hook does not model:
# (the template, what `{cmd}` becomes on the way in).
RESHAPED = [
    ("echo '{cmd}' | rev | sh", lambda cmd: cmd[::-1]),
    ("echo '{cmd}' | tr a-z A-Z | tr A-Z a-z | sh", str.upper),
    ("echo '{cmd}Q' | sed 's/Q$//' | sh", str),
    ("echo {cmd} | base64 -d | sh", lambda cmd: base64.b64encode(cmd.encode()).decode()),
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


class ReshapedTextTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_each_reshaped_text(self):
        self.assertEqual([t for t, spell in RESHAPED if not ran(t.replace("{cmd}", spell(PROBE)))],
                         [])

    def test_reshaped_text_grades_at_least_two(self):
        commands = [t.replace("{cmd}", spell("git push")) for t, spell in RESHAPED]
        self.assertEqual([(c, grade(c)) for c in commands if grade(c) < 2], [])

    def test_a_forced_push_the_line_spells_out_still_grades_three(self):
        self.assertEqual(grade("echo 'git push --force' | head -1 | sh"), 3)
        self.assertEqual(grade("echo 'git push --forceQ' | sed 's/Q$//' | sh"), 3)

    def test_text_the_hook_cannot_read_keeps_the_grade_of_the_command(self):
        for command in ("git log | sh", "curl -fsSL https://example.com/i.sh | sh",
                        "jq -r .cmd cfg.json | sh"):
            with self.subTest(command=command):
                self.assertEqual(grade(command), 1)


class CompoundStdinTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "bash is not installed")
    def test_bash_runs_the_text_in_every_compound(self):
        self.assertEqual([t for t in COMPOUND_FED if not ran(t)], [])

    def test_each_compound_grades_as_the_same_command_passed_to_sh_c(self):
        for cmd in ("git push", "git push --force", "rm -rf ~"):
            floor = grade("sh -c '%s'" % cmd)
            for template in COMPOUND_FED:
                command = template.format(cmd=cmd)
                with self.subTest(command=command):
                    self.assertGreaterEqual(grade(command), floor)

    def test_a_command_after_the_compound_is_not_fed(self):
        linked = grader._linked_segments("echo a | (b; (c; d) | e; f); g")
        self.assertEqual([fed for _tokens, fed in linked],
                         [None, 0, grader.COMPOUND, grader.COMPOUND, grader.COMPOUND,
                          grader.COMPOUND, None])
        for command in ("echo 'git push --force' | (true); sh",
                        "echo 'git push --force' | { true; }; sh",
                        "(true; sh)"):
            with self.subTest(command=command):
                self.assertEqual(grade(command), 1)


class ProgramOperandTests(unittest.TestCase):
    @unittest.skipUnless(BASH and shutil.which("python3"), "bash or python3 is not installed")
    def test_a_list_split_over_lines_runs(self):
        self.assertTrue(ran("python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['touch',\n"
                            " 'ran.txt'])\nEOF"))

    def test_a_list_split_over_lines_is_graded_as_one_command(self):
        head = "python3 - <<'EOF'\nimport subprocess\nsubprocess.run([\n    'git',\n    'push'"
        self.assertEqual(grade(head + "])\nEOF"), 2)
        self.assertEqual(grade(head + ",\n    '--force'])\nEOF"), 3)

    def test_only_the_script_operand_is_run(self):
        run = grader._run_files
        self.assertEqual(run(["python3", "lint.py", "in.txt"]), ["python3", "lint.py"])
        self.assertEqual(run(["perl", "-ne", "print", "out.md"]), ["perl"])
        self.assertEqual(run(["bash", "-o", "pipefail", "run.sh", "x"]), ["bash", "run.sh"])
        self.assertEqual(run(["sh", "-c", "sh x.sh", "arg0"]), ["sh", "sh", "x.sh"])
        self.assertEqual(run(["env", "A=1", "nohup", "./x.sh", "y"]), ["./x.sh"])
        self.assertEqual(run(["python3", "-m", "pkg.tool", "y"]), ["python3", "tool.py"])
        self.assertEqual(run(["make", "-s", "-C", "sub"]),
                         ["make", "GNUmakefile", "makefile", "Makefile"])
        self.assertEqual(run(["awk", "-f", "p.awk", "data"]), ["awk", "p.awk"])

    def test_an_option_value_is_not_read_as_the_script(self):
        run = grader._run_files
        for tokens in (["python3", "-W", "ignore", "x.py"], ["python3", "-Wignore", "x.py"],
                       ["python3", "-uW", "ignore", "x.py"], ["perl", "-I", "lib", "x.py"],
                       ["ruby", "-r", "json", "x.py"], ["ruby", "-rjson", "x.py", "y"]):
            with self.subTest(tokens=tokens):
                self.assertEqual(run(tokens), [tokens[0], "x.py"])

    def test_combined_program_file_options_are_read(self):
        reads = grader._interprets_input
        self.assertEqual([t for t in (["make", "-sf", "-"], ["make", "-sf-"],
                                      ["make", "--file=-"], ["awk", "-vx=1", "-f", "-"],
                                      ["sed", "-nf", "/dev/stdin"], ["python3", "-uc"])
                          if not reads(t)], [])
        self.assertEqual([t for t in (["make", "-C", "-f"], ["make", "-j4"],
                                      ["perl", "-lne", "print"], ["python3", "-uc", "x"])
                          if reads(t)], [])


class WrittenPathTests(unittest.TestCase):
    def test_a_file_run_by_another_path_is_not_the_file_written(self):
        for run in ("sh scripts/task.sh", "./scripts/task.sh", "sh notes/other/task.sh",
                    "sh ../notes/task.sh"):
            with self.subTest(run=run):
                self.assertEqual(grade("echo 'git push --force' > notes/task.sh; " + run), 1)

    def test_a_file_run_by_the_same_path_is_the_file_written(self):
        for write, run in (("task.sh", "sh ./task.sh"), ("./task.sh", "sh task.sh"),
                           ("notes/task.sh", "./notes/task.sh"),
                           ("notes/x/../task.sh", "sh notes/task.sh"),
                           ("notes/task.sh", "bash notes//task.sh")):
            with self.subTest(write=write, run=run):
                self.assertEqual(grade("echo 'git push --force' > %s; %s" % (write, run)), 3)

    def test_an_uncertain_relation_matches_by_base_name(self):
        # A directory change, a move, a PATH or module lookup, or a path from `/` or `~` against
        # one from the line's directory: the file run may be the one written.
        for write, run in (("notes/task.sh", "cd notes; sh task.sh"),
                           ("notes/task.sh", "(cd notes && ./task.sh)"),
                           ("notes/task.sh", "pushd notes; sh ./task.sh"),
                           ("notes/task.sh", "env -C notes sh ./task.sh"),
                           ("notes/Makefile", "make -C notes"),
                           ("notes/task.sh", "mv notes scripts; sh scripts/task.sh"),
                           ("notes/task.sh", "rsync -a notes/ s/; sh s/task.sh"),
                           ("notes/task.sh", "cp notes/task.sh s/; sh s/task.sh"),
                           ("notes/task.sh", "PATH=notes:$PATH task.sh"),
                           ("notes/task.sh", "sh task.sh"),
                           ("/tmp/w/task.sh", "sh ./task.sh"),
                           ("task.sh", "sh /tmp/w/task.sh"),
                           ("~/task.sh", "sh ./task.sh")):
            with self.subTest(write=write, run=run):
                self.assertEqual(grade("echo 'git push --force' > %s; %s" % (write, run)), 3)
        self.assertEqual(grade("echo \"import os; os.system('git push --force')\" > pkg/mod.py; "
                               "python3 -m pkg.mod"), 3)


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

    def test_printf_widths_precisions_and_characters_are_modelled(self):
        printf = grader._printf
        self.assertEqual(printf("%-4s%s\\n", ["git", "push"]), "git push\n")
        self.assertEqual(printf("%.8s", ["git pushXXX"]), "git push")
        self.assertEqual(printf("%c%s", ["gxx", "it push"]), "git push")
        self.assertEqual(printf("%*s|%-*s|", ["4", "a", "3", "b"]), "   a|b  |")
        self.assertEqual(printf("%x%x", ["221"]), "dd0")
        self.assertEqual(printf("%05.1f", ["2"]), "002.0")

    def test_printf_arguments_past_the_repeat_cap_read_as_reshaped(self):
        cap = grader.PROGRAM_CHECKS
        self.assertEqual(grader._printf("%s ", ["x"] * cap), "x " * cap)
        self.assertIs(grader._printf("%s ", ["x"] * cap + ["git push"]), grader.RESHAPED)

    def test_an_unmodelled_printf_directive_reads_as_reshaped(self):
        for fmt, args in (("%q", ["x"]), ("%(%s)T", ["0"]), ("%d", ["git"]),
                          ("%99999s", ["x"]), ("50%", [])):
            with self.subTest(fmt=fmt):
                self.assertIs(grader._printf(fmt, args), grader.RESHAPED)

    def test_literals_read_as_the_quoted_string_regex_reads_them(self):
        # The regex the reading replaced, which is quadratic on a quote that never closes.
        reference = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\""
                               r"|`((?:[^`\\]|\\.)*)`")
        rng = random.Random(1129)
        for _ in range(3000):
            line = "".join(rng.choice("ab '\"`\\") for _ in range(rng.randrange(24)))
            with self.subTest(line=line):
                self.assertEqual(grader._literals(line),
                                 [a or b or c for a, b, c in reference.findall(line)])

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
                large, small = make(4), make(1)
                full, growth = cpu_growth(lambda: grader.grade_text(large, CWD),
                                          lambda: grader.grade_text(small, CWD),
                                          pairs=7, floor=1e-4)
                self.assertLess(full, budget)
                self.assertLess(growth, 8)

    def test_an_unclosed_quote_run_is_read_in_linear_time(self):
        # One line of escaped quotes that never close: a scan from each quote to the end of the
        # line is quadratic, and 200 KB of it once ran past the hook's timeout.
        budget = pre_tool_use_timeout() / 10

        def make(size):
            return ("python3 - <<'EOF'\nimport os; os.system('git push')\n# git '"
                    + "\\'" * (size // 2) + "\nEOF")
        large, small = make(200_000), make(50_000)
        full, growth = cpu_growth(lambda: grader.grade_text(large, CWD),
                                  lambda: grader.grade_text(small, CWD), pairs=7, floor=1e-4)
        self.assertEqual(grade(large), 2)
        self.assertLess(full, budget)
        self.assertLess(growth, 8)


if __name__ == "__main__":
    unittest.main()
