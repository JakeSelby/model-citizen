# SPDX-License-Identifier: MIT
"""Unit tests for the builder agent and the build command. Run: python3 -m unittest discover tests"""
import importlib.machinery
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

AGENTS = REPO / "claude" / "agents"
BUILDER = AGENTS / "builder.md"
BUILD_COMMAND = REPO / "claude" / "commands" / "build.md"
HOW_IT_WORKS = REPO / "docs" / "how-it-works.md"
README = REPO / "README.md"
REQUIRED_KEYS = {"name", "description", "model", "tools", "effort"}
MODELS = {"opus", "sonnet", "haiku", "fable", "inherit"}
BODY_CAP = 60


def frontmatter(path):
    """The leading --- block as a flat mapping; values stay strings."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"{path.name} has no frontmatter block")
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, lines[end + 1:]


def agent_names():
    """Every agent the tree ships, so a doc listing them cannot go stale."""
    return sorted(p.stem for p in AGENTS.glob("*.md"))


def agents_bullet():
    """The `- **Agents**` bullet of the how-it-works doc, up to the next top-level bullet."""
    text = HOW_IT_WORKS.read_text(encoding="utf-8")
    match = re.search(r"^- \*\*Agents\*\*.*?(?=^- \*\*|\Z)", text, re.M | re.S)
    if not match:
        raise AssertionError("docs/how-it-works.md has no Agents bullet")
    return match.group(0)


class BuilderFrontmatterTests(unittest.TestCase):
    def test_it_declares_the_five_frontmatter_keys(self):
        fields, _ = frontmatter(BUILDER)
        self.assertEqual(REQUIRED_KEYS - set(fields), set())
        self.assertEqual(fields["name"], "builder")
        self.assertTrue(fields["description"])

    def test_model_and_effort_are_values_the_tool_accepts(self):
        fields, _ = frontmatter(BUILDER)
        self.assertIn(fields["model"], MODELS)
        self.assertIn(fields["effort"], {"low", "medium", "high", "xhigh", "max"})

    def test_it_can_write_and_cannot_re_delegate(self):
        fields, _ = frontmatter(BUILDER)
        tools = {t.strip() for t in fields["tools"].split(",")}
        self.assertLessEqual({"Edit", "Write", "Bash"}, tools)
        self.assertNotIn("Agent", tools)


class BuilderBodyTests(unittest.TestCase):
    def test_the_body_stays_short_enough_to_read(self):
        _, body = frontmatter(BUILDER)
        self.assertLessEqual(len(body), BODY_CAP)

    def test_the_body_carries_the_standing_brief(self):
        _, body = frontmatter(BUILDER)
        text = "\n".join(body).lower()
        for phrase in ("worktree", "gate", "closes #", "never push"):
            self.assertIn(phrase, text, msg=phrase)


class BuildCommandTests(unittest.TestCase):
    def test_the_command_spawns_the_builder_and_verifies_the_gate_itself(self):
        body = BUILD_COMMAND.read_text(encoding="utf-8")
        self.assertIn("`builder`", body)
        self.assertIn("gate yourself", body)


class AgentDocTests(unittest.TestCase):
    def test_readme_points_to_authoritative_catalog_instead_of_duplicating_inventory(self):
        self.assertIn("citizen catalog", README.read_text())
        self.assertIn("primitives/", README.read_text())



class BuilderSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_environ = dict(os.environ)
        isolate_home(self.home)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_environ)
        self.tmp.cleanup()

    def test_sync_links_the_builder_agent(self):
        code = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=True, adopt_codex=False, print_only=False))
        self.assertEqual(code, 0)
        link = self.home / ".claude" / "agents" / "builder.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), BUILDER.resolve())


if __name__ == "__main__":
    unittest.main()
