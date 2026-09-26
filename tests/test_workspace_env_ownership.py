# SPDX-License-Identifier: MIT
"""Workspace support owns one Claude env variable, by the native telemetry rules.

`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` makes Claude Code load an `--add-dir` folder's
`CLAUDE.md` and rules. `sync` writes it while `workspaces_dir` is set: a value equal to its own is
taken over silently, a different one is left and reported, and on unset the key goes back to
what it held before the harness first wrote it, only when the journal holds it. Every sync runs
under a temporary HOME. Run: python3 -m unittest discover -s tests -p test_workspace_env_ownership.py
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
VAR = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"
PATH = ["env", VAR]
OFF = {"export": "none", "native": False}


class Ownership(unittest.TestCase):
    """`apply_native_claude` on the workspace block alone: telemetry is off throughout."""

    def apply(self, live, held=None, directory="/work/spaces"):
        cfg = {"stances": {}}
        if directory:
            cfg["workspaces_dir"] = directory
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(merged, live, held or {}, OFF, cfg, OWNERSHIP)
        return merged, paths, notices

    def test_the_manifest_owns_the_variable_and_widens_never_touch(self):
        self.assertEqual(OWNERSHIP["claude"]["workspaces"]["env_keys"], [VAR])
        self.assertIn("env except native_telemetry.env_keys and workspaces.env_keys",
                      OWNERSHIP["claude"]["never_touch"])

    def test_set_writes_the_variable_beside_the_users_own(self):
        merged, paths, notices = self.apply({"env": {"MY_VAR": "keep"}})
        self.assertEqual(merged["env"], {"MY_VAR": "keep", VAR: "1"})
        self.assertEqual((paths, notices), ([PATH], []))

    def test_an_identical_hand_set_value_is_adopted_silently(self):
        merged, paths, notices = self.apply({"env": {VAR: "1"}})
        self.assertEqual(merged["env"], {VAR: "1"})
        self.assertEqual((paths, notices), ([PATH], []))

    def test_a_different_hand_set_value_is_left_and_reported(self):
        merged, paths, notices = self.apply({"env": {VAR: "0"}})
        self.assertEqual(merged["env"], {VAR: "0"})
        self.assertEqual(paths, [])
        self.assertEqual(len(notices), 1)
        self.assertIn("workspace support left it alone", notices[0])

    def test_unset_while_held_takes_the_variable_back_out(self):
        held = {json.dumps(PATH): {"prior": {"present": False, "value": None},
                                   "applied": {"present": True, "value": "1"}}}
        merged, paths, notices = self.apply({"env": {VAR: "1", "MY_VAR": "keep"}}, held, None)
        self.assertEqual(merged["env"], {"MY_VAR": "keep"})
        self.assertEqual((paths, notices), ([PATH], []))

    def test_unset_while_not_held_leaves_a_hand_set_value(self):
        merged, paths, notices = self.apply({"env": {VAR: "1"}}, None, None)
        self.assertEqual(merged["env"], {VAR: "1"})
        self.assertEqual((paths, notices), ([], []))


class Sync(unittest.TestCase):
    """The round trip through `sync`, with the journal deciding what unset takes back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        self.addCleanup(self.tmp.cleanup)
        isolate_home(self.home)
        self.settings_file = self.home / ".claude" / "settings.json"

    def configure(self, directory):
        config = json.loads((REPO / "config.example.json").read_text())
        config.pop("workspaces_dir", None)
        if directory:
            config["workspaces_dir"] = directory
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def sync(self):
        return harness.cmd_sync(harness.argparse.Namespace(
            dry_run=False, adopt=False, adopt_codex=False, print_only=False))

    def env(self):
        return json.loads(self.settings_file.read_text()).get("env") or {}

    def test_set_then_unset_writes_and_removes_only_the_variable(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {"MY_VAR": "keep"}}))
        self.configure(str(self.home / "spaces"))
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {"MY_VAR": "keep", VAR: "1"})
        self.configure(None)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {"MY_VAR": "keep"})

    def test_an_adopted_hand_set_value_is_restored_on_unset(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {VAR: "1"}}))
        self.configure(str(self.home / "spaces"))
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {VAR: "1"})
        self.configure(None)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env(), {VAR: "1"})

    def test_a_different_hand_set_value_stops_the_write_and_is_reported(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {VAR: "0"}}))
        self.configure(str(self.home / "spaces"))
        self.assertEqual(self.sync(), 2)
        self.assertEqual(self.env(), {VAR: "0"})


    def test_the_telemetry_removal_line_stays_quiet_when_only_the_workspace_key_is_owned(self):
        self.configure(str(self.home / "spaces"))
        for _ in range(2):  # the second sync holds the key, so it is an owned native path
            out = io.StringIO()
            os.environ.pop("HARNESS_QUIET", None)
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.sync(), 0)
            self.assertNotIn("no longer names claude-code", out.getvalue())
            self.assertIn("sync complete", out.getvalue())
        self.assertEqual(self.env(), {VAR: "1"})


if __name__ == "__main__":
    unittest.main()
