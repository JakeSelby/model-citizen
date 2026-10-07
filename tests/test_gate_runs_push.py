# SPDX-License-Identifier: MIT
"""A `git push` needs a green `## Gate` run recorded for the commit it sends.

Without one the grader asks, at any autonomy stance, naming the commit and the last run it saw;
a push of documentation only, a branch deletion and a repository with no gate are not checked.
Every checked push is a decision-log row carrying the record's verdict. The grader runs in
process for the verdicts and through the dispatcher's own entry point for the answer and its row.

Run: python3 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from test_gate_runs import IDENTITY, runs
from test_grade_bash import grader

REPO = Path(__file__).resolve().parent.parent
SCRIPT = ("import sys; sys.path.insert(0, %r)\n"
          "from harness_core import lifecycle\n"
          "lifecycle.main('claude-code', [])\n" % str(REPO / "lib"))


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        patcher = mock.patch.dict(os.environ, self.env())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q")
        (self.repo / "AGENTS.md").write_text("# a repo\n\n## Gate\n\n```sh\ntrue\n```\n")
        self.commit("app.py", "x = 1\n")

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_CONFIG_DIR" and not k.startswith("HARNESS_STANCE_")}
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Gate Fixture", "GIT_COMMITTER_NAME": "Gate Fixture",
                    "GIT_AUTHOR_EMAIL": IDENTITY, "GIT_COMMITTER_EMAIL": IDENTITY})
        return env

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env(),
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, name, text):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change " + name)

    def green(self):
        runs.record(runs.snapshot(self.repo), "## Gate", 0, "test")

    def check(self, command="git push origin main"):
        return grader.gate_push(command, str(self.repo))


class Verdicts(Fixture):
    def test_a_push_with_no_recorded_run_asks_and_names_the_commit(self):
        answer, sentence, verdicts = self.check()
        self.assertEqual((answer, verdicts), ("ask", ["missing"]))
        self.assertIn(self.git("rev-parse", "HEAD")[:12], sentence)
        self.assertIn("no gate run is recorded", sentence)
        self.assertIn("citizen gate", sentence)

    def test_a_green_run_on_the_pushed_commit_lets_it_run(self):
        self.green()
        self.assertEqual(self.check()[0::2], ("allow", ["green"]))

    def test_a_commit_after_the_green_run_asks_again(self):
        self.green()
        self.commit("app.py", "x = 2\n")
        answer, sentence, _ = self.check()
        self.assertEqual(answer, "ask")
        self.assertIn("the last run here was passed", sentence)

    def test_a_run_with_uncommitted_changes_does_not_count(self):
        (self.repo / "app.py").write_text("x = 3\n")
        self.green()
        answer, sentence, _ = self.check()
        self.assertEqual(answer, "ask")
        self.assertIn("with uncommitted changes", sentence)

    def test_the_stop_gate_subset_does_not_count_for_a_push(self):
        runs.record(runs.snapshot(self.repo), "## Stop gate", 0, "stop-gate")
        self.assertEqual(self.check()[0], "ask")

    def test_a_docs_only_push_is_exempt(self):
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("docs/guide.md", "words\n")
        self.assertEqual(self.check()[0::2], ("allow", ["docs"]))
        self.commit("README.md", "more\n")
        self.assertEqual(self.check()[0::2], ("allow", ["docs"]))

    def test_a_first_push_counts_every_commit_the_branch_adds(self):
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("lib.py", "y = 1\n")
        self.commit("docs/guide.md", "words\n")
        self.assertEqual(self.check("git push -u origin topic")[0::2], ("ask", ["missing"]))

    def test_a_push_with_no_resolvable_base_asks(self):
        self.commit("docs/guide.md", "words\n")
        self.assertEqual(self.check()[0::2], ("ask", ["missing"]))

    def test_a_markdown_rule_is_not_documentation(self):
        self.commit("rules/style.md", "be brief\n")
        self.assertEqual(self.check()[0], "ask")

    def test_a_repository_without_a_gate_is_not_checked(self):
        (self.repo / "AGENTS.md").write_text("# a repo\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "drop gate")
        self.assertEqual(self.check()[0::2], ("allow", ["undeclared"]))

    def test_a_push_from_an_unknown_directory_asks(self):
        answer, sentence, verdicts = self.check('cd "$DIR" && git push')
        self.assertEqual((answer, verdicts), ("ask", ["unknown"]))
        self.assertIn("cannot be known", sentence)

    def test_git_c_moves_the_push_to_that_repository(self):
        self.green()
        found = grader.gate_push("git -C %s push" % self.repo, str(self.home))
        self.assertEqual(found[0::2], ("allow", ["green"]))

    def test_a_branch_deletion_or_a_line_that_pushes_nothing_is_not_checked(self):
        self.assertIsNone(self.check("git push origin --delete old-branch"))
        self.assertIsNone(self.check("git status"))
        self.assertIsNone(self.check('echo "git push"'))

    def test_a_deletion_flag_on_a_later_line_does_not_exempt_the_push(self):
        self.assertEqual(self.check("git push origin main\necho -d")[0], "ask")


class Dispatch(Fixture):
    def run_hook(self, command):
        payload = {"hook_event_name": "PreToolUse", "session_id": "s-1", "cwd": str(self.repo),
                   "tool_name": "Bash", "tool_input": {"command": command},
                   "permission_mode": "default"}
        out = subprocess.run([sys.executable, "-c", SCRIPT], input=json.dumps(payload),
                             env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        block = (json.loads(out.stdout) if out.stdout.strip() else {}).get("hookSpecificOutput", {})
        return block.get("permissionDecision"), block.get("permissionDecisionReason", "")

    def rows(self):
        log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in rows if r.get("kind") == "decision" and not r.get("sampled")]

    def test_an_ungated_push_asks_under_the_execute_stance_and_is_logged(self):
        decision, reason = self.run_hook("git push origin main")
        self.assertEqual(decision, "ask")
        self.assertIn("no green `## Gate` run is recorded", reason)
        self.assertEqual([(r["deterministic_answer"], r.get("gate")) for r in self.rows()],
                         [("ask", "missing")])

    def test_a_gated_push_runs_and_is_logged_as_allowed(self):
        self.green()
        decision, _reason = self.run_hook("git push origin main")
        self.assertNotIn(decision, ("ask", "deny"))
        self.assertEqual([(r["deterministic_answer"], r.get("gate")) for r in self.rows()],
                         [("allow", "green")])


class StandaloneHook(Fixture):
    def test_the_hook_script_asks_for_an_ungated_push(self):
        payload = {"tool_name": "Bash", "cwd": str(self.repo), "permission_mode": "default",
                   "tool_input": {"command": "git push origin main"}}
        out = subprocess.run([sys.executable, str(REPO / "policy" / "hooks" / "grade-bash.py")],
                             input=json.dumps(payload), env=self.env(), capture_output=True,
                             text=True, timeout=60)
        block = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(block["permissionDecision"], "ask")
        self.assertIn("citizen gate", block["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
