# SPDX-License-Identifier: MIT
"""A directory change the walk cannot see leaves the directory unknown, and a line the grader
cannot read is graded, never passed.

Each bash expectation was checked with `/bin/bash -c '…; pwd'` (GNU bash 3.2) in a scratch tree
holding `b`, `x` and a script `s.sh` that runs `cd b`: `. ./s.sh` and `source s.sh` go to `b`;
`trap 'cd x' DEBUG; pwd` prints `x`; `trap 'cd x' EXIT; pwd` prints the start, the trap running
only as the shell exits; `shopt -s cdable_vars; name=$PWD/x; cd name` goes to `x`, as zsh's
`setopt cdAble_Vars` does; `enable -n cd; cd x` and `shopt -s expand_aliases; alias cd=:` then
`cd x` on the next line stay put. `trap 'shopt -s cdable_vars' DEBUG; b=../beta; cd b` goes to
`../beta`; `trap 'export HOME=/h' DEBUG; cd ~/beta` and `trap 'x=1; HOME=/h' DEBUG; cd ~/beta`
go to `/h/beta`; `trap 'set -e' DEBUG; cd ../beta` goes to `../beta`.

Run: python3 -m unittest discover -s tests
"""
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_governance_binding import SESSION, approvals, grader
from test_governance_cd_forms import Fixture
from test_grade_bash import cpu_growth, pre_tool_use_timeout

ro = grader.ro

LONG_PREFIXES = ("time", "nice", "command", "env", "nohup", "noglob", "exec")


class UnseenDirectoryChanges(Fixture):
    def test_a_sourced_script_leaves_the_directory_unknown(self):
        for command in (". ./s.sh; git push",
                        "source s.sh && git push",
                        "cd ../beta && source s.sh && git push",
                        "builtin source s.sh; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_no_cd_after_a_sourced_script_is_followed(self):
        # The script may leave the shell anywhere, or redefine `cd` so an absolute one stays put.
        (self.repo / "c.sh").write_text("cd() { :; }\n")
        for command in ("source .venv/bin/activate && cd ../beta && git push",
                        "source .venv/bin/activate && cd %s && git push" % self.beta,
                        ". ./s.sh; cd ~/beta; git push",
                        "source c.sh; cd %s; git push" % self.beta,
                        "source /dev/stdin <<< 'cd ..'; cd %s; git push" % self.beta,
                        ". <(echo 'cd() { :; }'); cd %s; git push" % self.beta):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_trap_or_definition_the_walk_cannot_read_leaves_every_cd_unknown(self):
        for command in ("eval \"trap 'cd ..' DEBUG\"; cd %s; git push",
                        "t=trap; $t 'cd ..' DEBUG; cd %s; git push",
                        "eval 'cd(){ :; }'; cd %s; git push",
                        "eval \"$(echo cd ..)\"; cd %s; git push",
                        "functions[cd]=':'; cd %s; git push",
                        "autoload -Uz cd; cd %s; git push",
                        "disable cd; cd %s; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command % self.beta)

    def test_a_shell_that_reads_a_startup_file_starts_in_an_unknown_directory(self):
        for command in ("BASH_ENV=t.sh bash -c 'cd %s; git push'",
                        "export BASH_ENV=t.sh; bash -c 'cd %s; git push'",
                        "env BASH_ENV=t.sh bash -c 'cd %s; git push'",
                        "ZDOTDIR=. zsh -c 'cd %s; git push'",
                        "source s.sh; bash -c 'cd %s; git push'"):
            with self.subTest(command=command):
                self.assert_unknown(command % self.beta)
        self.assert_beta("bash -c 'cd %s; git push'" % self.beta)

    def test_an_inert_trap_function_or_eval_keeps_the_cd(self):
        for command in ("trap 'echo failed' ERR; cd ../beta && git push",
                        "log() { echo \"$@\"; }; cd ../beta && git push",
                        "log(){ echo ${1:-x}; }\ncd ../beta && git push",
                        "f() ( echo hi ); cd ../beta && git push",
                        "eval 'echo hi'; cd %s; git push" % self.beta):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_trap_or_function_that_may_move_is_not_read_as_inert(self):
        for command in ("trap 'echo x; cd ..' ERR; cd ../beta; git push",
                        "trap \"c''d ..\" DEBUG; cd ../beta; git push",
                        "trap 'echo $x' DEBUG; cd ../beta; git push",
                        "f(){ echo }; cd ..; }; cd ../beta; f; git push",
                        "f() { echo '}'; cd ..; }; cd ../beta; f; git push",
                        "f() { \"$@\"; }; cd ../beta; f cd ..; git push",
                        "f() { command cd ..; }; cd ../beta; f; git push",
                        "f(){ { cd ..; }; }; cd ../beta; f; git push",
                        "f() echo; cd ../beta; git push",
                        "pushd() { :; }; cd ../beta; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_trap_that_may_run_leaves_every_later_directory_unknown(self):
        for command in ("trap 'cd ..' DEBUG; git push",
                        "trap 'cd ..' ERR; false; git push",
                        "trap 'cd ..' DEBUG; cd ../beta; git push",
                        "trap 'cd ..' RETURN; cd ../beta && git push",
                        "trap -- 'cd ..' DEBUG; cd ../beta && git push",
                        "trap 'cd ..' INT EXIT; cd ../beta && git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_trap_that_assigns_home_or_changes_cd_resolution_is_not_inert(self):
        for command in ("trap 'shopt -s cdable_vars' DEBUG; b=../beta; cd b; git push",
                        "trap 'setopt cdablevars' DEBUG; b=../beta; cd b; git push",
                        "trap 'set -o physical' DEBUG; cd ../beta/.; git push",
                        "trap 'export HOME=%s' DEBUG; cd ~/beta; git push" % self.repo,
                        "trap 'x=1; HOME=%s' DEBUG; cd ~/beta && git push" % self.repo,
                        "trap 'declare -a HOME[0]=%s' DEBUG; cd ~/beta; git push" % self.repo,
                        "trap 'HOME[0]=%s' DEBUG; cd ~/beta; git push" % self.repo):
            with self.subTest(command=command):
                self.assert_unknown(command)
        self.assert_beta("trap 'set -e' DEBUG; cd ../beta && git push")

    def test_a_sourced_script_or_moving_trap_leaves_home_and_cd_resolution_unknown(self):
        for text in (". ./s.sh; true", "source s.sh; true", "trap 'cd ..' DEBUG; true"):
            parts = grader.segments(text)
            with self.subTest(text=text):
                later = grader._assignment_contexts(text, parts)[-1]
                self.assertTrue(later[grader._HOME_UNKNOWN])
                self.assertTrue(later[grader._CD_UNKNOWN])
        later = grader._assignment_contexts("echo hi; true", grader.segments("echo hi; true"))[-1]
        self.assertFalse(later[grader._HOME_UNKNOWN] or later[grader._CD_UNKNOWN])

    def test_an_exit_trap_or_one_that_runs_no_text_keeps_the_cd(self):
        for command in ("trap 'echo bye' EXIT; cd ../beta && git push",
                        "trap 'echo bye' 0; cd ../beta && git push",
                        "trap - INT; trap '' HUP; trap -p; trap INT; cd ../beta && git push"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_cdable_vars_leaves_a_searched_operand_unknown(self):
        for command in ("shopt -s cdable_vars; b=../beta; cd b; git push",
                        "setopt cdablevars; cd b; git push",
                        "o=cdable_vars; shopt -s $o; cd b; git push"):
            with self.subTest(command=command):
                self.assert_unknown(command)
        self.assert_beta("shopt -s cdable_vars; cd %s; git push" % self.beta)

    def test_a_cd_that_may_not_be_the_builtin_is_not_followed(self):
        for command in ("enable -n cd; cd ../beta; git push",
                        "cd() { :; }; cd ../beta; git push",
                        "cd () { :; }; cd ../beta && git push",
                        "function cd { :; }; cd ../beta; git push",
                        "function cd() { :; }\ncd ../beta\ngit push",
                        "shopt -s expand_aliases; alias cd=:\ncd ../beta; git push",
                        "cd() { :; }; echo \"$(cd ../beta && git push)\"",
                        "cd() { :; }; bash -c 'cd ../beta && git push'"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_function_that_changes_directory_leaves_its_callers_unknown(self):
        for command in ("f(){ cd ..; }; f && git push",
                        "f(){ cd ..; }; cd ../beta; f; git push",
                        "f() { cd %s; }; git push" % self.beta):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_a_quoted_or_argument_function_word_defines_nothing(self):
        for command in ("echo 'x; f() { :; }'; cd ../beta && git push",
                        "echo function f; cd ../beta && git push"):
            with self.subTest(command=command):
                self.assert_beta(command)

    def test_a_policy_write_after_an_unseen_change_is_still_found(self):
        (self.repo / ".agent-harness").mkdir(exist_ok=True)
        for command in ("trap 'cd .agent-harness' DEBUG; echo {} > governance.json",
                        "source s.sh; echo {} > governance.json",
                        "enable -n cd; cd .agent-harness; cd ..; echo {} > governance.json",
                        "cd .agent-harness; cd() { :; }; cd ..; echo {} > governance.json",
                        "shopt -s cdable_vars; cd h; echo {} > governance.json"):
            with self.subTest(command=command):
                found = grader.governed_text(command, str(self.repo))
                self.assertTrue(grader._policy_hits(command, found), command)
                self.assertEqual(self.bash(command)[0], "ask", command)


class LongPrefixChains(Fixture):
    def test_a_thousand_prefix_words_grade_three_without_an_exception(self):
        for word in LONG_PREFIXES:
            command = (word + " ") * 1000 + "git push --force"
            with self.subTest(word=word):
                self.assertGreaterEqual(grader.grade_text(command, str(self.repo))[0], 3)
                answer, _reason = self.bash(command)
                self.assertEqual(answer, "ask")

    def test_a_chain_past_the_cap_keeps_its_write_and_asks(self):
        policy = ".agent-harness/" + "governance.json"
        for word in ("nice", "time", "command"):
            for count in (ro.MAX_PREFIXES + 1, 2 * ro.MAX_PREFIXES + 1):
                command = (word + " ") * count + "echo {} > " + policy
                with self.subTest(word=word, count=count):
                    grade = grader.grade_text(command, str(self.repo))
                    self.assertGreaterEqual(grade[0], 3)
                    self.assertEqual(grade[2], policy)
                    self.assertEqual(self.bash(command)[0], "ask")

    def test_a_chain_past_the_cap_writing_through_a_wrapper_output_file_is_held(self):
        # Past the cap the walk never reaches `time`, so its `-o` file is not named; the
        # capped grade alone must still hold the write.
        policy = ".agent-harness/" + "governance.json"
        command = "nice " * (ro.MAX_PREFIXES + 1) + "/usr/bin/time -o " + policy + " ls"
        self.assertGreaterEqual(grader.grade_text(command, str(self.repo))[0], 3)
        self.assertEqual(self.bash(command)[0], "ask")
        self.assertEqual(self.bash(command, mode="bypassPermissions")[0], "deny")

    def test_a_chain_past_the_cap_writing_the_approval_store_is_denied(self):
        command = "nice " * (ro.MAX_PREFIXES + 1) + "echo '{}' > %s" % approvals.store_path(SESSION)
        self.assertGreaterEqual(grader.grade_text(command, str(self.repo))[0], 3)
        self.assertEqual(self.bash(command, mode="bypassPermissions")[0], "deny")

    def test_a_long_prefix_chain_grades_inside_the_hook_budget(self):
        budget = pre_tool_use_timeout() / 10
        command = "time " * 1000 + "git push --force"
        seconds, _ratio = cpu_growth(
            lambda: (grader.grade_text(command, str(self.repo)),
                     grader.governed_text(command, str(self.repo))),
            lambda: grader.grade_text("git push --force", str(self.repo)), pairs=3)
        self.assertLess(seconds, budget)

    def test_a_short_prefix_chain_is_still_looked_through(self):
        self.assertEqual(grader.grade_text("time nice env nohup ls", str(self.repo))[0], 0)
        self.assertTrue(ro.segment_ok(["time"] * ro.MAX_PREFIXES + ["ls"]))
        self.assertFalse(ro.segment_ok(["time"] * (ro.MAX_PREFIXES + 1) + ["ls"]))
        self.assertFalse(ro.segment_ok(["time"] * 5000 + ["ls"]))

    def test_the_grader_counts_every_prefix_toward_the_cap(self):
        # `exec` and `uv run` are looked through by the grader, the rest by the read-only reader.
        for lead in ("", "exec ", "uv run ", "npx "):
            for word in ("time", "nice"):
                shown = lead.count(" ") - lead.count("run")
                within = lead + (word + " ") * (ro.MAX_PREFIXES - shown) + "ls"
                past = lead + (word + " ") * (ro.MAX_PREFIXES + 1 - shown) + "ls"
                with self.subTest(lead=lead, word=word):
                    self.assertEqual(grader.grade_text(within, str(self.repo))[0], 0)
                    self.assertEqual(grader.grade_text(past, str(self.repo))[:2],
                                     grader.PREFIX_CHAIN[:2])
                    self.assertEqual(self.bash(past)[0], "ask")


class UnreadableCommands(Fixture):
    def test_a_grader_that_raises_grades_the_command_three(self):
        with mock.patch.object(grader.library, "_grade_text", side_effect=RecursionError):
            self.assertEqual(grader.grade_text("ls", str(self.repo)), grader.UNREADABLE)
            with self.assertRaises(RecursionError):  # an inner reading leaves it to the top
                grader.grade_text("ls", str(self.repo), depth=1)

    def test_a_walk_that_raises_governs_a_push_at_an_unknown_directory(self):
        with mock.patch.object(grader.library, "governed_text", side_effect=RecursionError):
            answer, sentence = grader.govern("git push", str(self.repo), 2, "execute")
        self.assertEqual(answer, "ask")
        self.assertIn("coding.git_push on repo:unknown/local", sentence)

    def test_the_read_only_hook_passes_nothing_it_cannot_read(self):
        payload = io.StringIO('{"tool_name": "Bash", "tool_input": {"command": "ls"}}')
        out = io.StringIO()
        with mock.patch.object(ro, "command_ok", side_effect=RecursionError), \
                mock.patch("sys.stdin", payload), redirect_stdout(out):
            ro.main()
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
