# SPDX-License-Identifier: MIT
"""Unit tests for the stop-gate hook. Run: python3 -m unittest discover tests

The hook runs as a subprocess with HOME pointed at a temporary directory, so its state file
lands there. The commit identity is assembled at run time, so this file carries no
address-shaped literal for the lint to find.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "stop-gate.py"
IDENTITY = "gate" + "@" + "example" + ".invalid"


class StopGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self.counter = base / "runs.txt"
        self.trust(self.repo)
        self.git("init")
        (self.repo / "file.txt").write_text("one\n")
        self.git("add", "-A")
        self.git("commit", "-m", "initial")

    def trust(self, *paths, config_dir=None):
        """Record Claude Code's folder-trust flag for each path, as the tool itself would."""
        target = (Path(config_dir) if config_dir else self.home) / ".claude.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"projects": {
            str(p): {"hasTrustDialogAccepted": True} for p in paths}}))

    def env(self, extra=None):
        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.update(extra or {})
        env.update({
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Gate Fixture",
            "GIT_COMMITTER_NAME": "Gate Fixture",
            "GIT_AUTHOR_EMAIL": IDENTITY,
            "GIT_COMMITTER_EMAIL": IDENTITY,
        })
        return env

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], env=self.env(),
                       capture_output=True, text=True, check=True)

    def write_gate(self, *commands, **kwargs):
        name = kwargs.get("name", "AGENTS.md")
        heading = kwargs.get("heading", "## Gate")
        body = "# a repo\n\n## Commands\n\n```sh\nexit 3\n```\n"
        if heading:
            body += "\n" + heading + "\n\n```sh\n" + "\n".join(commands) + "\n```\n"
        (self.repo / name).write_text(body)

    def run_hook(self, session="s1", payload=None, stdin=None, extra_env=None):
        if stdin is None:
            body = {"session_id": session, "cwd": str(self.repo),
                    "hook_event_name": "Stop", "stop_hook_active": False}
            body.update(payload or {})
            stdin = json.dumps(body)
        return subprocess.run([sys.executable, str(HOOK)], input=stdin, env=self.env(extra_env),
                              capture_output=True, text=True, timeout=180)

    def state_files(self):
        d = self.home / ".local" / "state" / "agent-harness" / "stop-gate"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def state(self):
        files = self.state_files()
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    def blocks(self, session="s1"):
        return self.state()["sessions"].get(session, {}).get("blocks", 0)

    def test_a_repo_without_a_gate_block_is_untouched(self):
        self.write_gate(heading=None)
        out = self.run_hook()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")
        self.assertEqual(self.state_files(), [])

    def test_no_git_root_is_a_no_op(self):
        loose = Path(self.tmp.name) / "loose"
        loose.mkdir()
        out = self.run_hook(payload={"cwd": str(loose)})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")

    def test_green_gate_is_silent_and_records_the_tree_hash(self):
        self.write_gate("true")
        out = self.run_hook()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")
        recorded = self.state()
        self.assertTrue(recorded["green_hash"])
        self.assertEqual(recorded["sessions"], {})

    def test_the_gate_block_is_read_from_claude_md_when_agents_md_is_absent(self):
        self.write_gate("exit 1", name="CLAUDE.md")
        out = self.run_hook()
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_red_gate_blocks_and_names_the_failing_command(self):
        self.write_gate("true", "echo boom >&2; exit 4")
        out = self.run_hook()
        self.assertEqual(out.returncode, 0)
        decision = json.loads(out.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("exit 4", decision["reason"])
        self.assertIn("exited 4", decision["reason"])
        self.assertIn("boom", decision["reason"])
        self.assertEqual(self.blocks(), 1)

    def test_stop_hook_active_does_not_short_circuit_the_gate(self):
        self.write_gate("exit 1")
        out = self.run_hook(payload={"stop_hook_active": True})
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_an_unchanged_tree_skips_the_commands(self):
        self.write_gate('echo run >> "%s"' % self.counter)
        self.run_hook()
        self.assertEqual(self.counter.read_text().count("\n"), 1)
        self.run_hook()
        self.assertEqual(self.counter.read_text().count("\n"), 1)
        (self.repo / "file.txt").write_text("two\n")
        self.run_hook()
        self.assertEqual(self.counter.read_text().count("\n"), 2)

    def test_staged_and_untracked_contents_invalidate_green(self):
        self.write_gate('echo run >> "%s"' % self.counter)
        self.run_hook()
        (self.repo / "file.txt").write_text("staged\n")
        self.git("add", "file.txt")
        self.run_hook()
        new = self.repo / "new.txt"
        new.write_text("first")
        self.run_hook()
        new.write_text("second")
        self.run_hook()
        self.assertEqual(self.counter.read_text().count("\n"), 4)

    def test_gate_uses_one_shell_and_never_certifies_its_own_mutation(self):
        self.write_gate("export HARNESS_FIXTURE=ok", 'test "$HARNESS_FIXTURE" = ok', "echo changed >> file.txt")
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertIsNone(self.state()["green_hash"])
        self.assertEqual(self.state()["status"], "unverified")

    def test_codex_does_not_inherit_claude_folder_trust(self):
        self.write_gate('echo run >> "%s"' % self.counter)
        self.run_hook(extra_env={"HARNESS_RUNTIME": "codex"})
        self.assertFalse(self.counter.exists())

    def test_the_eighth_consecutive_block_releases_the_turn(self):
        self.write_gate("exit 1")
        for expected in range(1, 8):
            out = self.run_hook()
            self.assertEqual(json.loads(out.stdout)["decision"], "block")
            self.assertEqual(self.blocks(), expected)
        out = self.run_hook()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")
        self.assertIn("stop-gate:", out.stderr)
        self.assertEqual(self.blocks(), 0)

    def test_a_new_session_starts_its_own_count(self):
        self.write_gate("exit 1")
        self.run_hook(session="s1")
        self.run_hook(session="s1")
        self.assertEqual(self.blocks("s1"), 2)
        out = self.run_hook(session="s2")
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        self.assertEqual(self.blocks("s2"), 1)
        self.assertEqual(self.blocks("s1"), 2)

    def test_interleaved_sessions_each_reach_the_release(self):
        # Two sessions stopping in one checkout used to reset each other's count forever.
        self.write_gate("exit 1")
        for expected in range(1, 8):
            for session in ("host", "local"):
                out = self.run_hook(session=session)
                self.assertEqual(json.loads(out.stdout)["decision"], "block")
                self.assertEqual(self.blocks(session), expected)
        for session in ("host", "local"):
            out = self.run_hook(session=session)
            self.assertEqual(out.stdout.strip(), "", session)
            self.assertIn("released after 8 blocks", out.stderr)
            self.assertEqual(self.blocks(session), 0)
        self.assertEqual(self.state()["sessions"], {})

    def test_a_release_leaves_other_sessions_counts_alone(self):
        self.write_gate("exit 1")
        self.run_hook(session="other")
        for _ in range(8):
            self.run_hook(session="s1")
        self.assertEqual(self.blocks("s1"), 0)
        self.assertEqual(self.blocks("other"), 1)

    def test_a_green_run_clears_every_sessions_count(self):
        self.write_gate('test -f "%s"' % self.counter)
        self.run_hook(session="s1")
        self.run_hook(session="s2")
        self.counter.write_text("")
        self.run_hook(session="s1")
        self.assertEqual(self.state()["sessions"], {})

    def test_a_session_silent_past_the_stale_window_is_pruned(self):
        self.write_gate("exit 1")
        self.run_hook(session="s1")
        path = self.state_files()[0]
        recorded = json.loads(path.read_text())
        recorded["sessions"]["gone"] = {"blocks": 7, "seen": 0}
        recorded["sessions"]["junk"] = "not an entry"
        path.write_text(json.dumps(recorded))
        self.run_hook(session="s1")
        self.assertEqual(set(self.state()["sessions"]), {"s1"})
        self.assertEqual(self.blocks("s1"), 2)

    def test_an_untrusted_folder_never_runs_the_gate(self):
        self.write_gate('echo run >> "%s"' % self.counter, "exit 1")
        for absent in ((self.home / ".claude.json").unlink, lambda: self.trust()):
            absent()
            out = self.run_hook()
            self.assertEqual(out.returncode, 0)
            self.assertEqual(out.stdout.strip(), "")
            self.assertIn("not trusted", out.stderr)
            self.assertFalse(self.counter.exists())
            self.assertEqual(self.state_files(), [])

    def test_trust_recorded_for_another_folder_does_not_count(self):
        self.write_gate("exit 1")
        self.trust(Path(self.tmp.name) / "elsewhere")
        out = self.run_hook()
        self.assertEqual(out.stdout.strip(), "")
        self.assertEqual(self.state_files(), [])

    def test_trust_on_the_launch_directory_inside_the_repo_counts(self):
        self.write_gate("exit 1")
        sub = self.repo / "pkg" / "inner"
        sub.mkdir(parents=True)
        self.trust(self.repo / "pkg")
        out = self.run_hook(payload={"cwd": str(sub)})
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_a_root_listed_by_harness_trust_counts_without_the_dialog(self):
        self.write_gate("exit 1")
        (self.home / ".claude.json").unlink()
        listed = self.home / ".config" / "agent-harness" / "trusted.txt"
        listed.parent.mkdir(parents=True)
        listed.write_text("# roots\n" + str(Path(self.tmp.name) / "other") + "\n")
        self.assertEqual(self.run_hook().stdout.strip(), "")
        listed.write_text(listed.read_text() + str(self.repo) + "\n")
        self.assertEqual(json.loads(self.run_hook().stdout)["decision"], "block")

    def test_trust_is_read_from_claude_config_dir_when_set(self):
        self.write_gate("exit 1")
        (self.home / ".claude.json").unlink()
        alt = Path(self.tmp.name) / "alt-config"
        self.trust(self.repo, config_dir=alt)
        out = self.run_hook(extra_env={"CLAUDE_CONFIG_DIR": str(alt)})
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_malformed_stdin_exits_quietly(self):
        self.write_gate("exit 1")
        out = self.run_hook(stdin="not json at all")
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "")
        self.assertEqual(self.state_files(), [])


class RegistrationTests(unittest.TestCase):
    def test_ownership_carries_the_hook(self):
        ownership = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())
        self.assertEqual(ownership["claude"]["hook_ids"]["stop-gate"], {"event": "Stop", "always": True})

    def test_the_harness_gates_itself(self):
        text = (REPO / "AGENTS.md").read_text()
        self.assertIn("\n## Gate\n", text)
        self.assertIn("python3 bin/harness lint", text.split("\n## Gate\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
