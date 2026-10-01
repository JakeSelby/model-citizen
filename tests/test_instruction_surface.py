# SPDX-License-Identifier: MIT
"""The loaded instruction surface (#514): every source a session loads, harness-owned or not, with
a soft token estimate or `unmeasured`. MCP servers, hooks and plugins are unmeasured, never 0; a
harness-linked file is counted once, by the attribution; an unreadable file says why.

Run: python3 -m unittest discover -s tests -p 'test_instruction_surface.py'
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import instruction_surface as surface  # noqa: E402

ATTRIBUTION = {"estimand": "soft estimate", "method": "chars/4", "modules": {"rules/secrets": 222,
                                                                          "stances/voice": 300}}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Fixture:
    """A home, a harness checkout and a project, shaped as Claude Code reads them."""

    def __init__(self, tmp):
        tmp = Path(tmp)
        self.home, self.harness, self.project = tmp / "home", tmp / "harness", tmp / "home" / "work" / "proj"
        claude = self.home / ".claude"
        write(self.harness / "claude" / "rules" / "secrets.md", "s" * 400)
        write(self.harness / "claude" / "CLAUDE.md", "h" * 80 + "\n@~/.claude/CLAUDE.personal.md\n")
        (claude / "rules").mkdir(parents=True)
        os.symlink(str(self.harness / "claude" / "CLAUDE.md"), str(claude / "CLAUDE.md"))
        os.symlink(str(self.harness / "claude" / "rules" / "secrets.md"), str(claude / "rules" / "secrets.md"))
        write(claude / "CLAUDE.personal.md", "p" * 40)
        write(claude / "rules" / "mine.md", "m" * 100)
        write(claude / "skills" / "notes" / "SKILL.md", "---\nname: notes\ndescription: keep notes\n---\nbody " * 30)
        write(claude / "settings.json", json.dumps({
            "enabledPlugins": {"helper@market": True, "dormant@market": False},
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}}))
        write(self.home / ".claude.json", json.dumps({"mcpServers": {"github": {"command": "gh"}}}))
        (self.project / ".git").mkdir(parents=True)
        write(self.project / "CLAUDE.md", "c" * 120 + "\nSee `@not-an-import` and @docs/guide.md\n")
        write(self.project / "docs" / "guide.md", "g" * 60)
        write(self.project / "CLAUDE.local.md", "l" * 20)
        write(self.project / ".claude" / "rules" / "api.md", "---\npaths:\n  - \"src/**\"\n---\n" + "a" * 40)
        write(self.project / ".mcp.json", json.dumps({"mcpServers": {"tracker": {}}}))

    def sources(self, attribution=ATTRIBUTION, **kwargs):
        return surface.sources(str(self.home), str(self.project), str(self.harness), attribution, **kwargs)


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.found = self.fixture.sources()
        self.by_id = {s.id: s for s in self.found}

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_owner_is_represented(self):
        self.assertEqual({s.owner for s in self.found}, set(surface.OWNERS) - set())

    def test_harness_modules_come_from_the_attribution_and_links_are_not_counted_twice(self):
        self.assertEqual(self.by_id["rules/secrets"].tokens, 222)
        self.assertEqual(self.by_id["rules/secrets"].owner, "harness")
        self.assertNotIn("~/.claude/rules/secrets.md", self.by_id)
        self.assertEqual(self.by_id["~/.claude/CLAUDE.md"].owner, "harness")

    def test_own_files_imports_and_listings_are_estimated(self):
        personal = self.by_id["import:~/.claude/CLAUDE.personal.md"]
        self.assertEqual((personal.owner, personal.tokens, personal.estimand), ("own", 10, "soft estimate"))
        self.assertEqual(self.by_id["~/.claude/rules/mine.md"].tokens, 25)
        skill = self.by_id["~/.claude/skills/notes/SKILL.md"]
        self.assertEqual((skill.kind, skill.tokens), ("skill listing", 4))  # "notes: keep notes"

    def test_project_instructions_rules_and_imports_are_listed(self):
        project = {s.kind for s in self.found if s.owner == "project"}
        self.assertTrue({"project instructions", "local instructions", "import",
                         "rule (loads when a matching file is read)"} <= project, project)
        self.assertFalse([s for s in self.found if "not-an-import" in s.id])

    def test_mcp_hooks_and_plugins_are_unmeasured_never_zero(self):
        for owner in ("mcp", "hooks", "plugin", "runtime"):
            mine = [s for s in self.found if s.owner == owner]
            self.assertTrue(mine, owner)
            for source in mine:
                self.assertIsNone(source.tokens, source)
                self.assertEqual(source.estimand, "unmeasured")
                self.assertTrue(source.reason)
        names = sorted(s.id for s in self.found if s.owner == "mcp")
        self.assertEqual(names, ["github (user scope)", "tracker (project scope)"])
        self.assertEqual([s.id for s in self.found if s.owner == "plugin"], ["helper@market"])

    def test_an_unreadable_file_is_unmeasured_with_its_reason(self):
        path = self.fixture.home / ".claude" / "rules" / "mine.md"
        os.chmod(str(path), 0)
        try:
            if os.access(str(path), os.R_OK):
                self.skipTest("running as a user who can read any file")
            source = {s.id: s for s in self.fixture.sources()}["~/.claude/rules/mine.md"]
        finally:
            os.chmod(str(path), 0o644)
        self.assertIsNone(source.tokens)
        self.assertIn("unreadable", source.reason)

    def test_no_source_reads_zero_unless_its_text_is_empty(self):
        write(self.fixture.home / ".claude" / "rules" / "empty.md", "")
        for source in self.fixture.sources():
            if source.tokens == 0:
                self.assertEqual(source.id, "~/.claude/rules/empty.md")

    def test_an_unresolved_selection_reads_unmeasured(self):
        found = self.fixture.sources(attribution=None)
        modules = [s for s in found if s.id == "harness modules"]
        self.assertEqual(modules[0].tokens, None)

    def test_imports_stop_after_four_hops(self):
        for depth in range(6):
            write(self.fixture.project / ("hop%d.md" % depth), "x\n@hop%d.md\n" % (depth + 1))
        write(self.fixture.project / "CLAUDE.md", "@hop0.md\n")
        ids = [s.id for s in self.fixture.sources() if "hop" in s.id]
        self.assertEqual(len(ids), surface.IMPORT_DEPTH)

    def test_agents_md_is_read_only_when_no_claude_md_is_on_the_path(self):
        write(self.fixture.project / "AGENTS.md", "z" * 8)
        self.assertFalse([s for s in self.fixture.sources() if s.id.endswith("AGENTS.md")])
        os.remove(str(self.fixture.project / "CLAUDE.md"))
        os.remove(str(self.fixture.project / "CLAUDE.local.md"))
        self.assertTrue([s for s in self.fixture.sources() if s.id.endswith("AGENTS.md")])

    def test_files_the_sync_recorded_are_the_harness_s_even_from_another_checkout(self):
        mine = str(self.fixture.home / ".claude" / "rules" / "mine.md")
        found = self.fixture.sources(sync_manifest={"repo": "/elsewhere", "links": [{"path": mine}]})
        self.assertNotIn("~/.claude/rules/mine.md", {s.id for s in found})

    def test_the_report_totals_each_owner_and_joins_coverage(self):
        lines = surface.render(self.found, {"rules/secrets": "measured"}, home=str(self.fixture.home))
        total = surface.summary(self.found)
        self.assertEqual(lines[0], "surface: {:,} est. tokens (soft estimate, chars/4); {} source(s) unmeasured"
                         .format(total["total_tokens"], total["unmeasured"]))
        self.assertIn("harness: 550 est. tokens, 3 source(s), 0 unmeasured", lines)
        secrets = [line for line in lines if line.strip().startswith("rules/secrets")][0]
        self.assertIn("measured", secrets)
        self.assertTrue(any(line.startswith("  github (user scope)") and "unmeasured: tool definitions" in line
                            for line in lines))
        document = surface.as_json(self.found, {"rules/secrets": "measured"})
        self.assertEqual(document["owners"]["mcp"]["unmeasured"], 2)
        self.assertEqual([s["coverage"] for s in document["sources"] if s["id"] == "rules/secrets"], ["measured"])


if __name__ == "__main__":
    unittest.main()
