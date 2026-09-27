# SPDX-License-Identifier: MIT
"""Unit tests for bin/harness. Run: python3 -m unittest discover tests

Fixture strings that must trip the lint are assembled at run time, so this file itself
carries no identifier-shaped literal; the lint scans it like every other file.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import io
from contextlib import redirect_stdout
from pathlib import Path

from isolation import isolate_home, without_config_dir, without_harness_vars  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

sys.path.insert(0, str(REPO / "scripts"))
import cost_bench  # noqa: E402  the token estimate must match the benchmark's

TEMPLATE = json.loads((REPO / "claude" / "settings.template.json").read_text())
CFG = json.loads((REPO / "config.example.json").read_text())
NO_TERMS = ([], [])


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_home = os.environ.get("HOME")
        isolate_home(self.home)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()


class TrustTests(TempHome):
    def test_trust_records_the_git_root_and_remove_forgets_it(self):
        repo = self.home / "work" / "repo"
        (repo / "sub").mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        ns = harness.argparse.Namespace
        self.assertEqual(harness.cmd_trust(ns(path=str(repo / "sub"), remove=False)), 0)
        self.assertEqual(harness.cmd_trust(ns(path=str(repo), remove=False)), 0)
        listed = harness.trusted_path().read_text().splitlines()
        self.assertEqual(listed, [str(repo.resolve())])
        loose = self.home / "loose"
        loose.mkdir()
        harness.cmd_trust(ns(path=str(loose), remove=False))
        self.assertEqual(len(harness.trusted_path().read_text().splitlines()), 2)
        harness.cmd_trust(ns(path=str(repo), remove=True))
        self.assertEqual(harness.trusted_path().read_text().splitlines(), [str(loose.resolve())])


class UninstallSharedFilesTests(TempHome):
    def test_the_path_block_is_removed_and_the_rest_of_zprofile_kept(self):
        z = self.home / ".zprofile"
        z.write_text("export EDITOR=vim\n\n" + harness.PATH_BLOCK + "alias ll='ls -l'\n")
        self.assertTrue(harness.remove_local_bin_from_path())
        self.assertEqual(z.read_text(), "export EDITOR=vim\n\nalias ll='ls -l'\n")
        self.assertFalse(harness.remove_local_bin_from_path())

    def test_the_ignore_block_is_removed_and_user_lines_kept(self):
        g = harness.global_gitignore_path()
        g.parent.mkdir(parents=True)
        entries = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())["gitignore_entries"]
        g.write_text(".DS_Store\n" + harness.IGNORE_MARKER + "\n" + "\n".join(entries) + "\nmine/\n")
        self.assertTrue(harness.remove_gitignore_entries())
        self.assertEqual(g.read_text(), ".DS_Store\nmine/\n")
        self.assertFalse(harness.remove_gitignore_entries())

    def test_sync_then_uninstall_leaves_no_ignore_block(self):
        harness.ensure_gitignore_entries(dry=False)
        self.assertIn(harness.IGNORE_MARKER, harness.global_gitignore_path().read_text())
        harness.remove_gitignore_entries()
        self.assertEqual(harness.global_gitignore_path().read_text(), "")


class SettingsMergeTests(unittest.TestCase):
    def test_merge_is_idempotent(self):
        # The registration sync actually writes, not the hooks-less committed template.
        template = harness.runtime_template()
        once = harness.merge_claude_settings({}, template, CFG)
        twice = harness.merge_claude_settings(once, template, CFG)
        self.assertEqual(once, twice)
        self.assertTrue(once["hooks"])

    def test_allow_rules_are_a_union_and_user_rules_survive(self):
        live = {"permissions": {"allow": ["Bash(my-tool *)", "Read(~/**)"]}}
        merged = harness.merge_claude_settings(live, TEMPLATE, CFG)
        allow = merged["permissions"]["allow"]
        self.assertIn("Bash(my-tool *)", allow)
        self.assertEqual(allow.count("Read(~/**)"), 1)
        for rule in TEMPLATE["permissions"]["allow"]:
            self.assertIn(rule, allow)

    def test_retired_template_rules_are_dropped_and_user_rules_kept(self):
        live = {"permissions": {"allow": ["Bash(awk *)", "Bash(my-tool *)"]}}
        applied = {"template": {"allow": ["Bash(awk *)", "Read(~/**)"]}}
        retired = harness.retired_allow_rules(applied, TEMPLATE)
        self.assertEqual(retired, ["Bash(awk *)"])
        merged = harness.merge_claude_settings(live, TEMPLATE, CFG, retired=retired)
        allow = merged["permissions"]["allow"]
        self.assertNotIn("Bash(awk *)", allow)
        self.assertIn("Bash(my-tool *)", allow)
        self.assertEqual(harness.retired_allow_rules({}, TEMPLATE), [])

    def test_never_touched_keys_survive(self):
        live = {"model": "some-model", "theme": "dark", "permissions": {"defaultMode": "bypassPermissions"}}
        merged = harness.merge_claude_settings(live, TEMPLATE, CFG)
        self.assertEqual(merged["model"], "some-model")
        self.assertEqual(merged["theme"], "dark")
        self.assertEqual(merged["permissions"]["defaultMode"], "bypassPermissions")  # inherit

    def test_posture_sets_default_mode(self):
        cfg = dict(CFG, permissions="manual")
        merged = harness.merge_claude_settings({}, TEMPLATE, cfg)
        self.assertEqual(merged["permissions"]["defaultMode"], "default")

    def test_bypass_requires_acknowledgement(self):
        with self.assertRaises(SystemExit):
            harness.posture(dict(CFG, permissions="bypass"))
        self.assertEqual(harness.posture(dict(CFG, permissions="bypass", permissions_bypass_acknowledged=True)), "bypass")

    def test_hook_entries_replace_by_id_and_legacy_basename(self):
        legacy = {"hooks": {"PostToolUse": [
            {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "python3 /somewhere/validate-plan-card.py"}]},
            {"matcher": "Write", "hooks": [{"type": "command", "command": "echo mine"}]},
        ]}}
        merged = harness.merge_claude_settings(legacy, harness.runtime_template(), CFG)
        post = merged["hooks"]["PostToolUse"]
        commands = [h["command"] for e in post for h in e["hooks"]]
        self.assertEqual([c for c in commands if "validate-plan-card" in c], [])
        self.assertIn("echo mine", commands)
        self.assertTrue(any("# harness:runtime-posttooluse" in c for c in commands))

    def test_strip_removes_only_harness_material(self):
        template = harness.runtime_template()
        merged = harness.merge_claude_settings({"model": "m", "permissions": {"allow": ["Bash(mine)"]}}, template, CFG)
        self.assertTrue(merged["hooks"])
        stripped = harness.strip_claude_settings(merged, template)
        self.assertEqual(stripped["model"], "m")
        self.assertEqual(stripped["permissions"]["allow"], ["Bash(mine)"])
        self.assertNotIn("hooks", stripped)
        self.assertNotIn("outputStyle", stripped)


class ConfigTests(TempHome):
    def test_env_overrides_config(self):
        cfg = harness.load_config(env={"HARNESS_STANCE_TESTING": "off", "HARNESS_PERMISSIONS": "auto", "HARNESS_IDENTITY_NAME": "Ada"})
        self.assertEqual(cfg["stances"]["testing"], "off")
        self.assertEqual(cfg["permissions"], "auto")
        self.assertEqual(cfg["identity"]["name"], "Ada")

    def test_every_default_stance_resolves(self):
        stances = harness.resolve_stances(harness.load_config(env={}))
        self.assertEqual(set(stances), set(harness.STANCE_NAMES))
        for p in stances.values():
            self.assertTrue(p.exists())

    def test_unknown_variant_fails_loudly(self):
        with self.assertRaises(SystemExit):
            harness.resolve_stances(harness.load_config(env={"HARNESS_STANCE_TESTING": "nope"}))


class PersonalRenderTests(unittest.TestCase):
    def test_render_keeps_below_marker(self):
        cfg = {"identity": {"name": "Ada", "pronouns": "she/her", "role": "x", "github": "ada", "timezone": "UTC"}}
        first = harness.render_personal(cfg, None)
        self.assertIn("Ada", first)
        edited = first + "\n- I like tabs.\n"
        cfg["identity"]["name"] = "Grace"
        second = harness.render_personal(cfg, edited)
        self.assertIn("Grace", second)
        self.assertNotIn("Ada", second)
        self.assertIn("- I like tabs.", second)


class CodexTests(unittest.TestCase):
    def test_config_update_adds_owned_keys_before_tables(self):
        text = 'model = "x"\n\n[projects."/a"]\ntrust_level = "trusted"\n'
        cfg = dict(CFG, permissions="bypass", permissions_bypass_acknowledged=True)
        out = harness.update_codex_config(text, cfg)
        self.assertIn('project_doc_fallback_filenames = ["CLAUDE.md"]', out)
        self.assertLess(out.index("project_doc_fallback_filenames"), out.index("[projects"))
        self.assertIn('approval_policy = "never"', out)
        self.assertIn('model = "x"', out)
        self.assertEqual(out, harness.update_codex_config(out, cfg))

    def test_agents_render_has_banner_and_stances(self):
        stances = harness.resolve_stances(harness.load_config(env={}))
        text = harness.render_codex_agents(stances, "personal bit")
        self.assertTrue(text.startswith(harness.CODEX_BANNER))
        self.assertIn("<!-- stance testing: required -->", text)
        self.assertIn("personal bit", text)
        self.assertNotIn("@~/.claude/CLAUDE.personal.md", text)


class SyncTests(TempHome):
    def test_sync_links_and_manifest_round_trip(self):
        rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=True, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 0)
        cd = self.home / ".claude"
        self.assertTrue((cd / "rules" / "harness" / "secrets.md").is_symlink())
        self.assertTrue((cd / "rules" / "harness-stances" / "testing.md").is_symlink())
        self.assertTrue((cd / "skills" / "plan-authoring").is_symlink())
        self.assertTrue((cd / "CLAUDE.md").is_symlink())
        self.assertTrue((cd / "CLAUDE.personal.md").exists())
        settings = json.loads((cd / "settings.json").read_text())
        self.assertEqual(settings["outputStyle"], "Scannable")
        manifest = json.loads((self.home / ".local/state/agent-harness/manifest.json").read_text())
        self.assertEqual(manifest["repo"], str(REPO))
        self.assertGreater(len(manifest["links"]), 10)
        rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=False, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 0)
        self.assertEqual(harness._diff_lines(), [])
        settings["permissions"]["allow"].append("Bash(something-new *)")
        (cd / "settings.json").write_text(json.dumps(settings))
        self.assertTrue(any("live-only allow rule" in l for l in harness._diff_lines()))
        harness.cmd_uninstall(harness.argparse.Namespace())
        self.assertFalse((cd / "rules" / "harness").exists())
        self.assertFalse((cd / "CLAUDE.md").exists())

    def test_unmanaged_file_is_never_replaced_without_adopt(self):
        cd = self.home / ".claude"
        cd.mkdir()
        (cd / "CLAUDE.md").write_text("mine")
        rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=False, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 2)
        self.assertEqual((cd / "CLAUDE.md").read_text(), "mine")
        rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=True, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 0)
        self.assertTrue((cd / "CLAUDE.md").is_symlink())
        moved = list((self.home / ".local/state/agent-harness/pre-harness").rglob("CLAUDE.md"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_text(), "mine")


class SwitchKindSyncTests(TempHome):
    """Rules, skills, workflows and roles switched `off` are absent from every runtime home."""

    def sync(self):
        return harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=False, adopt_codex=False, print_only=False))

    def switch(self, kind, unit, value):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(harness.config_set(kind + "." + unit, value), 0)

    def manifest(self):
        return json.loads(harness.manifest_path().read_text())

    def test_an_off_rule_is_unlinked_and_on_restores_it(self):
        self.assertEqual(self.sync(), 0)
        rules = self.home / ".claude" / "rules" / "harness"
        link = rules / "decisions-and-plans.md"
        self.assertTrue(rules.is_dir() and not rules.is_symlink())
        self.assertEqual(Path(os.readlink(link)), REPO / "claude" / "rules" / "decisions-and-plans.md")
        self.switch("rules", "decisions-and-plans", "off")
        self.assertEqual(harness._diff_lines(), ["pending sync: switch selection changed since last sync"])
        self.assertEqual(self.sync(), 0)
        self.assertFalse(link.is_symlink())
        self.assertTrue((rules / "secrets.md").is_symlink())
        self.assertNotIn(str(link), [l["path"] for l in self.manifest()["links"]])
        self.assertNotIn("<!-- decisions-and-plans.md -->", (self.home / ".codex" / "AGENTS.md").read_text())
        self.assertEqual(harness._diff_lines(), [])
        self.switch("rules", "decisions-and-plans", "on")
        self.assertEqual(self.sync(), 0)
        self.assertTrue(link.is_symlink())
        self.assertIn("<!-- decisions-and-plans.md -->", (self.home / ".codex" / "AGENTS.md").read_text())
        self.assertEqual(harness._diff_lines(), [])

    def test_the_directory_link_migrates_and_uninstall_removes_both_states(self):
        self.assertEqual(self.sync(), 0)
        # What a release before this one left: the whole directory linked, and one manifest entry.
        rules = self.home / ".claude" / "rules" / "harness"
        shutil.rmtree(str(rules))
        rules.symlink_to(REPO / "claude" / "rules")
        manifest = self.manifest()
        manifest["links"] = [l for l in manifest["links"] if Path(l["path"]).parent != rules]
        manifest["links"].append({"path": str(rules), "target": str(REPO / "claude" / "rules")})
        harness.manifest_path().write_text(json.dumps(manifest))
        self.assertEqual(self.sync(), 0)
        self.assertTrue(rules.is_dir() and not rules.is_symlink())
        self.assertTrue((rules / "secrets.md").is_symlink())
        manifest = self.manifest()
        self.assertEqual(manifest["migrated"], [{"path": str(rules),
                                                 "prior": {"type": "symlink", "target": str(REPO / "claude" / "rules")},
                                                 "applied": {"type": "directory"}}])
        self.assertNotIn(str(rules), [l["path"] for l in manifest["links"]])
        self.assertEqual(harness._diff_lines(), [])
        self.assertEqual(harness.cmd_uninstall(harness.argparse.Namespace()), 0)
        self.assertFalse(rules.exists() or rules.is_symlink())

    def test_a_dry_run_reports_the_migration_and_writes_nothing(self):
        cd = self.home / ".claude"
        (cd / "rules").mkdir(parents=True)
        (cd / "rules" / "harness").symlink_to(REPO / "claude" / "rules")
        out = io.StringIO()
        os.environ.pop("HARNESS_QUIET", None)
        try:
            with redirect_stdout(out):
                rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=True, adopt=False, adopt_codex=False, print_only=False))
        finally:
            os.environ["HARNESS_QUIET"] = "1"
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("migrate", out.getvalue())
        self.assertNotIn("is not a harness link", out.getvalue())
        self.assertTrue((cd / "rules" / "harness").is_symlink())

    def test_a_user_link_at_the_rules_directory_is_preserved_then_restored_by_uninstall(self):
        cd = self.home / ".claude"
        (cd / "rules").mkdir(parents=True)
        mine = self.home / "my-rules"
        mine.mkdir()
        (cd / "rules" / "harness").symlink_to(mine)
        self.assertEqual(self.sync(), 2)
        self.assertEqual(Path(os.readlink(cd / "rules" / "harness")), mine)
        rc = harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=True, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 0)
        self.assertTrue((cd / "rules" / "harness" / "secrets.md").is_symlink())
        self.assertEqual(harness.cmd_uninstall(harness.argparse.Namespace()), 0)
        self.assertEqual(Path(os.readlink(cd / "rules" / "harness")), mine)

    def test_uninstall_leaves_a_rules_directory_the_user_already_had(self):
        rules = self.home / ".claude" / "rules" / "harness"
        rules.mkdir(parents=True)
        self.assertEqual(self.sync(), 0)
        self.assertTrue((rules / "secrets.md").is_symlink())
        self.assertEqual(harness.cmd_uninstall(harness.argparse.Namespace()), 0)
        self.assertTrue(rules.is_dir() and not rules.is_symlink())
        self.assertEqual(list(rules.iterdir()), [])

    def test_an_off_skill_workflow_and_role_leave_no_projection_in_either_home(self):
        self.assertEqual(self.sync(), 0)
        cd, codex, agents_skills = self.home / ".claude", self.home / ".codex", self.home / ".agents" / "skills"
        paths = [cd / "skills" / "plan-authoring", agents_skills / "plan-authoring",
                 cd / "commands" / "land.md", agents_skills / "harness-land" / "SKILL.md",
                 cd / "agents" / "reviewer.md", codex / "agents" / "reviewer.toml"]
        for path in paths:
            self.assertTrue(path.exists(), str(path))
        self.switch("skills", "plan-authoring", "off")
        self.switch("workflows", "land", "off")
        self.switch("workflows", "review", "off")  # the review workflow depends on the reviewer
        self.switch("roles", "reviewer", "off")
        self.assertEqual(self.sync(), 0)
        for path in paths:
            self.assertFalse(path.exists() or path.is_symlink(), str(path) + " survived its switch")
        self.assertFalse((agents_skills / "harness-land").exists())
        self.assertTrue((cd / "skills" / "delegation-tiering").is_symlink())
        self.assertTrue((cd / "agents" / "builder.md").exists())
        self.assertEqual(harness._diff_lines(), [])
        for kind, unit in (("skills", "plan-authoring"), ("workflows", "land"), ("roles", "reviewer"),
                           ("workflows", "review")):
            self.switch(kind, unit, "on")
        self.assertEqual(self.sync(), 0)
        for path in paths:
            self.assertTrue(path.exists(), str(path) + " did not come back")
        self.assertEqual(harness._diff_lines(), [])

    def test_an_off_role_a_cost_row_moves_is_neither_rendered_nor_linked(self):
        self.switch("workflows", "research", "off")  # research depends on worker-a
        self.switch("roles", "worker-a", "off")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(harness.config_set("stances.cost", "frugal"), 0)
        self.assertEqual(self.sync(), 0)
        target = self.home / ".claude" / "agents" / "worker-a.md"
        self.assertFalse(target.exists() or target.is_symlink())
        self.assertFalse((self.home / ".codex" / "agents" / "worker-a.toml").exists())
        self.assertTrue((self.home / ".claude" / "agents" / "worker-b.md").exists())
        self.assertEqual(harness._diff_lines(), [])

    def test_a_switch_that_breaks_a_dependency_is_refused_and_one_that_repairs_it_is_written(self):
        self.switch("workflows", "land", "on")  # writes the configuration the refusal must leave alone
        before = harness.config_path().read_text()
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as refused:
            harness.config_set("roles.reviewer", "off")
        self.assertIn("workflows/review depends on roles/reviewer", str(refused.exception))
        self.assertEqual(harness.config_path().read_text(), before)
        cfg = json.loads(before)
        cfg.setdefault("roles", {})["reviewer"] = "off"
        harness.config_path().write_text(json.dumps(cfg))
        with self.assertRaises(SystemExit):
            harness.load_selection(env={})
        self.switch("roles", "reviewer", "on")
        self.assertEqual(harness.load_selection(env={})["roles"]["reviewer"], "on")

    def test_a_switch_value_or_unit_config_set_cannot_apply_is_refused(self):
        with self.assertRaises(SystemExit):
            harness.config_set("rules.decisions-and-plans", "maybe")
        with self.assertRaises(SystemExit):
            harness.config_set("rules.no-such-rule", "off")
        with self.assertRaises(SystemExit):
            harness.config_set("rules", "off")

    def test_selection_reports_the_effective_count_and_the_lint_figure_does_not_move(self):
        before = harness.always_loaded_lines(REPO)[0]

        def printed():
            out = io.StringIO()
            os.environ.pop("HARNESS_QUIET", None)
            try:
                with redirect_stdout(out):
                    harness.cmd_selection(harness.argparse.Namespace(json=False))
            finally:
                os.environ["HARNESS_QUIET"] = "1"
            return [l for l in out.getvalue().splitlines() if l.startswith("always-loaded: ")][0]

        on = printed()
        self.switch("rules", "decisions-and-plans", "off")
        off = printed()
        rule = len((REPO / "claude" / "rules" / "decisions-and-plans.md").read_text().splitlines())
        self.assertEqual(int(on.split()[1]) - int(off.split()[1]), rule)
        self.assertIn(f"{before} of {harness.ALWAYS_LOADED_CAP}", off)
        self.assertEqual(harness.always_loaded_lines(REPO)[0], before)


class LinkAliasTests(TempHome):
    """A managed link that reaches its file through an alias of the checkout is still the harness's."""

    def sync(self):
        return harness.cmd_sync(harness.argparse.Namespace(dry_run=False, adopt=True, adopt_codex=False, print_only=False))

    def alias(self, link):
        # The checkout spells one directory two ways: claude/stances is a link to primitives/stances.
        recorded = Path(os.readlink(link))
        spelled = REPO / "claude" / "stances" / recorded.relative_to(REPO / "primitives" / "stances")
        self.assertNotEqual(spelled, recorded)
        self.assertEqual(spelled.resolve(), recorded.resolve())
        link.unlink()
        link.symlink_to(spelled)
        return spelled

    def test_an_alias_of_the_recorded_target_is_not_drift_and_a_real_redirect_still_is(self):
        self.assertEqual(self.sync(), 0)
        stances = self.home / ".claude" / "rules" / "harness-stances"
        self.alias(stances / "testing.md")
        self.assertEqual(harness._diff_lines(), [])
        self.assertEqual(self.sync(), 0)
        self.assertEqual(harness._diff_lines(), [])
        elsewhere = self.home / "mine.md"
        elsewhere.write_text("mine\n")
        (stances / "voice.md").unlink()
        (stances / "voice.md").symlink_to(elsewhere)
        self.assertEqual(harness._diff_lines(), [f"redirected link {stances / 'voice.md'} -> {elsewhere}"])

    def choose_voice(self, variant):
        path = harness.config_path()
        cfg = json.loads(path.read_text())
        cfg["stances"]["voice"] = variant
        path.write_text(json.dumps(cfg))

    def test_changing_a_stance_relinks_a_link_spelled_through_the_alias(self):
        self.assertEqual(self.sync(), 0)
        voice = self.home / ".claude" / "rules" / "harness-stances" / "voice.md"
        self.alias(voice)
        self.choose_voice("concise")
        self.assertEqual(self.sync(), 0)
        self.assertEqual(Path(os.readlink(voice)), REPO / "primitives" / "stances" / "voice" / "concise.md")
        self.assertEqual(harness._diff_lines(), [])

    def test_changing_a_stance_preserves_a_real_redirect(self):
        self.assertEqual(self.sync(), 0)
        voice = self.home / ".claude" / "rules" / "harness-stances" / "voice.md"
        elsewhere = self.home / "mine.md"
        elsewhere.write_text("mine\n")
        voice.unlink()
        voice.symlink_to(elsewhere)
        self.choose_voice("concise")
        self.assertNotEqual(self.sync(), 0)
        self.assertEqual(Path(os.readlink(voice)), elsewhere)

    def test_uninstall_removes_an_aliased_link_and_preserves_a_real_redirect(self):
        self.assertEqual(self.sync(), 0)
        stances = self.home / ".claude" / "rules" / "harness-stances"
        self.alias(stances / "testing.md")
        elsewhere = self.home / "mine.md"
        elsewhere.write_text("mine\n")
        (stances / "voice.md").unlink()
        (stances / "voice.md").symlink_to(elsewhere)
        harness.cmd_uninstall(harness.argparse.Namespace())
        self.assertFalse((stances / "testing.md").is_symlink())
        self.assertEqual(Path(os.readlink(stances / "voice.md")), elsewhere)


class LintTests(TempHome):
    def _fixture(self):
        root = Path(self.tmp.name) / "repo"
        (root / "claude").mkdir(parents=True)
        (root / "docs").mkdir()
        (root / ".github").mkdir()
        (root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 Ada Lovelace\n")
        (root / ".github" / "CODEOWNERS").write_text("* @adalovelace\n")
        return root

    def test_shapes_are_caught_without_any_configured_terms(self):
        root = self._fixture()
        account = str(10 ** 11 + 4242)                     # a 12-digit number, built at run time
        email = "someone" + "@" + "example" + ".com"
        home = "/" + "Users" + "/someone/" + "secret.txt"
        key = "AKIA" + "Q" * 16
        zone = "Z" + "0" * 13 + "ABCDEFG"
        tenant = "dev-" + "a1b2c3d4e5f6g7h8" + ".us.auth0.com"
        (root / "claude" / "leak.md").write_text("\n".join([account, email, home, key, zone, tenant]) + "\n")
        hits = harness.lint_tree(root, NO_TERMS)
        labels = " ".join(hits)
        for want in ("12-digit account id", "email address", "home-directory path", "secret pattern", "hosted-zone id", "identity-provider tenant"):
            self.assertIn(want, labels, msg=want)

    def test_legal_artifacts_exempt_only_upstream_email_addresses(self):
        root = self._fixture()
        address = "upstream" + "@" + "example.com"
        account = str(10 ** 11 + 4242)
        home = "/" + "Users" + "/someone/secret.txt"
        key = "AKIA" + "Q" * 16
        paths = [
            root / "studio" / "public" / "THIRD_PARTY_NOTICES.txt",
            root / "studio" / "third-party" / "licenses" / "react-remove-scroll-bar-2.3.8-LICENSE",
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join((address, account, home, key, "Example Corp")) + "\n")
        source = root / "studio" / "src" / "contact.txt"
        source.parent.mkdir(parents=True)
        source.write_text(address + "\n")
        hits = harness.lint_files(root, paths + [source], (["example corp"], []))
        for path in paths:
            matching = [hit for hit in hits if str(path.relative_to(root)) in hit]
            self.assertFalse(any("email address" in hit for hit in matching), matching)
            for label in ("12-digit account id", "home-directory path", "secret pattern", "lint-terms"):
                self.assertTrue(any(label in hit for hit in matching), (label, matching))
        self.assertTrue(any("studio/src/contact.txt" in hit and "email address" in hit for hit in hits), hits)

    def test_generated_studio_bundle_is_skipped_but_source_is_linted(self):
        root = self._fixture()
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        address = "upstream" + "@" + "example.com"
        generated = root / "studio" / "dist" / "assets" / "index-deadbeef.js"
        source = root / "studio" / "src" / "contact.ts"
        generated.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        generated.write_text(address + "\n")
        source.write_text(address + "\n")
        subprocess.run(["git", "-C", str(root), "add", "studio"], check=True)

        for hits in (harness.lint_tree(root, NO_TERMS), harness.lint_staged(root)):
            self.assertFalse(any("studio/dist/" in hit for hit in hits), hits)
            self.assertTrue(any("studio/src/contact.ts" in hit and "email address" in hit
                                for hit in hits), hits)

    def test_terms_file_and_maintainer_name_rules(self):
        root = self._fixture()
        (root / "claude" / "bad.md").write_text("Hand this to Ada; ship to Example Corp; see Widgetron docs.\n")
        (root / "docs" / "ok.md").write_text("Grew out of Widgetron.\n")
        (root / "README.md").write_text("Maintained by Ada Lovelace (@adalovelace).\n")
        terms = (["example corp"], ["widgetron"])
        hits = harness.lint_tree(root, terms)
        self.assertTrue(any("bad.md" in h and "maintainer name" in h for h in hits))
        self.assertTrue(any("bad.md" in h and "lint-terms" in h for h in hits))
        self.assertTrue(any("bad.md" in h and "project name" in h for h in hits))
        self.assertFalse(any("docs/ok.md" in h for h in hits))
        self.assertFalse(any("README.md" in h for h in hits))
        self.assertFalse(any("LICENSE" in h for h in hits))

    def test_untracked_local_work_is_skipped_until_it_is_staged(self):
        root = self._fixture()
        def git(*args):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        git("init", "-q")
        note = root / ".agent-harness" / "plans" / "note.md"
        note.parent.mkdir(parents=True)
        note.write_text("Built on Widgetron.\n")
        (root / "stray.md").write_text("Built on Widgetron.\n")
        terms = ([], ["widgetron"])
        hits = harness.lint_tree(root, terms)
        self.assertFalse(any("note.md" in h for h in hits), hits)
        self.assertTrue(any("stray.md" in h and "project name" in h for h in hits), hits)
        git("add", str(note))
        hits = harness.lint_tree(root, terms)
        self.assertTrue(any("note.md" in h and "project name" in h for h in hits), hits)

    def test_repo_carries_no_identifier_shapes_anywhere(self):
        """The regression test for the first release: no file, not even the lint or these
        tests, may contain an identifier-shaped literal."""
        self.assertEqual(harness.lint_tree(REPO, NO_TERMS), [])

    def test_repo_is_clean_under_the_local_terms_file_if_present(self):
        os.environ["HOME"] = self._old_home or ""
        try:
            self.assertEqual(harness.lint_tree(REPO), [])
        finally:
            os.environ["HOME"] = str(self.home)


class ContextCapTests(TempHome):
    """The always-loaded set is CLAUDE.md + every rule + the longest variant of each stance."""

    def _tree(self, rule_lines, stance_variants):
        root = Path(self.tmp.name) / "tree"
        (root / "claude" / "rules").mkdir(parents=True)
        (root / "claude" / "CLAUDE.md").write_text("# Global instructions\n\nshort.\n")
        (root / "claude" / "rules" / "a.md").write_text("\n".join(["line"] * rule_lines) + "\n")
        d = root / "claude" / "stances" / "testing"
        d.mkdir(parents=True)
        for name, n in stance_variants.items():
            (d / f"{name}.md").write_text("\n".join(["line"] * n) + "\n")
        return root

    def test_repo_is_under_the_cap(self):
        total, groups = harness.always_loaded_lines(REPO)
        self.assertLessEqual(total, harness.ALWAYS_LOADED_CAP, msg=f"{total} lines: {groups}")
        self.assertEqual(harness.check_context_cap(REPO), [])

    def test_breakdown_covers_claude_md_rules_and_one_variant_per_stance(self):
        total, groups = harness.always_loaded_lines(REPO)
        names = [g[0] for g in groups]
        self.assertIn("claude/CLAUDE.md", names)
        self.assertTrue(any(n.startswith("claude/rules/") for n in names))
        dims = sorted(p.name for p in (REPO / "claude" / "stances").iterdir() if p.is_dir())
        for dim in dims:
            self.assertEqual(len([n for n in names if n.startswith(f"claude/stances/{dim}/")]), 1)
        self.assertEqual(total, sum(g[1] for g in groups))

    def test_the_longest_stance_variant_is_the_one_counted(self):
        root = self._tree(3, {"off": 2, "required": 9, "pragmatic": 5})
        total, groups = harness.always_loaded_lines(root)
        stance = [g for g in groups if g[0].startswith("claude/stances/testing/")][0]
        self.assertIn("required.md", stance[0])
        self.assertEqual(stance[1], 9)
        self.assertEqual(total, 3 + 3 + 9)  # CLAUDE.md, the rule, the worst variant

    def test_over_cap_tree_fails_and_the_breakdown_names_the_offender(self):
        root = self._tree(harness.ALWAYS_LOADED_CAP + 20, {"off": 2})
        hits = harness.check_context_cap(root)
        self.assertTrue(hits)
        self.assertIn(f"over the {harness.ALWAYS_LOADED_CAP}-line cap", hits[0])
        self.assertTrue(any("claude/rules/" in h for h in hits[1:]), msg=str(hits))
        biggest = hits[1]
        self.assertIn("claude/rules/", biggest)
        self.assertIn(str(harness.ALWAYS_LOADED_CAP + 20), biggest)

    def test_cap_is_reported_by_the_lint_command(self):
        root = self._tree(harness.ALWAYS_LOADED_CAP + 20, {"off": 2})
        rc = harness.cmd_lint(harness.argparse.Namespace(path=str(root), staged=False))
        self.assertEqual(rc, 1)
        self.assertEqual(harness.cmd_lint(harness.argparse.Namespace(path=str(REPO), staged=False)), 0)

    def test_a_tree_without_harness_content_is_not_flagged(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(harness.check_context_cap(empty), [])

    def test_the_token_cap_derives_from_the_measured_standing_context(self):
        """A third of #430's measured figure plus #811's measured stance, not a round number."""
        self.assertEqual(harness.MEASURED_STANDING_CONTEXT_TOKENS, 12607)
        self.assertEqual(harness.ALWAYS_LOADED_TOKEN_CAP,
                         harness.MEASURED_STANDING_CONTEXT_TOKENS // 3
                         + harness.CONCISE_VOICE_STANCE_TOKENS)
        self.assertEqual(harness.CHARS_PER_TOKEN, cost_bench.CHARS_PER_TOKEN)

    def test_the_cap_comment_cites_its_source(self):
        text = (REPO / "bin" / "harness").read_text(encoding="utf-8")
        head = text.split("ALWAYS_LOADED_TOKEN_CAP", 1)[0]
        self.assertIn("code.claude.com/docs/en/memory", head)
        self.assertIn("#430", head)

    def test_repo_is_under_the_token_cap(self):
        total, groups = harness.always_loaded_tokens(REPO)
        self.assertLessEqual(total, harness.ALWAYS_LOADED_TOKEN_CAP, msg=f"{total} tokens: {groups}")
        self.assertEqual(total, harness.est_tokens(
            sum(g[2] for g in harness.always_loaded_groups(REPO))))

    def test_a_tree_under_the_line_cap_can_still_fail_on_tokens(self):
        """Long lines cost tokens the line count cannot see, so the token cap is the binding one."""
        root = self._tree(10, {"off": 2})
        fat = "x" * (harness.ALWAYS_LOADED_TOKEN_CAP * int(harness.CHARS_PER_TOKEN) + 400)
        (root / "claude" / "rules" / "a.md").write_text(fat + "\n")
        lines, _ = harness.always_loaded_lines(root)
        self.assertLessEqual(lines, harness.ALWAYS_LOADED_CAP)
        hits = harness.check_context_cap(root)
        self.assertTrue(hits)
        self.assertIn(f"over the {harness.ALWAYS_LOADED_TOKEN_CAP}-token cap", hits[0])
        self.assertNotIn("-line cap", hits[0])
        self.assertTrue(any("claude/rules/" in h and "tokens" in h for h in hits[1:]), msg=str(hits))

    def test_lint_reports_both_measures_when_the_tree_passes(self):
        out = io.StringIO()
        os.environ.pop("HARNESS_QUIET", None)  # `say` prints nothing while it is set
        with redirect_stdout(out):
            rc = harness.cmd_lint(harness.argparse.Namespace(path=str(REPO), staged=False))
        self.assertEqual(rc, 0)
        line = [ln for ln in out.getvalue().splitlines() if ln.startswith("context:")]
        self.assertEqual(len(line), 1, msg=out.getvalue())
        self.assertIn(f"of {harness.ALWAYS_LOADED_TOKEN_CAP}", line[0])
        self.assertIn(f"of {harness.ALWAYS_LOADED_CAP}", line[0])


class DetectorCoverageTests(TempHome):
    """Every rule file names a detector in the registry, or opts out with a reason."""

    def _copy(self):
        root = Path(self.tmp.name) / "copy"
        (root / "claude" / "hooks").mkdir(parents=True)
        shutil.copytree(REPO / "claude" / "rules", root / "claude" / "rules")
        shutil.copy2(REPO / "claude" / "hooks" / "rule-detectors.py",
                     root / "claude" / "hooks" / "rule-detectors.py")
        shutil.copy2(REPO / "claude" / "hooks" / "allow-readonly-bash.py",
                     root / "claude" / "hooks" / "allow-readonly-bash.py")
        return root

    def test_the_real_tree_is_covered(self):
        self.assertEqual(harness.check_detectors(REPO), [])
        self.assertEqual(harness.lint_tree(REPO, NO_TERMS), [])

    def test_a_rule_with_no_detector_and_no_opt_out_fails_the_lint(self):
        root = self._copy()
        (root / "claude" / "rules" / "brand-new.md").write_text("# Brand new\n\n- Do the thing.\n")
        hits = harness.check_detectors(root)
        self.assertTrue(any("brand-new.md" in h for h in hits), msg=str(hits))
        self.assertTrue(any("OPT_OUT" in h for h in hits))
        self.assertTrue(any("brand-new.md" in h for h in harness.lint_tree(root, NO_TERMS)))

    def test_a_missing_registry_is_itself_a_finding(self):
        root = self._copy()
        (root / "claude" / "hooks" / "rule-detectors.py").unlink()
        self.assertTrue(any("rule-detectors.py" in h for h in harness.check_detectors(root)))

    def test_a_tree_with_no_rules_is_not_flagged(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(harness.check_detectors(empty), [])

    def test_the_lint_reads_its_secret_patterns_from_the_registry(self):
        module = harness.load_detectors(REPO)
        self.assertEqual(harness.SECRET_PATTERNS, module.SECRET_PATTERNS)
        self.assertTrue(harness.SECRET_PATTERNS)

    def test_an_empty_secret_list_is_a_finding_not_a_pass(self):
        # The pre-commit path lints staged files only and never calls check_detectors,
        # so a registry that fails to import must surface here rather than skip secrets.
        with unittest.mock.patch.object(harness, "SECRET_PATTERNS", []):
            hits = harness.lint_files(REPO, [REPO / "README.md"], NO_TERMS)
        self.assertTrue(any("rule-detectors.py" in h for h in hits), msg=str(hits))


class RetiredNameLintTests(TempHome):
    """The old brand, slug and site host fail outside the dated areas and the "Formerly" lines.

    The retired strings are assembled at run time, so this file passes the rule it tests.
    """
    OLD = "agent" + "-harness"
    BRAND = "Agent" + " Harness"
    SLUG = "Jake" + "Selby/" + OLD
    HOST = OLD + ".jake" + "selby.com"

    def _fixture(self, this_repository=True):
        root = Path(self.tmp.name) / "repo"
        root.mkdir()
        if this_repository:
            (root / "primitives" / "roles").mkdir(parents=True)
        return root

    def _write(self, root, rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        return path

    def _retired(self, root, *rels):
        return [h for h in harness.lint_files(root, [root / r for r in rels], NO_TERMS) if "retired-name" in h]

    def test_each_retired_name_fails_a_living_file(self):
        root = self._fixture()
        self._write(root, "README.md", "\n".join([
            "Welcome to " + self.BRAND + ".",
            "Clone https://github.com/" + self.SLUG + ".git to start.",
            "Read https://" + self.HOST + "/docs/ for the reference.",
            "utm_campaign=" + self.SLUG.replace("/", "%2F"),
            "Upper case slug: " + self.SLUG.upper()]))
        hits = self._retired(root, "README.md")
        self.assertEqual([h.split(":")[1] for h in hits], ["1", "2", "3", "4", "5"], hits)
        text = " ".join(hits)
        for label in ("brand", "repository slug", "site host"):
            self.assertIn("the old project " + label, text)

    def test_the_new_names_and_on_disk_names_pass(self):
        root = self._fixture()
        self._write(root, "docs/install.md", "\n".join([
            "Clone https://github.com/" + "Jake" + "Selby/model-citizen and read https://model-citizen.dev.",
            "Config lives in ~/.config/" + self.OLD + "/config.json; local plans in ." + self.OLD + "/plans.",
            "The launchd label is com.example." + self.OLD + ".observer.",
            "A sibling repository, " + self.SLUG + "-site, is a different name.",
            "Every coding tool needs an agent harness of some kind."]))
        self.assertEqual(self._retired(root, "docs/install.md"), [])

    def test_formerly_lines_pass_and_the_rest_of_the_file_does_not(self):
        root = self._fixture()
        self._write(root, "README.md", "Formerly " + self.BRAND + ".\n\n" + self.BRAND + " is here.")
        hits = self._retired(root, "README.md")
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("README.md:3:"), hits)

    def test_dated_areas_keep_the_old_names(self):
        root = self._fixture()
        line = "See https://github.com/" + self.SLUG + "/issues/1 on " + self.HOST + "."
        dated = ["_bmad-output/issue-map.json", "_bmad-output/implementation-artifacts/X-1.md",
                 "docs/plans/p.md", ".agent-harness/handoffs/h.md", "compatibility/evidence/e.json",
                 "docs/spikes/2026-09-22-note.md", "tests/test_bmad_fixture.py"]
        for rel in dated:
            self._write(root, rel, line)
        self._write(root, "docs/spikes/note.md", line)
        hits = self._retired(root, *dated + ["docs/spikes/note.md"])
        self.assertTrue(hits, "the undated file must still fail")
        self.assertTrue(all(h.startswith("docs/spikes/note.md:") for h in hits), hits)

    def test_only_the_changelogs_released_sections_keep_the_old_names(self):
        root = self._fixture()
        self._write(root, "CHANGELOG.md", "\n".join([
            "# Changelog", "",
            "## [Unreleased]", "", "- Moved to " + self.HOST + ".", "",
            "## [0.13.1] \u2014 2026-09-24", "", "- Released as " + self.BRAND + ".", "",
            "## [0.1.0] \u2014 2026-01-02", "", "- First cut of " + self.SLUG + "."]))
        hits = self._retired(root, "CHANGELOG.md")
        self.assertEqual([h.split(":")[1] for h in hits], ["5"], hits)

    def test_another_repository_is_not_held_to_this_projects_rename(self):
        root = self._fixture(this_repository=False)
        self._write(root, "README.md", "Built on " + self.BRAND + ": https://github.com/" + self.SLUG)
        self.assertEqual(self._retired(root, "README.md"), [])

    def test_the_staged_path_applies_the_rule_too(self):
        root = self._fixture()
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        self._write(root, "notes.md", "Moved from " + self.HOST + ".")
        subprocess.run(["git", "-C", str(root), "add", "notes.md"], check=True)
        self.assertTrue(any("retired-name" in h for h in harness.lint_staged(root)))


class CliTests(unittest.TestCase):
    def test_version_and_help(self):
        out = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "--version"], capture_output=True, text=True)
        self.assertIn("model-citizen", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
