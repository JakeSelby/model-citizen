# SPDX-License-Identifier: MIT
"""The minimal regular-file distribution submitted to the Claude Directory."""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO


SPEC = importlib.util.spec_from_file_location(
    "sync_directory_plugin", REPO / "scripts" / "sync_directory_plugin.py")
DIRECTORY_PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIRECTORY_PLUGIN)


class DirectoryPluginTests(unittest.TestCase):
    def test_committed_bundle_is_current(self):
        self.assertEqual(DIRECTORY_PLUGIN.bundle_errors(REPO), [])

    def test_bundle_is_small_and_contains_only_regular_files(self):
        bundle = REPO / DIRECTORY_PLUGIN.BUNDLE_RELATIVE
        files = [path for path in bundle.rglob("*") if path.is_file()]
        self.assertLessEqual(len(files), DIRECTORY_PLUGIN.MAX_DIRECTORY_FILES)
        self.assertFalse(any(path.is_symlink() for path in bundle.rglob("*")))

    def test_bundle_contains_the_declared_plugin_surface(self):
        bundle = REPO / DIRECTORY_PLUGIN.BUNDLE_RELATIVE
        manifest = json.loads(
            (bundle / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(len(list((bundle / "primitives" / "skills").glob("*/SKILL.md"))), 15)
        self.assertEqual(len(manifest["agents"]), 11)
        self.assertEqual(len(list((bundle / "claude" / "commands").glob("*.md"))), 7)
        self.assertTrue((bundle / "primitives" / "presentation" / "scannable.md").is_file())

    def test_source_list_is_file_level_and_excludes_unlisted_files(self):
        listed = DIRECTORY_PLUGIN._source_files(REPO)
        self.assertEqual(len(listed), 47)
        self.assertNotIn(Path("primitives/skills/not-submitted/SKILL.md"), listed)

    def test_source_list_rejects_symlinked_ancestors(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            source_list = root / DIRECTORY_PLUGIN.SOURCE_LIST
            source_list.parent.mkdir(parents=True)
            source_list.write_text("linked/payload.md\n")
            payload = Path(outside) / "payload.md"
            payload.write_text("outside\n")
            (root / "linked").symlink_to(Path(outside), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "crosses a symlink"):
                DIRECTORY_PLUGIN._source_files(root)

    def test_source_list_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_list = root / DIRECTORY_PLUGIN.SOURCE_LIST
            source_list.parent.mkdir(parents=True)
            source_list.write_text("../outside.md\n")
            with self.assertRaisesRegex(ValueError, "normalized relative path"):
                DIRECTORY_PLUGIN._source_files(root)

    def test_manifest_target_types_are_checked(self):
        bundle = REPO / DIRECTORY_PLUGIN.BUNDLE_RELATIVE
        manifest = json.loads(
            (bundle / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        manifest["agents"][0] = "./claude/agents/"
        errors = []
        for component, target in DIRECTORY_PLUGIN._manifest_targets(manifest):
            resolved = (bundle / target).resolve()
            type_error = DIRECTORY_PLUGIN._target_type_error(component, resolved)
            if type_error:
                errors.append((component, target, type_error))
        self.assertIn(("agents", "./claude/agents/", "must be a .md file"), errors)

    def test_bundle_readme_discloses_external_services(self):
        readme = (REPO / DIRECTORY_PLUGIN.BUNDLE_RELATIVE / "README.md").read_text()
        for disclosure in ("repository content and metadata", "WebFetch and WebSearch",
                           "native permissions", "does not receive or retain"):
            self.assertIn(disclosure, readme)

    def test_bundle_markdown_has_no_broken_relative_links(self):
        bundle = REPO / DIRECTORY_PLUGIN.BUNDLE_RELATIVE
        for document in (bundle / "README.md", bundle / "docs" / "privacy.md"):
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
                if "://" in target:
                    continue
                relative = target.split("#", 1)[0]
                self.assertTrue((document.parent / relative).resolve().exists(),
                                msg="%s -> %s" % (document, target))

    def test_check_detects_stale_and_extra_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, data in DIRECTORY_PLUGIN.desired_files(REPO).items():
                path = root / DIRECTORY_PLUGIN.BUNDLE_RELATIVE / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            source_list = root / DIRECTORY_PLUGIN.SOURCE_LIST
            source_list.parent.mkdir(parents=True, exist_ok=True)
            source_list.write_text((REPO / DIRECTORY_PLUGIN.SOURCE_LIST).read_text())
            for relative, source in DIRECTORY_PLUGIN._source_files(REPO).items():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            (root / "VERSION").write_text((REPO / "VERSION").read_text())
            readme = root / DIRECTORY_PLUGIN.BUNDLE_RELATIVE / "README.md"
            readme.write_text("stale\n")
            extra = root / DIRECTORY_PLUGIN.BUNDLE_RELATIVE / "history.txt"
            extra.write_text("not part of the plugin\n")
            errors = DIRECTORY_PLUGIN.bundle_errors(root)
            self.assertIn("bundle file is stale: README.md", errors)
            self.assertIn("bundle has an unexpected file: history.txt", errors)
