# SPDX-License-Identifier: MIT
"""Tests for managed, revisioned Studio drafts."""
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness_drafts_test", str(ROOT / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness_drafts_test", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)
from harness_core.studio import drafts


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repos" / "project"
        self.repo.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test" + "@" + "example.invalid")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        (self.repo / "projection.txt").write_text("projected\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.home = self.root / "home"
        self.config = self.home / ".config" / "agent-harness" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"identity":{"name":"A Name"}}\n', encoding="utf-8")
        self.old_home = os.environ.get("HARNESS_HOME")
        self.old_worktrees = os.environ.get("HARNESS_WORKTREE_ROOT")
        self.old_quiet = os.environ.pop("HARNESS_QUIET", None)
        os.environ["HARNESS_HOME"] = str(self.home)
        os.environ["HARNESS_WORKTREE_ROOT"] = str(self.root / "worktrees")

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HARNESS_HOME", None)
        else:
            os.environ["HARNESS_HOME"] = self.old_home
        if self.old_worktrees is None:
            os.environ.pop("HARNESS_WORKTREE_ROOT", None)
        else:
            os.environ["HARNESS_WORKTREE_ROOT"] = self.old_worktrees
        if self.old_quiet is not None:
            os.environ["HARNESS_QUIET"] = self.old_quiet
        self.temporary.cleanup()

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(
            ["git", "-C", str(cwd or self.repo), *args], check=check,
            capture_output=True, text=True,
        )

    def create_worktree(self, repo, name, branch, base):
        dest, run = harness.create_managed_worktree(repo, name, branch, base, fetch=False)
        if run.returncode:
            raise SystemExit(run.stderr)
        return dest

    def remove_worktree(self, repo, dest, branch, revision):
        status, detail = harness.remove_managed_worktree_exact(repo, dest, branch, revision)
        if status:
            raise SystemExit(detail)

    def create(self, name="tone-down"):
        return drafts.create(
            self.repo, name, "installed", self.config,
            self.create_worktree, self.remove_worktree,
        )

    def checkpoint(self, name, revision, key, files=None, config=None):
        return drafts.checkpoint(
            self.repo, name, revision, key, files=files, config=config,
            check_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )

    def test_create_is_managed_and_copies_config_without_touching_live_state(self):
        live_config = self.config.read_bytes()
        live_checkout = (self.repo / "README.md").read_bytes()
        live_projection = (self.repo / "projection.txt").read_bytes()

        created = self.create()

        path = Path(created["path"])
        self.assertTrue(path.is_dir())
        self.assertEqual(path.parent, (self.root / "worktrees" / "project").resolve())
        self.assertEqual(created["branch"], "draft/tone-down")
        self.assertEqual(self.config.read_bytes(), live_config)
        self.assertEqual((self.repo / "README.md").read_bytes(), live_checkout)
        self.assertEqual((self.repo / "projection.txt").read_bytes(), live_projection)
        self.assertFalse((path / ".agent-harness" / "draft-config.json").exists())
        private = drafts._paths(path)
        self.assertEqual(private["config"].read_bytes(), live_config)
        self.assertEqual(private["config"].stat().st_mode & 0o777, 0o600)
        self.assertEqual([item["name"] for item in drafts.list_drafts(self.repo)], ["tone-down"])

    def test_create_accepts_only_an_exact_release_tag_as_an_alternate_base(self):
        self.git("tag", "v0.1.0")
        created = drafts.create(
            self.repo, "release", "v0.1.0", self.config,
            self.create_worktree, self.remove_worktree,
        )
        self.assertEqual(created["base"], "v0.1.0")
        self.assertEqual(created["base_revision"], drafts._revision(self.repo, "refs/tags/v0.1.0"))
        with self.assertRaises(drafts.DraftError) as invalid:
            drafts.create(
                self.repo, "escape", "HEAD~1", self.config,
                self.create_worktree, self.remove_worktree,
            )
        self.assertEqual(invalid.exception.code, "invalid-base")

    def test_checkpoint_is_versioned_exactly_once_and_live_files_remain_unchanged(self):
        created = self.create()
        live_config = self.config.read_bytes()
        live_readme = (self.repo / "README.md").read_bytes()
        live_projection = (self.repo / "projection.txt").read_bytes()
        changed_config = b'{"identity":{"name":"A Different Name"}}\n'

        saved = self.checkpoint(
            "tone-down", created["revision"], "save-1",
            files={"README.md": b"draft\n"}, config=changed_config,
        )

        self.assertNotEqual(saved["revision"], created["revision"])
        self.assertFalse(saved["replayed"])
        self.assertEqual(self.config.read_bytes(), live_config)
        self.assertEqual((self.repo / "README.md").read_bytes(), live_readme)
        self.assertEqual((self.repo / "projection.txt").read_bytes(), live_projection)
        replay = self.checkpoint(
            "tone-down", created["revision"], "save-1",
            files={"README.md": b"draft\n"}, config=changed_config,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], saved["revision"])
        with self.assertRaisesRegex(drafts.DraftError, "different save") as conflict:
            self.checkpoint(
                "tone-down", saved["revision"], "save-1", files={"README.md": b"other\n"},
            )
        self.assertEqual(conflict.exception.code, "idempotency-conflict")
        with self.assertRaisesRegex(drafts.DraftError, "reload") as stale:
            self.checkpoint("tone-down", created["revision"], "save-2", files={"README.md": b"other\n"})
        self.assertEqual(stale.exception.code, "stale-revision")
        record = drafts.diff(self.repo, "tone-down")
        self.assertIn("+draft", record["source_patch"])
        self.assertIn("A Different Name", record["config_patch"])
        self.assertTrue(record["nothing_applied"])

    def test_a_failed_check_restores_the_draft_and_writes_no_checkpoint(self):
        created = self.create()
        path = Path(created["path"])
        with self.assertRaises(drafts.DraftError) as failure:
            drafts.checkpoint(
                self.repo, "tone-down", created["revision"], "bad-save",
                files={"new.txt": b"not kept\n"}, config=b'{"bad":true}\n',
                check_command=[sys.executable, "-c", "raise SystemExit(7)"],
            )
        self.assertEqual(failure.exception.code, "check-failed")
        self.assertFalse((path / "new.txt").exists())
        self.assertEqual(drafts._revision(path), created["revision"])
        self.assertFalse(drafts._paths(path)["journal"].exists())
        self.assertEqual(drafts._paths(path)["config"].read_bytes(), self.config.read_bytes())

    def test_invalid_configuration_is_refused_before_the_draft_changes(self):
        created = self.create()
        path = Path(created["path"])
        with self.assertRaises(drafts.DraftError) as failure:
            self.checkpoint("tone-down", created["revision"], "bad-config", config=b"[1, 2]")
        self.assertEqual(failure.exception.code, "invalid-config")
        self.assertEqual(drafts._revision(path), created["revision"])
        self.assertEqual(drafts._paths(path)["config"].read_bytes(), self.config.read_bytes())

    def test_recovery_completes_a_commit_before_returning_an_exact_retry(self):
        created = self.create()
        path = Path(created["path"])
        content = b"recovered\n"
        digest = drafts._request_digest({"README.md": content}, None)
        journal = {
            "schema_version": 1,
            "key": "recover-1",
            "digest": digest,
            "base_revision": created["revision"],
            "files": [{
                "path": "README.md",
                "before": {"kind": "file", "mode": 0o644, "content": drafts._encoded(b"base\n")},
            }],
            "config_before": drafts._encoded(self.config.read_bytes()),
            "config_after": drafts._encoded(self.config.read_bytes()),
        }
        drafts._atomic_json(drafts._paths(path)["journal"], journal)
        (path / "README.md").write_bytes(content)
        self.git("add", "README.md", cwd=path)
        message = (
            "chore(draft): checkpoint tone-down\n\n"
            "Draft-Idempotency-Key: recover-1\n"
            "Draft-Request-Digest: " + digest + "\n"
        )
        self.git("commit", "-qm", message, cwd=path)

        recovered = self.checkpoint(
            "tone-down", created["revision"], "recover-1", files={"README.md": content},
        )

        self.assertTrue(recovered["replayed"])
        self.assertEqual(recovered["revision"], drafts._revision(path))
        self.assertFalse(drafts._paths(path)["journal"].exists())

    def test_installed_drift_rebases_cleanly_and_updates_the_base(self):
        created = self.create()
        saved = self.checkpoint(
            "tone-down", created["revision"], "save-1", files={"draft.txt": b"draft\n"},
        )
        (self.repo / "installed.txt").write_text("new installed state\n", encoding="utf-8")
        self.git("add", "installed.txt")
        self.git("commit", "-qm", "installed update")

        before = drafts.list_drafts(self.repo)[0]
        self.assertTrue(before["behind_installed"])
        self.assertTrue(before["can_rebase"])
        rebased = drafts.rebase(self.repo, "tone-down")

        self.assertFalse(rebased["behind_installed"])
        self.assertEqual(rebased["base_revision"], drafts._revision(self.repo))
        self.assertNotEqual(rebased["revision"], saved["revision"])
        self.assertTrue((Path(rebased["path"]) / "installed.txt").is_file())

    def test_git_components_are_rejected_case_insensitively_before_filesystem_access(self):
        for relative in (".git/config", ".GIT/config", "nested/.GiT/config"):
            with self.subTest(relative=relative), mock.patch.object(
                Path, "is_symlink", side_effect=AssertionError("filesystem accessed"),
            ):
                with self.assertRaises(drafts.DraftError) as failure:
                    drafts._safe_target(self.repo, relative)
            self.assertEqual(failure.exception.code, "invalid-path")

    def test_checkpoint_preserves_executable_mode_on_success_and_failed_check(self):
        executable = self.repo / "bin" / "harness"
        executable.parent.mkdir()
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        self.git("add", "bin/harness")
        self.git("commit", "-qm", "add executable")
        created = self.create()
        path = Path(created["path"])

        saved = self.checkpoint(
            "tone-down", created["revision"], "executable-success",
            files={"bin/harness": b"#!/bin/sh\nexit 1\n"},
        )
        self.assertEqual((path / "bin" / "harness").stat().st_mode & 0o777, 0o755)
        before = (path / "bin" / "harness").read_bytes()
        with self.assertRaises(drafts.DraftError) as failure:
            drafts.checkpoint(
                self.repo, "tone-down", saved["revision"], "executable-failure",
                files={"bin/harness": None},
                check_command=[sys.executable, "-c", "raise SystemExit(7)"],
            )
        self.assertEqual(failure.exception.code, "check-failed")
        self.assertEqual((path / "bin" / "harness").read_bytes(), before)
        self.assertEqual((path / "bin" / "harness").stat().st_mode & 0o777, 0o755)

    def test_safe_symlink_delete_is_opt_in_and_failed_check_restores_it(self):
        os.symlink("README.md", self.repo / "readme-link")
        self.git("add", "readme-link")
        self.git("commit", "-qm", "add safe symlink")
        created = self.create()
        path = Path(created["path"])
        with self.assertRaises(drafts.DraftError) as refused:
            self.checkpoint(
                "tone-down", created["revision"], "symlink-refused",
                files={"readme-link": None},
            )
        self.assertEqual(refused.exception.code, "symlink-path")
        with self.assertRaises(drafts.DraftError) as failure:
            drafts.checkpoint(
                self.repo, "tone-down", created["revision"], "symlink-rollback",
                files={"readme-link": None}, safe_symlinks=["readme-link"],
                check_command=[sys.executable, "-c", "raise SystemExit(7)"],
            )
        self.assertEqual(failure.exception.code, "check-failed")
        self.assertTrue((path / "readme-link").is_symlink())
        self.assertEqual(os.readlink(path / "readme-link"), "README.md")
        saved = drafts.checkpoint(
            self.repo, "tone-down", created["revision"], "symlink-delete",
            files={"readme-link": None}, safe_symlinks=["readme-link"],
            check_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        self.assertFalse((path / "readme-link").exists())
        self.assertNotEqual(saved["revision"], created["revision"])

    def test_create_failure_removes_worktree_branch_and_private_config(self):
        secret = b'{"token":"not-a-real-secret-marker"}\n'
        self.config.write_bytes(secret)
        original = drafts._atomic_json

        def fail_state(path, value):
            if path.name == "state.json":
                raise OSError("injected state failure")
            return original(path, value)

        with mock.patch.object(drafts, "_atomic_json", side_effect=fail_state):
            with self.assertRaises(drafts.DraftError) as failure:
                self.create("failed")
        self.assertEqual(failure.exception.code, "create-failed")
        self.assertFalse((self.root / "worktrees" / "project" / "draft-failed").exists())
        self.assertNotIn("draft/failed", self.git("branch", "--format=%(refname:short)").stdout.split())
        admin = self.repo / ".git" / "worktrees"
        if admin.exists():
            for candidate in admin.rglob("*"):
                if candidate.is_file():
                    self.assertNotIn(secret, candidate.read_bytes())

    def test_prunable_registered_worktree_does_not_break_valid_draft_lookup(self):
        created = self.create()
        stale, result = harness.create_managed_worktree(
            self.repo, "stale", "stale-branch", created["revision"], fetch=False,
        )
        self.assertEqual(result.returncode, 0)
        shutil.rmtree(stale)

        self.assertEqual([item["name"] for item in drafts.list_drafts(self.repo)], ["tone-down"])
        self.assertEqual(drafts.find(self.repo, "tone-down")[1]["draft_id"], created["draft_id"])

    def test_discard_refuses_external_commit_and_branch_move_race(self):
        created = self.create()
        path = Path(created["path"])
        (path / "external.txt").write_text("external\n", encoding="utf-8")
        self.git("add", "external.txt", cwd=path)
        self.git("commit", "-qm", "external commit", cwd=path)
        with self.assertRaises(drafts.DraftError) as stale:
            drafts.discard(self.repo, "tone-down", self.remove_worktree)
        self.assertEqual(stale.exception.code, "stale-revision")

        self.git("reset", "--hard", created["revision"], cwd=path)
        tree = self.git("rev-parse", "HEAD^{tree}", cwd=path).stdout.strip()
        moved = self.git("commit-tree", tree, "-m", "moved branch").stdout.strip()
        original_git = harness._git

        def race(repo, *args, **kwargs):
            if args[:2] == ("update-ref", "-d"):
                original_git(repo, "update-ref", "refs/heads/draft/tone-down", moved, capture=True)
            return original_git(repo, *args, **kwargs)

        with mock.patch.object(harness, "_git", side_effect=race):
            with self.assertRaises(drafts.DraftError) as raced:
                drafts.discard(self.repo, "tone-down", self.remove_worktree)
        self.assertEqual(raced.exception.code, "discard-failed")
        self.assertEqual(self.git("rev-parse", "draft/tone-down").stdout.strip(), moved)
        self.assertTrue(path.exists())

    def test_discard_is_serialized_by_the_draft_writer_lock(self):
        created = self.create()
        lock = drafts._paths(Path(created["path"]))["lock"]
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(drafts.DraftError) as busy:
                drafts.discard(self.repo, "tone-down", self.remove_worktree)
        self.assertEqual(busy.exception.code, "busy")
        self.assertTrue(Path(created["path"]).exists())

    def test_rebase_stops_on_conflict_and_discard_refuses_it(self):
        created = self.create()
        self.checkpoint(
            "tone-down", created["revision"], "save-1", files={"README.md": b"draft line\n"},
        )
        (self.repo / "README.md").write_text("installed line\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "conflicting installed update")

        with self.assertRaises(drafts.DraftError) as conflict:
            drafts.rebase(self.repo, "tone-down")
        self.assertEqual(conflict.exception.code, "rebase-conflict")
        self.assertTrue(drafts.list_drafts(self.repo)[0]["rebase_conflicted"])
        with self.assertRaises(drafts.DraftError):
            drafts.discard(self.repo, "tone-down", self.remove_worktree)

    def test_json_cli_uses_the_domain_and_discard_removes_worktree_and_branch(self):
        old_repo = harness.REPO
        harness.REPO = self.repo
        self.addCleanup(setattr, harness, "REPO", old_repo)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(harness.main(["draft", "create", "cli", "--json"]), 0)
        created = json.loads(output.getvalue())
        path = Path(created["path"])
        self.checkpoint("cli", created["revision"], "save-1", files={"cli.txt": b"cli\n"})

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(harness.main(["draft", "list", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["drafts"][0]["name"], "cli")
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(harness.main(["draft", "diff", "cli", "--json"]), 0)
        self.assertIn("cli.txt", json.loads(output.getvalue())["source_patch"])
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(harness.main(["draft", "discard", "cli", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["discarded"], "cli")
        self.assertFalse(path.exists())
        self.assertNotIn("draft/cli", self.git("branch", "--format=%(refname:short)").stdout.split())


if __name__ == "__main__":
    unittest.main()
