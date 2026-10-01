# SPDX-License-Identifier: MIT
"""A wrapper's options never hide what the command it wraps does (#1139).

Bash opens a redirect's file before it starts the wrapper, so `nice -n 5 echo x > f` writes `f`
whatever `nice` then runs: a write to a governance policy file or to the approvals store is
decided through every wrapper exactly as without one. Each wrapper's options are read, and an
option the grader does not know, or an `env -S` string env would interpret itself, makes the
wrapped command unknown, graded 3 rather than passed with nothing emitted. `env` takes any word
holding a `=` as an assignment, so `env 'X%=1' rm -rf /` runs the `rm`.

Every decision is the dispatcher's, under a temporary HOME. Run: python3 -m unittest discover -s tests
"""
import unittest

from test_governance_binding import SESSION, Home, grader

WRAPPERS = ("nice -n 5", "nice --adjustment=5", "nice -5", "env -i", "env -u X", "env -",
            "env -i X=1", "timeout -s KILL 5", "timeout -k 1 --signal=TERM 5",
            "timeout --foreground 5", "stdbuf -o0", "stdbuf -o 0 -eL", "command -p", "nohup",
            "time -p", "uv run", "uv run --with ruff", "uv run --frozen", "npx --yes")
MODES = ("default", "bypassPermissions")


class Fixture(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")
        self.user_policy({"defaults": {"coding.git_push": 2}})

    def decisions(self, command):
        return tuple(self.bash(command, mode=mode)[0] for mode in MODES)


class RecordedWritesSurviveTheWrapper(Fixture):
    def assert_as_unwrapped(self, write, expected):
        self.assertEqual(self.decisions(write), expected, write)
        for wrapper in WRAPPERS:
            command = "%s %s" % (wrapper, write)
            with self.subTest(command=command):
                self.assertEqual(self.decisions(command), expected)

    def test_a_write_to_the_repository_policy_file_asks_through_every_wrapper(self):
        self.assert_as_unwrapped("echo {} > .agent-harness/governance.json", ("ask", "deny"))

    def test_a_write_to_the_user_policy_file_asks_through_every_wrapper(self):
        self.assert_as_unwrapped("cat x >> ~/.config/agent-harness/governance.json",
                                 ("ask", "deny"))

    def test_a_write_to_the_approvals_store_is_refused_through_every_wrapper(self):
        write = "echo '{}' > ~/.local/state/agent-harness/approvals/%s.json" % SESSION
        self.assert_as_unwrapped(write, ("ask", "deny"))
        for wrapper in WRAPPERS:
            command = "%s %s" % (wrapper, write)
            self.assertEqual(grader.grade_text(command, str(self.repo))[0], 3, command)

    def test_a_wrapped_redirect_is_a_local_write(self):
        for wrapper in WRAPPERS:
            command = "%s echo x > out.txt" % wrapper
            with self.subTest(command=command):
                self.assertEqual(grader.grade_text(command, str(self.repo))[0], 1)
                written = [p for e in grader.governed_text(command, str(self.repo)) for p in e[3]]
                self.assertIn(str(self.repo / "out.txt"), written)

    def test_a_wrapper_with_read_options_still_reads_as_what_it_runs(self):
        for wrapper in ("nice -n 5", "timeout -s KILL 5", "stdbuf -o0", "env -i", "command -p"):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(grader.grade_text(wrapper + " ls", str(self.repo))[0], 0)
                self.assertEqual(grader.grade_text(wrapper + " git push --force",
                                                   str(self.repo))[0], 3)


class UnreadableFormsAreNeverPassedSilently(Fixture):
    def assert_refused(self, command, grade=3):
        self.assertEqual(grader.grade_text(command, str(self.repo))[0], grade, command)
        self.assertEqual(self.decisions(command), ("ask", "deny"), command)

    def test_env_takes_every_word_with_an_equals_sign_as_an_assignment(self):
        for command in ("env 'X%=1' git push --force", "env X%=1 rm -rf /",
                        "env 'A B=1' rm -rf /", "env -i 'X%=1' rm -rf /",
                        "env 1=1 git push --force", "env =1 git push --force"):
            with self.subTest(command=command):
                self.assert_refused(command)

    def test_env_split_string_is_read_as_the_command_line_it_holds(self):
        for command in ("env -S 'git push --force'", "env -S'rm -rf /'",
                        "env --split-string='rm -rf /'", "env -S'-i git push --force'",
                        "env -iS 'git push --force'"):
            with self.subTest(command=command):
                self.assert_refused(command)
        self.assertIn("coding.git_push",
                      [e[0] for e in grader.governed_text("env -S 'git push'", str(self.repo))])

    def test_an_env_split_string_env_would_interpret_itself_is_unknown(self):
        for command in ('env -S"echo \'a b\'"', "env -S'echo \"$X\"' ls",
                        "env -S'ls \\_x'"):
            with self.subTest(command=command):
                self.assert_refused(command)

    def test_an_option_a_wrapper_does_not_take_makes_its_command_unknown(self):
        for command in ("nice --adj 5 ls", "nice -z ls", "timeout --bogus 5 ls",
                        "stdbuf -x ls", "command -x ls", "env --bogus ls", "time --bogus ls",
                        "nohup -x ls", "nice --adj 5 echo {} > .agent-harness/governance.json"):
            with self.subTest(command=command):
                self.assert_refused(command)

    def test_an_option_a_runner_may_give_a_value_is_read_both_ways(self):
        for command in ("uv run --with git push --force", "uv run --foo git push --force",
                        "poetry run --bogus rm -rf /", "uv run -p 3.12 rm -rf /"):
            with self.subTest(command=command):
                self.assert_refused(command)
        self.assertIn("coding.git_push", [e[0] for e in grader.governed_text(
            "uv run --with x git push", str(self.repo))])
        self.assertEqual(grader.grade_text("uv run --with ruff ruff check", str(self.repo))[0], 1)


POLICY = ".agent-harness/governance.json"
STORE = "~/.local/state/agent-harness/approvals/%s.json" % SESSION


class WrapperOutputFilesAreWrites(Fixture):
    """`time -o f`, `script f`, `flock f` and `firejail --output=f` write f themselves."""

    FORMS = ("nice time -o {} ls", "command time -o {} ls", "env time -o {} true",
             "/usr/bin/time -o {} ls", "nice time --output={} ls", "command time -ao {} true",
             "exec time -o {} ls", "time -p nice time -o {} ls", "\\time -o {} ls",
             "script -q {} ls", "flock {} true", "firejail --output={} ls")

    def test_a_governed_file_a_wrapper_writes_is_decided_as_a_redirect_to_it(self):
        for form in self.FORMS:
            for path in (POLICY, STORE):
                command = form.format(path)
                with self.subTest(command=command):
                    self.assertEqual(self.decisions(command), ("ask", "deny"))
            written = [p for e in grader.governed_text(form.format("out.txt"), str(self.repo))
                       for p in e[3]]
            self.assertIn(str(self.repo / "out.txt"), written, form)

    def test_the_reserved_word_time_takes_no_output_file(self):
        command = "time -o %s ls" % POLICY
        self.assertEqual(grader.grade_text(command, str(self.repo))[0], 0)
        self.assertNotIn("ask", self.decisions(command))


class EveryWrappedPushIsDecided(Fixture):
    PUSHES = ("nice nice nice nice nice git push", "nice " * 7 + "git push",
              "env $X git push", "$X git push", "builtin command git push",
              "builtin git push", "caffeinate git push", "caffeinate -i -t 5 git push",
              "setsid -w git push", "flock /tmp/l git push", "flock -x /tmp/l -c 'git push'",
              "script -q /dev/null git push", "sandbox-exec -n no-network git push",
              "mise exec node@20 -- git push", "mise x -- git push", "pipenv run git push",
              "bunx git push", "chrt -f 5 git push", "ionice -c 3 git push",
              "taskset -c 1 git push", "unbuffer git push", "firejail --net=none git push",
              "nix-shell -p git --run 'git push'", "poetry run git push", "pnpm exec git push",
              "direnv exec . git push", "asdf exec git push", "doas git push",
              "runuser -u x -- git push", "su -c 'git push'", "pkexec git push",
              "arch -arm64 git push", "timeout -k 5 git push", "xargs git push",
              "somewrapper --flag git push", "uv run " * 6 + "git push")

    def test_a_push_behind_any_wrapper_asks_and_is_refused_where_nothing_can_prompt(self):
        for command in self.PUSHES:
            with self.subTest(command=command):
                self.assertEqual(self.decisions(command), ("ask", "deny"))

    def test_a_long_run_of_wrappers_is_unknown_not_an_error(self):
        for command in ("nice " * 500 + "git push", "nice " * 3000 + "ls",
                        "env " * 1000 + "true", "uv run " * 1000 + "ls",
                        "nice X=1 " * 500 + "true"):
            with self.subTest(command=command[:40]):
                self.assertEqual(grader.grade_text(command, str(self.repo))[0], 3)
                self.assertEqual(self.decisions(command), ("ask", "deny"))

    def test_the_common_wrapped_forms_still_pass(self):
        for command in ("env FOO=1 pytest", "timeout 60 npm test", "nice make", "uv run pytest -q",
                        "uv run --frozen pytest", "command -v git", "caffeinate -i make",
                        "nice git commit -m push", "nice time ls", "arch"):
            with self.subTest(command=command):
                self.assertFalse({"ask", "deny"} & set(self.decisions(command)))


class ReadOnlyWrappers(Fixture):
    def test_arch_is_read_only_only_with_no_command(self):
        self.assertTrue(grader.ro.command_ok("arch"))
        for command in ("arch -arm64 git push --force", "arch -x86_64 rm -rf /",
                        "arch -arm64 tee %s" % POLICY, "arch -arm64 ls"):
            with self.subTest(command=command):
                self.assertFalse(grader.ro.command_ok(command))
        self.assertEqual(self.decisions("arch -arm64 git push --force"), ("ask", "deny"))
        self.assertEqual(self.decisions("arch -arm64 tee %s" % POLICY), ("ask", "deny"))

    def test_a_long_run_of_read_only_wrappers_is_not_approved_and_does_not_raise(self):
        self.assertTrue(grader.ro.command_ok("nice timeout 5 nice ls"))
        self.assertFalse(grader.ro.command_ok("nice " * 1500 + "ls"))

    def test_an_env_split_string_holding_a_comment_is_unknown(self):
        for command in ("env -S'#' git push", "env -S'#' rm -rf /", "env -S '# c' git push",
                        "env --split-string=# git push", "nice env -S'#' git push",
                        "env -S'ls #' git push", "env -S'#' tee %s" % POLICY):
            with self.subTest(command=command):
                self.assertEqual(grader.grade_text(command, str(self.repo))[0], 3)
                self.assertEqual(self.decisions(command), ("ask", "deny"))


if __name__ == "__main__":
    unittest.main()
