# SPDX-License-Identifier: MIT
"""The workspace resolver: the JSONC reader, members, overrides and which workspace a cwd attaches.

Every workspace here is synthetic, built in a temporary directory. The module is loaded by file
path, as a policy hook will load it, which also proves it needs no other `harness_core` module.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "workspaces_by_path", str(REPO / "lib" / "harness_core" / "workspaces.py"))
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


class ReaderTests(unittest.TestCase):
    def test_line_and_block_comments_go(self):
        self.assertEqual(ws.read_jsonc('{ // note\n "a": /* inline */ 1 }'), {"a": 1})

    def test_comment_markers_inside_strings_stay(self):
        text = '{"url": "https://example.com//x", "glob": "a/*b*/c"}'
        self.assertEqual(ws.read_jsonc(text), {"url": "https://example.com//x", "glob": "a/*b*/c"})

    def test_an_escaped_quote_does_not_end_the_string(self):
        self.assertEqual(ws.read_jsonc(r'{"q": "say \"hi\" // not a comment"}'),
                         {"q": 'say "hi" // not a comment'})

    def test_trailing_commas_go_but_not_inside_strings(self):
        text = '{"a": [1, 2,\n], "b": ", ]", "c": {"d": 1,},\n}'
        self.assertEqual(ws.read_jsonc(text), {"a": [1, 2], "b": ", ]", "c": {"d": 1}})

    def test_a_comment_between_a_comma_and_a_bracket_still_drops_the_comma(self):
        self.assertEqual(ws.read_jsonc('[1, // last\n]'), [1])

    def test_broken_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            ws.read_jsonc('{"folders": [')


class Fixture(unittest.TestCase):
    """A workspaces folder and repositories beside it, all real directories."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.dir = self.root / "ws"
        self.dir.mkdir()
        self.env = {}

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, name):
        path = self.root / "repos" / name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def workspace(self, name, *paths, body=None):
        folders = [{"path": p} for p in paths]
        text = body if body is not None else json.dumps({"folders": folders})
        (self.dir / (name + ".code-workspace")).write_text(text)

    def overrides(self, data):
        (self.dir / "overrides.json").write_text(json.dumps(data))

    def resolve(self, cwd, **kwargs):
        return ws.resolve(cwd, str(self.dir), env=kwargs.pop("env", self.env), **kwargs)


class MemberTests(Fixture):
    def test_relative_paths_resolve_against_the_file_and_order_is_kept(self):
        a, b = self.repo("a"), self.repo("b")
        self.workspace("demo", "../repos/b", "../repos/a")
        parsed = ws.parse_workspace(str(self.dir / "demo.code-workspace"))
        self.assertEqual(parsed["name"], "demo")
        self.assertEqual(parsed["members"], [b, a])

    def test_a_tilde_path_expands_to_home(self):
        home = self.root / "home"
        (home / "code").mkdir(parents=True)
        self.workspace("demo", "~/code")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            parsed = ws.parse_workspace(str(self.dir / "demo.code-workspace"))
        self.assertEqual(parsed["members"], [str(home / "code")])

    def test_a_uri_folder_is_skipped_and_a_missing_one_reported(self):
        a = self.repo("a")
        body = json.dumps({"folders": [{"uri": "vscode-remote://host/x"}, {"path": a},
                                       {"path": str(self.root / "gone")}]})
        self.workspace("demo", body=body)
        parsed = ws.parse_workspace(str(self.dir / "demo.code-workspace"))
        self.assertEqual(parsed["members"], [a])
        self.assertEqual(parsed["missing"], [str(self.root / "gone")])

    def test_the_map_reads_every_file_and_reports_the_broken_one(self):
        self.workspace("good", self.repo("a"))
        self.workspace("broken", body='{"folders": [')
        (self.dir / "notes.json").write_text("{}")
        found = ws.workspace_map(str(self.dir))
        self.assertEqual([w["name"] for w in found["workspaces"]], ["good"])
        self.assertEqual(len(found["errors"]), 1)
        self.assertIn("broken.code-workspace", found["errors"][0])

    def test_a_commented_workspace_with_trailing_commas_parses(self):
        self.repo("a")
        self.workspace("demo", body='{\n // members\n "folders": [{"path": "../repos/a"},],\n}')
        self.assertEqual(len(ws.workspace_map(str(self.dir))["workspaces"][0]["members"]), 1)


class ResolveTests(Fixture):
    def test_a_folder_in_no_workspace_attaches_nothing(self):
        self.workspace("demo", self.repo("a"))
        answer = self.resolve(self.repo("elsewhere"))
        self.assertEqual((answer["rule"], answer["workspace"]), ("none", None))

    def test_single_membership_attaches_from_a_subfolder(self):
        a, b = self.repo("a"), self.repo("b")
        self.workspace("demo", a, b)
        sub = Path(b) / "src" / "deep"
        sub.mkdir(parents=True)
        answer = self.resolve(str(sub))
        self.assertEqual(answer["rule"], "single")
        self.assertEqual(answer["folder"], b)
        self.assertEqual(answer["members"], [a, b])

    def test_a_sibling_sharing_a_prefix_is_not_a_subfolder(self):
        self.workspace("demo", self.repo("app"))
        self.assertEqual(self.resolve(self.repo("app-two"))["rule"], "none")

    def test_the_longest_member_wins(self):
        outer = self.repo("mono")
        inner = str(Path(outer) / "pkg")
        os.makedirs(inner)
        self.workspace("outer", outer)
        self.workspace("inner", inner, self.repo("x"))
        answer = self.resolve(inner)
        self.assertEqual((answer["folder"], answer["workspace"]["name"]), (inner, "inner"))

    def test_first_position_settles_a_shared_folder(self):
        shared, other = self.repo("shared"), self.repo("other")
        self.workspace("lead", shared, other)
        self.workspace("follow", other, shared)
        self.assertEqual(self.resolve(shared)["workspace"]["name"], "lead")
        self.assertEqual(self.resolve(other)["rule"], "first")

    def test_a_folder_first_in_two_workspaces_lists_the_candidates(self):
        shared = self.repo("shared")
        self.workspace("one", shared, self.repo("x"))
        self.workspace("two", shared, self.repo("y"))
        answer = self.resolve(shared)
        self.assertEqual(answer["rule"], "ambiguous")
        self.assertIsNone(answer["workspace"])
        self.assertEqual(answer["candidates"], ["one", "two"])

    def test_a_folder_first_in_none_is_ambiguous(self):
        shared = self.repo("shared")
        self.workspace("one", self.repo("x"), shared)
        self.workspace("two", self.repo("y"), shared)
        self.assertEqual(self.resolve(shared)["rule"], "ambiguous")

    def test_an_override_beats_first_position(self):
        shared, other = self.repo("shared"), self.repo("other")
        self.workspace("lead", shared, other)
        self.workspace("follow", other, shared)
        self.overrides({shared: "follow"})
        answer = self.resolve(shared)
        self.assertEqual((answer["rule"], answer["workspace"]["name"]), ("override", "follow"))

    def test_an_override_path_resolves_against_the_workspaces_folder(self):
        shared = self.repo("shared")
        self.workspace("one", shared, self.repo("x"))
        self.workspace("two", shared, self.repo("y"))
        self.overrides({"../repos/shared": "two"})
        self.assertEqual(self.resolve(shared)["workspace"]["name"], "two")

    def test_a_null_override_opts_the_folder_out(self):
        a = self.repo("a")
        self.workspace("demo", a, self.repo("b"))
        self.overrides({a: None})
        answer = self.resolve(a)
        self.assertEqual((answer["rule"], answer["workspace"]), ("override-none", None))

    def test_an_unknown_override_is_reported_and_ignored(self):
        a = self.repo("a")
        self.workspace("demo", a)
        self.overrides({a: "ghost"})
        read = ws.read_overrides(str(self.dir), {"demo"})
        self.assertEqual(read["unknown"], {a: "ghost"})
        self.assertEqual(self.resolve(a)["rule"], "single")

    def test_an_override_naming_a_workspace_without_the_folder_falls_through(self):
        a, b = self.repo("a"), self.repo("b")
        self.workspace("demo", a)
        self.workspace("elsewhere", b)
        self.overrides({a: "elsewhere"})
        self.assertEqual(self.resolve(a)["rule"], "single")

    def test_an_unreadable_overrides_file_is_an_error_not_a_crash(self):
        a = self.repo("a")
        self.workspace("demo", a)
        (self.dir / "overrides.json").write_text("{")
        self.assertEqual(len(ws.read_overrides(str(self.dir))["errors"]), 1)
        self.assertEqual(self.resolve(a)["rule"], "single")

    def test_the_environment_beats_an_override(self):
        shared = self.repo("shared")
        self.workspace("one", shared, self.repo("x"))
        self.workspace("two", shared, self.repo("y"))
        self.overrides({shared: "one"})
        answer = self.resolve(shared, env={"HARNESS_WORKSPACE": "two"})
        self.assertEqual((answer["rule"], answer["workspace"]["name"]), ("env", "two"))

    def test_an_environment_naming_another_workspace_is_ignored(self):
        a = self.repo("a")
        self.workspace("demo", a)
        self.workspace("unrelated", self.repo("z"))
        self.assertEqual(self.resolve(a, env={"HARNESS_WORKSPACE": "unrelated"})["rule"], "single")

    def test_the_launch_add_dirs_pick_the_workspace_they_match(self):
        shared, x, y = self.repo("shared"), self.repo("x"), self.repo("y")
        self.workspace("one", shared, x)
        self.workspace("two", shared, y)
        self.overrides({shared: "one"})
        answer = self.resolve(shared, add_dirs=[y])
        self.assertEqual((answer["rule"], answer["workspace"]["name"]), ("add-dirs", "two"))

    def test_add_dirs_that_match_no_workspace_exactly_fall_through(self):
        shared, x, y = self.repo("shared"), self.repo("x"), self.repo("y")
        self.workspace("one", shared, x, y)
        self.workspace("two", self.repo("z"), shared)
        self.assertEqual(self.resolve(shared, add_dirs=[x])["rule"], "first")

    def test_a_linked_worktree_resolves_through_its_main_checkout(self):
        main = self.repo("app")
        git = ["git", "-c", "user.name=t", "-c", "user.email=" + "t" + "@example.invalid"]
        subprocess.run(git + ["-C", main, "init", "-q"], check=True)
        subprocess.run(git + ["-C", main, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        linked = str(self.root / "worktrees" / "app-task")
        subprocess.run(git + ["-C", main, "worktree", "add", "-q", linked], check=True)
        self.workspace("demo", main, self.repo("b"))
        answer = self.resolve(linked)
        self.assertEqual((answer["rule"], answer["folder"]), ("single", main))

    def test_a_worktree_in_a_subfolder_resolves_to_the_same_place_in_the_main_checkout(self):
        main = self.repo("app")
        git = ["git", "-c", "user.name=t", "-c", "user.email=" + "t" + "@example.invalid"]
        subprocess.run(git + ["-C", main, "init", "-q"], check=True)
        subprocess.run(git + ["-C", main, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        linked = self.root / "worktrees" / "app-sub"
        subprocess.run(git + ["-C", main, "worktree", "add", "-q", str(linked)], check=True)
        (linked / "pkg").mkdir()
        self.assertEqual(ws._main_checkout(str(linked / "pkg")),
                         os.path.join(main, "pkg"))

    def test_a_git_file_without_a_commondir_is_not_a_linked_worktree(self):
        sub = Path(self.repo("sub"))
        modules = self.root / "outer" / ".git" / "modules" / "sub"
        modules.mkdir(parents=True)
        (sub / ".git").write_text("gitdir: " + str(modules) + "\n")
        self.assertIsNone(ws._main_checkout(str(sub)))
        self.assertIsNone(ws._main_checkout(str(self.root)))


class InstructionTests(Fixture):
    def test_dot_claude_claude_md_counts_as_claude_md(self):
        a = Path(self.repo("a"))
        (a / ".claude").mkdir()
        (a / ".claude" / "CLAUDE.md").write_text("nested")
        (a / "AGENTS.md").write_text("never read")
        found = ws.member_instructions(str(a))
        self.assertEqual([text for _, text in found["files"]], ["nested"])
        self.assertEqual(ws.claude_files(str(a)), [str(a / ".claude" / "CLAUDE.md")])

    def test_one_file_is_read_up_to_the_read_limit(self):
        a = Path(self.repo("a"))
        (a / "CLAUDE.md").write_text("x" * (ws.READ_LIMIT + 10))
        text = ws.member_instructions(str(a))["files"][0][1]
        self.assertEqual(len(text), ws.READ_LIMIT)

    def test_claude_md_wins_over_agents_md_and_imports_follow(self):
        a = Path(self.repo("a"))
        (a / "CLAUDE.md").write_text("see @docs/more.md and `@ignored.md` and me" "@example.com\n")
        (a / "AGENTS.md").write_text("never read")
        (a / "docs").mkdir()
        (a / "docs" / "more.md").write_text("more, which imports @../CLAUDE.md again")
        (a / "ignored.md").write_text("inline code is not an import")
        files = [Path(p).name for p, _ in ws.member_instructions(str(a))["files"]]
        self.assertEqual(files, ["CLAUDE.md", "more.md"])

    def test_agents_md_alone_is_supplied(self):
        a = Path(self.repo("a"))
        (a / "AGENTS.md").write_text("only this")
        self.assertEqual([t for _, t in ws.member_instructions(str(a))["files"]], ["only this"])

    def test_an_import_inside_a_fence_is_not_followed(self):
        a = Path(self.repo("a"))
        (a / "CLAUDE.md").write_text("```\n" "@fenced.md\n```\n")
        (a / "fenced.md").write_text("x")
        self.assertEqual(len(ws.member_instructions(str(a))["files"]), 1)

    def test_rules_load_unless_scoped_by_paths(self):
        a = Path(self.repo("a"))
        rules = a / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "always.md").write_text("---\ndescription: x\n---\nalways")
        (rules / "scoped.md").write_text("---\npaths:\n  - src/**\n---\nonly src")
        (a / "CLAUDE.local.md").write_text("local")
        found = ws.member_instructions(str(a))
        self.assertEqual([Path(p).name for p, _ in found["files"]], ["CLAUDE.local.md", "always.md"])
        self.assertEqual([Path(p).name for p in found["scoped"]], ["scoped.md"])

    def test_imports_stop_at_depth_five(self):
        a = Path(self.repo("a"))
        (a / "CLAUDE.md").write_text("@f1.md")
        for i in range(1, 8):
            (a / ("f%d.md" % i)).write_text("@f%d.md" % (i + 1))
        self.assertEqual(len(ws.member_instructions(str(a))["files"]), 6)


if __name__ == "__main__":
    unittest.main()
