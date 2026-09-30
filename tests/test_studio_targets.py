"""Immutable Studio targets and their isolated built profiles."""
import hashlib
import concurrent.futures
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO  # noqa: F401  loads the repository library path
from harness_core.studio import runs, targets
from studio_target_support import FixtureTargetService


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False)
    if done.returncode:
        raise AssertionError(done.stderr or done.stdout)
    return done.stdout.strip()


def tree_image(root):
    """Every directory, file and link below root, including type, mode and bytes/target."""
    image = {}

    def visit(path, relative):
        metadata = os.lstat(path)
        mode = stat.S_IMODE(metadata.st_mode)
        name = relative.as_posix() if relative.parts else "."
        if stat.S_ISLNK(metadata.st_mode):
            image[name] = ("link", mode, os.readlink(path))
        elif stat.S_ISDIR(metadata.st_mode):
            image[name] = ("directory", mode)
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child, relative / child.name)
        elif stat.S_ISREG(metadata.st_mode):
            image[name] = ("file", mode, path.read_bytes())
        else:
            image[name] = ("other", mode)

    visit(Path(root), Path())
    return image


class StudioTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "-b", "main")
        git(self.repo, "config", "user.name", "Target Test")
        git(self.repo, "config", "user.email", "target.invalid")
        (self.repo / "bin").mkdir()
        (self.repo / "primitives").mkdir()
        (self.repo / "bin" / "harness").write_text(
            """#!/usr/bin/env python3
import json, os, socket
from pathlib import Path
source = Path.cwd()
home = Path(os.environ["HOME"])
config = json.loads((home / ".config/agent-harness/config.json").read_text())
(home / "synced.json").write_text(json.dumps({
    "config": config,
    "module": (source / "primitives/module.md").read_text(),
}, sort_keys=True))
(home / "sync-env.json").write_text(json.dumps(dict(os.environ), sort_keys=True))
try:
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9))
    network = "allowed"
except OSError:
    network = "denied"
(home / "sync-network.txt").write_text(network)
""", encoding="utf-8")
        (self.repo / "VERSION").write_text("0.17.0\n", encoding="utf-8")
        (self.repo / "primitives" / "module.md").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "initial")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "v0.17.0")
        git(self.repo, "checkout", "--quiet", "-b", "feature/example")
        (self.repo / "VERSION").write_text("0.18.0-dev\n", encoding="utf-8")
        (self.repo / "primitives" / "module.md").write_text("branch\n", encoding="utf-8")
        git(self.repo, "commit", "--quiet", "-am", "branch")
        self.branch = git(self.repo, "rev-parse", "HEAD")

    def built(self, kind, ref, name):
        if sys.platform.startswith("linux"):
            command = [sys.executable, "bin/harness", "sync"]
            with mock.patch.object(targets, "_sync_command", return_value=command):
                return targets.build(self.repo, kind, ref, self.root / name)
        return targets.build(self.repo, kind, ref, self.root / name)

    def test_installed_release_branch_and_commit_resolve_to_immutable_profiles(self):
        installed = self.built("installed", "current", "installed")
        release = self.built("release", "v0.17.0", "release")
        branch = self.built("branch", "feature/example", "branch")
        commit = self.built("branch", self.base, "commit")

        self.assertEqual((installed["revision"], installed["version"]),
                         (self.branch, "0.18.0-dev"))
        self.assertEqual((release["revision"], release["version"]),
                         (self.base, "0.17.0"))
        self.assertEqual(branch["revision"], self.branch)
        self.assertEqual(commit["revision"], self.base)
        hidden = subprocess.run(
            ["git", "-C", str(self.root / "release" / "source"), "cat-file", "-e", self.branch],
            capture_output=True, check=False,
        )
        self.assertNotEqual(hidden.returncode, 0)
        for name, expected in (("installed", "branch\n"), ("release", "base\n"),
                               ("branch", "branch\n"), ("commit", "base\n")):
            profile = json.loads((self.root / name / "profile" / "synced.json").read_text())
            self.assertEqual(profile, {"config": {}, "module": expected})

    def test_suite_process_executes_from_the_prepared_target_source(self):
        catalog = self.root / "target-source-catalog.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "source-check", "version": 1,
            "argv": [sys.executable, "-c", (
                "from pathlib import Path; "
                "print(Path('primitives/module.md').read_text().strip())")],
            "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["release"], "cases": ["source-check"],
        }]}), encoding="utf-8")
        supervisor = runs.RunSupervisor(
            self.root / "target-source-state", catalog, repository=self.repo)
        self.addCleanup(supervisor.close)
        if sys.platform.startswith("linux"):
            command = [sys.executable, "bin/harness", "sync"]
            with mock.patch.object(targets, "_sync_command", return_value=command):
                created = supervisor.start("source-check", {}, "release", "v0.17.0")
        else:
            created = supervisor.start("source-check", {}, "release", "v0.17.0")
        deadline = time.monotonic() + 10
        record = supervisor.show(created["run_id"])
        while record["status"] not in runs.TERMINAL and time.monotonic() < deadline:
            time.sleep(0.03)
            record = supervisor.show(created["run_id"])
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(
            supervisor.read_output(created["run_id"], "stdout")["chunk"].strip(), "base")

    def test_target_sync_receives_only_allowlisted_nonsecret_environment(self):
        secrets = {
            "GITHUB_" + "TOKEN": "github-secret",
            "AWS_" + "SECRET_ACCESS_" + "KEY": "aws-secret",
            "OPENAI_" + "API_KEY": "model-secret",
            "CLAUDE_CODE_" + "OAUTH_TOKEN": "oauth-secret",
        }
        with mock.patch.dict(os.environ, secrets, clear=False):
            self.built("installed", "current", "scrubbed")
        environment = json.loads(
            (self.root / "scrubbed/profile/sync-env.json").read_text(encoding="utf-8"))
        for name, value in secrets.items():
            self.assertNotIn(name, environment)
            self.assertNotIn(value, environment.values())
        self.assertEqual(environment["HOME"], str(self.root / "scrubbed/profile"))
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
            self.assertEqual(
                (self.root / "scrubbed/profile/sync-network.txt").read_text(encoding="utf-8"),
                "denied")

    def test_linux_sync_uses_a_network_namespace_or_fails_closed(self):
        command = [sys.executable, "bin/harness", "sync"]
        with mock.patch.object(targets.platform, "system", return_value="Linux"), \
                mock.patch.object(targets.shutil, "which", return_value=None):
            with self.assertRaisesRegex(targets.TargetError, "network namespace isolation"):
                targets._sync_command()
        with mock.patch.object(targets.platform, "system", return_value="Linux"), \
                mock.patch.object(targets.shutil, "which", return_value="/usr/bin/unshare"):
            self.assertEqual(targets._sync_command(),
                             ["/usr/bin/unshare", "--net", "--"] + command)
        if sys.platform.startswith("linux"):
            try:
                prefix = targets._sync_command()[:3]
            except targets.TargetError:
                return
            probe = subprocess.run(prefix + [sys.executable, "-c", (
                "import socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                "\ntry: s.sendto(b'x',('127.0.0.1',9))"
                "\nexcept OSError: sys.exit(0)"
                "\nsys.exit(42)")], capture_output=True, check=False)
            self.assertNotEqual(probe.returncode, 42, "raw socket escaped the network namespace")

    def test_dirty_managed_worktree_becomes_a_snapshot_without_changing_the_worktree(self):
        worktree = self.root / "managed"
        git(self.repo, "worktree", "add", "--quiet", "--detach", str(worktree), self.base)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True, check=False))
        module = worktree / "primitives" / "module.md"
        module.write_text("dirty\n", encoding="utf-8")
        untracked = worktree / "primitives" / "new.md"
        untracked.write_text("new\n", encoding="utf-8")
        before = git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
        before_image = tree_image(worktree)

        result = self.built("worktree", str(worktree), "worktree")

        self.assertTrue(result["snapshot"])
        self.assertNotEqual(result["revision"], self.base)
        self.assertEqual(git(worktree, "status", "--porcelain=v1", "--untracked-files=all"), before)
        self.assertEqual(tree_image(worktree), before_image)
        self.assertEqual(module.read_text(), "dirty\n")
        self.assertEqual(untracked.read_text(), "new\n")
        source = self.root / "worktree" / "source"
        self.assertEqual((source / "primitives" / "module.md").read_text(), "dirty\n")
        self.assertEqual((source / "primitives" / "new.md").read_text(), "new\n")
        self.assertEqual(git(source, "rev-parse", "HEAD"), result["revision"])

    def test_clone_keeps_only_ancestor_tags_in_a_constant_number_of_ref_updates(self):
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "v0.0.1")
        git(self.repo, "switch", "--quiet", "-c", "side")
        (self.repo / "side.txt").write_text("side\n", encoding="utf-8")
        git(self.repo, "add", "side.txt")
        git(self.repo, "commit", "--quiet", "-m", "side")
        git(self.repo, "tag", "v0.0.2-side")
        git(self.repo, "switch", "--quiet", "main")
        for index in range(40):
            git(self.repo, "branch", "extra-%02d" % index)

        destination = self.root / "clone"
        with mock.patch.object(targets.subprocess, "run", wraps=subprocess.run) as run:
            targets._clone(self.repo, head, destination)
        refs = git(destination, "for-each-ref", "--format=%(refname)").splitlines()
        self.assertIn("refs/tags/v0.0.1", refs)
        self.assertNotIn("refs/tags/v0.0.2-side", refs)
        self.assertEqual([ref for ref in refs if not ref.startswith("refs/tags/")], [])
        updates = [call for call in run.call_args_list if "update-ref" in call.args[0]]
        self.assertLessEqual(len(updates), 1)

    def test_committed_and_untracked_symlinks_cannot_escape_the_immutable_clone(self):
        outside = self.root / "outside"
        outside.write_text("host data\n", encoding="utf-8")
        committed = self.repo / "primitives" / "escape.md"
        committed.symlink_to(outside)
        git(self.repo, "add", "primitives/escape.md")
        git(self.repo, "commit", "--quiet", "-m", "unsafe link")
        with self.assertRaisesRegex(targets.TargetError, "symlink escapes"):
            self.built("installed", "current", "committed-escape")

        git(self.repo, "reset", "--hard", "HEAD^")
        (self.repo / "primitives" / "loop-a").symlink_to("loop-b")
        (self.repo / "primitives" / "loop-b").symlink_to("loop-a")
        git(self.repo, "add", "primitives/loop-a", "primitives/loop-b")
        git(self.repo, "commit", "--quiet", "-m", "unsafe loop")
        with self.assertRaisesRegex(targets.TargetError, "unsafe symlink"):
            self.built("installed", "current", "committed-loop")

        git(self.repo, "reset", "--hard", "HEAD^")
        worktree = self.root / "linked-worktree"
        git(self.repo, "worktree", "add", "--quiet", "--detach", str(worktree), self.base)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True, check=False))
        (worktree / "primitives" / "escape.md").symlink_to(outside)
        with self.assertRaisesRegex(targets.TargetError, "symlink escapes"):
            self.built("worktree", str(worktree), "untracked-escape")
        (worktree / "primitives" / "escape.md").unlink()
        (worktree / "primitives" / "loop-a").symlink_to("loop-b")
        (worktree / "primitives" / "loop-b").symlink_to("loop-a")
        with self.assertRaisesRegex(targets.TargetError, "unsafe symlink"):
            self.built("worktree", str(worktree), "untracked-loop")

    def test_worktree_snapshot_refuses_two_consecutive_torn_captures(self):
        worktree = self.root / "moving-worktree"
        git(self.repo, "worktree", "add", "--quiet", "--detach", str(worktree), self.base)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True, check=False))
        changing = worktree / "primitives" / "module.md"
        changing.write_text("first\n", encoding="utf-8")
        original = targets._worktree_image
        calls = 0

        def capture(path):
            nonlocal calls
            calls += 1
            if calls in (2, 4):
                changing.write_text("changed-%d\n" % calls, encoding="utf-8")
            return original(path)

        with mock.patch.object(targets, "_worktree_image", side_effect=capture):
            with self.assertRaisesRegex(targets.TargetError, "changed while"):
                self.built("worktree", str(worktree), "torn")
        self.assertEqual(calls, 4)
        self.assertEqual(changing.read_text(encoding="utf-8"), "changed-4\n")
        self.assertFalse((self.root / "torn/source").exists())

    def test_draft_checkpoint_modules_and_configuration_build_without_touching_live_home(self):
        config = {"mode": "minimal", "stances": {"cost": "balanced"}}
        live = self.root / "live-home"
        live.mkdir()
        nested = live / "nested"
        nested.mkdir()
        os.chmod(nested, 0o711)
        sentinel = nested / "sentinel"
        sentinel.write_bytes(b"unchanged\x00bytes")
        os.chmod(sentinel, 0o640)
        (live / "sentinel-link").symlink_to("nested/sentinel")
        empty = live / "empty"
        empty.mkdir()
        os.chmod(empty, 0o700)
        before = tree_image(live)
        revision = git(REPO, "rev-parse", "HEAD")
        draft = {"name": "experiment", "revision": revision,
                 "actual_revision": revision}
        with mock.patch.dict(os.environ, {"HOME": str(live)}, clear=False), \
                mock.patch.object(targets.drafts, "read_config",
                                  return_value={"draft": draft, "config": config}), \
                mock.patch.object(targets.drafts, "find", return_value=(REPO, {})):
            result = targets.build(REPO, "draft", "experiment", self.root / "draft")

        self.assertEqual(tree_image(live), before)
        self.assertEqual(result["draft"], "experiment")
        self.assertEqual(result["revision"], revision)
        expected_digest = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(result["config_digest"], expected_digest)
        run = self.root / "draft"
        self.assertEqual(json.loads(
            (run / "profile/.config/agent-harness/config.json").read_text()), config)
        rule = run / "profile/.claude/rules/harness/conciseness.md"
        self.assertTrue(rule.is_symlink())
        self.assertEqual(rule.resolve(),
                         (run / "source/primitives/rules/conciseness.md").resolve())
        self.assertEqual(rule.read_bytes(),
                         (run / "source/primitives/rules/conciseness.md").read_bytes())

    def test_started_record_carries_version_commit_draft_and_config_digest(self):
        catalog = self.repo / "policy" / "studio" / "suites.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1,
            "argv": [sys.executable, "-c", "print('ok')"], "parameters": {},
            "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["draft"], "cases": ["fixture"],
        }]}), encoding="utf-8")
        supervisor = runs.RunSupervisor(self.root / "state", catalog, repository=REPO)
        revision = git(REPO, "rev-parse", "HEAD")
        version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        config = {"stances": {"cost": "balanced"}}
        digest = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        draft = {"name": "experiment", "revision": revision,
                 "actual_revision": revision}
        with mock.patch.object(targets.drafts, "read_config",
                               return_value={"draft": draft, "config": config}), \
                mock.patch.object(targets.drafts, "find", return_value=(REPO, {})), \
                mock.patch.object(supervisor, "_admit_locked"):
            created = supervisor.start("fixture", {}, "draft", "experiment")
        record = supervisor._read(created["run_id"])
        run = self.root / "state/targets" / created["run_id"]
        self.assertEqual(record["target"], {
            "kind": "draft", "ref": "experiment", "version": version,
            "revision": revision, "draft": "experiment", "config_digest": digest,
            "source_path": str(run / "source"), "profile_path": str(run / "profile"),
        })
        self.assertTrue((run / "profile/.claude/rules/harness/conciseness.md").is_symlink())
        indexed = supervisor.history.get(created["run_id"])
        self.assertEqual(indexed["target"], {
            "kind": "draft", "ref": "experiment", "version": version,
            "commit": revision, "draft": "experiment", "config_digest": digest,
        })

    def test_unsupported_target_is_refused_before_profile_build_or_process_start(self):
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1,
            "argv": [sys.executable, "-c", "print('ok')"], "parameters": {},
            "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["installed"], "cases": ["fixture"],
        }]}), encoding="utf-8")
        supervisor = runs.RunSupervisor(self.root / "state-refusal", catalog)
        with mock.patch.object(targets, "build") as build, \
                mock.patch.object(runs.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(runs.RunError, "does not support target draft"):
                supervisor.start("fixture", {}, "draft", "experiment")
        build.assert_not_called()
        launch.assert_not_called()

    def test_relocated_catalog_without_explicit_target_service_fails_closed(self):
        catalog = self.root / "relocated.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1,
            "argv": [sys.executable, "-c", "print('ok')"], "parameters": {},
            "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["installed"], "cases": ["fixture"],
        }]}), encoding="utf-8")
        supervisor = runs.RunSupervisor(self.root / "relocated-state", catalog)
        with mock.patch.object(targets, "build") as build, \
                mock.patch.object(runs.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(runs.RunError, "explicit repository or target service"):
                supervisor.start("fixture", {}, "installed", "current")
        build.assert_not_called()
        launch.assert_not_called()

    def test_concurrent_target_builds_do_not_hold_the_supervisor_lock(self):
        catalog = self.root / "concurrent.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1,
            "argv": [sys.executable, "-c", "print('ok')"], "parameters": {},
            "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["installed"], "cases": ["fixture"],
        }]}), encoding="utf-8")
        entered = threading.Barrier(3)
        release = threading.Event()
        active = 0
        maximum = 0
        guard = threading.Lock()

        class BlockingTargets(FixtureTargetService):
            def build(inner_self, kind, ref, destination):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                entered.wait(timeout=5)
                release.wait(timeout=5)
                try:
                    return super().build(kind, ref, destination)
                finally:
                    with guard:
                        active -= 1

        state = self.root / "concurrent-state"
        service = BlockingTargets()

        def start_one():
            supervisor = runs.RunSupervisor(state, catalog, target_service=service)
            try:
                return supervisor.start("fixture", {}, "installed", "current")
            finally:
                supervisor.close()

        observer = runs.RunSupervisor(state, catalog, target_service=service)
        self.addCleanup(observer.close)
        with mock.patch.object(runs.RunSupervisor, "_admit_locked"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(start_one) for _ in range(2)]
                entered.wait(timeout=5)
                stale_id = str(uuid.uuid4())
                stale_target = state / "targets" / stale_id
                stale_target.mkdir()
                stale_preparation = state / "preparations" / (stale_id + ".json")
                stale_preparation.write_text(json.dumps({
                    "schema_version": 1,
                    "run_id": stale_id,
                    "owner_pid": 99999999,
                    "owner_identity": "dead-owner",
                    "queue_sequence": 999,
                    "created_at": "2026-09-28T00:00:00+00:00",
                    "confirmation_token": None,
                    "request_digest": None,
                }), encoding="utf-8")
                os.chmod(stale_preparation, 0o600)
                started = time.monotonic()
                self.assertEqual(observer.list(), [])
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertFalse(stale_target.exists())
                self.assertFalse(stale_preparation.exists())
                release.set()
                records = [future.result(timeout=5) for future in futures]
        self.assertEqual(maximum, 2)
        self.assertEqual(len({record["run_id"] for record in records}), 2)
        self.assertEqual(len(observer.list()), 2)
        self.assertEqual(list((state / "preparations").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
