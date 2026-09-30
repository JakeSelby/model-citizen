# SPDX-License-Identifier: MIT
"""The Codex plugin manifest stays in step with the Claude Code one and ships skills only.

Codex prefers `.codex-plugin/plugin.json` over `.claude-plugin/plugin.json` at a plugin root, so
this file decides what a Codex install loads. Field names and path rules follow OpenAI's plugin
packaging page, https://developers.openai.com/plugins/build/plugins, and the listing limits its
submission field reference, https://developers.openai.com/plugins/deploy/submission.
"""
import json
import unittest

from test_harness import REPO

CODEX = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text())
CLAUDE = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
SHARED = ("name", "version", "description", "author", "license", "homepage", "repository",
          "keywords")


class SharedMetadataTests(unittest.TestCase):
    def test_every_shared_field_matches_the_claude_manifest(self):
        for field in SHARED:
            with self.subTest(field=field):
                self.assertIn(field, CODEX)
                self.assertEqual(CODEX[field], CLAUDE[field])

    def test_both_manifests_carry_the_release_version(self):
        version = (REPO / "VERSION").read_text().strip()
        self.assertEqual(CODEX["version"], version)
        self.assertEqual(CLAUDE["version"], version)

    def test_the_interface_names_the_same_plugin_and_policy(self):
        interface = CODEX["interface"]
        self.assertEqual(interface["displayName"], CLAUDE["displayName"])
        self.assertEqual(interface["developerName"], CLAUDE["author"]["name"])
        self.assertEqual(interface["privacyPolicyURL"], CLAUDE["privacyPolicyUrl"])


class SkillsOnlyTests(unittest.TestCase):
    def test_the_manifest_declares_no_hooks(self):
        # OpenAI's directory refuses plugins with lifecycle hooks, and a synced home already
        # registers them; see docs/runtime-installation.md.
        self.assertNotIn("hooks", CODEX)
        openai = CODEX.get("extensions", {}).get("com.openai", {})
        self.assertNotIn("hooks", openai)

    def test_no_default_hook_file_sits_at_the_plugin_root(self):
        # Codex loads hooks/hooks.json when the manifest names no hooks.
        self.assertFalse((REPO / "hooks" / "hooks.json").exists())

    def test_the_manifest_packages_the_skills_and_nothing_else(self):
        self.assertEqual(CODEX["skills"], CLAUDE["skills"])
        for component in ("agents", "commands", "mcpServers", "apps", "outputStyles"):
            self.assertNotIn(component, CODEX, msg=component)

    def test_the_skills_path_resolves_to_skill_folders_inside_the_plugin_root(self):
        path = CODEX["skills"]
        self.assertTrue(path.startswith("./"))
        self.assertNotIn("..", path.split("/"))
        skills = REPO / path
        self.assertTrue(skills.is_dir())
        self.assertTrue(list(skills.glob("*/SKILL.md")))

    def test_interface_assets_are_relative_paths_to_real_files(self):
        interface = CODEX["interface"]
        for field in ("composerIcon", "logo"):
            with self.subTest(field=field):
                self.assertTrue(interface[field].startswith("./"))
                self.assertTrue((REPO / interface[field]).is_file())

    def test_interface_links_are_https(self):
        interface = CODEX["interface"]
        for field in ("websiteURL", "supportURL", "privacyPolicyURL"):
            with self.subTest(field=field):
                self.assertTrue(interface[field].startswith("https://"))


class ListingLimitTests(unittest.TestCase):
    def test_the_codex_format_carries_its_required_fields(self):
        self.assertNotIn("$schema", CODEX)
        self.assertTrue(CODEX["author"]["name"])
        interface = CODEX["interface"]
        for field in ("displayName", "shortDescription", "longDescription", "developerName",
                      "category", "capabilities", "composerIcon", "logo"):
            with self.subTest(field=field):
                self.assertIn(field, interface)

    def test_listing_text_fits_the_submission_limits(self):
        interface = CODEX["interface"]
        self.assertLessEqual(len(CODEX["name"]), 64)
        self.assertRegex(CODEX["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(interface["displayName"]), 30)
        self.assertLessEqual(len(interface["shortDescription"]), 30)
        self.assertLessEqual(len(interface["longDescription"]), 4000)
        self.assertLessEqual(len(interface["developerName"]), 80)
        self.assertLessEqual(len(interface["capabilities"]), 20)

    def test_default_prompts_are_few_short_and_distinct(self):
        prompts = CODEX["interface"]["defaultPrompt"]
        self.assertLessEqual(len(prompts), 3)
        self.assertEqual(len(prompts), len(set(prompts)))
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertLessEqual(len(prompt), 128)

    def test_the_brand_colour_is_a_hex_triplet(self):
        self.assertRegex(CODEX["interface"]["brandColor"], r"^#[0-9A-Fa-f]{6}$")


if __name__ == "__main__":
    unittest.main()
