# SPDX-License-Identifier: MIT
"""A `~` is expanded only with the HOME bash would use, and a `git -C` operand is named as the
cause of an unknown directory only when the operand itself is not literal.

Each bash expectation below was checked with `bash -c` (GNU bash 3.2):
`HOME=$(pwd)/evil; echo ~/beta`
prints `<cwd>/evil/beta`, `HOME=-x; echo ~/beta` prints `-x/beta`, `echo ~"/beta" ~'/beta'`
prints `~/beta ~/beta` while `echo ~/"beta"` expands, `HOME=/x bash -c 'echo ~'`,
`HOME=/x; bash -c 'echo ~'`, `env HOME=/x bash -c 'echo ~'`, `HOME=/x; echo "$(echo ~)"` and
`HOME=/x eval 'echo ~'` print `/x`, `HOME=/x; cd ~/beta` goes to `/x/beta`, and
`HOME=/x git -C ~/beta …` changes to the caller's `~/beta`, the tilde being expanded first.

Run: python3 -m unittest discover tests
"""
import unittest

from test_governance_binding import Home, grader, make_repo


class Fixture(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")
        self.beta = make_repo(self.home / "beta", branch="trunk")
        self.user_policy({"defaults": {"coding.git_push": 2},
                          "pairs": {"repo:alpha": {"coding.git_push": 3}}})

    def places(self, command):
        return [entry[2] for entry in grader.governed_text(command, str(self.repo))
                if entry[0] == "coding.git_push"]

    def assert_unknown(self, command):
        self.assertEqual(self.places(command), [None], command)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask", command)
        self.assertIn("repo:unknown/local", reason, command)
        self.assertNotIn("repo:beta", reason, command)

    def assert_beta(self, command):
        self.assertEqual(self.places(command), [str(self.beta)], command)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask", command)
        self.assertIn("repo:beta/trunk", reason, command)


class HomeAndTilde(Fixture):
    def test_an_unreadable_home_assignment_never_falls_back_to_the_hooks_home(self):
        for command in ("HOME=$(pwd)/evil; git -C ~/beta push",
                        "HOME=-x; git -C ~/beta push",
                        "HOME=`pwd`/evil; git -C ~ push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_an_unreadable_assignment_leaves_its_variable_unknown(self):
        contexts = grader._assignment_contexts(
            "WORKTREE=/tmp; WORKTREE=%s/evil; true" % grader.PLACEHOLDER,
            [["WORKTREE=/tmp"], ["WORKTREE=%s/evil" % grader.PLACEHOLDER], ["true"]])
        self.assertIs(contexts[2]["WORKTREE"], grader._UNKNOWN_VALUE)
        for command in ("WORKTREE=%s; WORKTREE=$(pwd)/evil; git -C $WORKTREE push" % self.beta,
                        "HOME=%s; HOME=-x; git -C $HOME push" % self.home):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_reassigned_home_moves_a_later_cd(self):
        for command in ("HOME=/tmp/elsewhere; cd ~/beta; git push",
                        "HOME=$(pwd)/evil; cd; git push",
                        "export HOME=/tmp/elsewhere && cd ~/beta && git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_an_inner_shell_or_substitution_inherits_the_reassigned_home(self):
        for command in ("HOME=/tmp/elsewhere bash -c 'git -C ~/beta push'",
                        "HOME=/tmp/elsewhere; bash -c 'git -C ~/beta push'",
                        "env HOME=/tmp/elsewhere bash -c 'git -C ~/beta push'",
                        "HOME=/tmp/elsewhere eval 'git -C ~/beta push'",
                        'HOME=/tmp/elsewhere; echo "$(git -C ~/beta push)"'):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_the_callers_home_still_resolves(self):
        for command in ("git -C ~/beta push",
                        "git -C ~/\"beta\" push",
                        "HOME=/tmp/elsewhere git -C ~/beta push",
                        "cd ~/beta; git push"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_quoted_tilde_prefix_is_not_expanded(self):
        for command in ('git -C ~"/beta" push', "git -C ~'/beta' push"):
            with self.subTest(command=command):
                self.assert_unknown(command)
                self.assertEqual(grader._unresolved_git_c_operands(command, str(self.repo)),
                                 ["~/beta"])


class LiteralOperandUnderUnknownDirectory(Fixture):
    def test_a_literal_relative_operand_is_not_named_as_the_cause(self):
        command = "cd $OTHER; git -C sub push"
        self.assert_unknown(command)
        self.assertEqual(grader._unresolved_git_c_operands(command, str(self.repo)), [None])
        _answer, reason = self.bash(command)
        self.assertNotIn("Git -C operand", reason)

    def test_a_dynamic_operand_under_an_unknown_directory_is_still_named(self):
        command = "cd $OTHER; git -C $MISSING push"
        self.assertEqual(grader._unresolved_git_c_operands(command, str(self.repo)),
                         ["$MISSING"])
        _answer, reason = self.bash(command)
        self.assertIn("Git -C operand `$MISSING` could not be resolved", reason)

    def test_a_later_literal_operand_keeps_the_earlier_unresolved_cause(self):
        command = "git -C $MISSING -C sub push"
        self.assert_unknown(command)
        self.assertEqual(grader._unresolved_git_c_operands(command, str(self.repo)),
                         ["$MISSING"])
        _answer, reason = self.bash(command)
        self.assertIn("Git -C operand `$MISSING` could not be resolved", reason)

    def test_a_later_absolute_operand_clears_the_earlier_cause(self):
        command = "git -C $MISSING -C %s push" % self.repo
        self.assertEqual(grader._unresolved_git_c_operands(command, str(self.repo)), [None])


if __name__ == "__main__":
    unittest.main()
