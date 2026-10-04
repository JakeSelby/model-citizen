# SPDX-License-Identifier: MIT
"""The stop gate runs a repository's declared fast block, trusts worktrees of a trusted checkout,
and logs every timeout and forced release with its reason and elapsed time.

Run: python3 -m unittest discover -s tests -p "test_stop_gate*.py"
"""
import contextlib
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "stop-gate.py"
IDENTITY = "gate" + "@" + "example" + ".invalid"


def gate_file(gate=None, stop=None):
    body = "# a repo\n"
    if gate is not None:
        body += "\n## Gate\n\n```sh\n" + "\n".join(gate) + "\n```\n"
    if stop is not None:
        body += "\n## Stop gate\n\n```sh\n" + "\n".join(stop) + "\n```\n"
    return body


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.counter = self.base / "runs.txt"
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        self.trust(self.repo)

    def trust(self, *paths):
        (self.home / ".claude.json").write_text(json.dumps({"projects": {
            str(p): {"hasTrustDialogAccepted": True} for p in paths}}))

    def env(self, extra=None):
        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("HARNESS_RUNTIME", None)
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Gate Fixture",
                    "GIT_COMMITTER_NAME": "Gate Fixture", "GIT_AUTHOR_EMAIL": IDENTITY,
                    "GIT_COMMITTER_EMAIL": IDENTITY})
        env.update(extra or {})
        return env

    def git(self, *args, where=None):
        subprocess.run(["git", "-C", str(where or self.repo), *args], env=self.env(),
                       capture_output=True, text=True, check=True)

    def commit(self, text):
        (self.repo / "AGENTS.md").write_text(text)
        self.git("init")
        self.git("add", "-A")
        self.git("commit", "-m", "initial")

    def run_hook(self, cwd=None, session="s1", extra_env=None):
        stdin = json.dumps({"session_id": session, "cwd": str(cwd or self.repo),
                            "hook_event_name": "Stop", "stop_hook_active": False})
        return subprocess.run([sys.executable, str(HOOK)], input=stdin, env=self.env(extra_env),
                              capture_output=True, text=True, timeout=180)

    def rows(self):
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def decisions(self):
        return [r for r in self.rows() if r.get("kind") == "decision" and r["point"] == "stop-gate"]

    def runs(self):
        return self.counter.read_text().count("\n") if self.counter.exists() else 0


class StopGateBlockTests(Fixture):
    def test_a_declared_stop_gate_runs_instead_of_the_full_gate(self):
        self.commit(gate_file(gate=["exit 7"], stop=['echo run >> "%s"' % self.counter]))
        out = self.run_hook()
        self.assertEqual(out.stdout.strip(), "")
        self.assertEqual(self.runs(), 1)
        self.assertEqual(self.decisions()[-1]["gate_block"], "## Stop gate")

    def test_without_a_stop_gate_the_full_gate_runs(self):
        self.commit(gate_file(gate=['echo run >> "%s"' % self.counter, "exit 7"]))
        out = self.run_hook()
        decision = json.loads(out.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("`## Gate`", decision["reason"])
        self.assertEqual(self.runs(), 1)
        self.assertEqual(self.decisions()[-1]["gate_block"], "## Gate")

    def test_a_red_stop_gate_blocks_and_names_its_block(self):
        self.commit(gate_file(gate=["true"], stop=["exit 5"]))
        decision = json.loads(self.run_hook().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("`## Stop gate`", decision["reason"])

    def test_a_stop_gate_alone_is_enough_to_opt_in(self):
        self.commit(gate_file(stop=["exit 5"]))
        self.assertEqual(json.loads(self.run_hook().stdout)["decision"], "block")

    def test_editing_the_stop_gate_invalidates_a_green_result(self):
        self.commit(gate_file(gate=["true"], stop=['echo run >> "%s"' % self.counter]))
        self.run_hook()
        self.run_hook()
        self.assertEqual(self.runs(), 1)
        (self.repo / "AGENTS.md").write_text(gate_file(
            gate=["true"], stop=['echo run >> "%s"' % self.counter, "true"]))
        self.git("commit", "-am", "edit the stop gate")
        self.run_hook()
        self.assertEqual(self.runs(), 2)


class WorktreeTrustTests(Fixture):
    def setUp(self):
        super().setUp()
        self.commit(gate_file(gate=['echo run >> "%s"' % self.counter, "exit 1"]))
        self.tree = self.base / "trees" / "branch"
        self.git("worktree", "add", "-b", "branch", str(self.tree))

    def test_a_worktree_of_a_trusted_checkout_runs_the_gate(self):
        out = self.run_hook(cwd=self.tree)
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        self.assertEqual(self.runs(), 1)

    def test_a_subdirectory_of_the_worktree_is_trusted_too(self):
        sub = self.tree / "pkg"
        sub.mkdir()
        self.assertEqual(json.loads(self.run_hook(cwd=sub).stdout)["decision"], "block")

    def test_a_worktree_of_an_untrusted_checkout_is_skipped(self):
        self.trust(self.base / "elsewhere")
        out = self.run_hook(cwd=self.tree)
        self.assertEqual(out.stdout.strip(), "")
        self.assertIn("not trusted", out.stderr)
        self.assertEqual(self.runs(), 0)

    def test_trusting_only_the_worktree_does_not_trust_the_main_checkout(self):
        self.trust(self.tree)
        self.assertEqual(self.run_hook(cwd=self.repo).stdout.strip(), "")
        self.assertEqual(self.runs(), 0)

    def test_codex_takes_the_main_checkouts_listed_trust(self):
        listed = self.home / ".config" / "agent-harness" / "trusted.txt"
        listed.parent.mkdir(parents=True)
        listed.write_text(str(self.repo) + "\n")
        out = self.run_hook(cwd=self.tree, extra_env={"HARNESS_RUNTIME": "codex"})
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_main_worktree_names_the_main_checkout_only_from_a_linked_worktree(self):
        hook = load_hook(self.home)
        with mock.patch.dict(os.environ, self.env()):
            self.assertEqual(hook.main_worktree(str(self.tree)), str(self.repo))
            self.assertIsNone(hook.main_worktree(str(self.repo)))


def load_hook(home):
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        spec = importlib.util.spec_from_file_location("stop_gate_fast_under_test", str(HOOK))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ReleaseLoggingTests(Fixture):
    def test_a_forced_release_logs_its_reason_and_elapsed_time(self):
        self.commit(gate_file(gate=["exit 1"]))
        for _ in range(8):
            out = self.run_hook()
        self.assertIn("released after 8 blocks", out.stderr)
        self.assertIn("gate ran", out.stderr)
        row = self.decisions()[-1]
        self.assertEqual(row["deterministic_answer"], "released")
        self.assertIn("released after 8 blocks", row["release_reason"])
        self.assertIsInstance(row["elapsed_seconds"], float)
        blocked = self.decisions()[0]
        self.assertEqual(blocked["deterministic_answer"], "blocked")
        self.assertIn("elapsed_seconds", blocked)
        self.assertNotIn("release_reason", blocked)

    def test_a_timeout_kills_the_gate_and_logs_its_reason_and_elapsed_time(self):
        marker = self.base / "survived.txt"
        # The sleep is a grandchild of the hook; only a process-group kill stops it.
        self.commit(gate_file(gate=['bash -c "sleep 6; echo late > %s"' % marker]))
        hook = load_hook(self.home)
        hook.BUDGET_SECONDS = 1
        stdin = io.StringIO(json.dumps({"session_id": "s1", "cwd": str(self.repo)}))
        err = io.StringIO()
        started = time.monotonic()
        with mock.patch.dict(os.environ, self.env()), mock.patch.object(sys, "stdin", stdin), \
                contextlib.redirect_stderr(err):
            hook.main()
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("gate ran past 1s", err.getvalue())
        row = self.decisions()[-1]
        self.assertEqual(row["deterministic_answer"], "released")
        self.assertIn("ran past 1s", row["release_reason"])
        self.assertGreaterEqual(row["elapsed_seconds"], 1.0)
        outcomes = [r for r in self.rows() if r.get("kind") == "outcome"]
        self.assertEqual(outcomes[-1]["outcome"], "timeout")
        state = json.loads(next((self.home / ".local" / "state" / "agent-harness"
                                 / "stop-gate").glob("*.json")).read_text())
        self.assertEqual(state["status"], "unverified")
        self.assertGreaterEqual(state["elapsed_seconds"], 1.0)
        time.sleep(6)
        self.assertFalse(marker.exists())


class RepositoryStopGateTests(unittest.TestCase):
    """This repository's own stop gate is the fast subset; its `## Gate` stays the full suite."""

    def setUp(self):
        hook = load_hook(Path(tempfile.gettempdir()))
        self.heading, self.commands = hook.stop_commands(REPO)
        self.full = hook.gate_commands(REPO)

    def test_the_hook_runs_the_declared_stop_gate(self):
        self.assertEqual(self.heading, "## Stop gate")

    def test_it_keeps_the_lint_and_bmad_checks(self):
        for command in ("python3 bin/harness lint",
                        "python3 scripts/bmad_issue_sync.py sprint-status --check",
                        "python3 scripts/bmad_issue_sync.py audit"):
            self.assertIn(command, self.commands)

    def test_it_never_runs_the_full_suite_and_the_push_gate_still_does(self):
        self.assertNotIn("python3 -m unittest discover -s tests", self.commands)
        self.assertIn("python3 -m unittest discover -s tests", self.full)

    def test_every_test_selection_matches_a_test_file(self):
        selections = [c for c in self.commands if "unittest" in c]
        self.assertTrue(selections)
        for command in selections:
            pattern = command.split("-p", 1)[1].strip().strip('"')
            self.assertTrue(glob.glob(str(REPO / "tests" / pattern)), command)


if __name__ == "__main__":
    unittest.main()
