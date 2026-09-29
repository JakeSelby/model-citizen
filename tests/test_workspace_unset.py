# SPDX-License-Identifier: MIT
"""`citizen config unset workspaces_dir` switches workspace support off from the CLI.

After it runs the key is gone from the user config, `workspace list` says how to set it, `sync`
puts `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` back as its journal holds it, and the session
hook supplies no workspace block. `unset` removes only keys whose absence is a documented state,
so it is never a way around the checks `config set` runs. Everything runs under a temporary HOME.
Run: python3 -m unittest discover -s tests -p test_workspace_unset.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home, without_harness_vars
from test_harness import harness

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "harness"
HOOK = REPO / "adapters" / "claude-code" / "hook.py"
VAR = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"
INHERITED = ("CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", VAR)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.home = base / "home"
        self.ws = base / "ws"
        for name in ("root", "member"):
            (self.ws / name).mkdir(parents=True)
        (self.ws / "member" / "CLAUDE.md").write_text("Codeword ALPHA.\n")
        (self.ws / "demo.code-workspace").write_text(
            json.dumps({"folders": [{"path": "root"}, {"path": "member"}]}))
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        self.addCleanup(self.tmp.cleanup)
        isolate_home(self.home)
        self.config_file = self.home / ".config" / "agent-harness" / "config.json"
        self.settings_file = self.home / ".claude" / "settings.json"

    def config(self):
        return json.loads(self.config_file.read_text())

    def quietly(self, call, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return call(*args)

    def run_cli(self, *argv):
        env = dict(without_harness_vars(), HOME=str(self.home), HARNESS_HOME=str(self.home))
        env.pop("CLAUDE_CONFIG_DIR", None)
        return subprocess.run([sys.executable, str(CLI)] + list(argv), capture_output=True,
                              text=True, env=env, cwd=str(self.ws))

    def sync(self):
        return self.quietly(harness.cmd_sync, harness.argparse.Namespace(
            dry_run=False, adopt=False, adopt_codex=False, print_only=False))

    def env(self):
        return json.loads(self.settings_file.read_text()).get("env") or {}

    def session_context(self):
        env = without_harness_vars()
        for key in INHERITED + ("CLAUDE_CONFIG_DIR",):
            env.pop(key, None)
        env["HOME"] = str(self.home)
        cwd = str(self.ws / "root")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup", "cwd": cwd,
                              "session_id": "s"})
        out = subprocess.run([sys.executable, str(HOOK), "workspace"], input=payload, cwd=cwd,
                             env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        if not out.stdout.strip():
            return ""
        return json.loads(out.stdout).get("hookSpecificOutput", {}).get("additionalContext", "")


class UnsetCommand(Fixture):
    """Acceptance 1: the key goes, and `workspace list` says how to set it and exits 1."""

    def test_unset_removes_the_key_and_list_says_how_to_set_it(self):
        self.assertEqual(self.run_cli("config", "set", "workspaces_dir", str(self.ws)).returncode, 0)
        self.assertEqual(self.run_cli("workspace", "list").returncode, 0)
        before = self.config()
        done = self.run_cli("config", "unset", "workspaces_dir")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("workspaces_dir unset", done.stdout)
        after = self.config()
        self.assertNotIn("workspaces_dir", after)
        before.pop("workspaces_dir")
        self.assertEqual(after, before)
        listed = self.run_cli("workspace", "list")
        self.assertEqual(listed.returncode, 1)
        self.assertIn("citizen config set workspaces_dir", listed.stderr)

    def test_unsetting_an_absent_key_says_so_and_changes_nothing(self):
        self.assertEqual(self.quietly(harness.config_unset, "workspaces_dir", None), 0)
        self.assertFalse(self.config_file.exists())
        self.quietly(harness.config_set, "identity.name", "Tester")
        written = self.config_file.read_text()
        done = self.run_cli("config", "unset", "workspaces_dir")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("workspaces_dir is not set", done.stdout)
        self.assertEqual(self.config_file.read_text(), written)

    def test_a_value_is_refused(self):
        self.quietly(harness.config_set, "workspaces_dir", str(self.ws))
        with self.assertRaises(SystemExit) as caught:
            harness.config_unset("workspaces_dir", "/elsewhere")
        self.assertIn("takes no value", str(caught.exception))
        self.assertEqual(self.config()["workspaces_dir"], str(self.ws))


class SwitchedOff(Fixture):
    """Acceptance 2: sync restores the variable and a new session gets no workspace block."""

    def test_sync_restores_the_variable_and_the_session_gets_no_block(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {"MY_VAR": "keep"}}))
        self.quietly(harness.config_set, "workspaces_dir", str(self.ws))
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {"MY_VAR": "keep", VAR: "1"})
        self.assertIn("Codeword ALPHA.", self.session_context())

        self.assertEqual(self.quietly(harness.config_unset, "workspaces_dir", None), 0)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {"MY_VAR": "keep"})
        self.assertEqual(self.session_context(), "")

    def test_an_adopted_hand_set_value_stays_after_the_unset(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {VAR: "1"}}))
        self.quietly(harness.config_set, "workspaces_dir", str(self.ws))
        self.assertEqual(self.sync(), 0)
        self.quietly(harness.config_unset, "workspaces_dir", None)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {VAR: "1"})


class Guard(Fixture):
    """An unset is refused wherever it would skip a check `config set` runs."""

    def test_switches_acknowledgements_governance_and_stances_are_refused(self):
        self.quietly(harness.config_set, "hooks.workspace-session", "off")
        self.quietly(harness.config_set, "governance.provider", "local")
        written = self.config_file.read_text()
        for key in ("hooks.workspace-session", "rules.secrets", "core_switches_acknowledged",
                    "permissions_bypass_acknowledged", "governance", "governance.provider",
                    "governance.jev.mode", "stances", "stances.testing", "mode", "permissions",
                    "claude.manage", "identity.name", "primitive_roots", "plan_allow_tools",
                    "integrations.architecture-viewer.adapter", "workspace_dir"):
            with self.subTest(key=key), self.assertRaises(SystemExit) as caught:
                harness.config_unset(key, None)
            self.assertIn("not a key this command unsets", str(caught.exception))
        self.assertEqual(self.config_file.read_text(), written)

    def test_the_cli_refusal_exits_nonzero_and_writes_nothing(self):
        self.assertEqual(self.run_cli("config", "set", "workspaces_dir", str(self.ws)).returncode, 0)
        written = self.config_file.read_text()
        done = self.run_cli("config", "unset", "core_switches_acknowledged")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("citizen config set core_switches_acknowledged", done.stderr)
        self.assertEqual(self.config_file.read_text(), written)

    def test_a_symlinked_config_is_refused(self):
        self.quietly(harness.config_set, "workspaces_dir", str(self.ws))
        real = self.home / "real.json"
        self.config_file.rename(real)
        self.config_file.symlink_to(real)
        with self.assertRaises(SystemExit) as caught:
            harness.config_unset("workspaces_dir", None)
        self.assertIn("symlink", str(caught.exception))
        self.assertIn("workspaces_dir", json.loads(real.read_text()))


class Documented(unittest.TestCase):
    """Acceptance 3: the workspace page names the command."""

    def test_the_workspace_page_names_the_unset_command(self):
        text = (REPO / "docs" / "workspaces.md").read_text(encoding="utf-8")
        self.assertIn("citizen config unset workspaces_dir", text)


if __name__ == "__main__":
    unittest.main()
