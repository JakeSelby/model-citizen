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

Run: python3 -m unittest discover -s tests
"""
import os
import unittest

from test_governance_binding import Home, grader, make_repo


class Fixture(Home):
    def setUp(self):
        super().setUp()
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
                        "time -p builtin cd ../beta; git push"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_cd_the_shells_read_differently_or_expand_leaves_the_directory_unknown(self):
        for command in ("command cd ../beta && git push",
                        "noglob cd ../beta && git push",
                        "eval cd ../beta && git push",
                        "eval 'cd ../beta'; git push",
                        "{cd,../beta} && git push",
                        "c=cd; $c ../beta; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_command_v_is_not_a_directory_change(self):
        self.assertEqual(self.places("command -v cd; git -C ../beta push"), [str(self.beta)])

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
                        "CDPATH=.. bash -c 'cd beta && git push'"):
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
                        "set -P; echo \"$(cd l/.. && git push)\""):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_the_logical_parent_is_kept_without_the_option(self):
        self.assertEqual(self.places("cd l/..; git push"), [str(self.repo)])


if __name__ == "__main__":
    unittest.main()
