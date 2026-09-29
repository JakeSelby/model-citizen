# SPDX-License-Identifier: MIT
"""The two plugin manifests, the marketplace listing they describe, and its catalog surface.

A green run says a Claude Code user who adds this repository as a marketplace would be pointed
at real files. It is not native qualification: the marketplace surface is `unqualified` in
`compatibility/catalog.json` and stays there until an acceptance round records evidence.
"""
import json
import os
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from pathlib import Path

from test_harness import harness, REPO

PLUGIN = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
MARKETPLACE = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
# The slug an install recorded before the repository was renamed, assembled so the
# retired-name lint passes this file.
OLD_REPOSITORY = "JakeSelby/" + "agent-harness"
SURFACE = "claude-code-plugin-marketplace"


def entry():
    return next(row for row in MARKETPLACE["plugins"] if row["name"] == PLUGIN["name"])


class MarketplaceManifestTests(unittest.TestCase):
    def test_the_listing_carries_the_fields_claude_code_requires(self):
        self.assertTrue(MARKETPLACE["name"])
        self.assertTrue(MARKETPLACE["owner"]["name"])
        self.assertEqual(len(MARKETPLACE["plugins"]), 1)
        self.assertTrue(entry()["description"])

    def test_the_plugin_listing_links_to_public_documentation_and_support(self):
        self.assertEqual(PLUGIN["documentationUrl"], "https://model-citizen.dev/")
        self.assertEqual(PLUGIN["supportUrl"],
                         "https://github.com/JakeSelby/model-citizen/issues")

    def test_the_plugin_listing_links_to_its_privacy_policy(self):
        self.assertEqual(
            PLUGIN["privacyPolicyUrl"],
            "https://github.com/JakeSelby/model-citizen/blob/main/docs/privacy.md",
        )
        self.assertTrue((REPO / "docs" / "privacy.md").is_file())
        self.assertIn("[Privacy](docs/privacy.md)", (REPO / "README.md").read_text())

    def test_the_readme_discloses_directory_plugin_external_services(self):
        readme = (REPO / "README.md").read_text()
        self.assertIn("Data and external services in the Claude Directory plugin", readme)
        self.assertIn("repository content and metadata", readme)
        self.assertIn("WebFetch and WebSearch", readme)
        self.assertIn("native permissions", readme)

    def test_the_entry_resolves_to_the_plugin_manifest_rather_than_copying_it(self):
        source = (REPO / entry()["source"]).resolve()
        self.assertTrue((source / ".claude-plugin" / "plugin.json").is_file())
        for key in ("skills", "agents", "commands", "outputStyles"):
            self.assertNotIn(key, entry(), msg=key)

    def test_every_path_the_plugin_manifest_names_exists(self):
        targets = [PLUGIN["skills"], PLUGIN["commands"], PLUGIN["outputStyles"], *PLUGIN["agents"]]
        for target in targets:
            self.assertTrue((REPO / target).exists(), msg=target)
            self.assertFalse((REPO / target).is_symlink(), msg=target)

    def test_the_directory_icon_is_a_regular_square_svg(self):
        icon = REPO / ".claude-plugin" / "icon.svg"
        self.assertTrue(icon.is_file())
        self.assertFalse(icon.is_symlink())
        root = ET.parse(icon).getroot()
        self.assertEqual(root.attrib["width"], root.attrib["height"])
        self.assertGreaterEqual(int(root.attrib["width"]), 128)

    def test_complex_command_argument_hints_are_quoted(self):
        commands = list((REPO / "claude" / "commands").glob("*.md"))
        commands += list((REPO / "primitives" / "workflows").glob("*.md"))
        for command in commands:
            block = command.read_text(encoding="utf-8").split("---", 2)[1]
            hints = [line.split(":", 1)[1].strip() for line in block.splitlines()
                     if line.startswith("argument-hint:")]
            for hint in hints:
                if hint.count("[") > 1 or ": " in hint:
                    self.assertTrue(hint.startswith(('"', "'")), msg=command.name)
                    self.assertEqual(hint[-1], hint[0], msg=command.name)

    def test_the_plugin_ships_no_hooks_because_a_synced_home_already_registers_them(self):
        # Plugin hooks merge with user hooks rather than replacing them, so a machine running
        # both would fire every hook twice. See docs/runtime-installation.md.
        self.assertNotIn("hooks", PLUGIN)


class CatalogSurfaceTests(unittest.TestCase):
    def test_the_marketplace_install_is_its_own_unqualified_client_surface(self):
        data = json.loads((REPO / "compatibility" / "catalog.json").read_text())
        rows = [row for row in data["clients"] if row["id"] == SURFACE]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "unqualified")
        self.assertFalse(rows[0]["required_for_release"])
        self.assertEqual(rows[0]["evidence"], [])
        self.assertEqual(rows[0]["runtime"], "claude-code")

    def test_the_install_doc_names_what_the_marketplace_path_leaves_out(self):
        text = (REPO / "docs" / "runtime-installation.md").read_text()
        self.assertIn("## Install from the plugin marketplace", text)
        self.assertIn("/plugin marketplace add JakeSelby/model-citizen", text)
        self.assertIn("/plugin install %s@%s" % (PLUGIN["name"], MARKETPLACE["name"]), text)
        for missing in ("ownership journal", "Stance selection", "Codex projection", "hooks"):
            self.assertIn(missing.lower(), text.lower(), msg=missing)

    def test_the_install_doc_carries_the_migration_from_the_old_plugin_id(self):
        text = (REPO / "docs" / "runtime-installation.md").read_text()
        heading = "### Moving an `agent-harness` plugin install"
        self.assertIn(heading, text)
        section = text.split(heading, 1)[1]
        self.assertIn("/model-citizen:<name>", section)
        # Claude Code ignores a second add of a repository whose old marketplace is still
        # registered, so the old plugin and marketplace go before the new install.
        steps = ("/plugin uninstall agent-harness@agent-harness",
                 "/plugin marketplace remove agent-harness", "/plugin marketplace add ",
                 "/plugin install %s@%s" % (PLUGIN["name"], MARKETPLACE["name"]))
        for step in steps:
            self.assertIn(step, section, msg=step)
        positions = [section.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))


class DoctorInstallPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = unittest.mock.patch.dict(os.environ, {"HARNESS_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(self.tmp.name)

    def write(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_an_untouched_home_reports_neither_path_and_names_both_commands(self):
        line = harness._plugin_install_line()
        self.assertIn("neither", line)
        self.assertIn("bin/harness install", line)
        self.assertIn("/plugin install model-citizen@model-citizen", line)

    def test_an_ownership_manifest_reports_the_synced_home(self):
        self.write(".local/state/agent-harness/manifest.json", {})
        self.assertIn("synced home", harness._plugin_install_line())

    def test_an_enabled_plugin_reports_the_marketplace_install(self):
        self.write(".claude/settings.json", {"enabledPlugins": {"agent-harness@agent-harness": True}})
        line = harness._plugin_install_line()
        self.assertIn("marketplace plugin agent-harness@agent-harness", line)
        self.assertNotIn("synced home", line)

    def test_both_paths_are_reported_rather_than_one_hiding_the_other(self):
        self.write(".local/state/agent-harness/manifest.json", {})
        self.write(".claude/settings.json", {"enabledPlugins": {"agent-harness@agent-harness": True}})
        line = harness._plugin_install_line()
        self.assertIn("marketplace plugin", line)
        self.assertIn("synced home", line)

    def test_a_registered_marketplace_with_no_install_says_so(self):
        self.write(".claude/plugins/known_marketplaces.json",
                   {"agent-harness": {"source": {"source": "github", "repo": OLD_REPOSITORY}}})
        line = harness._plugin_install_line()
        self.assertIn("marketplace registered, plugin not installed", line)

    def test_another_projects_marketplace_is_not_mistaken_for_this_one(self):
        self.write(".claude/plugins/known_marketplaces.json",
                   {"official": {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"}}})
        self.assertNotIn("marketplace registered", harness._plugin_install_line())

    def test_a_disabled_plugin_is_not_counted_as_installed(self):
        self.write(".claude/settings.json", {"enabledPlugins": {"agent-harness@agent-harness": False}})
        self.assertIn("neither", harness._plugin_install_line())

    def test_the_cache_directory_alone_is_enough_to_report_the_marketplace_install(self):
        (self.home / ".claude" / "plugins" / "cache" / "agent-harness" / "agent-harness").mkdir(parents=True)
        self.assertIn("marketplace plugin agent-harness/agent-harness", harness._plugin_install_line())


class PluginRenameTests(unittest.TestCase):
    """Doctor recognizes the plugin under its old and new IDs, and warns when both load."""

    OLD = "agent-harness@agent-harness"
    NEW = "model-citizen@model-citizen"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = unittest.mock.patch.dict(os.environ, {"HARNESS_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.home = Path(self.tmp.name)

    def enable(self, *keys):
        path = self.home / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"enabledPlugins": {key: True for key in keys}}))

    def test_the_manifests_carry_the_new_name(self):
        self.assertEqual(PLUGIN["name"], "model-citizen")
        self.assertEqual(PLUGIN["displayName"], "Model Citizen")
        self.assertEqual(MARKETPLACE["name"], "model-citizen")
        self.assertIn("Model Citizen", PLUGIN["description"])
        self.assertIn("Model Citizen", MARKETPLACE["metadata"]["description"])
        self.assertIn("Model Citizen", entry()["description"])

    def test_the_old_id_alone_is_reported_as_installed(self):
        self.enable(self.OLD)
        line = harness._plugin_install_line()
        self.assertIn("marketplace plugin " + self.OLD, line)
        self.assertNotIn("warning", line)

    def test_the_new_id_alone_is_reported_as_installed(self):
        self.enable(self.NEW)
        line = harness._plugin_install_line()
        self.assertIn("marketplace plugin " + self.NEW, line)
        self.assertNotIn("warning", line)

    def test_both_ids_enabled_warn_that_the_skills_load_twice(self):
        self.enable(self.OLD, self.NEW)
        line = harness._plugin_install_line()
        self.assertIn("marketplace plugin " + self.NEW, line)
        self.assertIn("loads twice", line)
        self.assertIn("/plugin uninstall " + self.OLD, line)
        self.assertNotIn("/plugin uninstall " + self.NEW, line)

    def test_either_marketplace_name_in_the_cache_is_recognized(self):
        for market, plugin in (("agent-harness", "agent-harness"), ("model-citizen", "model-citizen")):
            (self.home / ".claude" / "plugins" / "cache" / market / plugin).mkdir(parents=True)
        state = harness.plugin_install_state()
        self.assertEqual(state["cached"], ["agent-harness/agent-harness", "model-citizen/model-citizen"])

    def test_a_marketplace_registered_from_either_repository_name_is_recognized(self):
        path = self.home / ".claude" / "plugins" / "known_marketplaces.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "agent-harness": {"source": {"source": "github", "repo": OLD_REPOSITORY}},
            "model-citizen": {"source": {"source": "github", "repo": "JakeSelby/model-citizen"}}}))
        self.assertEqual(harness.plugin_install_state()["registered"], ["agent-harness", "model-citizen"])


if __name__ == "__main__":
    unittest.main()
