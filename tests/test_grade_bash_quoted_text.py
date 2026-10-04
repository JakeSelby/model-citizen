# SPDX-License-Identifier: MIT
"""A destructive verb that is only text is not graded as a command; one that runs still is.

Text: a commit message, an argument, a here-document written to a file, a string in an edit
script that Python or Node runs without calling anything that runs a command, and a file the
line writes beside a script it runs. Execution: `bash -c`, `sh -c`, `eval`, `xargs`, `$(…)`, a
body a shell reads, and a program that calls `subprocess`, `os.system`, backticks or `eval`.

Run: python3 -m unittest discover tests
"""
import unittest

from test_grade_bash import CWD, grade, grader

FORCE = "git push --force origin main"
WIPE = "rm -rf /"

# Each `{v}` is a destructive verb that only appears as text.
TEXT = [
    'git commit -m "fix: stop {v} in the hook"',
    "git commit -m 'docs: never {v}' && git log -1",
    "git commit -F - <<'EOF'\nfix: no more {v}\nEOF",
    "cat > notes.md <<'EOF'\nrun {v} to wipe\nEOF",
    "cat <<'EOF' > notes.md\n{v}\nEOF",
    "cat <<EOF > notes.md\n{v}\nEOF",
    'echo "{v}" > notes.txt',
    "printf '%s\\n' '{v}' >> doc.md",
    'grep -n "{v}" file.py',
    # An edit script: Python reads the body and calls nothing that runs a command.
    "python3 - <<'EOF'\nimport re\np = 'doc.md'\ns = open(p).read()\n"
    "s = s.replace('old', '`{v}`')\nopen(p, 'w').write(s)\nEOF",
    "cd /work/repo && python3 - <<'PY'\ncases = ['{v}', 'ls']\nfor c in cases:\n    print(c)\nPY",
    "node - <<'EOF'\nconst s = `{v}`;\nconsole.log(s);\nEOF",
    # A probe written beside the data it reads: the script run is not the data file.
    "S=/tmp/probe; cat > $S/cases.txt <<'EOF'\n{v}\nEOF\npython3 $S/probe.py $S/cases.txt",
    # A Python script written and then run by Python is Python's program, not a shell script.
    "cat > probe.py <<'EOF'\nimport sys\nprint(sys.argv)\nEOF\npython3 probe.py '{v}'",
]

# Each runs the verb, so each grades 3.
RUNS = [
    "bash -c '{v}'",
    'sh -c "{v}"',
    'eval "{v}"',
    "echo $({v})",
    'git commit -m "$({v})"',
    "bash <<'EOF'\n{v}\nEOF",
    "cat > f.md <<EOF\nsee $({v})\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run('{v}', shell=True)\nEOF",
    "python3 - <<'EOF'\nimport os\nos.system('{v}')\nEOF",
    "python3 - <<'EOF'\nexec(\"import os; os.system('{v}')\")\nEOF",
    "perl - <<'EOF'\nmy $x = `{v}`;\nEOF",
    "ruby - <<'EOF'\nsystem '{v}'\nEOF",
    "node - <<'EOF'\nrequire('child_process').execSync('{v}');\nEOF",
    "cat > run.sh <<'EOF'\n{v}\nEOF\nbash run.sh",
    "S=/tmp/probe; cat > $S/run.sh <<'EOF'\n{v}\nEOF\nbash $S/run.sh",
    "cat > $F <<'EOF'\n{v}\nEOF\nsh $G",
]


class QuotedText(unittest.TestCase):
    def test_a_verb_that_is_only_text_is_not_graded_as_a_command(self):
        for verb in (FORCE, WIPE):
            for template in TEXT:
                command = template.format(v=verb)
                with self.subTest(command=command):
                    self.assertLess(grade(command), 3)

    def test_a_verb_that_runs_still_grades_three(self):
        for verb in (FORCE, WIPE):
            for template in RUNS:
                command = template.format(v=verb)
                with self.subTest(command=command):
                    self.assertEqual(grade(command), 3)

    def test_xargs_still_runs_what_it_is_handed(self):
        self.assertEqual(grade("echo x | xargs " + FORCE), 3)

    def test_a_program_whose_reader_is_unknown_is_read_as_before(self):
        command = "$PY - <<'EOF'\ncases = ['%s']\nEOF" % FORCE
        self.assertEqual(grade(command), 3)

    def test_the_inert_check_reads_only_the_families_it_knows(self):
        script = "print('%s')" % FORCE
        self.assertTrue(grader._inert_program(script, {"python"}))
        self.assertFalse(grader._inert_program(script, None))
        self.assertFalse(grader._inert_program(script, {"make"}))
        self.assertFalse(grader._inert_program("x = `%s`" % FORCE, {"perl"}))
        self.assertTrue(grader._inert_program("x = `%s`" % FORCE, {"node"}))


class ApprovalsStore(unittest.TestCase):
    STORE = "~/.local/state/agent-harness/approvals"

    def test_reading_the_store_beside_other_work_is_not_a_write_to_it(self):
        for command in (
                "S=$T/.local/state/agent-harness/approvals; ls $S; cat $S/s.json; ./hook.sh < p.json",
                "cat %s/s.json; make build" % self.STORE,
                "ls %s > /tmp/list.txt; make" % self.STORE,
                "python3 - > cases.txt <<'PY'\nprint('nice cp x %s/s.json')\nPY\nmake test" % self.STORE):
            with self.subTest(command=command):
                self.assertNotEqual(grader.grade_text(command, CWD)[1], "write to")

    def test_every_write_to_the_store_still_grades_three(self):
        for command in (
                "S=$T/.local/state/agent-harness/approvals; ls $S; cp forged.json $S/s.json",
                "export S=%s; touch $S/x" % self.STORE,
                "S=%s; X=\"cp a $S/s.json\"; eval \"$X\"" % self.STORE,
                "cd %s && for f in *; do python3 -c \"open('$f', 'w').write('x')\"; done" % self.STORE,
                # A double-quoted program with a shell expansion can be rewritten by a crafted file name.
                "cd %s && for f in *; do python3 -c \"import json; print(json.load(open('$f')))\"; done"
                % self.STORE,
                "ls %s | xargs rm" % self.STORE,
                "for f in %s/*; do rm $f; done" % self.STORE,
                "ls %s; cat a > %s/s.json" % (self.STORE, self.STORE),
                "bash <<'EOF'\ncp x %s/s.json\nEOF" % self.STORE,
                "cd ~/.local/state/agent-harness && ls approvals && make"):
            with self.subTest(command=command):
                self.assertEqual(grader.grade_text(command, CWD)[:2], (3, "write to"))


if __name__ == "__main__":
    unittest.main()
