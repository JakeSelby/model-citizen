# SPDX-License-Identifier: MIT
"""Draft selection editing parity, refusal and transport tests."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import drafts, selection_editing, server  # noqa: E402

import draft_support  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "harness_studio_selection_editing_test", str(ROOT / "bin" / "harness"),
)
spec = importlib.util.spec_from_loader("harness_studio_selection_editing_test", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)


class SelectionEditingTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.draft = {"draft": {"name": "test", "revision": "a" * 40},
                      "config": self.config}

    def manual_cli(self, changes):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / ".config" / "agent-harness" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("HARNESS_")}
            env.update({"HARNESS_HOME": str(home), "HARNESS_QUIET": "1"})
            for key, value in changes:
                run = subprocess.run(
                    [sys.executable, str(ROOT / "bin" / "harness"), "config", "set",
                     key, str(value).lower() if isinstance(value, bool) else value],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
            return json.loads(path.read_text(encoding="utf-8"))

    def test_candidate_equals_matching_config_set_commands(self):
        changes = {"mode": "minimal", "stances.voice": "concise", "rules.cache-hygiene": "off"}
        expected = self.manual_cli(list(changes.items()))
        self.assertEqual(selection_editing.candidate(ROOT, self.config, changes), expected)

    def test_saved_checkpoint_equals_sequential_real_config_set_output(self):
        changes = [("mode", "minimal"), ("stances.voice", "concise"),
                   ("rules.cache-hygiene", "off")]
        expected = self.manual_cli(changes)
        expected_bytes = (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode()
        name = "selection-parity-" + uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({
                "HARNESS_HOME": str(home),
                "HARNESS_WORKTREE_ROOT": str(Path(temporary) / "worktrees"),
            })
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name,
                 "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                revision = json.loads(created.stdout)["revision"]
                with mock.patch.dict(os.environ, environment, clear=True):
                    result = selection_editing.save(
                        ROOT, name, revision, "parity-save", dict(changes),
                    )
                    replayed = selection_editing.save(
                        ROOT, name, revision, "parity-save", dict(changes),
                    )
                self.assertTrue(result["saved"], result["error"])
                self.assertTrue(replayed["saved"], replayed["error"])
                self.assertTrue(replayed["result"]["replayed"])
                self.assertEqual(replayed["result"]["revision"], result["result"]["revision"])
                worktree, _ = drafts.find(ROOT, name)
                persisted = drafts._paths(worktree)["config"].read_bytes()
                self.assertEqual(persisted, expected_bytes)
                self.assertEqual(json.loads(persisted), expected)
                with mock.patch.dict(os.environ, environment, clear=True):
                    later = selection_editing.save(
                        ROOT, name, result["result"]["revision"], "later-save",
                        {"rules.conciseness": "off"},
                    )
                    delayed_retry = selection_editing.save(
                        ROOT, name, revision, "parity-save", dict(changes),
                    )
                self.assertTrue(later["saved"], later["error"])
                self.assertTrue(delayed_retry["saved"], delayed_retry["error"])
                self.assertTrue(delayed_retry["result"]["replayed"])
                self.assertEqual(delayed_retry["result"]["revision"],
                                 result["result"]["revision"])
                self.assertEqual(delayed_retry["base_revision"], later["result"]["revision"])
                reloaded = drafts.read_config(ROOT, name)
                self.assertEqual(reloaded["draft"]["revision"], later["result"]["revision"])
                self.assertEqual(reloaded["config"]["rules"]["conciseness"], "off")
            finally:
                draft_support.discard_draft(self, name, environment)

    def test_dependency_refusal_is_the_cli_refusal_and_checkpoints_nothing(self):
        config = json.loads(json.dumps(self.config))
        config.setdefault("roles", {})["designer"] = "off"
        config.setdefault("roles", {})["design-judge"] = "off"
        config.setdefault("skills", {})["design-loop"] = "off"
        draft = {"draft": self.draft["draft"], "config": config}
        with mock.patch.object(selection_editing.drafts, "read_config", return_value=draft), \
                mock.patch.object(selection_editing.drafts, "replay_config_request",
                                  return_value=None), \
                mock.patch.object(selection_editing.drafts, "checkpoint_config") as checkpoint:
            result = selection_editing.save(
                ROOT, "test", "a" * 40, "save-1", {"skills.design-loop": "on"},
            )
        self.assertFalse(result["saved"])
        self.assertIn("depends on roles/designer", result["error"])
        checkpoint.assert_not_called()

    def test_multi_field_dependency_repairs_are_applied_before_dependents(self):
        config = json.loads(json.dumps(self.config))
        config.setdefault("workflows", {})["build"] = "off"
        config.setdefault("roles", {})["builder"] = "off"
        changes = {"workflows.build": "on", "roles.builder": "on"}
        expected_config = json.loads(json.dumps(config))
        with mock.patch.object(self, "config", expected_config):
            expected = self.manual_cli([
                ("roles.builder", "on"),
                ("workflows.build", "on"),
            ])
        self.assertEqual(selection_editing.candidate(ROOT, config, changes), expected)

    def test_multi_field_conflict_swap_turns_old_unit_off_before_new_unit_on(self):
        with tempfile.TemporaryDirectory() as temporary:
            primitives = Path(temporary)
            rules = primitives / "rules"
            rules.mkdir()
            (rules / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
            (rules / "beta.md").write_text("# Beta\n", encoding="utf-8")
            entry = {
                "claims": ["test"], "surface": ["resident-context"],
                "instruments": [], "slot": None, "dependencies": [],
            }
            manifest = {
                "schema_version": 1,
                "rules": {
                    "alpha": dict(entry, conflicts=["rules/beta"]),
                    "beta": dict(entry, conflicts=["rules/alpha"]),
                },
            }
            (primitives / "manifests.json").write_text(
                json.dumps(manifest), encoding="utf-8",
            )
            config = json.loads(json.dumps(self.config))
            config["primitive_roots"] = [str(primitives)]
            config.setdefault("rules", {}).update({"alpha": "on", "beta": "off"})
            changes = {"rules.beta": "on", "rules.alpha": "off"}
            with mock.patch.object(self, "config", config):
                expected = self.manual_cli([
                    ("rules.alpha", "off"),
                    ("rules.beta", "on"),
                ])
            self.assertEqual(selection_editing.candidate(ROOT, config, changes), expected)

    def test_core_hook_requires_explicit_acknowledgement_in_the_same_candidate(self):
        with self.assertRaisesRegex(selection_editing.SelectionEditError,
                                    "core_switches_acknowledged"):
            selection_editing.candidate(ROOT, self.config, {"hooks.stop-gate": "off"})
        candidate = selection_editing.candidate(
            ROOT, self.config,
            {"hooks.stop-gate": "off", "core_switches_acknowledged": True},
        )
        self.assertEqual(candidate["hooks"]["stop-gate"], "off")
        self.assertIs(candidate["core_switches_acknowledged"], True)

    def test_optimistic_revision_conflict_is_returned_without_a_checkpoint(self):
        stale = drafts.DraftError("stale-revision", "draft changed; reload it before saving")
        with mock.patch.object(selection_editing.drafts, "read_config", return_value=self.draft), \
                mock.patch.object(selection_editing.drafts, "replay_config_request",
                                  return_value=None), \
                mock.patch.object(selection_editing.drafts, "checkpoint_config",
                                  side_effect=stale) as checkpoint:
            result = selection_editing.save(
                ROOT, "test", "0" * 40, "stale-save", {"mode": "minimal"},
            )
        self.assertFalse(result["saved"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], "stale-revision")
        self.assertIn("reload", result["error"])
        checkpoint.assert_called_once()

    def test_preview_has_before_after_budget_and_stance_operative_text(self):
        with mock.patch.object(selection_editing.drafts, "read_config", return_value=self.draft):
            result = selection_editing.preview(ROOT, "test", {"stances.voice": "concise"})
        self.assertTrue(result["valid"])
        self.assertFalse(result["unchanged"])
        self.assertEqual(result["before"]["selection"]["stances"]["voice"],
                         self.config["stances"]["voice"])
        self.assertEqual(result["after"]["selection"]["stances"]["voice"], "concise")
        self.assertIn("voice", result["before"]["stance_text"])
        self.assertIn("voice", result["after"]["stance_text"])
        self.assertIn("selected_lines", result["before"]["budget"])
        voice = next(control for control in result["controls"]["stances"]
                     if control["name"] == "voice")
        self.assertEqual(voice["value"], "concise")

    def test_preview_contains_before_and_after_for_every_switch_kind(self):
        cases = {
            "rules.cache-hygiene": ("rules", "cache-hygiene"),
            "hooks.allow-plan-webfetch": ("hooks", "allow-plan-webfetch"),
            "skills.architecture-viewer": ("skills", "architecture-viewer"),
            "workflows.close-out": ("workflows", "close-out"),
            "roles.log-compressor": ("roles", "log-compressor"),
        }
        with mock.patch.object(selection_editing.drafts, "read_config",
                               return_value=self.draft):
            for path, (kind, unit) in cases.items():
                with self.subTest(path=path):
                    result = selection_editing.preview(ROOT, "test", {path: "off"})
                    self.assertTrue(result["valid"], result["error"])
                    self.assertEqual(result["before"]["selection"][kind][unit], "on")
                    self.assertEqual(result["after"]["selection"][kind][unit], "off")

    def test_json_type_change_is_checkpointed_and_reloads_as_boolean(self):
        config = json.loads(json.dumps(self.config))
        config["core_switches_acknowledged"] = 1
        name = "selection-json-type-" + uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({
                "HARNESS_HOME": str(home),
                "HARNESS_WORKTREE_ROOT": str(Path(temporary) / "worktrees"),
            })
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name,
                 "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                revision = json.loads(created.stdout)["revision"]
                with mock.patch.dict(os.environ, environment, clear=True):
                    result = selection_editing.save(
                        ROOT, name, revision, "json-type-save",
                        {"core_switches_acknowledged": True},
                    )
                    reloaded = drafts.read_config(ROOT, name)
                self.assertTrue(result["saved"], result["error"])
                self.assertFalse(result["unchanged"])
                self.assertIs(reloaded["config"]["core_switches_acknowledged"], True)
                self.assertNotEqual(reloaded["draft"]["revision"], revision)
            finally:
                draft_support.discard_draft(self, name, environment)

    def test_noop_change_returns_unchanged_without_checkpointing(self):
        changes = {"stances.voice": self.config["stances"]["voice"]}
        with mock.patch.object(selection_editing.drafts, "read_config", return_value=self.draft), \
                mock.patch.object(selection_editing.drafts, "replay_config_request",
                                  return_value=None), \
                mock.patch.object(selection_editing.drafts, "replay_config_checkpoint",
                                  return_value=None), \
                mock.patch.object(selection_editing.drafts, "checkpoint_config") as checkpoint:
            preview = selection_editing.preview(ROOT, "test", changes)
            saved = selection_editing.save(ROOT, "test", "a" * 40, "noop-save", changes)
        self.assertTrue(preview["valid"])
        self.assertTrue(preview["unchanged"])
        self.assertEqual(preview["changed"], [])
        self.assertFalse(saved["saved"])
        self.assertTrue(saved["unchanged"])
        self.assertIsNone(saved["result"])
        checkpoint.assert_not_called()

    def test_read_catalog_contains_modes_variants_switches_and_revision(self):
        with mock.patch.object(selection_editing.drafts, "read_config", return_value=self.draft):
            result = selection_editing.read(ROOT, "test")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["draft"]["revision"], "a" * 40)
        self.assertIn("minimal", result["controls"]["modes"])
        voice = next(item for item in result["controls"]["stances"] if item["name"] == "voice")
        self.assertIn("concise", voice["options"])
        self.assertIn("hooks", [item["kind"] for item in result["controls"]["switches"]])

    def test_routes_are_private_cli_mapped_and_saves_use_the_mutation_lane(self):
        routes = {(route.method, route.path): route for route in server.ROUTES.entries}
        paths = (
            "/api/configure/selection/read",
            "/api/configure/selection/preview",
            "/api/configure/selection/save",
        )
        for path in paths:
            route = routes[("POST", path)]
            self.assertIsNone(route.parity_exemption)
            self.assertEqual(route.cli_command[:3], ("citizen", "draft", "selection"))
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        handler.request_json = {"draft": "test", "base_revision": "a" * 40,
                                "idempotency_key": "save-1", "changes": {"mode": "minimal"}}
        payload = {"valid": True, "error": "", "error_code": "", "changed": ["mode"],
                   "unchanged": False,
                   "base_revision": "a" * 40, "before": {}, "after": {},
                   "controls": {}, "applied": ["mode"], "saved": True,
                   "result": {"revision": "b" * 40}}
        route = routes[("POST", "/api/configure/selection/save")]
        with mock.patch.object(selection_editing, "save", return_value=payload):
            server._draft_selection_save(handler, route)
        handler.server.mutations.call.assert_called_once()
        handler._json.assert_called_once_with(200, payload)

    def test_cli_preview_and_http_share_the_same_core_payload(self):
        payload = {"valid": True, "error": "", "error_code": "", "changed": ["mode"],
                   "unchanged": False,
                   "base_revision": "a" * 40, "before": {}, "after": {},
                   "controls": {}, "applied": ["mode"]}
        changes = {"mode": "minimal"}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as source:
            json.dump(changes, source)
            source.flush()
            output = StringIO()
            with mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                    mock.patch.object(harness, "git_root", return_value=ROOT), \
                    mock.patch.object(harness.studio_selection_editing, "preview",
                                      return_value=payload) as preview, \
                    redirect_stdout(output):
                code = harness.main([
                    "draft", "selection", "preview", "test", "--changes", source.name, "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), payload)
        preview.assert_called_once_with(ROOT, "test", changes)


if __name__ == "__main__":
    unittest.main()
