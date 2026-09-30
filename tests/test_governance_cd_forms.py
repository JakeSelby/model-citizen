# SPDX-License-Identifier: MIT
"""A `cd` the walk follows reaches the directory bash reaches, or leaves it unknown; it never
names a concrete directory bash is not in.

Each bash expectation below was checked with `/bin/bash -c '…; pwd'` (GNU bash 3.2) in a
scratch tree holding `b`, `w/b`, `x` and a symlink `l` to `real/sub`: `builtin cd b`,
`command cd b`, `time cd b`, `eval cd b`, `{cd,b}` and `c=cd; $c b` all go to `b`, where zsh's
`command cd b` stays put; `CDPATH=w; cd b` and `CDPATH=w cd b` go to `w/b` though `b` exists,
where zsh goes to `b`; `HOME=$PWD/x cd` and `v=HOM; eval ${v}E=$PWD/x; cd ~` go to `x`, while
`HOME=$PWD/x cd ~` goes to the caller's home; `set -P; cd l/..` and
`set -o physical; cd l; cd ..` go to `real`, where `cd l/..` alone stays put.

Bash stays put for `x=1 time cd b`, `\\time cd b`, `time time cd b`, `builtin time cd b`,
`builtin -p cd b`, `>f time cd b`, `! time cd b` and `true || cd b`, and goes to `b` for
`command -- cd b`, `builtin -- cd b`, `: ${CDPATH:=w}; cd b` (to `w/b`) and
`cd b || cd x`; zsh stays put for `time -p cd b`, `command -- cd b` and `builtin -- cd b`, and
goes to `b` for `>f time cd b` and `! time cd b`. `cd b || { pwd; }`, `cd b || ( pwd )` and
`cd b || if true; then pwd; fi` print nothing, and the starting directory when `b` is missing;
their `&&` forms print `b`.

Run: python3 -m unittest discover -s tests
"""
import os
import unittest

from test_governance_binding import Home, grader, make_repo


class Fixture(Home):
    def setUp(self):
        super().setUp()
        os.environ.pop("CDPATH", None)  # an inherited search path would move `cd ../beta`
        self.configure("local")
        self.beta = make_repo(self.home / "beta", branch="trunk")
        self.user_policy({"defaults": {"coding.git_push": 2}})

    def places(self, command):
        return [entry[2] for entry in grader.governed_text(command, str(self.repo))
                if entry[0] == "coding.git_push"]

    def assert_unknown(self, command):
        self.assertEqual(self.places(command), [None], command)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask", command)
        self.assertIn("repo:unknown/local", reason, command)

    def assert_beta(self, command):
        self.assertEqual(self.places(command), [str(self.beta)], command)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask", command)
        self.assertIn("repo:beta/trunk", reason, command)


class PrefixedAndExpandedCd(Fixture):
    def test_builtin_and_time_run_the_cd_they_prefix(self):
        for command in ("builtin cd ../beta && git push",
                        "time cd ../beta && git push",
                        "time builtin cd ../beta; git push",
                        "time ! cd ../beta; git push"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_cd_the_shells_read_differently_or_expand_leaves_the_directory_unknown(self):
        for command in ("command cd ../beta && git push",
                        "noglob cd ../beta && git push",
                        "eval cd ../beta && git push",
                        "eval 'cd ../beta'; git push",
                        "{cd,../beta} && git push",
                        "c=cd; $c ../beta; git push",
                        "time -p builtin cd ../beta; git push",
                        "command -- cd ../beta; git push",
                        "command -p -- cd ../beta; git push",
                        "command -pp cd ../beta; git push",
                        "builtin -- cd ../beta; git push",
                        "\\time cd ../beta; git push",
                        "'time' cd ../beta; git push",
                        "! time cd ../beta; git push",
                        ">/dev/null time cd ../beta; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_time_that_is_not_the_reserved_word_runs_no_cd(self):
        for command in ("x=1 time cd ../beta; git push",
                        "time time cd ../beta; git push",
                        "builtin time cd ../beta; git push",
                        "builtin -p cd ../beta; git push"):
            with self.subTest(command=command):
                self.assertEqual(self.places(command), [str(self.repo)], command)

    def test_a_policy_write_after_a_cd_not_followed_is_still_found(self):
        (self.repo / ".agent-harness").mkdir(exist_ok=True)
        for command in ("cd .agent-harness; x=1 time cd ..; echo {} > governance.json",
                        "cd .agent-harness; \\time cd ..; echo {} > governance.json",
                        "command -- cd .agent-harness; echo {} > governance.json",
                        "cd .agent-harness; true || cd ..; echo {} > governance.json",
                        "cd .agent-harness; true ||\ncd ..\necho {} > governance.json",
                        "cd .agent-harness; command -pp cd ..; echo {} > governance.json"):
            with self.subTest(command=command):
                found = grader.governed_text(command, str(self.repo))
                self.assertTrue(grader._policy_hits(command, found), command)

    def test_command_v_is_not_a_directory_change(self):
        self.assertEqual(self.places("command -v cd; git -C ../beta push"), [str(self.beta)])

    def test_command_with_a_printing_or_invalid_option_letter_runs_no_cd(self):
        for command in ("command -pv cd ../beta; git push",
                        "command -pV cd ../beta; git push",
                        "command -px cd ../beta; git push"):
            with self.subTest(command=command):
                self.assertEqual(self.places(command), [str(self.repo)], command)

    def test_a_policy_write_after_builtin_cd_is_a_level_one_action(self):
        answer, reason = self.bash("builtin cd .agent-harness && echo {} > governance.json")
        self.assertEqual(answer, "ask")
        self.assertIn("level 1", reason)


class CdPath(Fixture):
    def setUp(self):
        super().setUp()
        (self.repo / "beta").mkdir()  # bash still goes to CDPATH's `beta`

    def test_cdpath_set_in_the_line_leaves_a_searched_operand_unknown(self):
        for command in ("CDPATH=..; cd beta; git push",
                        "CDPATH=.. cd beta && git push",
                        "export CDPATH=..; cd beta; git push",
                        "CDPATH=..; echo \"$(cd beta && git push)\"",
                        "CDPATH=.. bash -c 'cd beta && git push'",
                        ": ${CDPATH:=..}; cd beta; git push",
                        ": ${CDPATH=..}; cd beta; git push",
                        ": ${CDPATH:?}; cd beta; git push",
                        "declare CDPATH=..; cd beta; git push",
                        "read CDPATH <<< ..; cd beta; git push",
                        "printf -v CDPATH ..; cd beta; git push",
                        "v=CDPATH; read $v <<< ..; cd beta; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_cdpath_in_the_environment_leaves_a_searched_operand_unknown(self):
        os.environ["CDPATH"] = str(self.home)
        self.assert_unknown("cd beta && git push")

    def test_an_operand_cdpath_never_searches_still_resolves(self):
        for command in ("CDPATH=/nowhere; cd %s; git push" % self.beta,
                        "CDPATH=/nowhere; cd ~/beta; git push",
                        "CDPATH=/nowhere; cd ./beta; cd %s; git push" % self.beta):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_dot_dot_operand_under_cdpath_is_not_followed(self):
        # CDPATH itself skips `../beta`; the walk trusts no `..` once CDPATH or a physical
        # option may be set, since it keeps one flag for both.
        self.assert_unknown("CDPATH=/nowhere; cd ../beta; git push")

    def test_an_expansion_in_an_assigned_value_cannot_name_cdpath(self):
        for command in ("export PATH=$HOME/bin:$PATH; cd ../beta; git push",
                        "declare x=`pwd`; cd ../beta; git push"):
            with self.subTest(command=command):
                self.assert_beta(command)


class CdThatMayNotRun(Fixture):
    def test_a_cd_after_a_list_operator_or_in_a_compound_leaves_the_directory_unknown(self):
        for command in ("true || cd ../beta; git push",
                        "true && cd ../beta; git push",
                        "cd ../beta || cd ../alpha; git push",
                        "! cd ../beta; git push",
                        "if true; then cd ../beta; fi; git push",
                        "while false; do cd ../beta; done; git push",
                        "for d in x; do cd ../beta; done; git push",
                        "case x in x) cd ../beta;; esac; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_cd_on_the_line_after_a_list_or_pipe_operator_may_not_run(self):
        for command in ("false &&\ncd ../beta\ngit push",
                        "true ||\ncd ../beta\ngit push",
                        "true |\ncd ../beta\ngit push",
                        "true |&\ncd ../beta\ngit push",
                        "true ||\n\ncd ../beta\ngit push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_command_past_a_list_operator_after_a_cd_is_unknown(self):
        for command in ("cd ../beta || git push",
                        "cd ../beta && true || git push",
                        "cd ../beta || { git push; }",
                        "cd ../beta || ( git push )",
                        "cd ../beta || if true; then git push; fi",
                        "cd ../beta || { true; { git push; }; }",
                        "cd ../beta && true || { git push; }"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_command_that_runs_only_where_the_cd_succeeded_is_there(self):
        for command in ("if cd ../beta; then git push; fi",
                        "if true && cd ../beta; then true; git push; fi",
                        "[ -d ../beta ] && cd ../beta && git push",
                        "true && cd ../beta && true && git push",
                        "true || true && cd ../beta && git push",
                        "true && cd .. && cd beta && git push",
                        "true && cd ../beta && { git push; }",
                        "cd ../beta && { git push; }",
                        "cd ../beta && ( git push )",
                        "cd ../beta && if true; then git push; fi"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_command_that_may_run_where_the_cd_failed_is_unknown(self):
        for command in ("true && ! cd ../beta && git push",
                        "true && time ! cd ../beta && git push",
                        "true || cd ../beta && git push",
                        "cd ../alpha || true && cd ../beta && git push",
                        "if ! cd ../beta; then git push; fi",
                        "if cd ../beta || true; then git push; fi",
                        "if cd ../beta; true; then git push; fi",
                        "if cd ../beta; then true || cd ..; git push; fi",
                        "for d in x; do true && cd ../beta && git push; done",
                        "for d in x; do if cd ../beta; then git push; fi; done"):
            with self.subTest(command=command):
                self.assert_unknown(command)
        self.assertEqual(self.places("if cd ../beta; then git push; fi; git push"),
                         [str(self.beta), None])

    def test_a_cd_that_leads_its_list_is_followed(self):
        for command in ("cd ../beta && git push",
                        "cd ../beta || exit; git push",
                        "true || true; cd ../beta && git push"):
            with self.subTest(command=command):
                self.assert_beta(command)


class HomeForTheCd(Fixture):
    def test_a_home_prefixed_to_a_bare_cd_is_where_it_goes(self):
        self.assert_unknown("HOME=%s cd; git push" % self.beta)

    def test_home_assigned_through_eval_leaves_a_later_tilde_unknown(self):
        for command in ("v=HOM; eval ${v}E=%s; cd ~; git push" % self.beta,
                        "v=HOM; eval ${v}E=%s; git -C ~ push" % self.beta):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_tilde_operand_is_expanded_before_the_prefixed_home(self):
        self.assertEqual(self.places("HOME=/tmp/elsewhere cd ~/beta; git push"),
                         [str(self.beta)])


class PhysicalCd(Fixture):
    def setUp(self):
        super().setUp()
        (self.beta / "sub").mkdir()
        os.symlink(str(self.beta / "sub"), str(self.repo / "l"))

    def test_a_physical_option_leaves_a_dot_dot_operand_unknown(self):
        for command in ("set -P; cd l/..; git push",
                        "set -eP; cd l/..; git push",
                        "set -o physical; cd l; cd ..; git push",
                        "setopt chase_links; cd l/..; git push",
                        "set -w; cd l/..; git push",
                        "set -P; echo \"$(cd l/.. && git push)\""):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_the_logical_parent_is_kept_without_the_option(self):
        self.assertEqual(self.places("cd l/..; git push"), [str(self.repo)])


if __name__ == "__main__":
    unittest.main()
