# SPDX-License-Identifier: MIT
"""Studio module-library inventory, provenance, projections, and performance."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import module_library, server  # noqa: E402


class ModuleLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.custom = Path(self.temporary.name) / "team-primitives"
        (self.custom / "rules").mkdir(parents=True)
        (self.custom / "rules" / "cache-hygiene.md").write_text(
            "# Team cache hygiene\n\nTeam-specific text.\n", encoding="utf-8")
        (self.custom / "rules" / "custom-rule.md").write_text(
            "# Custom rule\n\nVisible from the external root.\n", encoding="utf-8")
        (self.custom / "modes").mkdir()
        (self.custom / "modes" / "focus.json").write_text(json.dumps({
            "schema_version": 1, "description": "Focus mode", "rules": {"custom-rule": "off"},
        }), encoding="utf-8")
        (self.custom / "modes" / "wide.json").write_text(json.dumps({
            "schema_version": 1, "description": "Wide mode",
        }), encoding="utf-8")
        manifest = json.loads((ROOT / "primitives" / "manifests.json").read_text(encoding="utf-8"))
        custom_manifest = {"schema_version": 1, "rules": {
            name: copy.deepcopy(manifest["rules"]["cache-hygiene"])
            for name in ("cache-hygiene", "custom-rule")
        }}
        (self.custom / "manifests.json").write_text(
            json.dumps(custom_manifest), encoding="utf-8")
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = [str(self.custom)]
        config["mode"] = "focus"
        config["rules"] = {"custom-rule": "off"}
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def inventory(self):
        with mock.patch.dict(os.environ, {"HARNESS_HOME": str(self.home)}):
            return module_library.inventory(ROOT)

    def test_collisions_keep_both_modules_and_name_their_roots(self):
        modules = [item for item in self.inventory()["modules"]
                   if item["kind"] == "rules" and item["name"] == "cache-hygiene"]
        self.assertEqual(len(modules), 2)
        self.assertTrue(all(item["collision"] for item in modules))
        self.assertEqual({item["root"]["id"] for item in modules}, {"core", "root-1"})
        self.assertTrue(all(item["manifest"]["claims"] for item in modules))

    def test_off_module_names_the_layer_that_switched_it_off(self):
        item = next(item for item in self.inventory()["modules"]
                    if item["kind"] == "rules" and item["name"] == "custom-rule")
        self.assertEqual(item["state"], {"value": "off", "layer": "user", "switchable": True})

    def test_modes_show_the_active_choice_and_selection_provenance(self):
        modes = {item["name"]: item["state"] for item in self.inventory()["modules"]
                 if item["kind"] == "modes"}
        self.assertEqual(modes["focus"],
                         {"value": "on", "layer": "user", "switchable": True})
        self.assertEqual(modes["wide"],
                         {"value": "off", "layer": "not selected", "switchable": True})

    def test_nonselectable_presentation_is_marked_informational(self):
        item = next(item for item in self.inventory()["modules"]
                    if item["kind"] == "presentation")
        self.assertEqual(item["state"],
                         {"value": "on", "layer": "not switchable", "switchable": False})

    def test_rule_shows_both_runtime_projections_source_render_and_static_cost(self):
        item = next(item for item in self.inventory()["modules"]
                    if item["key"] == "core:rules:cache-hygiene")
        self.assertEqual({entry["runtime"] for entry in item["projections"]},
                         {"claude-code", "codex"})
        self.assertIn("cache-hygiene.md", item["source"]["path"])
        self.assertEqual(item["source"]["text"], item["rendered"]["text"])
        self.assertGreater(item["context_cost"]["tokens"], 0)
        self.assertEqual(item["context_cost"]["estimate"], "soft estimate")

    def test_presentation_modules_name_both_runtime_projections(self):
        item = next(item for item in self.inventory()["modules"]
                    if item["kind"] == "presentation")
        self.assertEqual({entry["runtime"] for entry in item["projections"]},
                         {"claude-code", "codex"})
        self.assertIn("output-styles", next(entry["path"] for entry in item["projections"]
                                            if entry["runtime"] == "claude-code"))
        self.assertIn("AGENTS.md", next(entry["path"] for entry in item["projections"]
                                        if entry["runtime"] == "codex"))

    def test_filtering_500_modules_stays_below_100_ms(self):
        template = next(item for item in self.inventory()["modules"] if item["kind"] == "rules")
        modules = []
        for index in range(500):
            item = copy.deepcopy(template)
            item["name"] = "rule-%03d" % index
            item["root"]["id"] = "root-%d" % (index % 5)
            item["state"]["value"] = "off" if index % 3 == 0 else "on"
            item["context_cost"]["tokens"] = index * 5
            modules.append(item)
        started = time.perf_counter()
        result = module_library.filter_modules(
            modules, query="rule-4", kind="rules", root="root-4", state="on", cost="high")
        elapsed = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed, 100)
        self.assertTrue(result)
        self.assertTrue(all(item["kind"] == "rules" and item["root"]["id"] == "root-4"
                            and item["state"]["value"] == "on"
                            and item["context_cost"]["tokens"] >= 1000 for item in result))

    def test_library_route_is_registered_and_emits_the_versioned_inventory(self):
        route = server.ROUTES.resolve("GET", "/api/library")
        self.assertIsNotNone(route)
        self.assertEqual(route.cli_command, ("citizen", "catalog", "--library", "--json"))
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        payload = {"schema_version": 1, "modules": [],
                   "summary": {"modules": 0, "collisions": 0, "roots": 1, "generated_ms": 0}}
        with mock.patch.object(module_library, "inventory", return_value=payload):
            server._library(handler, route)
        self.assertEqual(payload["repository"], str(ROOT.resolve()))
        handler._json.assert_called_once_with(200, payload)


if __name__ == "__main__":
    unittest.main()
