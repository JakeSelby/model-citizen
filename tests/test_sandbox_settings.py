# SPDX-License-Identifier: MIT
"""The settings template's sandbox block, rendered by `sync` only for a user who opts in.

`sandbox.enabled` in the user config (default false) makes `sync` write the template's `sandbox`
block into `~/.claude/settings.json`, with `filesystem.allowWrite` derived from the workspace:
the state directory, the task-worktree root, Claude Code's scratch root and the folders of every
workspace. Off, nothing is written; switched back off, the key returns to what it held. Every
sync runs under a temporary HOME. Run: python3 -m unittest discover -s tests -p test_sandbox_settings.py
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home
from test_harness import harness

REPO = Path(__file__).resolve().parent.parent
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())
TEMPLATE = json.loads((REPO / "claude" / "settings.template.json").read_text())
OFF = {"export": "none", "native": False}


class Template(unittest.TestCase):
    def test_the_template_turns_the_sandbox_on_and_keeps_bash_prompts(self):
        block = TEMPLATE["sandbox"]
        self.assertIs(block["enabled"], True)
        self.assertIs(block["autoAllowBashIfSandboxed"], False)
        self.assertEqual(block["filesystem"]["allowWrite"], [])

    def test_the_manifest_owns_the_key_only_while_opted_in(self):
        self.assertEqual(OWNERSHIP["claude"]["sandbox"]["settings_keys"], ["sandbox"])
        self.assertNotIn("sandbox", OWNERSHIP["claude"]["owned_keys"])
        self.assertIn("sandbox unless sandbox.enabled is true", OWNERSHIP["claude"]["never_touch"])

    def test_the_example_config_leaves_it_off(self):
        example = json.loads((REPO / "config.example.json").read_text())
        self.assertEqual(example["sandbox"], {"enabled": False, "strict": False})


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        self.addCleanup(self.tmp.cleanup)
        isolate_home(self.home)
        os.environ["HARNESS_WORKTREE_ROOT"] = str(self.home / "trees")
        os.environ["CLAUDE_CODE_TMPDIR"] = str(self.home / "tmp")

    def apply(self, cfg, live=None, held=None):
        live = live or {}
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(merged, live, held or {}, OFF,
                                                     dict(cfg, stances={}), OWNERSHIP, TEMPLATE)
        return merged, paths, notices

    def test_off_by_default_writes_nothing(self):
        for cfg in ({}, {"sandbox": {"enabled": False}}, {"sandbox": {"enabled": "true"}}):
            merged, paths, notices = self.apply(cfg)
            self.assertNotIn("sandbox", merged)
            self.assertEqual((paths, notices), ([], []))

    def test_opted_in_renders_the_derived_write_paths(self):
        merged, paths, notices = self.apply({"sandbox": {"enabled": True}})
        self.assertEqual((paths, notices), ([["sandbox"]], []))
        block = merged["sandbox"]
        self.assertIs(block["enabled"], True)
        self.assertIs(block["autoAllowBashIfSandboxed"], False)
        self.assertNotIn("allowUnsandboxedCommands", block)
        allow = block["filesystem"]["allowWrite"]
        self.assertEqual(allow[:2], [str(self.home / ".local" / "state" / "agent-harness"),
                                     str(self.home / "trees")])
        self.assertIn(str((self.home / "tmp").resolve() / ("claude-%d" % os.getuid())), allow)

    def test_workspace_folders_are_writable(self):
        spaces, member = self.home / "spaces", self.home / "repo-b"
        spaces.mkdir()
        member.mkdir()
        (spaces / "pair.code-workspace").write_text(json.dumps({"folders": [{"path": str(member)}]}))
        merged, _, _ = self.apply({"sandbox": {"enabled": True}, "workspaces_dir": str(spaces)})
        self.assertIn(str(member), merged["sandbox"]["filesystem"]["allowWrite"])

    def test_strict_closes_the_unsandboxed_retry_and_the_fallback(self):
        merged, _, _ = self.apply({"sandbox": {"enabled": True, "strict": True}})
        self.assertIs(merged["sandbox"]["allowUnsandboxedCommands"], False)
        self.assertIs(merged["sandbox"]["failIfUnavailable"], True)

    def test_a_hand_written_sandbox_block_is_left_and_reported(self):
        live = {"sandbox": {"enabled": True, "filesystem": {"allowWrite": ["~/mine"]}}}
        merged, paths, notices = self.apply({"sandbox": {"enabled": True}}, live)
        self.assertEqual(merged["sandbox"], live["sandbox"])
        self.assertEqual(paths, [])
        self.assertIn("the sandbox left it alone", notices[0])


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        self.addCleanup(self.tmp.cleanup)
        isolate_home(self.home)
        self.settings_file = self.home / ".claude" / "settings.json"

    def configure(self, enabled):
        config = json.loads((REPO / "config.example.json").read_text())
        config["sandbox"] = {"enabled": enabled, "strict": False}
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def sync(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = harness.cmd_sync(harness.argparse.Namespace(
                dry_run=False, adopt=False, adopt_codex=False, print_only=False))
        return code, out.getvalue()

    def settings(self):
        return json.loads(self.settings_file.read_text())

    def test_opt_in_then_out_writes_and_removes_only_the_sandbox(self):
        self.configure(False)
        self.assertEqual(self.sync()[0], 0)
        self.assertNotIn("sandbox", self.settings())
        self.configure(True)
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertIs(self.settings()["sandbox"]["enabled"], True)
        self.assertNotIn("no longer names claude-code", out)
        self.configure(False)
        self.assertEqual(self.sync()[0], 0)
        self.assertNotIn("sandbox", self.settings())

    def test_config_set_accepts_the_switches_as_booleans(self):
        self.assertIs(harness.coerce_config_value("sandbox.enabled", "true"), True)
        self.assertIs(harness.coerce_config_value("sandbox.strict", "false"), False)
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("sandbox.enabled", "on")


if __name__ == "__main__":
    unittest.main()
