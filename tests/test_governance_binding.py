# SPDX-License-Identifier: MIT
"""The decision provider bound into Bash command grading, and the policy-file guard.

`grade-bash` asks the configured provider about every command the autonomy stance lets through,
classified per simple command, and can only tighten: a provider `ask` is an ask in a prompting
mode and a deny carrying an approval code in auto mode. Under provider `none` nothing changes.
Every test runs under a temporary HOME with its own git repositories.

Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home, without_config_dir

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import decision, lifecycle  # noqa: E402

HOOKS = REPO / "policy" / "hooks"
SESSION = "s-govern"


def _module(name, alias):
    spec = importlib.util.spec_from_file_location(alias, str(HOOKS / name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grader = _module("grade-bash.py", "harness_grade_bash_governance_test")
approvals = _module("approvals.py", "harness_approvals_governance_test")

# Commands across every grade and shape, for the provider-`none` identity check.
CORPUS = ["ls -la", "git status", "git push origin main", "git push --force origin main",
          "git commit -m x", "gh pr merge 12 --squash", "vercel --prod", "rm -rf build",
          "echo hi > out.txt", "cd sub && git push", "echo {} > .agent-harness/governance.json",
          "npm test", "bash -c 'git push'", "terraform apply", "curl -X POST https://example.com"]


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=tester",
                    "-c", "commit.gpgsign=false", "-C", str(cwd)] + list(args),
                   check=True, capture_output=True, text=True)


def make_repo(path, branch="main"):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", branch)
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


class Home(unittest.TestCase):
    stance = "execute"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(os.path.realpath(tmp.name))
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        isolate_home(self.home)
        os.environ["HARNESS_STANCE_AUTONOMY"] = self.stance
        self.repo = make_repo(self.home / "alpha")
        grader._LEDGER[:] = []

    def configure(self, provider=None, **extra):
        config = dict(extra)
        if provider is not None:
            config["governance"] = {"provider": provider}
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config))

    def policy(self, data, repo=None):
        path = (repo or self.repo) / ".agent-harness" / "governance.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def user_policy(self, data):
        path = self.home / ".config" / "agent-harness" / "governance.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def bash(self, command, mode="default", cwd=None, runtime="claude-code"):
        """`(permissionDecision, reason)` the dispatcher gives one Bash call."""
        out = lifecycle.dispatch(runtime, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                           "tool_input": {"command": command},
                                           "session_id": SESSION, "permission_mode": mode,
                                           "cwd": str(cwd or self.repo)})
        block = out.get("hookSpecificOutput", {})
        return block.get("permissionDecision"), block.get("permissionDecisionReason", "")

    def hook(self, command, mode="default", cwd=None):
        """The standalone hook's raw stdout for one Bash call."""
        env = dict(without_config_dir(), HOME=str(self.home),
                   HARNESS_STANCE_AUTONOMY=self.stance)
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(cwd or self.repo), "permission_mode": mode, "session_id": SESSION}
        out = subprocess.run([sys.executable, str(HOOKS / "grade-bash.py")],
                             input=json.dumps(payload), capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def write(self, path, mode="default", tool="Write", runtime="claude-code", content="{}"):
        inputs = {"file_path": str(path), "content": content}
        out = lifecycle.dispatch(runtime, {"hook_event_name": "PreToolUse", "tool_name": tool,
                                           "tool_input": inputs, "session_id": SESSION,
                                           "permission_mode": mode, "cwd": str(self.repo)})
        block = out.get("hookSpecificOutput", {})
        return block.get("permissionDecision"), block.get("permissionDecisionReason", "")

    def rows(self):
        path = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        if not path.is_file():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [r for r in rows if r.get("point") == "governance"]

    def approve(self, reason):
        code = reason.split("`approve ")[1][:6]
        self.assertEqual(approvals.record(SESSION, "approve " + code), [code])


class ProviderNone(Home):
    """AC 1: provider `none` leaves the hook's output byte-identical."""

    def main_output(self, command, mode):
        """The standalone hook's stdout, run in process so the corpus stays fast."""
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(self.repo), "permission_mode": mode, "session_id": SESSION}
        out, old = io.StringIO(), sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            with contextlib.redirect_stdout(out):
                grader.main()
        finally:
            sys.stdin = old
        return out.getvalue()

    def outputs(self):
        return [(mode, command, self.main_output(command, mode), self.bash(command, mode))
                for mode in ("default", "auto", "bypassPermissions") for command in CORPUS]

    def test_none_is_byte_identical_to_no_governance_at_all(self):
        self.policy({"defaults": {"coding.git_push": 1, "coding.shell_exec": 1}})
        baseline = self.outputs()
        self.configure("none")
        self.assertEqual(self.outputs(), baseline)
        self.configure(None)
        self.assertEqual(self.outputs(), baseline)
        self.assertEqual(self.rows(), [])
        # The same corpus and policy under `local` does change the output, so the check bites.
        self.configure("local")
        self.assertNotEqual(self.outputs(), baseline)

    def test_none_imports_no_provider_and_writes_no_row(self):
        self.configure("none")
        sys.modules.pop("harness_core.decision", None)
        try:
            self.assertIsNone(grader.govern("git push origin main", str(self.repo), 2, "execute"))
            self.assertNotIn("harness_core.decision", sys.modules)
        finally:
            sys.modules["harness_core.decision"] = decision
        self.assertEqual(self.rows(), [])

    def test_none_leaves_a_policy_file_write_ungated(self):
        self.configure("none")
        self.assertEqual(self.write(self.repo / ".agent-harness" / "governance.json"), (None, ""))


class Levels(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")

    def test_a_level_two_push_asks_in_a_prompting_mode_naming_level_and_source(self):
        path = self.policy({"pairs": {"repo:alpha/main": {"coding.git_push": 2}}})
        answer, reason = self.bash("git push origin main")
        self.assertEqual(answer, "ask")
        self.assertIn("coding.git_push on repo:alpha/main is level 2", reason)
        self.assertIn(str(path), reason)
        self.assertIn("pairs.repo:alpha/main.coding.git_push", reason)

    def test_a_level_two_push_denies_with_a_code_in_auto_mode_and_runs_once_approved(self):
        self.policy({"pairs": {"repo:alpha/main": {"coding.git_push": 2}}})
        answer, reason = self.bash("git push origin main", "auto")
        self.assertEqual(answer, "deny")
        self.assertIn("level 2", reason)
        self.assertIn("`approve ", reason)
        self.approve(reason)
        self.assertEqual(self.bash("git push origin main", "auto"), (None, ""))
        self.assertEqual(self.bash("git push origin main", "auto")[0], "deny")

    def test_the_standalone_hook_asks_and_denies_the_same_way(self):
        self.policy({"pairs": {"repo:alpha": {"coding.git_push": 2}}})
        ask = json.loads(self.hook("git push origin main"))["hookSpecificOutput"]
        self.assertEqual(ask["permissionDecision"], "ask")
        deny = json.loads(self.hook("git push origin main", "auto"))["hookSpecificOutput"]
        self.assertEqual(deny["permissionDecision"], "deny")
        self.assertIn("`approve ", deny["permissionDecisionReason"])

    def test_a_level_three_pair_prints_nothing(self):
        self.policy({"pairs": {"repo:alpha/main": {"coding.git_push": 3}}})
        self.assertEqual(self.hook("git push origin main"), "")
        self.assertEqual(self.bash("git push origin main"), (None, ""))

    def test_the_provider_never_relaxes_a_grade_the_grader_gates(self):
        self.policy({"pairs": {"repo:alpha/main": {"coding.git_push": 3}}})
        answer, reason = self.bash("git push --force origin main")
        self.assertEqual(answer, "ask")
        self.assertNotIn("Governance", reason)
        self.assertEqual(self.rows(), [])

    def test_a_deploy_asks_under_the_built_in_cap(self):
        self.assertEqual(self.bash("vercel deploy")[0], "ask")

    def test_a_provider_deny_is_a_deny_in_every_mode(self):
        class Denier:
            name = "deny-all"

            def decide(self, action, counterparty, context=None):
                return decision.Decision("deny", 1, "deny-all", "refused by test")

        original = decision.select_provider
        decision.select_provider = lambda *a, **k: Denier()
        self.addCleanup(setattr, decision, "select_provider", original)
        answer, reason = self.bash("git commit -m x")
        self.assertEqual(answer, "deny")
        self.assertIn("refused by test", reason)
        self.assertNotIn("`approve ", reason)


class Classification(Home):
    """AC 5: each simple command's class, and the strictest across a compound command."""

    def classes(self, command):
        return [entry[0] for entry in grader.governed_text(command, str(self.repo))]

    def test_each_verb_family_has_its_class(self):
        cases = {"git push origin main": ["coding.git_push"],
                 "git commit -m x": ["coding.git_commit"],
                 "gh pr merge 12 --squash": ["coding.pr_merge"],
                 "vercel --prod": ["coding.deploy"],
                 "fly deploy": ["coding.deploy"],
                 "netlify deploy": ["coding.deploy"],
                 "npm test": ["coding.shell_exec"],
                 "git status": ["coding.shell_exec"]}
        for command, expected in cases.items():
            self.assertEqual(self.classes(command), expected, command)

    def test_wrappers_and_shell_text_are_looked_through(self):
        for command in ("timeout 60 git push", "env FOO=1 git push", "bash -c 'git push'",
                        "nice -n 5 git push origin main"):
            self.assertIn("coding.git_push", self.classes(command), command)

    def test_a_compound_command_takes_the_strictest_answer(self):
        self.configure("local")
        self.policy({"defaults": {"coding.git_push": 2, "coding.git_commit": 3}})
        answer, reason = self.bash("git add -A && git commit -m x && git push")
        self.assertEqual(answer, "ask")
        self.assertIn("coding.git_push", reason)
        self.assertEqual(len(self.rows()), 3)


class Counterparty(Home):
    """AC 6: `git -C` and a leading `cd` name that directory's repository and branch."""

    def setUp(self):
        super().setUp()
        self.configure("local")
        self.beta = make_repo(self.home / "beta", branch="trunk")
        self.policy({"pairs": {"repo:beta/trunk": {"coding.git_push": 2}}}, repo=self.beta)

    def test_git_dash_c_names_the_other_repository(self):
        self.assertEqual(self.bash("git push")[0], None)
        answer, reason = self.bash("git -C %s push" % self.beta)
        self.assertEqual(answer, "ask")
        self.assertIn("repo:beta/trunk", reason)

    def test_git_dash_c_expands_the_current_users_home(self):
        answer, reason = self.bash("git -C ~/beta push")
        self.assertEqual(answer, "ask")
        self.assertIn("repo:beta/trunk", reason)

    def test_git_dash_c_resolves_an_earlier_static_assignment(self):
        for operand in ("$WORKTREE", '"$WORKTREE"', "${WORKTREE}", '"${WORKTREE}"'):
            with self.subTest(operand=operand):
                answer, reason = self.bash("WORKTREE=%s; git -C %s push" %
                                           (self.beta, operand))
                self.assertEqual(answer, "ask")
                self.assertIn("repo:beta/trunk", reason)

    def test_a_leading_cd_names_the_other_repository(self):
        answer, reason = self.bash("cd ../beta && git push")
        self.assertEqual(answer, "ask")
        self.assertIn("repo:beta/trunk", reason)

    def test_a_worktree_is_named_for_its_repository_not_its_directory(self):
        worktree = self.home / "task-directory"
        git(self.repo, "worktree", "add", "-q", str(worktree), "-b", "topic")
        self.assertEqual(decision.locate(str(worktree)), ("repo:alpha/topic", str(worktree)))
        self.user_policy({"pairs": {"repo:alpha": {"coding.git_push": 1}}})
        answer, reason = self.bash("git push", cwd=worktree)
        self.assertEqual(answer, "ask")
        self.assertIn("repo:alpha/topic", reason)

    def test_the_repository_name_reads_the_common_directory(self):
        self.assertEqual(decision.repository_name("/w/task", "/src/alpha/.git"), "alpha")
        self.assertEqual(decision.repository_name("/w/task", "/srv/alpha.git"), "alpha")
        self.assertEqual(decision.repository_name("/w/task", ""), "task")


class UnknownDirectory(Home):
    """A segment after a directory change the walk cannot know is governed as
    `repo:unknown/local`, so no pair of the starting repository applies to it."""

    def setUp(self):
        super().setUp()
        self.configure("local")
        self.other = make_repo(self.home / "other")
        # The starting repository allows a push; the class default and `other` ask for one.
        self.user_policy({"defaults": {"coding.git_push": 2},
                          "pairs": {"repo:alpha": {"coding.git_push": 3}}})

    def places(self, command):
        return [(entry[0], entry[2]) for entry in grader.governed_text(command, str(self.repo))
                if entry[0] == "coding.git_push"]

    def assert_unknown(self, command):
        self.assertEqual(self.places(command), [("coding.git_push", None)], command)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask", command)
        self.assertIn("repo:unknown/local", reason, command)

    def test_the_starting_repository_allows_a_plain_push(self):
        self.assertIsNone(self.bash("git push")[0])

    def test_regression_cd_dash_is_unknown(self):
        self.assert_unknown("cd - && git push")

    def test_regression_a_substitution_runs_in_the_directory_its_segment_has(self):
        command = 'cd ../other && echo "$(git push)"'
        self.assertEqual(self.places(command), [("coding.git_push", str(self.other))])
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask")
        self.assertIn("repo:other/main", reason)
        self.assertNotIn("repo:alpha", reason)

    def test_a_variable_target_is_unknown(self):
        self.assert_unknown('cd "$D" && git push')

    def test_pushd_to_a_literal_repository_resolves_and_popd_is_unknown(self):
        answer, reason = self.bash("pushd ../other && git push")
        self.assertEqual(answer, "ask")
        self.assertIn("repo:other/main", reason)
        self.assert_unknown("popd && git push")
        self.assert_unknown("pushd && git push")

    def test_a_pushd_to_a_literal_that_is_no_repository_is_unknown(self):
        (self.home / "plain").mkdir()
        answer, reason = self.bash("pushd ../plain && git push")
        self.assertEqual(answer, "ask")
        self.assertIn("repo:unknown/local", reason)

    def test_a_cd_in_a_subshell_pipeline_or_substitution_is_unknown(self):
        self.assert_unknown("(cd ../other && true) ; git push")
        self.assert_unknown("cd ../other | true; git push")
        self.assert_unknown("echo $(cd ../other && git push)")

    def test_a_dynamic_git_dash_c_or_env_chdir_is_unknown(self):
        self.assert_unknown("git -C $(pwd) push")
        self.assert_unknown("env -C ../other git push")
        self.assert_unknown("git --git-dir=../other/.git push")

    def test_unresolved_git_dash_c_names_the_operand_and_requests_a_literal(self):
        answer, reason = self.bash("git -C $MISSING push")
        self.assertEqual(answer, "ask")
        self.assertIn("repo:unknown/local", reason)
        self.assertIn("Git -C operand `$MISSING` could not be resolved", reason)
        self.assertIn("pass the repository path literally", reason)

    def test_assignments_with_dynamic_or_ambiguous_scope_stay_unknown(self):
        spaced = self.home / "other path"
        cases = (
            "WORKTREE=$(pwd); git -C $WORKTREE push",
            "WORKTREE=../oth*; git -C $WORKTREE push",
            "WORKTREE=%s; git -C '$WORKTREE' push" % self.other,
            r"WORKTREE=%s; git -C \$WORKTREE push" % self.other,
            "WORKTREE='~/other'; git -C $WORKTREE push",
            'WORKTREE="%s"; git -C "$WORKTREE" push' % spaced,
            "WORKTREE=%s git -C $WORKTREE push" % self.other,
            "WORKTREE=%s && git -C $WORKTREE push" % self.other,
            "WORKTREE=%s | true; git -C $WORKTREE push" % self.other,
            "WORKTREE=%s & wait; git -C $WORKTREE push" % self.other,
            "(WORKTREE=%s); git -C $WORKTREE push" % self.other,
            "WORKTREE=%s; if true; then git -C $WORKTREE push; fi" % self.other,
            "true; WORKTREE=%s; git -C $WORKTREE push" % self.other,
        )
        for command in cases:
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_quote_and_environment_semantics_never_turn_dynamic_paths_literal(self):
        cases = (
            "git -C '~/other' push",
            "WORKTREE=%s; git -C '$'WORKTREE push" % self.other,
            "HOME=/unsafe; git -C ~/other push",
            "WORKTREE=/allowed:~/unsafe; git -C $WORKTREE push",
            "typeset -i WORKTREE; WORKTREE=6/2; git -C $WORKTREE push",
            "git -C '~/other' push | cat",
            "git -C '~/other' push & wait",
        )
        for command in cases:
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_assignment_before_a_failed_redirect_is_not_assumed_to_run(self):
        command = ("WORKTREE=/unsafe; WORKTREE=%s </definitely/missing; "
                   "git -C $WORKTREE push" % self.other)
        self.assert_unknown(command)

    def test_transparent_shell_names_its_unresolved_git_operand(self):
        answer, reason = self.bash("bash -c 'git -C $MISSING push'")
        self.assertEqual(answer, "ask")
        self.assertIn("Git -C operand `$MISSING` could not be resolved", reason)
        self.assertIn("pass the repository path literally", reason)

    def test_unresolved_nonpush_operand_is_not_attached_to_a_later_push(self):
        command = "git -C $MISSING status; cd $OTHER; git push"
        self.assert_unknown(command)
        _answer, reason = self.bash(command)
        self.assertNotIn("Git -C operand", reason)

    def test_continued_and_quoted_git_options_keep_operand_semantics(self):
        cases = (
            "WORKTREE=/unsafe; git -\\\nC '$WORKTREE' push",
            "WORKTREE=/unsafe; git \"-C\" '~/beta' push",
        )
        for command in cases:
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_shell_special_assignments_never_supply_static_paths(self):
        for command in ("SECONDS=6/2; git -C $SECONDS push",
                        "IFS=/; WORKTREE=/unsafe; git -C $WORKTREE push"):
            with self.subTest(command=command):
                self.assert_unknown(command)

    def test_eval_names_its_unresolved_git_operand(self):
        answer, reason = self.bash("eval 'git -C $MISSING push'")
        self.assertEqual(answer, "ask")
        self.assertIn("Git -C operand `$MISSING` could not be resolved", reason)

    def test_substitution_operand_diagnostic_stays_with_its_push(self):
        command = 'echo "$(git -C $FIRST push)"; git -C $SECOND push'
        self.assertEqual(self.places(command), [("coding.git_push", None),
                                                ("coding.git_push", None)])
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask")
        self.assertIn("Git -C operand `$FIRST` could not be resolved", reason)
        self.assertNotIn("`$SECOND`", reason)

    def test_a_literal_cd_still_resolves(self):
        answer, reason = self.bash("cd %s && git push" % self.other)
        self.assertEqual(answer, "ask")
        self.assertIn("repo:other/main", reason)
        self.assertIsNone(self.bash("cd %s && git push" % self.repo)[0])


class FailClosed(Home):
    """AC 7: a configured provider that raises asks, or denies with a code, and names the error."""

    def test_a_malformed_policy_asks_and_names_the_policy_error(self):
        self.configure("local")
        self.policy("{not json")
        answer, reason = self.bash("npm test")
        self.assertEqual(answer, "ask")
        self.assertIn("PolicyError", reason)
        answer, reason = self.bash("npm test", "auto")
        self.assertEqual(answer, "deny")
        self.assertIn("PolicyError", reason)
        self.assertIn("`approve ", reason)
        self.assertEqual(self.rows()[-1]["deterministic_answer"], "ask")

    # `cd $(cat x)` grades 0 here, proved read-only, so it is rightly never governed; the lines
    # below grade 1 while the segment walk used to find no graded segment in them.
    HIDDEN = ("cd sub 2> err.log && ls", "cd $(cat x) 2> err.log && ls")

    def test_regression_an_unknown_provider_tightens_a_command_with_no_graded_segment(self):
        self.configure("no-such-provider")
        self.assertEqual(self.bash("cd $(cat x)"), ("allow", ""))
        for command in self.HIDDEN:
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("no-such-provider", reason, command)
            self.assertEqual(self.bash(command, "auto")[0], "deny", command)

    def test_regression_a_malformed_policy_tightens_a_command_with_no_graded_segment(self):
        self.configure("local")
        self.policy("{not json")
        for command in self.HIDDEN:
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("PolicyError", reason, command)

    def test_a_malformed_policy_fails_before_any_segment_is_judged(self):
        self.configure("local")
        self.policy("{not json")
        answer = grader.govern("git status > log.txt", str(self.repo), 1, "execute")
        self.assertEqual(answer[0], "ask")
        self.assertIn("provider local could not answer", answer[1])

    def test_an_unknown_provider_name_asks(self):
        self.configure("no-such-provider")
        answer, reason = self.bash("npm test")
        self.assertEqual(answer, "ask")
        self.assertIn("no-such-provider", reason)

    def test_a_provider_that_raises_asks_naming_the_exception(self):
        self.configure("local")

        def broken(*args, **kwargs):
            raise RuntimeError("provider exploded")

        original = decision.select_provider
        decision.select_provider = broken
        self.addCleanup(setattr, decision, "select_provider", original)
        answer, reason = self.bash("npm test")
        self.assertEqual(answer, "ask")
        self.assertIn("RuntimeError: provider exploded", reason)


class PolicyWrites(Home):
    """AC 8: an agent write to a governance policy file is a level-1 action."""

    def setUp(self):
        super().setUp()
        self.configure("local")

    def test_bash_writes_to_the_repository_policy_ask(self):
        for command in ("echo {} > .agent-harness/governance.json",
                        "tee .agent-harness/governance.json < x",
                        "sed -i '' s/2/3/ .agent-harness/governance.json",
                        "cp /tmp/p.json .agent-harness/governance.json",
                        "mv .agent-harness/governance.json /tmp/old.json",
                        "cd .agent-harness && echo {} > governance.json",
                        "python3 -c 'open(\".agent-harness/governance.json\",\"w\")'"):
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("level 1", reason, command)

    def test_bash_writes_to_the_user_policy_ask(self):
        answer, _ = self.bash("echo {} > ~/.config/agent-harness/governance.json")
        self.assertEqual(answer, "ask")
        answer, _ = self.bash("cd ~/.config/agent-harness && tee governance.json < x")
        self.assertEqual(answer, "ask")

    def test_a_policy_write_under_an_unknown_directory_is_gated_by_its_name(self):
        for command in ('cd "$T" && echo x > governance.json',
                        'cd "$(git rev-parse --show-toplevel)/.agent-harness" && '
                        "sed -i '' s/2/3/ governance.json",
                        'cd "$T" && tee config.json < x',
                        'echo x > "$T"/governance.json'):
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("level 1", reason, command)

    def test_another_write_under_an_unknown_directory_is_not_gated(self):
        self.assertIsNone(self.bash('cd "$T" && echo x > notes.txt')[0])

    def test_reading_the_policy_is_not_gated(self):
        self.policy({})
        self.assertEqual(self.bash("cat .agent-harness/governance.json")[0], "allow")

    def test_a_file_tool_write_asks_or_denies_with_a_code_that_covers_it_once(self):
        target = self.repo / ".agent-harness" / "governance.json"
        answer, reason = self.write(target)
        self.assertEqual(answer, "ask")
        self.assertIn("level 1", reason)
        answer, reason = self.write(target, "auto")
        self.assertEqual(answer, "deny")
        self.approve(reason)
        self.assertEqual(self.write(target, "auto", content='{"x": 1}')[0], "deny")
        self.assertEqual(self.write(target, "auto"), (None, ""))
        self.assertEqual(self.write(target, "auto")[0], "deny")

    def test_a_file_tool_write_to_the_user_policy_is_gated_and_codex_denies(self):
        target = self.home / ".config" / "agent-harness" / "governance.json"
        self.assertEqual(self.write(target, tool="Edit")[0], "ask")
        self.assertEqual(self.write(target, runtime="codex")[0], "deny")

    def test_bash_writes_to_the_user_config_ask(self):
        for command in ("echo '{}' > ~/.config/agent-harness/config.json",
                        "cd ~/.config/agent-harness && tee config.json < x",
                        "sed -i '' s/local/none/ ~/.config/agent-harness/config.json",
                        "cp /tmp/c.json $HOME/.config/agent-harness/config.json"):
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("level 1", reason, command)
            self.assertIn("harness configuration", reason, command)

    def test_setting_a_governance_key_asks_and_denies_with_a_code_in_auto_mode(self):
        for command in ("harness config set governance.provider none",
                        "python3 bin/harness config set governance.provider none",
                        "~/repos/h/bin/harness config set 'governance.provider' none",
                        "citizen config set governance.provider none"):
            answer, reason = self.bash(command)
            self.assertEqual(answer, "ask", command)
            self.assertIn("harness config set", reason, command)
        answer, reason = self.bash("harness config set governance.provider none", "auto")
        self.assertEqual(answer, "deny")
        self.assertIn("`approve ", reason)

    def test_other_config_keys_and_reads_are_not_gated(self):
        self.assertIsNone(self.bash("harness config set identity.name x")[0])
        self.assertIsNone(self.bash("harness config get governance.provider")[0])
        self.assertEqual(self.bash("cat ~/.config/agent-harness/config.json")[0], "allow")

    def test_a_file_tool_write_to_the_user_config_asks_or_denies(self):
        target = self.home / ".config" / "agent-harness" / "config.json"
        answer, reason = self.write(target, tool="Edit")
        self.assertEqual(answer, "ask")
        self.assertIn("level 1", reason)
        self.assertEqual(self.write(target, "auto")[0], "deny")

    def test_under_none_the_config_guard_is_off(self):
        self.configure("none")
        target = self.home / ".config" / "agent-harness" / "config.json"
        self.assertEqual(self.write(target), (None, ""))
        self.assertIsNone(self.bash("harness config set governance.provider local")[0])

    def patch(self, path, mode="default"):
        patch = "*** Begin Patch\n*** Delete File: %s\n*** End Patch\n" % path
        out = lifecycle.dispatch("codex", {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                                           "tool_input": {"command": patch}, "session_id": SESSION,
                                           "permission_mode": mode, "cwd": str(self.repo)})
        return out.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_regression_a_delete_only_patch_of_a_policy_file_is_gated(self):
        self.policy({})
        self.assertEqual(self.patch(self.repo / ".agent-harness" / "governance.json"), "deny")
        self.assertEqual(self.patch(".agent-harness/governance.json"), "deny")

    def test_regression_a_delete_only_patch_of_the_approvals_store_is_denied(self):
        store = self.home / ".local" / "state" / "agent-harness" / "approvals" / (SESSION + ".json")
        self.configure(None)
        self.assertEqual(self.patch(store), "deny")

    def test_a_delete_only_patch_of_another_file_is_not_gated(self):
        self.assertIsNone(self.patch(self.repo / "notes.txt"))

    def test_another_json_file_is_not_gated(self):
        self.assertEqual(self.write(self.repo / "governance.json"), (None, ""))


class Rows(Home):
    """AC 9: one decision-log row per governed decision."""

    def test_each_decision_is_one_row_with_class_counterparty_level_grade_and_outcome(self):
        self.configure("local")
        self.policy({"pairs": {"repo:alpha/main": {"coding.git_push": 2}}})
        self.bash("git push origin main")
        self.bash("npm test")
        self.bash("ls")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        first = json.loads(rows[0]["input"])
        self.assertEqual(first, {"action": "coding.git_push", "counterparty": "repo:alpha/main",
                                 "grade": 2, "level": 2, "outcome": "ask", "provider": "local"})
        self.assertEqual(rows[0]["deterministic_answer"], "ask")
        self.assertEqual(rows[0]["module"], "hooks/grade-bash")
        self.assertEqual(json.loads(rows[1]["input"])["outcome"], "allow")
        self.assertNotIn("npm", rows[1]["input"])


if __name__ == "__main__":
    unittest.main()
