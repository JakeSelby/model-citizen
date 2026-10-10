"""Testing a draft whose configuration was edited (#1270): an unedited draft runs source-only; an edit
applies to both arms, the draft's configuration to its arm and the one it was created with to the
base's, snapshotted once at start, so the pair differs by the edit alone and the replay is
exploratory; the engine's refusal and reason are relayed unchanged; the run shows both digests the
engine measured; and a configuration edit after registering or testing marks the draft stale. The
launch is a fake and the engine a fake except where a test says otherwise; nothing is built or
spent."""
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
from harness_core.studio import draft_tests, drafts, replay, server, targets

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


class FakeEngine:
    """`replay.engine_snapshot` without a checkout: writes each configuration where the engine would
    and answers with a stand-in digest, or refuses every one with `refuse`."""

    def __init__(self, fail=False, refuse=None):
        self.calls, self.fail, self.refuse = [], fail, refuse

    @staticmethod
    def digest(config):
        return targets._config_digest({"measured": config})

    def __call__(self, repository, pairs, out):
        if self.fail:
            raise AssertionError("the engine was asked")
        self.calls.append([(revision, dict(config)) for revision, config in pairs])
        Path(out).mkdir(parents=True, exist_ok=True)
        answers = []
        for number, (_revision, config) in enumerate(pairs):
            if self.refuse:
                answers.append({"applied": False, "config_sha256": None, "code": self.refuse[0],
                                "reason": self.refuse[1], "path": None})
                continue
            path = Path(out) / ("config-%d.json" % number)
            path.write_text(json.dumps(config))
            answers.append({"applied": True, "config_sha256": self.digest(config), "code": None,
                            "reason": None, "path": str(path)})
        return answers


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

    def resolve(self, draft, engine=None):
        """`ReplayAdmission.resolve`'s target resolution and engine check, without its task catalog."""
        with mock.patch.object(replay, "engine_snapshot", engine or FakeEngine()):
            request = replay.resolve_request({
                "targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": draft}],
                "model": "claude-test", "repetitions": 1, "tasks": ["one"],
                "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None,
            }, self.admission._resolve)
            self.admission._check_configs(request)
        return request

    # Finding 1: an unedited draft runs source-only and asks the engine nothing.
    def test_an_unedited_draft_runs_source_only_and_asks_the_engine_nothing(self):
        self.create("inherits", config=None)
        engine = FakeEngine(fail=True)
        resolved = self.resolve("inherits", engine)
        self.assertFalse(resolved.targets[1].edited)
        self.assertEqual(replay.target_configs(self.repo, resolved), [None, None])
        self.assertFalse(replay.configured(resolved))

    # AC1, and the pairing: an edit applies to both arms.
    def test_an_edited_draft_runs_its_configuration_and_the_base_the_inherited_one(self):
        self.create("edited")
        engine = FakeEngine()
        resolved = self.resolve("edited", engine)
        base, target = resolved.targets
        self.assertTrue(target.edited)
        self.assertEqual(target.base_config_digest, targets._config_digest(INHERITED))
        self.assertEqual(engine.calls[0], [(base.revision, INHERITED), (target.revision, EDITED)])
        self.assertEqual(replay.target_configs(self.repo, resolved), [INHERITED, EDITED])

    # Finding 6: clearing an inherited configuration is an edit, measured against it.
    def test_clearing_an_inherited_configuration_is_measured(self):
        self.create("cleared", config={})
        engine = FakeEngine()
        resolved = self.resolve("cleared", engine)
        self.assertTrue(resolved.targets[1].edited)
        self.assertEqual(replay.target_configs(self.repo, resolved), [INHERITED, None])
        self.assertEqual(engine.calls[0], [(resolved.targets[0].revision, INHERITED)])

    # Finding 2: a replay applying a configuration is exploratory and says it writes no history.
    def test_a_replay_applying_a_configuration_is_exploratory(self):
        self.create("labelled")
        resolved = self.resolve("labelled")
        registered = replay._replace(resolved, evidence=replay.PREREGISTERED,
                                     targets=(replay._replace(resolved.targets[0], kind="release", ref="v1.0.0"),
                                              resolved.targets[1]))
        self.assertEqual(replay.replay_evidence(registered), replay.EXPLORATORY)
        self.assertFalse(any(replay._registered(registered, target) for target in registered.targets))
        with mock.patch.object(replay, "whole_set", return_value=True):
            labelled = replay.label_evidence(self.repo, replay._replace(registered, pre_registration="plan.md"))
        self.assertEqual(labelled.evidence, replay.EXPLORATORY)
        with mock.patch.object(replay, "registered_sample", side_effect=AssertionError("read")):
            note = replay.sampling_payload(self.repo, labelled)["note"]
        self.assertIn("writes no history row", note)

    # AC2 and finding 5: the engine's refusal, relayed with its reason; only its config_* codes.
    def test_the_engines_refusal_is_relayed_with_its_code_and_reason(self):
        self.create("refused")
        for code in ("config_unresolved", "config_some_future_code"):
            reason = "the engine's own words for %s" % code
            with self.subTest(code=code), self.assertRaises(replay.ReplayRefusal) as caught:
                self.resolve("refused", FakeEngine(refuse=(code, reason)))
            self.assertEqual(caught.exception.code, "replay_target_" + code)
            self.assertEqual((str(caught.exception), caught.exception.reason), (reason, reason))

    def test_the_engine_answer_is_parsed_and_only_config_codes_are_relayed(self):
        def answered(code, returncode=2):
            line = json.dumps({"configs": [{"applied": False, "config_sha256": None, "code": code,
                                            "reason": "why", "path": None}]})
            return SimpleNamespace(returncode=returncode, stdout=line + "\n", stderr="")

        out = self.root / "snap"
        with mock.patch.object(replay.subprocess, "run", return_value=answered("config_new_reason")) as run:
            got = replay.engine_snapshot(self.repo, [("c" * 40, EDITED)], out)
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], [str(self.repo / "scripts" / "cost_bench.py"), "snapshot-config"])
        self.assertEqual(command[command.index("--out") + 1], str(out))
        self.assertEqual(got[0]["code"], "config_new_reason")
        for done in (SimpleNamespace(returncode=1, stdout="", stderr="boom\n"), answered("busy"),
                     answered(None, 2), answered("Config_X")):
            with self.subTest(done=done.stdout), mock.patch.object(replay.subprocess, "run", return_value=done), \
                    self.assertRaises(replay.ReplayError) as caught:
                replay.engine_snapshot(self.repo, [("c" * 40, EDITED)], out)
            self.assertNotIsInstance(caught.exception, replay.ReplayRefusal)

    def test_an_evaluation_tier_whose_engine_cannot_apply_it_still_refuses(self):
        self.create("tier")
        with self.assertRaises(replay.ReplayRefusal) as caught:
            self.admission._resolve("draft", "tier", apply_config=False)
        self.assertEqual(caught.exception.code, "replay_target_config_unsupported")

    # Finding 4: a save holding the writer lock answers busy, not a generic error.
    def test_a_draft_being_saved_answers_busy_when_its_configuration_is_read(self):
        self.create("busy-config")
        resolved = self.resolve("busy-config")
        worktree, _state = drafts.find(self.repo, "busy-config")
        with drafts._locked(worktree), mock.patch.object(replay, "CONFIG_READ_TIMEOUT", 0.1):
            with self.assertRaises(replay.ReplayRefusal) as caught:
                replay.target_configs(self.repo, resolved)
        self.assertEqual(caught.exception.code, "replay_target_busy")

    # Finding 7: confirming re-runs the engine check before anything spends.
    def test_confirming_asks_the_engine_again(self):
        self.create("confirm")
        resolved = self.resolve("confirm")
        with mock.patch.object(self.admission, "_confirm_resolved"), \
                mock.patch.object(replay, "engine_snapshot", FakeEngine(refuse=("config_unresolved", "moved"))):
            with self.assertRaises(replay.ReplayRefusal) as caught:
                self.admission.confirm(resolved.as_dict())
        self.assertEqual(caught.exception.code, "replay_target_config_unresolved")

    def test_the_real_engine_admits_a_realistic_configuration_with_an_applied_personal_root(self):
        """Un-mocked: the example configuration every install starts from, plus the personal root an
        apply writes beside the user's configuration, through this checkout's `snapshot-config`."""
        personal = self.root / "home" / ".config" / "agent-harness" / "personal-primitives"
        (personal / "stances" / "voice").mkdir(parents=True)
        (personal / "stances" / "voice" / "mine.md").write_text("# Voice: mine\n\nShort answers.\n")
        base = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        base["primitive_roots"] = [str(personal)]
        edited = dict(base, stances=dict(base["stances"], voice="mine"))
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()
        got = replay.engine_snapshot(ROOT, [(head, base), (head, edited)], self.root / "snapshot")
        self.assertEqual([answer["code"] for answer in got], [None, None])
        roots = [json.loads(Path(answer["path"]).read_text())["primitive_roots"] for answer in got]
        self.assertEqual(roots, [[str(self.root / "snapshot" / "roots" / "0")]] * 2)
        refused = replay.engine_snapshot(ROOT, [(head, dict(base, rules={"secrets": "maybe"}))],
                                         self.root / "snapshot-2")
        self.assertEqual(refused[0]["code"], "config_unresolved")

    # AC1 and finding 3: one snapshot at start, both digests shown.
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
        resolved = self.resolve("one-stance")
        digests = [FakeEngine.digest(inherited), FakeEngine.digest(edited)]
        calls, launch = self.launch_with(digests)
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(replay, "engine_snapshot", engine):
            summary = replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
            base, draft = (self.given(command) for command in calls)
            stored = replay.read_summary(Path(temporary) / "out" / replay.SUMMARY_NAME)
            snapshot = (Path(temporary) / "out" / replay.CONFIG_SNAPSHOT).resolve()
            self.assertTrue(all(Path(c[c.index("--harness-config") + 1]).parent == snapshot for c in calls))
        self.assertEqual(len(engine.calls), 1)  # one snapshot, taken at start, for both targets
        self.assertEqual((base, draft), (inherited, edited))
        self.assertEqual({key for key in base if base[key] != draft[key]}, {"stances"})
        self.assertEqual({key for key in base["stances"] if base["stances"][key] != draft["stances"][key]},
                         {"voice"})
        self.assertEqual(summary["measured_config_digests"], digests)
        self.assertEqual(stored["measured_config_digests"], digests)
        self.assertEqual(summary["measures"], replay.MEASURES_CONFIGURED)
        commands = replay.native_commands(resolved, [True, True])
        self.assertIn("--harness-config configuration/config-0.json", commands)
        self.assertIn("--harness-config configuration/config-1.json", commands)

    def test_a_result_measuring_another_configuration_is_refused(self):
        self.create("mismatch")
        resolved = self.resolve("mismatch")
        _calls, launch = self.launch_with([FakeEngine.digest(INHERITED), "e" * 64])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(replay, "engine_snapshot", FakeEngine()), \
                self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("configuration other than its target's", str(caught.exception))

    def test_a_configuration_edited_after_confirmation_launches_nothing(self):
        created = self.create("moved")
        resolved = self.resolve("moved")
        drafts.checkpoint_config(self.repo, "moved", created["revision"], "save-2", dict(EDITED, x={"y": "z"}),
                                 check_command=OK_CHECK)
        calls, launch = self.launch_with([None, None])
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(replay, "engine_snapshot", FakeEngine()), \
                self.assertRaises(replay.ReplayError) as caught:
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertIn("changed after the replay was confirmed", str(caught.exception))
        self.assertEqual(calls, [])

    def test_a_configuration_the_engine_refuses_at_start_launches_nothing(self):
        self.create("late-refusal")
        resolved = self.resolve("late-refusal")
        calls, launch = self.launch_with([None, None])
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(replay, "engine_snapshot", FakeEngine(refuse=("config_root_unreadable", "gone"))), \
                self.assertRaises(replay.ReplayError):
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
        self.assertEqual(calls, [])

    def test_a_summary_whose_measures_contradict_its_digests_is_refused(self):
        self.create("summary")
        resolved = self.resolve("summary")
        digests = [FakeEngine.digest(INHERITED), FakeEngine.digest(EDITED)]
        _calls, launch = self.launch_with(digests)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(replay, "engine_snapshot", FakeEngine()):
            replay.execute(resolved, self.repo, Path(temporary) / "out", launch)
            path = Path(temporary) / "out" / replay.SUMMARY_NAME
            value = json.loads(path.read_text())
            for measures, measured in (("source", digests), ("source and configuration", [None, None])):
                with self.subTest(measures=measures):
                    path.write_text(json.dumps(dict(value, measures=measures, measured_config_digests=measured)))
                    with self.assertRaisesRegex(replay.ReplayError, "invalid schema"):
                        replay.read_summary(path)

    def test_the_error_payload_carries_the_engines_reason(self):
        sent = {}
        handler = SimpleNamespace(_send=lambda code, body, media: sent.update(code=code, body=json.loads(body)))
        server.Handler._error(handler, 400, "replay_target_config_unresolved", "unknown mode 'x'")
        self.assertEqual(sent["body"], {"error": "replay_target_config_unresolved", "reason": "unknown mode 'x'"})
        server.Handler._error(handler, 400, "replay_refused")
        self.assertEqual(sent["body"], {"error": "replay_refused"})

    # AC3: staleness keeps reading the configuration digest.
    def test_a_configuration_edit_after_testing_or_registering_marks_the_draft_stale(self):
        created = self.create("stale")
        resolved = self.resolve("stale")
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
