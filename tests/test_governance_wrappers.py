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


if __name__ == "__main__":
    unittest.main()
