"""Admission without a configuration, as an evaluation tier resolves a draft, against real drafts:
an inherited configuration runs and an edited one is refused. A replay applies an edited one
instead; that path is tested in test_studio_replay_draft_config."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from draft_support import draft_branch_exists
from test_harness import REPO as ROOT  # also puts lib/ on the import path
from harness_core.studio import drafts, replay, targets

_loader = importlib.machinery.SourceFileLoader("harness_replay_drafts_test", str(ROOT / "bin" / "harness"))
_spec = importlib.util.spec_from_loader("harness_replay_drafts_test", _loader)
harness = importlib.util.module_from_spec(_spec)
_loader.exec_module(harness)

OK_CHECK = [sys.executable, "-c", "raise SystemExit(0)"]


class ResolvingService:
    """The real AH-S301 resolution without the profile sync, which these tests do not need."""

    def __init__(self, repo):
        self.repo = repo

    def build(self, kind, ref, destination):
        resolved = targets.resolve(self.repo, kind, ref, Path(destination) / "source")
        return {"kind": kind, "ref": ref, "revision": resolved.revision,
                "version": resolved.version, "draft": resolved.draft,
                "config_digest": targets._config_digest(resolved.config),
                "snapshot": resolved.snapshot}


class ReplayDraftConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))
        self.repo = self.root / "repos" / "project"
        (self.repo / "bin").mkdir(parents=True)
        (self.repo / "bin" / "harness").write_text("#!/bin/sh\n", encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test" + "@" + "example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        home = self.root / "home"
        self.config = home / ".config" / "agent-harness" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"identity":{"name":"A Name"}}\n', encoding="utf-8")
        environment = {"HARNESS_HOME": str(home),
                       "HARNESS_WORKTREE_ROOT": str(self.root / "worktrees")}
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("HARNESS_QUIET", None)
        self.addCleanup(self.temporary.cleanup)
        self.admission = replay.ReplayAdmission(
            self.repo, self.root / "state", mock.Mock(), ResolvingService(self.repo))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True)

    def create_worktree(self, repo, name, branch, base):
        dest, run = harness.create_managed_worktree(repo, name, branch, base, fetch=False)
        if run.returncode:
            raise SystemExit(run.stderr)
        return dest

    def remove_worktree(self, repo, dest, branch, revision):
        status, detail = harness.remove_managed_worktree_exact(repo, dest, branch, revision)
        if status:
            raise SystemExit(detail)

    def create(self, name):
        created = drafts.create(self.repo, name, "installed", self.config,
                                self.create_worktree, self.remove_worktree)

        def discard():
            drafts.discard(self.repo, name, self.remove_worktree)
            self.assertFalse(draft_branch_exists(name, self.repo))

        self.addCleanup(discard)
        return created

    def resolve_pair(self, draft):
        return replay.resolve_request({
            "targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": draft}],
            "model": "claude-test", "repetitions": 1, "tasks": ["one"],
            "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None,
        }, lambda kind, ref: self.admission._resolve(kind, ref, apply_config=False))

    def test_an_inherited_unchanged_configuration_runs_as_source_only(self):
        self.create("inherits")
        resolved = self.resolve_pair("inherits")
        self.assertNotEqual(resolved.targets[1].config_digest, replay.DEFAULT_CONFIG_DIGEST)
        self.assertEqual(resolved.targets[1].draft, "inherits")

    def test_an_edited_configuration_is_refused_with_a_named_reason(self):
        created = self.create("edited")
        drafts.checkpoint_config(self.repo, "edited", created["revision"], "save-1",
                                 {"identity": {"name": "Another Name"}}, check_command=OK_CHECK)
        with self.assertRaises(replay.ReplayRefusal) as caught:
            self.resolve_pair("edited")
        self.assertEqual(caught.exception.code, "replay_target_config_unsupported")

    def test_a_configuration_cleared_to_the_defaults_runs(self):
        created = self.create("cleared")
        drafts.checkpoint_config(self.repo, "cleared", created["revision"], "save-1", {},
                                 check_command=OK_CHECK)
        resolved = self.admission._resolve("draft", "cleared", apply_config=False)
        self.assertEqual(resolved["config_digest"], replay.DEFAULT_CONFIG_DIGEST)

    def test_a_draft_being_saved_is_a_distinct_retryable_refusal(self):
        created = self.create("busy")
        worktree = Path(created["path"])
        with drafts._locked(worktree):
            with self.assertRaises(replay.ReplayRefusal) as caught:
                self.admission._resolve("draft", "busy")
        self.assertEqual(caught.exception.code, "replay_target_busy")
        self.assertIn("preview again", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
