# SPDX-License-Identifier: MIT
"""Tests for the per-folder Remote Control launch agents."""
import argparse
import importlib.machinery
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import remote_control  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)


def harness_http_error(url, code):
    from urllib.error import HTTPError
    from io import BytesIO
    return HTTPError(url, code, "status %d" % code, {}, BytesIO(b""))


class SettingsTests(unittest.TestCase):
    def test_defaults_serve_nothing_in_worktree_mode(self):
        opts = remote_control.settings({})
        self.assertEqual(opts["folders"], [])
        self.assertEqual(opts["spawn"], "worktree")
        self.assertEqual(opts["permission_mode"], "default")

    def test_example_config_is_valid(self):
        example = json.loads((REPO / "config.example.json").read_text())
        self.assertEqual(remote_control.settings(example)["folders"], [])

    def test_folders_resolve_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = remote_control.settings({"remote_control": {"folders": [tmp, tmp + "/"]}})
            self.assertEqual(opts["folders"], [Path(tmp).resolve()])

    def test_bad_values_are_rejected(self):
        for block in ({"spawn": "fork"}, {"permission_mode": "yolo"}, {"folders": "/x"},
                      {"folders": [""]}, {"folder": []}, {"folders": [3]},
                      {"folders": [{"spawn": "same-dir"}]}, {"folders": [{"path": ""}]},
                      {"folders": [{"path": "/x", "spawn": "fork"}]},
                      {"folders": [{"path": "/x", "env": ["A=1"]}]},
                      {"folders": [{"path": "/x", "env": {"A": 1}}]},
                      {"folders": [{"path": "/x", "permission_mode": "auto"}]}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                remote_control.settings({"remote_control": block})

    def test_errors_name_the_offending_entry(self):
        with self.assertRaises(ValueError) as caught:
            remote_control.settings({"remote_control": {"folders": ["/a", {"path": "/b", "spawn": "x"}]}})
        self.assertIn("remote_control.folders[1].spawn", str(caught.exception))

    def test_object_folders_carry_their_own_spawn_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / "one", Path(tmp) / "two"
            opts = remote_control.settings({"remote_control": {"spawn": "worktree", "folders": [
                str(one), {"path": str(two), "spawn": "same-dir", "env": {"X_FLAG": "1"}}]}})
        self.assertEqual(opts["folders"], [one.resolve(), two.resolve()])
        self.assertEqual(remote_control.folder_options(one.resolve(), opts),
                         {"spawn": "worktree", "env": {}})
        self.assertEqual(remote_control.folder_options(two.resolve(), opts),
                         {"spawn": "same-dir", "env": {"X_FLAG": "1"}})

    def test_a_folder_listed_twice_must_agree_with_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            same = remote_control.settings({"remote_control": {"folders": [tmp, {"path": tmp}]}})
            self.assertEqual(same["folders"], [Path(tmp).resolve()])
            with self.assertRaises(ValueError):
                remote_control.settings({"remote_control": {"folders": [
                    tmp, {"path": tmp, "spawn": "same-dir"}]}})


class PlistTests(unittest.TestCase):
    OPTS = {"spawn": "worktree", "permission_mode": "auto", "keep_awake": False}

    def test_labels_differ_for_same_named_folders(self):
        a, b = remote_control.label("/one/api"), remote_control.label("/two/api")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(remote_control.LABEL_PREFIX + "api-"))

    def test_plist_runs_the_server_in_the_folder(self):
        body = remote_control.render("/work/My Repo", self.OPTS, "/opt/tools/claude", "/home/u", "/logs")
        data = plistlib.loads(body)
        self.assertEqual(data["ProgramArguments"], [
            "/opt/tools/claude", "remote-control", "--name", "My Repo", "--spawn", "worktree",
            "--permission-mode", "auto"])
        # 2.1.280 reads the bridge pointer only with createSessionInDir on.
        self.assertNotIn("--no-create-session-in-dir", data["ProgramArguments"])
        self.assertEqual(data["WorkingDirectory"], "/work/My Repo")
        self.assertTrue(data["KeepAlive"] and data["RunAtLoad"])
        self.assertEqual(data["ThrottleInterval"], remote_control.THROTTLE_SECONDS)
        self.assertEqual(data["EnvironmentVariables"]["PATH"].split(":")[0], "/opt/tools")
        self.assertIn("/usr/bin", data["EnvironmentVariables"]["PATH"].split(":"))

    def test_a_folder_s_spawn_and_env_reach_its_plist_only(self):
        opts = dict(self.OPTS, folder_options={
            Path("/w/dsa"): {"spawn": "same-dir", "env": {"X_FLAG": "1"}}})
        dsa = plistlib.loads(remote_control.render("/w/dsa", opts, "/bin/claude", "/home/u", "/logs"))
        other = plistlib.loads(remote_control.render("/w/app", opts, "/bin/claude", "/home/u", "/logs"))
        self.assertIn("same-dir", dsa["ProgramArguments"])
        self.assertEqual(dsa["EnvironmentVariables"]["X_FLAG"], "1")
        self.assertEqual(dsa["EnvironmentVariables"]["HOME"], "/home/u")
        self.assertIn("worktree", other["ProgramArguments"])
        self.assertNotIn("X_FLAG", other["EnvironmentVariables"])
        self.assertEqual(dsa["Label"], remote_control.label("/w/dsa"))

    def test_every_host_turns_on_uploads_for_the_files_its_sessions_send(self):
        data = plistlib.loads(remote_control.render("/w/app", self.OPTS, "/bin/claude", "/home/u", "/logs"))
        self.assertEqual(data["EnvironmentVariables"]["CLAUDE_CODE_BRIEF_UPLOAD"], "1")

    def test_a_folder_s_own_env_can_turn_uploads_off(self):
        opts = dict(self.OPTS, folder_options={
            Path("/w/dsa"): {"env": {"CLAUDE_CODE_BRIEF_UPLOAD": "", "X_FLAG": "1"}}})
        dsa = plistlib.loads(remote_control.render("/w/dsa", opts, "/bin/claude", "/home/u", "/logs"))
        self.assertEqual(dsa["EnvironmentVariables"]["CLAUDE_CODE_BRIEF_UPLOAD"], "")
        self.assertEqual(dsa["EnvironmentVariables"]["X_FLAG"], "1")

    def test_keep_awake_wraps_the_server(self):
        argv = remote_control.command("/w/r", dict(self.OPTS, keep_awake=True), "/bin/claude")
        self.assertEqual(argv[:3], ["/usr/bin/caffeinate", "-is", "/bin/claude"])


class TrustAndPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.trusted = self.base / "trusted"
        self.nested = self.trusted / "pkg"
        self.untrusted = self.base / "untrusted"
        for d in (self.nested, self.untrusted):
            d.mkdir(parents=True)
        self.state = self.base / ".claude.json"
        self.state.write_text(json.dumps({"projects": {
            str(self.trusted): {"hasTrustDialogAccepted": True},
            str(self.untrusted): {"hasTrustDialogAccepted": False}}}))
        self.agents = self.base / "LaunchAgents"

    def tearDown(self):
        self.tmp.cleanup()

    def test_trust_is_read_from_the_exact_folder(self):
        self.assertTrue(remote_control.trusted(self.trusted, self.state))
        self.assertFalse(remote_control.trusted(self.nested, self.state))
        self.assertFalse(remote_control.trusted(self.untrusted, self.state))

    def test_the_hint_names_the_command_that_fixes_it(self):
        hint = remote_control.trust_hint(self.nested)
        self.assertIn(f"bin/harness trust {self.nested}", hint)
        self.assertIn("claude", hint)

    def test_missing_or_corrupt_state_is_untrusted(self):
        self.assertFalse(remote_control.trusted(self.trusted, self.base / "absent.json"))
        self.state.write_text("{")
        self.assertFalse(remote_control.trusted(self.trusted, self.state))

    def test_plan_skips_untrusted_and_missing_and_finds_stale(self):
        self.agents.mkdir()
        stale = remote_control.LABEL_PREFIX + "gone-00000000"
        (self.agents / (stale + ".plist")).write_bytes(b"")
        (self.agents / "com.other.thing.plist").write_bytes(b"")
        opts = {"folders": [self.trusted, self.untrusted, self.base / "absent"]}
        serve, skipped, found = remote_control.plan(opts, self.agents, self.state)
        self.assertEqual(serve, [self.trusted])
        self.assertEqual([f for f, _ in skipped], [self.untrusted, self.base / "absent"])
        self.assertIn("trust", skipped[0][1])
        self.assertEqual(found, [stale])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.folder = self.home / "repos" / "project"
        self.folder.mkdir(parents=True)
        self.other = self.home / "repos" / "untrusted"
        self.other.mkdir()
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.folder): {"hasTrustDialogAccepted": True}}}))
        self.write_config([str(self.folder), str(self.other)])
        self.calls = []
        self.loaded = set()
        patches = [
            mock.patch.dict(os.environ, {"HARNESS_HOME": str(self.home)}),
            mock.patch.object(harness.platform, "system", return_value="Darwin"),
            mock.patch.object(harness.shutil, "which", return_value="/opt/tools/claude"),
            mock.patch.object(harness, "launchctl", side_effect=self.launchctl),
            # `status` asks the API which sessions are stranded; the suite never leaves the Mac.
            mock.patch.object(harness, "lost_sessions",
                              lambda folders: remote_control.SessionPage(
                                  remote_control.SessionPage.OK)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # patch.dict restores both on cleanup; other suites leave HARNESS_QUIET set, which mutes say().
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("HARNESS_QUIET", None)
        self.addCleanup(self.tmp.cleanup)

    def write_config(self, folders):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"remote_control": {"folders": folders, "permission_mode": "auto"}}))

    def launchctl(self, *args):
        self.calls.append(args)
        name = args[-1].split("/")[-1]
        if args[0] == "bootstrap":
            self.loaded.add(Path(args[-1]).stem)
        elif args[0] == "bootout":
            self.loaded.discard(name)
        code = 0 if args[0] != "print" or name in self.loaded else 113
        return subprocess.CompletedProcess(args, code, stdout="\tstate = running\n", stderr="")

    def run_action(self, action, dry_run=False):
        out = StringIO()
        with redirect_stdout(out):
            code = harness.cmd_remote_control(argparse.Namespace(action=action, dry_run=dry_run))
        return code, out.getvalue()

    def plist_path(self, folder):
        return self.home / "Library" / "LaunchAgents" / (remote_control.label(folder) + ".plist")

    def test_install_loads_trusted_folder_and_refuses_untrusted(self):
        code, out = self.run_action("install")
        self.assertEqual(code, 1)
        data = plistlib.loads(self.plist_path(self.folder).read_bytes())
        self.assertEqual(data["WorkingDirectory"], str(self.folder))
        self.assertIn("auto", data["ProgramArguments"])
        self.assertFalse(self.plist_path(self.other).exists())
        self.assertIn(f"refusing {self.other}", out)
        self.assertIn(f"bin/harness trust {self.other}", out)
        self.assertIn("bootstrap", [c[0] for c in self.calls])

    def test_reinstall_leaves_a_loaded_unchanged_agent_alone(self):
        self.run_action("install")
        self.calls.clear()
        _, out = self.run_action("install")
        self.assertIn("unchanged", out)
        self.assertEqual({c[0] for c in self.calls}, {"print"})

    def test_dry_run_writes_nothing(self):
        _, out = self.run_action("install", dry_run=True)
        self.assertIn("would serve", out)
        self.assertFalse(self.plist_path(self.folder).exists())
        self.assertEqual({c[0] for c in self.calls}, {"print"})

    def test_dropping_a_folder_removes_its_agent(self):
        self.run_action("install")
        self.write_config([])
        self.run_action("install")
        self.assertFalse(self.plist_path(self.folder).exists())
        self.assertNotIn(remote_control.label(self.folder), self.loaded)

    def test_status_reports_state_and_skips(self):
        self.run_action("install")
        _, out = self.run_action("status")
        self.assertIn(f"{self.folder}: running", out)
        self.assertIn(f"{self.other}: skipped", out)

    def test_uninstall_removes_every_agent(self):
        self.run_action("install")
        code, _ = self.run_action("uninstall")
        self.assertEqual(code, 0)
        self.assertFalse(self.plist_path(self.folder).exists())
        self.assertEqual(self.loaded, set())

    def test_a_changed_plist_on_a_running_host_adopts_its_environment(self):
        self.run_action("install")
        name = remote_control.label(self.folder)
        log_dir = self.home / ".local" / "state" / "agent-harness" / "remote-control"
        (log_dir / (name + ".log")).write_text("Registered, server environmentId=env_01LIVE\n")
        projects = self.home / ".claude" / "projects"
        pointer = remote_control.pointer_path(projects, self.folder)
        pointer.parent.mkdir(parents=True)
        pointer.write_text(json.dumps({"sessionId": "cse_01PRE", "environmentId": "env_01LIVE",
                                       "source": "standalone", "pid": 4242, "procStart": "x"}))
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.write_text(json.dumps({"remote_control": {"folders": [str(self.folder)],
                                                       "permission_mode": "plan"}}))
        order = []
        self.calls.clear()
        with mock.patch.object(harness, "host_pid", lambda n: 4242), \
             mock.patch.object(harness, "claude_host_pid", lambda pid: 4243), \
             mock.patch.object(harness, "remote_control_log_dir", lambda: log_dir), \
             mock.patch.object(harness.os, "kill", lambda pid, sig: order.append(("kill", pid, sig))):
            real = self.launchctl
            with mock.patch.object(harness, "launchctl",
                                   side_effect=lambda *a: (order.append((a[0],)), real(*a))[1]):
                code, out = self.run_action("install")
        self.assertEqual(code, 0)
        self.assertIn("adopting env_01LIVE from pid 4242", out)
        written = json.loads(pointer.read_text())
        self.assertEqual(written, {"sessionId": "cse_01PRE", "environmentId": "env_01LIVE",
                                   "source": "standalone"})
        steps = [o for o in order if o[0] != "print"]
        self.assertEqual(steps[0], ("kill", 4243, harness.signal.SIGKILL))
        self.assertEqual([s[0] for s in steps[1:3]], ["bootout", "bootstrap"])

    def test_an_unchanged_plist_is_never_adopted(self):
        self.run_action("install")
        with mock.patch.object(harness, "host_pid", lambda n: 4242), \
             mock.patch.object(harness.os, "kill") as kill:
            _, out = self.run_action("install")
        self.assertIn("unchanged", out)
        kill.assert_not_called()

    def test_bootstrap_failure_is_a_nonzero_exit(self):
        def fail(*args):
            return subprocess.CompletedProcess(args, 5 if args[0] != "bootout" else 0, stdout="", stderr="Input/output error")
        with mock.patch.object(harness, "launchctl", side_effect=fail):
            code, out = self.run_action("install")
        self.assertEqual(code, 1)
        self.assertIn("Input/output error", out)

    def test_other_platforms_do_nothing(self):
        with mock.patch.object(harness.platform, "system", return_value="Linux"):
            code, _ = self.run_action("install")
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])

    def test_invalid_config_exits(self):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.write_text(json.dumps({"remote_control": {"spawn": "fork"}}))
        with self.assertRaises(SystemExit):
            self.run_action("status")


class PointerTests(unittest.TestCase):
    def test_slug_matches_claude_code_project_key(self):
        self.assertEqual(remote_control.project_slug("/u/dev/repos/app"),
                         "-u-dev-repos-app")
        self.assertEqual(
            remote_control.project_slug("/u/dev/repos/app/.claude/worktrees/bridge-cse_01A"),
            "-u-dev-repos-app--claude-worktrees-bridge-cse-01A")

    def test_payload_carries_only_validated_keys(self):
        previous = {"sessionId": "cse_01A", "environmentId": "env_01NEW", "source": "standalone",
                    "pid": 1, "procStart": "old", "activeSessionIds": ["cse_01B"],
                    "activeSessionIdsPersistedAt": 42, "somethingElse": "drop me"}
        out = remote_control.pointer_payload("env_01NEW", 77, "Tue Sep 22 13:36:19 2026", previous)
        self.assertEqual(out["environmentId"], "env_01NEW")
        self.assertEqual(out["pid"], 77)
        self.assertEqual(out["procStart"], "Tue Sep 22 13:36:19 2026")
        self.assertEqual(out["source"], "standalone")
        self.assertEqual(out["sessionId"], "cse_01A")
        self.assertEqual(out["activeSessionIds"], ["cse_01B"])
        self.assertNotIn("somethingElse", out)
        self.assertLessEqual(set(out), set(remote_control.POINTER_KEYS))

    def test_2_1_280_parked_keys_survive_the_rewrite(self):
        previous = {"sessionId": "cse_01A", "environmentId": "env_01NEW", "source": "standalone",
                    "parkedProjectThreadSessionIds": ["cse_01P"],
                    "parkedProjectThreadSessionIdsPersistedAt": 99}
        out = remote_control.pointer_payload("env_01NEW", 77, "now", previous)
        self.assertEqual(out["parkedProjectThreadSessionIds"], ["cse_01P"])
        self.assertEqual(out["parkedProjectThreadSessionIdsPersistedAt"], 99)

    def test_an_adoption_pointer_names_no_process(self):
        previous = {"sessionId": "cse_01A", "environmentId": "env_01NEW", "pid": 5, "procStart": "x"}
        out = remote_control.pointer_payload("env_01NEW", None, None, previous)
        self.assertEqual(out, {"sessionId": "cse_01A", "environmentId": "env_01NEW",
                               "source": "standalone"})

    def test_install_step(self):
        step = remote_control.install_step
        self.assertEqual(step(True, True, 10, "env_01A"), "unchanged")
        self.assertEqual(step(True, False, 10, "env_01A"), "adopt")
        self.assertEqual(step(True, False, 10, None), "load")
        self.assertEqual(step(True, False, None, "env_01A"), "load")
        self.assertEqual(step(False, True, None, None), "load")

    def test_ids_from_another_environment_are_dropped(self):
        previous = {"sessionId": "cse_01A", "environmentId": "env_01OLD", "source": "standalone",
                    "activeSessionIds": ["cse_01B"], "activeSessionIdsPersistedAt": 42}
        out = remote_control.pointer_payload("env_01NEW", 77, "now", previous)
        self.assertEqual(out["sessionId"], "")
        self.assertNotIn("activeSessionIds", out)

    def test_payload_without_a_previous_file(self):
        out = remote_control.pointer_payload("env_01NEW", 5, "now")
        self.assertEqual(out["sessionId"], "")
        self.assertNotIn("activeSessionIds", out)

    def test_environment_id_takes_the_last_one_named(self):
        log = "registered env_01AAA\nlater\nregistered env_01BBB\ntail\n"
        self.assertEqual(remote_control.environment_id(log), "env_01BBB")
        self.assertIsNone(remote_control.environment_id("nothing here"))

    def test_pointer_is_current_only_while_young(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge-pointer.json"
            wanted = remote_control.pointer_payload("env_01A", 1, "now")
            path.write_text(json.dumps(wanted))
            self.assertTrue(remote_control.pointer_is_current(wanted, wanted, path))
            self.assertFalse(remote_control.pointer_is_current({"environmentId": "env_01B"}, wanted, path))
            old = os.stat(path).st_mtime - remote_control.POINTER_TTL_SECONDS
            os.utime(path, (old, old))
            self.assertFalse(remote_control.pointer_is_current(wanted, wanted, path))

    def test_read_pointer_tolerates_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge-pointer.json"
            self.assertIsNone(remote_control.read_pointer(path))
            path.write_text("not json")
            self.assertIsNone(remote_control.read_pointer(path))
            path.write_text("[1]")
            self.assertIsNone(remote_control.read_pointer(path))


class RetryRunTests(unittest.TestCase):
    def test_trailing_run_is_measured(self):
        log = ("[01:30:00] Connected\n"
               "[01:40:00] Connection error, retrying in 2s (0s elapsed): fetch failed\n"
               "[01:47:30] Connection error, retrying in 2m (450s elapsed): fetch failed\n")
        self.assertEqual(remote_control.unreachable_seconds(log), 450)

    def test_a_later_line_resets_the_run(self):
        log = ("[01:40:00] Connection error, retrying in 2s\n"
               "[01:47:30] Connection error, retrying in 2m\n"
               "[01:48:00] Connected\n")
        self.assertEqual(remote_control.unreachable_seconds(log), 0)

    def test_a_run_crossing_midnight_does_not_go_negative(self):
        log = "[23:56:00] Connection error, retrying\n[00:04:00] Connection error, retrying\n"
        self.assertEqual(remote_control.unreachable_seconds(log), 480)

    def test_a_single_line_is_not_yet_a_run(self):
        self.assertEqual(remote_control.unreachable_seconds("[01:40:00] Connection error, retrying\n"), 0)


class HealPlistTests(unittest.TestCase):
    def test_interval_and_command(self):
        body = plistlib.loads(remote_control.render_heal("/bin/harness", "/u/dev", "/tmp/logs"))
        self.assertEqual(body["StartInterval"], 60)
        self.assertEqual(body["ProgramArguments"],
                         ["/bin/harness", "remote-control", "heal", "--once"])
        self.assertEqual(body["Label"], "com.agent-harness.remote-control-heal")

    def test_label_is_outside_the_folder_agent_glob(self):
        # `installed()` globs the folder labels; the heal agent must not look stale to `install`.
        self.assertFalse(remote_control.HEAL_LABEL.startswith(remote_control.LABEL_PREFIX))


class HealRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.folder = self.home / "repos" / "app"
        (self.folder / ".claude" / "worktrees").mkdir(parents=True)
        # `settings()` resolves every folder, and /var is a symlink to /private/var on macOS.
        self.folder = self.folder.resolve()
        (self.home / ".config" / "agent-harness").mkdir(parents=True)
        (self.home / ".config" / "agent-harness" / "config.json").write_text(
            json.dumps({"remote_control": {"folders": [str(self.folder)]}}))
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.folder): {"hasTrustDialogAccepted": True}}}))
        self.log_dir = self.home / ".local" / "state" / "agent-harness" / "remote-control"
        self.log_dir.mkdir(parents=True)
        self.label = remote_control.label(self.folder)
        (self.log_dir / (self.label + ".log")).write_text("bridge up on env_01LIVE\n")
        self.token = None
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": ""}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_heal(self, **flags):
        fields = dict(action="heal", once=True, dry_run=False, preserve_worktrees=False)
        fields.update(flags)
        args = argparse.Namespace(**fields)
        lines = []
        with mock.patch.object(harness, "say", lines.append), \
             mock.patch.object(harness, "home", lambda: self.home), \
             mock.patch.object(harness, "remote_control_log_dir", lambda: self.log_dir), \
             mock.patch.object(harness, "claude_state_file", lambda: self.home / ".claude.json"), \
             mock.patch.object(harness, "host_pid", lambda name: 4242), \
             mock.patch.object(harness, "proc_start", lambda pid: "Tue Sep 22 13:36:19 2026"), \
             mock.patch.object(harness, "claude_oauth_token", lambda: self.token), \
             mock.patch.object(harness.platform, "system", lambda: "Darwin"):
            code = harness.cmd_remote_control_heal(args)
        return code, "\n".join(lines)

    def pointer(self):
        return remote_control.pointer_path(self.home / ".claude" / "projects", self.folder)

    def test_writes_the_live_environment(self):
        code, _ = self.run_heal()
        self.assertEqual(code, 0)
        written = json.loads(self.pointer().read_text())
        self.assertEqual(written["environmentId"], "env_01LIVE")
        self.assertEqual(written["pid"], 4242)
        self.assertEqual(written["source"], "standalone")
        self.assertIn("pointer", (self.log_dir / "heal.log").read_text())

    def test_dry_run_writes_nothing(self):
        code, out = self.run_heal(dry_run=True)
        self.assertEqual(code, 0)
        self.assertFalse(self.pointer().exists())
        self.assertFalse((self.log_dir / "heal.log").exists())
        self.assertIn("would", out)

    def test_a_stopped_host_is_skipped_without_writing(self):
        args = argparse.Namespace(action="heal", once=True, dry_run=False, preserve_worktrees=False)
        with mock.patch.object(harness, "home", lambda: self.home), \
             mock.patch.object(harness, "remote_control_log_dir", lambda: self.log_dir), \
             mock.patch.object(harness, "claude_state_file", lambda: self.home / ".claude.json"), \
             mock.patch.object(harness, "host_pid", lambda name: None), \
             mock.patch.object(harness, "claude_oauth_token", lambda: None), \
             mock.patch.object(harness, "say", lambda line: None), \
             mock.patch.object(harness.platform, "system", lambda: "Darwin"):
            self.assertEqual(harness.cmd_remote_control_heal(args), 0)
        self.assertFalse(self.pointer().exists())

    def write_log(self, text):
        (self.log_dir / (self.label + ".log")).write_text(text)

    def fake_api(self, rows, status=200, fail=False):
        """A stand-in for `urlopen`: the sessions page on GET, a recorded POST otherwise."""
        sent = []

        def opener(request, timeout=None):
            if request.get_method() == "GET":
                body = json.dumps({"data": rows, "next_cursor": None}).encode()
            else:
                if fail:
                    raise OSError("no route to host")
                sent.append((request.full_url, json.loads(request.data), request.get_header("Anthropic-beta")))
                if status != 200:
                    raise harness_http_error(request.full_url, status)
                body = b'{"environment_id": "env"}'
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = body
            response.__enter__.return_value.status = status
            return response
        return opener, sent

    ROWS = [
        {"id": "cse_01LOST", "status": "active", "connection_status": "disconnected",
         "environment_id": "env_01LIVE", "last_event_at": "2026-09-23T10:00:00Z"},
        {"id": "cse_01PARK", "status": "active", "connection_status": "disconnected",
         "environment_id": "env_01PTR", "last_event_at": "2026-09-23T09:00:00Z"},
        {"id": "cse_01HERE", "status": "active", "connection_status": "connected",
         "environment_id": "env_01LIVE", "last_event_at": "2026-09-23T08:00:00Z"},
        {"id": "cse_01THEIRS", "status": "active", "connection_status": "disconnected",
         "environment_id": "env_01OTHERMAC", "last_event_at": "2026-09-23T07:00:00Z"}]

    def seed_pointer_env(self):
        # heal's own pass rewrites a pointer to the live env, so the pointer names a second
        # folder's environment here: every configured folder's pointer counts as this Mac's.
        second = (self.home / "repos" / "dsa").resolve()
        second.mkdir(parents=True)
        (self.home / ".config" / "agent-harness" / "config.json").write_text(json.dumps(
            {"remote_control": {"folders": [str(self.folder), str(second)]}}))
        path = remote_control.pointer_path(self.home / ".claude" / "projects", second)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"sessionId": "", "environmentId": "env_01PTR", "source": "standalone"}))

    def test_disconnected_sessions_on_this_mac_are_reconnected_once(self):
        self.token = "sk-live"
        self.seed_pointer_env()
        opener, sent = self.fake_api(self.ROWS)
        with mock.patch.object(remote_control, "urlopen", opener):
            code, out = self.run_heal()
            self.assertEqual(code, 0)
            self.assertEqual([(u, b["session_id"]) for u, b, _ in sent], [
                ("https://api.anthropic.com/v1/environments/env_01LIVE/bridge/reconnect", "cse_01LOST"),
                ("https://api.anthropic.com/v1/environments/env_01PTR/bridge/reconnect", "cse_01PARK")])
            self.assertEqual(sent[0][2], "oauth-2025-04-20,environments-2025-11-01")
            log = (self.log_dir / "heal.log").read_text()
            self.assertIn("reconnect cse_01LOST on env_01LIVE: 200", log)
            self.assertNotIn("sk-live", log + out)
            # The next pass, a minute later, is inside the ten-minute window.
            self.run_heal()
        self.assertEqual(len(sent), 2)
        state = json.loads((self.log_dir / remote_control.STATE_NAME).read_text())
        self.assertEqual(set(state["reconnected"]), {"cse_01LOST", "cse_01PARK"})

    def test_reconnect_honours_dry_run(self):
        self.token = "sk-live"
        opener, sent = self.fake_api(self.ROWS)
        with mock.patch.object(remote_control, "urlopen", opener):
            _, out = self.run_heal(dry_run=True)
        self.assertEqual(sent, [])
        self.assertIn("would reconnect cse_01LOST on env_01LIVE", out)
        self.assertFalse((self.log_dir / remote_control.STATE_NAME).exists())

    def test_a_failed_reconnect_is_logged_and_does_not_raise(self):
        self.token = "sk-live"
        opener, _ = self.fake_api(self.ROWS, fail=True)
        with mock.patch.object(remote_control, "urlopen", opener):
            code, _ = self.run_heal()
        self.assertEqual(code, 0)
        self.assertIn("reconnect cse_01LOST on env_01LIVE: failed: OSError no route to host",
                      (self.log_dir / "heal.log").read_text())

    def test_a_refused_reconnect_logs_its_status(self):
        self.token = "sk-live"
        opener, _ = self.fake_api(self.ROWS, status=404)
        with mock.patch.object(remote_control, "urlopen", opener):
            self.run_heal()
        self.assertIn("reconnect cse_01LOST on env_01LIVE: 404",
                      (self.log_dir / "heal.log").read_text())

    def test_no_token_skips_reconnect_quietly(self):
        opener = mock.MagicMock()
        with mock.patch.object(remote_control, "urlopen", opener):
            code, _ = self.run_heal()
        self.assertEqual(code, 0)
        opener.assert_not_called()
        self.assertNotIn("reconnect", (self.log_dir / "heal.log").read_text())

    def test_an_outage_short_of_the_stop_is_only_warned_about(self):
        self.write_log("bridge up on env_01LIVE\n"
                       "[01:39:00] Connection error, retrying in 2s (0s elapsed): fetch failed\n"
                       "[01:47:00] Connection error, retrying in 2m (480s elapsed): fetch failed\n")
        with mock.patch.object(harness.os, "kill") as kill:
            code, out = self.run_heal()
        self.assertEqual(code, 0)
        self.assertIn("unreachable for 480s", out)
        self.assertIn("give-up at 600s", out)
        kill.assert_not_called()

    def test_the_host_is_stopped_once_at_nine_minutes(self):
        self.write_log("bridge up on env_01LIVE\n"
                       "[01:39:00] Connection error, retrying in 2s (0s elapsed): fetch failed\n"
                       "[01:48:01] Connection error, retrying in 2m (541s elapsed): fetch failed\n")
        with mock.patch.object(harness, "claude_host_pid", lambda pid: 9001), \
             mock.patch.object(harness.os, "kill") as kill:
            _, out = self.run_heal()
            self.assertEqual(kill.call_args[0], (9001, harness.signal.SIGTERM))
            self.assertIn("SIGTERM to claude pid 9001 after 541s", out)
            kill.reset_mock()
            # The same host, still failing, on the next minute's pass: the pid is the guard.
            _, again = self.run_heal()
            kill.assert_not_called()
        self.assertNotIn("SIGTERM", again)
        state = remote_control.read_state(self.log_dir / remote_control.STATE_NAME)
        self.assertEqual(state["stopped"], {self.label: 4242})

    def test_system_sleep_resets_the_budget_and_stops_nothing(self):
        self.write_log("bridge up on env_01LIVE\n"
                       "[01:39:00] Connection error, retrying in 2s (0s elapsed): fetch failed\n"
                       "[01:48:01] Connection error, retrying in 2m (541s elapsed): fetch failed\n"
                       "[bridge:work] Detected system sleep (312s gap), resetting error budget\n")
        with mock.patch.object(harness, "claude_host_pid", lambda pid: 9001), \
             mock.patch.object(harness.os, "kill") as kill:
            _, out = self.run_heal()
        kill.assert_not_called()
        self.assertNotIn("SIGTERM", out)

    def test_a_removed_worktree_is_recreated_at_its_path_and_branch(self):
        root = self.folder
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "init"],
                       check=True, env=env)
        tree = root / ".claude" / "worktrees" / "bridge-cse_019mg2am4VzseDSBP658Z3j6"
        self.write_log(
            "bridge up on env_01LIVE\n"
            "[01:47:17] Error: Persistent errors for 10 minutes, giving up.\n"
            "[01:47:17] Shutting down 5 active session(s)…\n"
            f"[01:47:19] removed worktree {tree}\n"
            f"[01:47:23] kept worktree {root}/.claude/worktrees/bridge-cse_01X · uncommitted changes\n")
        with mock.patch.dict(os.environ, env):
            _, out = self.run_heal()
        self.assertTrue(tree.is_dir())
        self.assertIn(f"worktree {tree}: recreated on worktree-bridge-cse_019mg2am4VzseDSBP658Z3j6", out)
        self.assertNotIn("bridge-cse_01X", out)
        branch = subprocess.run(["git", "-C", str(tree), "branch", "--show-current"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "worktree-bridge-cse_019mg2am4VzseDSBP658Z3j6")
        # A second pass has the line in its state and does not fight a worker for the directory.
        with mock.patch.dict(os.environ, env):
            _, again = self.run_heal()
        self.assertNotIn("worktree", again)

    def test_wip_guard_commits_a_dirty_session_worktree(self):
        tree = self.folder / ".claude" / "worktrees" / "bridge-cse_01A"
        tree.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        subprocess.run(["git", "-C", str(tree), "commit", "-q", "--allow-empty", "-m", "init"],
                       check=True, env=env)
        (tree / "work.txt").write_text("unpushed")
        self.assertEqual([p.name for p in harness.dirty_bridge_worktrees(self.folder)],
                         ["bridge-cse_01A"])
        with mock.patch.dict(os.environ, env):
            self.assertTrue(harness.wip_commit(tree, dry=False))
        self.assertEqual(harness.dirty_bridge_worktrees(self.folder), [])
        subject = subprocess.run(["git", "-C", str(tree), "log", "-1", "--format=%s"],
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, "chore(wip): preserve work before reconnect")


# Every line here is copied from a real host log, so a wording change upstream fails a test
# rather than silently disarming the supervisor.
GIVE_UP_LOG = """[01:47:17] Error: Persistent errors for 10 minutes, giving up.
[01:47:17] Shutting down 5 active session(s)…
[01:47:19] removed worktree /repos/app/.claude/worktrees/bridge-cse_019mg2am4VzseDSBP658Z3j6
[01:47:23] kept worktree /repos/app/.claude/worktrees/bridge-cse_01XvYaz9pZw94g7wzjJ6EJHM · uncommitted changes
"""


class HostLogTests(unittest.TestCase):
    def test_the_elapsed_figure_the_host_prints_wins_over_the_clock(self):
        log = ("[01:39:00] Connected\n"
               "[01:48:01] Connection error, retrying in 2m (541s elapsed): fetch failed\n")
        self.assertEqual(remote_control.unreachable_seconds(log), 541)

    def test_a_reconnect_ends_the_run(self):
        log = ("[01:48:01] Connection error, retrying in 2m (541s elapsed): fetch failed\n"
               "[02:25:43] Reconnected after 7s\n")
        self.assertEqual(remote_control.unreachable_seconds(log), 0)

    def test_system_sleep_resets_the_budget(self):
        log = ("[01:48:01] Connection error, retrying in 2m (541s elapsed): fetch failed\n"
               "[bridge:work] Detected system sleep (312s gap), resetting error budget\n")
        self.assertEqual(remote_control.unreachable_seconds(log), 0)
        self.assertTrue(remote_control.SLEEP_RESET.search(log))

    def test_the_give_up_and_shutdown_lines_are_recognised(self):
        self.assertTrue(remote_control.gave_up(GIVE_UP_LOG))
        self.assertEqual(remote_control.SHUTTING_DOWN.search(GIVE_UP_LOG).group(1), "5")
        self.assertFalse(remote_control.gave_up("[01:39:00] Connected\n"))

    def test_removed_worktrees_leave_out_the_kept_one(self):
        self.assertEqual(remote_control.removed_worktrees(GIVE_UP_LOG),
                         [("01:47:19", "/repos/app/.claude/worktrees/bridge-cse_019mg2am4VzseDSBP658Z3j6")])

    def test_the_caffeinate_wrapper_is_not_the_host(self):
        self.assertFalse(remote_control.is_host_process(
            "/usr/bin/caffeinate -is /u/.local/bin/claude remote-control --name app"))
        self.assertTrue(remote_control.is_host_process(
            "/u/.local/bin/claude remote-control --name app --spawn worktree"))


class SupervisorStateTests(unittest.TestCase):
    def test_a_missing_or_junk_file_is_an_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / remote_control.STATE_NAME
            empty = {"stopped": {}, "recreated": [], "reconnected": {}}
            self.assertEqual(remote_control.read_state(path), empty)
            path.write_text("[]")
            self.assertEqual(remote_control.read_state(path), empty)

    def test_the_stop_fires_once_per_host_process(self):
        state = {"stopped": {}, "recreated": []}
        self.assertFalse(remote_control.stop_is_due(539, "label", 10, state))
        self.assertTrue(remote_control.stop_is_due(540, "label", 10, state))
        remote_control.record_stop(state, "label", 10)
        self.assertFalse(remote_control.stop_is_due(700, "label", 10, state))
        # launchd's relaunch is a new pid, and its own outage gets its own stop.
        self.assertTrue(remote_control.stop_is_due(700, "label", 11, state))

    def test_a_session_is_reconnected_at_most_once_per_window(self):
        state = remote_control.read_state("/nonexistent")
        self.assertTrue(remote_control.reconnect_is_due("cse_01A", state, 1000))
        remote_control.record_reconnect(state, "cse_01A", 1000)
        self.assertFalse(remote_control.reconnect_is_due("cse_01A", state, 1599))
        self.assertTrue(remote_control.reconnect_is_due("cse_01A", state, 1600))
        self.assertTrue(remote_control.reconnect_is_due("cse_01B", state, 1001))
        remote_control.record_reconnect(state, "cse_01B", 1700)
        self.assertEqual(state["reconnected"], {"cse_01B": 1700})

    def test_a_worktree_line_is_acted_on_once(self):
        state = {"stopped": {}, "recreated": []}
        removed = [("01:47:19", "/repos/app/.claude/worktrees/bridge-cse_01A")]
        self.assertEqual(remote_control.unseen_worktrees(removed, state), removed)
        remote_control.record_worktree(state, *removed[0])
        self.assertEqual(remote_control.unseen_worktrees(removed, state), [])

    def test_the_add_command_reuses_a_surviving_branch(self):
        fresh = remote_control.worktree_add_argv("/r", "/r/w", "worktree-bridge-cse_01A", "main", False)
        self.assertEqual(fresh[-4:], ["/r/w", "-b", "worktree-bridge-cse_01A", "main"])
        kept = remote_control.worktree_add_argv("/r", "/r/w", "worktree-bridge-cse_01A", "main", True)
        self.assertEqual(kept[-2:], ["/r/w", "worktree-bridge-cse_01A"])


class LostSessionTests(unittest.TestCase):
    # One page of `GET /v1/code/sessions?limit=50` in the shape and row order observed
    # 2026-09-22. The page is newest-first by `last_event_at`; `updated_at` goes backwards
    # within it, twice here as on the live account, which is why the order check reads the
    # former. `next_cursor` is null, so a count over this page may speak for the account.
    PAGE = {"data": [
        {"id": "cse_01LOST", "status": "active", "connection_status": "disconnected",
         "environment_id": "env_01LIVE", "title": "0.12 release checklist",
         "updated_at": "2026-09-22T17:49:06.555477Z",
         "last_event_at": "2026-09-22T17:49:06.555477Z"},
        {"id": "cse_01HERE", "status": "active", "connection_status": "connected",
         "environment_id": "env_01LIVE", "title": "still served",
         "updated_at": "2026-09-22T17:50:26Z",
         "last_event_at": "2026-09-22T17:30:12.001000Z"},
        {"id": "cse_01OLD", "status": "archived", "connection_status": "disconnected",
         "environment_id": "env_01LIVE", "title": "archived",
         "updated_at": "2026-09-21T00:00:00Z",
         "last_event_at": "2026-09-21T23:11:00.000000Z"},
        {"id": "cse_01THEIRS", "status": "active", "connection_status": "disconnected",
         "environment_id": "env_01OTHERMAC", "title": "another device",
         "updated_at": "2026-09-22T17:00:00Z",
         "last_event_at": "2026-09-21T18:00:00.000000Z"}], "next_cursor": None}

    def test_the_token_comes_out_of_the_keychain_payload(self):
        self.assertEqual(remote_control.oauth_token(
            json.dumps({"claudeAiOauth": {"accessToken": "sk-live", "scopes": []}})), "sk-live")
        self.assertIsNone(remote_control.oauth_token("not json"))
        self.assertIsNone(remote_control.oauth_token(json.dumps({"claudeAiOauth": {}})))

    def test_owned_environments_join_logs_and_pointers(self):
        self.assertEqual(remote_control.owned_environments(
            ["env_01A", "env_01B"], [{"environmentId": "env_01B"}, {"environmentId": "env_01C"},
                                     None, {"environmentId": ""}]),
            ["env_01A", "env_01B", "env_01C"])

    def test_the_warning_says_heal_reconnects_first(self):
        self.assertIn("heal reconnects these automatically", remote_control.REATTACH_WARNING)

    def test_the_request_carries_the_oauth_beta_headers(self):
        request = remote_control.sessions_request("sk-live")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-live")
        self.assertEqual(request.get_header("Anthropic-beta"), "oauth-2025-04-20")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")

    def test_only_this_mac_s_active_disconnected_sessions_count(self):
        rows = remote_control.disconnected_sessions(self.PAGE["data"], ["env_01LIVE"])
        self.assertEqual([r["id"] for r in rows], ["cse_01LOST"])
        self.assertEqual(remote_control.disconnected_sessions(self.PAGE["data"], []), [])

    def test_a_failed_call_is_a_failed_page_and_not_an_exception(self):
        def boom(request, timeout=None):
            raise OSError("no route to host")
        page = remote_control.fetch_sessions("sk-live", opener=boom)
        self.assertEqual(page.status, remote_control.SessionPage.FAILED)
        self.assertEqual(page.rows, [])

    def test_status_prints_the_manual_command_with_its_warning(self):
        payload = json.dumps(self.PAGE).encode()
        opener = mock.MagicMock()
        opener.return_value.__enter__.return_value.read.return_value = payload
        lines = []
        with mock.patch.object(remote_control, "urlopen", opener), \
             mock.patch.object(harness, "claude_oauth_token", lambda: "sk-live"), \
             mock.patch.object(harness, "host_environment_ids", lambda folders: ["env_01LIVE"]), \
             mock.patch.object(harness, "say", lines.append):
            found = harness.report_lost_sessions([Path("/repos/app")], {"permission_mode": "auto"})
        out = "\n".join(lines)
        self.assertEqual(found, 1)
        self.assertIn("1 active but disconnected", out)
        self.assertIn("claude remote-control --session-id cse_01LOST --permission-mode auto", out)
        self.assertIn(remote_control.REATTACH_WARNING, out)
        self.assertNotIn("cse_01THEIRS", out)
        self.assertNotIn("sk-live", out)

    def test_no_token_reads_differently_from_no_lost_sessions(self):
        lines = []
        with mock.patch.object(harness, "claude_oauth_token", lambda: None), \
             mock.patch.object(harness, "say", lines.append):
            self.assertEqual(harness.report_lost_sessions([], {"permission_mode": "default"}), 0)
        self.assertIn("not checked", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
