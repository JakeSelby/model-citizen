"""Testing a draft whose configuration was edited (#1270): the replay starts instead of refusing and
hands the draft's configuration to the engine, whose refusal is relayed unchanged; the run shows the
configuration digest the engine measured; and a configuration edit after registering or testing
marks the draft stale. The engine's check and launch are fakes; nothing is built or spent."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from draft_support import draft_branch_exists
from test_harness import REPO as ROOT  # also puts lib/ on the import path
from harness_core.studio import draft_tests, drafts, replay, targets

_loader = importlib.machinery.SourceFileLoader("harness_replay_draft_config_test", str(ROOT / "bin" / "harness"))
_spec = importlib.util.spec_from_loader("harness_replay_draft_config_test", _loader)
harness = importlib.util.module_from_spec(_spec)
_loader.exec_module(harness)

OK_CHECK = [sys.executable, "-c", "raise SystemExit(0)"]
INHERITED = {"identity": {"name": "A Name"}}
EDITED = {"identity": {"name": "A Name"}, "stances": {"voice": "concise"}}


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


def answer(code=None, reason=None, applied=True):
    return {"applied": applied and code is None, "config_sha256": targets._config_digest(EDITED),
            "code": code, "reason": reason}


class DraftConfigurationTests(unittest.TestCase):
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
        self.config.write_text(json.dumps(INHERITED) + "\n", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HARNESS_HOME": str(home),
                                               "HARNESS_WORKTREE_ROOT": str(self.root / "worktrees")})
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

    def create(self, name, config=EDITED):
        created = drafts.create(self.repo, name, "installed", self.config,
                                self.create_worktree, self.remove_worktree)

        def discard():
            drafts.discard(self.repo, name, self.remove_worktree)
            self.assertFalse(draft_branch_exists(name, self.repo))

        self.addCleanup(discard)
        if config is None:
            return created
        return drafts.checkpoint_config(self.repo, name, created["revision"], "save-1", config,
                                        check_command=OK_CHECK)

    def resolve(self, draft, check):
        with mock.patch.object(replay, "engine_config_check", check):
            return replay.resolve_request({
                "targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": draft}],
                "model": "claude-test", "repetitions": 1, "tasks": ["one"],
                "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None,
            }, self.admission._resolve)

    # AC1: the refusal is lifted and the engine is handed the configuration.
    def test_an_edited_configuration_resolves_and_the_engine_is_asked_about_it(self):
        self.create("edited")
        check = mock.Mock(return_value=answer())
        resolved = self.resolve("edited", check)
        target = resolved.targets[1]
        self.assertEqual(target.config_digest, targets._config_digest(EDITED))
        (repository, revision, config, base, _work), _ = check.call_args
        self.assertEqual((repository, revision, config, base), (self.repo.resolve(), target.revision,
                                                                EDITED, INHERITED))

    def test_an_inherited_configuration_is_the_engines_to_leave_unapplied(self):
        self.create("inherits", config=None)
        check = mock.Mock(return_value=dict(answer(), applied=False, config_sha256=None))
        self.assertEqual(self.resolve("inherits", check).targets[1].config_digest,
                         targets._config_digest(INHERITED))
        (_repository, _revision, config, base, _work), _ = check.call_args
        self.assertEqual(config, base)

    def test_an_empty_configuration_never_asks_the_engine(self):
        self.create("empty", config={})
        check = mock.Mock(side_effect=AssertionError("asked"))
        self.assertEqual(self.resolve("empty", check).targets[1].config_digest, replay.DEFAULT_CONFIG_DIGEST)

    # AC2: the engine's refusal, relayed unchanged.
    def test_the_engines_refusal_is_relayed_with_its_code_and_reason(self):
        self.create("refused")
        reason = "the commit's resolver refuses it: unknown mode 'x'"
        with self.assertRaises(replay.ReplayRefusal) as caught:
            self.resolve("refused", mock.Mock(return_value=answer("config_unresolved", reason)))
        self.assertEqual(caught.exception.code, "replay_target_config_unresolved")
        self.assertEqual(str(caught.exception), reason)

    def test_an_evaluation_tier_whose_engine_cannot_apply_it_still_refuses(self):
        self.create("tier")
        with self.assertRaises(replay.ReplayRefusal) as caught:
            self.admission._resolve("draft", "tier", apply_config=False)
        self.assertEqual(caught.exception.code, "replay_target_config_unsupported")

    def test_the_engine_check_runs_the_engines_command_and_reads_its_answer(self):
        seen = {}

        def run(command, **kwargs):
            seen["command"] = command
            config = json.loads(Path(command[command.index("--harness-config") + 1]).read_text())
            seen["config"] = config
            return SimpleNamespace(returncode=2, stdout=json.dumps(answer("config_host_path", "names /x")) + "\n",
                                   stderr="")

        with mock.patch.object(replay.subprocess, "run", run):
            got = replay.engine_config_check(self.repo, "c" * 40, EDITED, INHERITED, self.root)
        self.assertEqual(got["code"], "config_host_path")
        self.assertEqual(seen["command"][1:4], [str(self.repo / "scripts" / "cost_bench.py"), "check-config", "--tag"])
        self.assertEqual(seen["config"], EDITED)
        for done in (SimpleNamespace(returncode=1, stdout="", stderr="boom\n"),
                     SimpleNamespace(returncode=2, stdout=json.dumps(answer()) + "\n", stderr=""),
                     SimpleNamespace(returncode=2, stdout=json.dumps(answer("made_up", "x")), stderr="")):
            with self.subTest(done=done), mock.patch.object(replay.subprocess, "run", return_value=done), \
                    self.assertRaises(replay.ReplayError):
                replay.engine_config_check(self.repo, "c" * 40, EDITED, INHERITED, self.root)

    # AC1: the run hands the engine the configuration and shows the digest it measured.
    def launch_with(self, resolved, stamp):
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            ref = command[command.index("--tag") + 1]
            out = Path(command[command.index("--out") + 1]) / ref
            out.mkdir(parents=True)
            rows = [dict({"schema_version": 1, "tag": ref, "task": "one", "arm": arm, "rep": 1, "passed": True,
                          "error": False, "cost_usd": 0.25, "harness_sha": ref, "model": "claude-test"},
                         **({"arm_configuration_sha256": stamp} if arm == "harness" and "--harness-config"
                            in command else {}))
                    for arm in replay.ARM_NAMES]
            (out / replay.RESULTS_NAME).write_text("".join(json.dumps(item) + "\n" for item in rows))
            spend = out / replay.SPEND_NAME
            spend.write_text(json.dumps({"schema_version": 1, "tag": ref,
                                         "run_cap_usd": float(command[command.index("--run-cap") + 1]),
                                         "spend_cap_usd": float(command[command.index("--spend-cap") + 1]),
                                         "preflight_spend_usd": 0.0, "scored_spend_usd": 0.5,
                                         "charged_spend_usd": 0.5, "stopped_at_cap": False}) + "\n")
            spend.chmod(0o600)
            return SimpleNamespace(returncode=0)

        return calls, launch

    def test_the_run_passes_the_configuration_and_shows_the_digest_the_engine_measured(self):
        self.create("measured")
        resolved = self.resolve("measured", mock.Mock(return_value=answer()))
        digest = resolved.targets[1].config_digest
        calls, launch = self.launch_with(resolved, digest)
        with tempfile.TemporaryDirectory() as temporary:
            summary = replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
            draft = calls[1]
            written = json.loads(Path(draft[draft.index("--harness-config") + 1]).read_text())
            inherited = json.loads(Path(draft[draft.index("--inherited-config") + 1]).read_text())
            stored = replay.read_summary(Path(temporary) / "out" / replay.SUMMARY_NAME)
        self.assertNotIn("--harness-config", calls[0])
        self.assertEqual((written, inherited), (EDITED, INHERITED))
        self.assertEqual(summary["measured_config_digests"], [None, digest])
        self.assertEqual(summary["measures"], replay.MEASURES_CONFIGURED)
        self.assertEqual(stored["measured_config_digests"], [None, digest])
        self.assertIn("--harness-config target-2/harness-config.json", replay.native_commands(resolved))

    def test_a_result_measuring_another_configuration_is_refused(self):
        self.create("mismatch")
        resolved = self.resolve("mismatch", mock.Mock(return_value=answer()))
        _calls, launch = self.launch_with(resolved, "e" * 64)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("configuration other than its target's", str(caught.exception))

    def test_a_configuration_edited_after_confirmation_launches_nothing_for_it(self):
        created = self.create("moved")
        resolved = self.resolve("moved", mock.Mock(return_value=answer()))
        drafts.checkpoint_config(self.repo, "moved", created["revision"], "save-2", INHERITED,
                                 check_command=OK_CHECK)
        calls, launch = self.launch_with(resolved, resolved.targets[1].config_digest)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("changed after the replay was confirmed", str(caught.exception))
        self.assertEqual(len(calls), 1)  # the base ran; the draft did not

    # AC3: staleness keeps reading the configuration digest.
    def test_a_configuration_edit_after_testing_or_registering_marks_the_draft_stale(self):
        created = self.create("stale")
        resolved = self.resolve("stale", mock.Mock(return_value=answer()))
        target = resolved.targets[1]
        self.assertEqual(replay.draft_staleness(self.repo, target), (False, None))
        before = draft_tests.identity(self.repo, "stale")
        self.assertEqual(draft_tests.staleness(before, target.revision, target.config_digest), (False, None))
        drafts.checkpoint_config(self.repo, "stale", created["revision"], "save-2", dict(EDITED, rules={"x": "off"}),
                                 check_command=OK_CHECK)
        after = draft_tests.identity(self.repo, "stale")
        self.assertTrue(replay.draft_staleness(self.repo, target)[0])
        self.assertTrue(draft_tests.staleness(after, target.revision, target.config_digest)[0])
        same_checkpoint = dict(after, revision=target.revision)
        self.assertEqual(draft_tests.staleness(same_checkpoint, target.revision, target.config_digest),
                         (True, "the draft's configuration changed"))


if __name__ == "__main__":
    unittest.main()
