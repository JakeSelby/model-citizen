# SPDX-License-Identifier: MIT
"""A program the shell may rewrite before the interpreter runs it is no read of the store.

`python3 -c "…read()${W}"` reads the approvals store as written, but the shell appends whatever
`W` holds, a write included, before Python sees the text. Any expansion outside single quotes
makes the program a possible write; a single-quoted program is read as written.

Run: python3 -m unittest discover tests
"""
import unittest

from test_grade_bash import CWD, grader

STORE = "~/.local/state/agent-harness/approvals"
READ = "import pathlib; print(pathlib.Path('%s/s.json').read_text())" % STORE


class StoreProgramExpansions(unittest.TestCase):
    def test_regression_an_expanded_program_reaching_the_store_is_a_write(self):
        for expansion in ("${WRITER}", "$WRITER", "$(printf x)", "`printf x`"):
            command = "python3 -c \"%s%s\"" % (READ, expansion)
            with self.subTest(command=command):
                self.assertTrue(grader._store_write(command))
                self.assertEqual(grader.grade_text(command, CWD)[:2], (3, "write to"))
        command = "python3 -c '%s'\"$W\"" % READ
        self.assertTrue(grader._store_write(command))

    def test_a_program_the_shell_leaves_alone_still_reads(self):
        for command in ("python3 -c \"%s\"" % READ,
                        "python3 -c '%s; print(\"$HOME\")'" % READ,
                        "python3 -W ignore -c \"%s\"" % READ):
            with self.subTest(command=command):
                self.assertFalse(grader._store_write(command))
                self.assertNotEqual(grader.grade_text(command, CWD)[1], "write to")


if __name__ == "__main__":
    unittest.main()
