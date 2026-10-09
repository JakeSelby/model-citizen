"""Testing a draft whose configuration was edited (#1270): the replay starts instead of refusing; the
draft's arm runs its configuration and the base's the one the draft was created with, so the pair
differs by the edit alone; the engine's refusal is relayed unchanged; the run shows both digests the
engine measured; and a configuration edit after registering or testing marks the draft stale. The
launch is a fake and the engine check a fake except where a test says otherwise; nothing is built
or spent."""
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
        """`ReplayAdmission.resolve`'s target resolution and engine check, without its task catalog."""
        with mock.patch.object(replay, "engine_config_check", check):
            request = replay.resolve_request({
                "targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": draft}],
                "model": "claude-test", "repetitions": 1, "tasks": ["one"],
                "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None,
            }, self.admission._resolve)
            self.admission._check_configs(request)
        return request

    def checked(self, check):
        return {call.args[1]: call.args[2] for call in check.call_args_list}

    # AC1, and the pairing: the draft runs its configuration, the base the one it was created with.
    def test_an_edited_draft_runs_its_configuration_and_the_base_the_inherited_one(self):
        self.create("edited")
        check = mock.Mock(return_value=answer())
        resolved = self.resolve("edited", check)
        base, target = resolved.targets
        self.assertEqual(target.config_digest, targets._config_digest(EDITED))
        self.assertEqual(self.checked(check), {base.revision: INHERITED, target.revision: EDITED})
        self.assertEqual(replay.target_configs(self.repo, resolved), [INHERITED, EDITED])

    def test_an_unedited_draft_measures_two_identical_configurations(self):
        self.create("inherits", config=None)
        resolved = self.resolve("inherits", mock.Mock(return_value=answer()))
        self.assertEqual(replay.target_configs(self.repo, resolved), [INHERITED, INHERITED])

    def test_an_empty_configuration_never_asks_the_engine(self):
        self.create("empty", config={})
        check = mock.Mock(side_effect=AssertionError("asked"))
        resolved = self.resolve("empty", check)
        self.assertEqual(resolved.targets[1].config_digest, replay.DEFAULT_CONFIG_DIGEST)
        self.assertEqual(replay.target_configs(self.repo, resolved), [None, None])

    # AC2: the engine's refusal, relayed unchanged, whatever its code.
    def test_the_engines_refusal_is_relayed_with_its_code_and_reason(self):
        self.create("refused")
        for code in ("config_unresolved", "config_some_future_code"):
            reason = "the engine's own words for %s" % code
            with self.subTest(code=code), self.assertRaises(replay.ReplayRefusal) as caught:
                self.resolve("refused", mock.Mock(return_value=answer(code, reason)))
            self.assertEqual(caught.exception.code, "replay_target_" + code)
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
            seen["config"] = json.loads(Path(command[command.index("--harness-config") + 1]).read_text())
            return SimpleNamespace(returncode=2, stdout=json.dumps(answer("config_new_reason", "why")) + "\n",
                                   stderr="")

        with mock.patch.object(replay.subprocess, "run", run):
            got = replay.engine_config_check(self.repo, "c" * 40, EDITED, self.root)
        self.assertEqual((got["code"], got["reason"]), ("config_new_reason", "why"))
        self.assertEqual(seen["command"][1:4], [str(self.repo / "scripts" / "cost_bench.py"), "check-config", "--tag"])
        self.assertNotIn("--inherited-config", seen["command"])
        self.assertEqual(seen["config"], EDITED)
        for done in (SimpleNamespace(returncode=1, stdout="", stderr="boom\n"),
                     SimpleNamespace(returncode=2, stdout=json.dumps(answer()) + "\n", stderr=""),
                     SimpleNamespace(returncode=2, stdout=json.dumps(answer("Not A Code", "x")), stderr="")):
            with self.subTest(done=done), mock.patch.object(replay.subprocess, "run", return_value=done), \
                    self.assertRaises(replay.ReplayError):
                replay.engine_config_check(self.repo, "c" * 40, EDITED, self.root)

    def test_the_real_engine_admits_a_realistic_configuration_with_an_applied_personal_root(self):
        """Un-mocked: the example configuration every install starts from, plus the personal root an
        apply writes beside the user's configuration, through this checkout's `check-config`."""
        personal = self.root / "home" / ".config" / "agent-harness" / "personal-primitives"
        (personal / "stances" / "voice").mkdir(parents=True)
        (personal / "stances" / "voice" / "mine.md").write_text("# Voice: mine\n\nShort answers.\n")
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config.update(primitive_roots=[str(personal)], stances=dict(config["stances"], voice="mine"))
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()
        got = replay.engine_config_check(ROOT, head, config, self.root)
        self.assertEqual((got["code"], got["applied"]), (None, True))
        self.assertEqual(got["config_sha256"], targets._config_digest(config))
        broken = dict(config, rules={"secrets": "maybe"})
        refused = replay.engine_config_check(ROOT, head, broken, self.root)
        self.assertEqual(refused["code"], "config_unresolved")

    # AC1: the run hands the engine each configuration and shows both digests it measured.
    def launch_with(self, stamps):
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            ref = command[command.index("--tag") + 1]
            out = Path(command[command.index("--out") + 1]) / ref
            out.mkdir(parents=True)
            stamp = stamps[len(calls) - 1]
            rows = [dict({"schema_version": 1, "tag": ref, "task": "one", "arm": arm, "rep": 1, "passed": True,
                          "error": False, "cost_usd": 0.25, "harness_sha": ref, "model": "claude-test"},
                         **({"arm_configuration_sha256": stamp} if arm == "harness" and stamp else {}))
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

    @staticmethod
    def given(command):
        return json.loads(Path(command[command.index("--harness-config") + 1]).read_text())

    def test_a_one_stance_edit_runs_configurations_that_differ_only_in_that_stance(self):
        inherited = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.config.write_text(json.dumps(inherited) + "\n", encoding="utf-8")
        edited = dict(inherited, stances=dict(inherited["stances"], voice="concise"))
        self.create("one-stance", config=edited)
        resolved = self.resolve("one-stance", mock.Mock(return_value=answer()))
        digests = [targets._config_digest(inherited), targets._config_digest(edited)]
        calls, launch = self.launch_with(digests)
        with tempfile.TemporaryDirectory() as temporary:
            summary = replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
            base, draft = (self.given(command) for command in calls)
            stored = replay.read_summary(Path(temporary) / "out" / replay.SUMMARY_NAME)
        self.assertEqual((base, draft), (inherited, edited))
        self.assertEqual({key for key in base if base[key] != draft[key]}, {"stances"})
        self.assertEqual({key for key in base["stances"] if base["stances"][key] != draft["stances"][key]},
                         {"voice"})
        self.assertEqual(summary["measured_config_digests"], digests)
        self.assertEqual(stored["measured_config_digests"], digests)
        self.assertEqual(summary["measures"], replay.MEASURES_CONFIGURED)
        self.assertNotIn("--inherited-config", calls[1])
        commands = replay.native_commands(resolved, [True, True])
        self.assertIn("--harness-config target-1/harness-config.json", commands)
        self.assertIn("--harness-config target-2/harness-config.json", commands)

    def test_a_result_measuring_another_configuration_is_refused(self):
        self.create("mismatch")
        resolved = self.resolve("mismatch", mock.Mock(return_value=answer()))
        _calls, launch = self.launch_with([targets._config_digest(INHERITED), "e" * 64])
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("configuration other than its target's", str(caught.exception))

    def test_a_configuration_edited_after_confirmation_launches_nothing_more(self):
        created = self.create("moved")
        resolved = self.resolve("moved", mock.Mock(return_value=answer()))
        drafts.checkpoint_config(self.repo, "moved", created["revision"], "save-2", INHERITED,
                                 check_command=OK_CHECK)
        calls, launch = self.launch_with([None, None])
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("changed after the replay was confirmed", str(caught.exception))
        self.assertEqual(calls, [])

    def test_a_summary_whose_measures_contradict_its_digests_is_refused(self):
        self.create("summary")
        resolved = self.resolve("summary", mock.Mock(return_value=answer()))
        digests = [targets._config_digest(INHERITED), targets._config_digest(EDITED)]
        _calls, launch = self.launch_with(digests)
        with tempfile.TemporaryDirectory() as temporary:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
            path = Path(temporary) / "out" / replay.SUMMARY_NAME
            value = json.loads(path.read_text())
            for measures, measured in (("source", digests), ("source and configuration", [None, None])):
                with self.subTest(measures=measures):
                    path.chmod(0o600)
                    path.write_text(json.dumps(dict(value, measures=measures, measured_config_digests=measured)))
                    with self.assertRaisesRegex(replay.ReplayError, "invalid schema"):
                        replay.read_summary(path)

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
