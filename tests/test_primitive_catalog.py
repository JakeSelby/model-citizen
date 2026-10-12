"""Authoring and resolution tests for shared primitives and generated adapters."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_harness import harness, CFG, REPO
from harness_core import catalog


class CatalogTests(unittest.TestCase):
    def test_every_native_role_has_one_shared_source_and_round_trips(self):
        self.assertEqual(catalog.projection_drift(REPO), [])
        for source in (REPO / "primitives/roles").glob("*.md"):
            fields, body = catalog.frontmatter(source)
            self.assertNotIn("model", fields)
            self.assertNotIn("tools", fields)
            claude = catalog.role_projection(REPO, "claude-code", source)
            codex = catalog.role_projection(REPO, "codex", source)
            self.assertIn(body, claude)
            instructions = next(line for line in codex.splitlines() if line.startswith("developer_instructions = "))
            self.assertEqual(json.loads(instructions.partition(" = ")[2]), body)
            self.assertIn('sandbox_mode = "read-only"' if fields["authority"] != "workspace-write"
                          else 'sandbox_mode = "workspace-write"', codex)

    def test_roles_name_a_class_and_each_adapter_maps_it(self):
        classes = {p.stem: catalog.frontmatter(p)[0]["tier"] for p in (REPO / "primitives/roles").glob("*.md")}
        self.assertEqual(classes, {"builder": "strong", "design-judge": "strong", "designer": "strong", "gatherer": "strong",
                                   "log-compressor": "standard", "planner": "strong", "reviewer": "strong",
                                   "spec-reviewer": "standard", "worker-a": "standard",
                                   "worker-b": "strong", "worker-c": "strong"})
        for source in (REPO / "primitives/roles").glob("*.md"):
            self.assertNotIn("\nmodel: inherit\n", catalog.role_projection(REPO, "claude-code", source))
            self.assertIn("\nmodel = ", catalog.role_projection(REPO, "codex", source))
        reviewer = REPO / "primitives/roles/reviewer.md"
        self.assertIn('model = "chosen"', catalog.role_projection(REPO, "codex", reviewer, {"model": "chosen"}))
        # `inherit` is the override back to the session model: Codex omits the key, Claude Code spells it.
        self.assertNotIn("\nmodel = ", catalog.role_projection(REPO, "codex", reviewer, {"model": "inherit"}))
        self.assertIn("\nmodel: inherit\n", catalog.role_projection(REPO, "claude-code", reviewer, {"model": "inherit"}))
        for runtime in ("claude-code", "codex"):
            tiers = json.loads((REPO / "adapters" / runtime / "bindings.json").read_text())["tiers"]
            self.assertEqual(list(tiers), list(catalog.TIER_CLASSES), msg=runtime)

    def test_an_unmapped_class_resolves_upward_or_not_at_all(self):
        self.assertEqual(catalog.native_model({"strong": "b", "light": "d"}, "standard"), "b")
        self.assertEqual(catalog.native_model({"strong": "b", "light": "d"}, "light"), "d")
        self.assertIsNone(catalog.native_model({"standard": "c", "light": "d"}, "strong"))
        self.assertIsNone(catalog.native_model({}, "frontier"))

    def test_bindings_reject_unknown_classes_and_effort_above_high(self):
        fields = catalog.role_contract(REPO, "reviewer")[0]
        for effort in ("xhigh", "max"):
            with self.assertRaisesRegex(ValueError, "effort must be one of"):
                catalog.role_binding(REPO, "claude-code", fields, {"effort": effort})
        self.assertEqual(catalog.role_binding(REPO, "claude-code", fields, {"model": "chosen"})["model"], "chosen")
        with patch.object(catalog.json, "loads", return_value={"tiers": {"premium": "x"}, "roles": {"reviewer": {}}}):
            with self.assertRaisesRegex(ValueError, "adapter tiers"):
                catalog.role_binding(REPO, "claude-code", fields)
        with patch.object(catalog, "frontmatter", return_value=({"name": "reviewer", "authority": "read-only",
                          "context": "fresh", "delegation": "none", "tier": "premium"}, "body")):
            with self.assertRaisesRegex(ValueError, "tier must be one of"):
                catalog.role_contract(REPO, "reviewer")

    def test_a_configured_class_table_lays_over_the_adapters(self):
        reviewer = REPO / "primitives/roles/reviewer.md"
        self.assertIn('model = "next-strong"', catalog.role_projection(REPO, "codex", reviewer, None, {"strong": "next-strong"}))
        fields = catalog.role_contract(REPO, "reviewer")[0]
        self.assertEqual(catalog.role_binding(REPO, "claude-code", fields, None, {"strong": "next"})["model"], "next")
        # A per-role override still beats the class, and an unknown class is refused wherever it comes from.
        self.assertEqual(catalog.role_binding(REPO, "claude-code", fields, {"model": "mine"}, {"strong": "next"})["model"], "mine")
        with self.assertRaisesRegex(ValueError, "adapter tiers"):
            catalog.role_binding(REPO, "codex", fields, None, {"premium": "x"})

    def test_a_class_table_is_checked_against_the_providers_catalog(self):
        tiers = {"frontier": "a", "strong": "b", "standard": "c", "light": "d"}
        models = [{"slug": "a", "priority": 1, "upgrade": None}, {"slug": "b", "priority": 4},
                  {"slug": "c", "priority": 7}, {"slug": "d", "priority": 8}, "junk"]
        self.assertEqual(catalog.tier_findings(tiers, models), [])
        self.assertEqual(catalog.tier_findings(tiers, []), [(n, m, "not in the provider's catalog") for n, m in tiers.items()])
        models[1] = {"slug": "b", "priority": 4, "upgrade": {"model": "b2"}}
        models[2] = {"slug": "c", "priority": 2, "upgrade": "c2"}
        self.assertEqual(catalog.tier_findings(tiers, models),
                         [("strong", "b", "superseded by b2"), ("standard", "c", "superseded by c2"),
                          ("standard", "c", "the catalog ranks it above the class before it")])
        self.assertEqual(catalog.tier_findings({"light": "d"}, models), [])

    def test_tiers_check_reports_stale_models_and_never_passes_a_missing_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = []
            with patch.object(harness, "codex_dir", return_value=Path(tmp)), patch.object(harness, "say", out.append), \
                    patch.object(harness, "load_config", return_value={"tiers": {"codex": {"strong": "old-sol"}}}):
                self.assertEqual(harness.main(["tiers", "check"]), 0)
                self.assertIn("unverified", out[-1])
                (Path(tmp) / "models_cache.json").write_text(json.dumps({"models": [
                    {"slug": "gpt-6-astra", "priority": 1}, {"slug": "old-sol", "priority": 4, "upgrade": "new-sol"},
                    {"slug": "gpt-5.6-terra", "priority": 7}, {"slug": "gpt-5.6-luna", "priority": 8}]}))
                self.assertEqual(harness.main(["tiers", "check"]), 1)
                self.assertEqual(out[-1], "codex: strong -> old-sol: superseded by new-sol")

    def test_catalog_uses_neutral_unique_source_ids(self):
        items = catalog.catalog(REPO)["primitives"]
        self.assertEqual(len(items), len({(x["kind"], x["id"]) for x in items}))
        self.assertTrue(all(x["source"].startswith("policy/hooks/" if x["kind"] == "hooks" else "primitives/")
                            for x in items))
        self.assertEqual(len([x for x in items if x["kind"] == "roles"]), 11)

    def test_custom_stance_switch_changes_both_instruction_projections(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "stances/feedback").mkdir(parents=True)
            for choice in ("direct", "gentle"):
                (root / "stances/feedback" / (choice + ".md")).write_text("Use " + choice + " feedback.\n")
            cfg = dict(CFG, primitive_roots=[temp], stances=dict(CFG["stances"], feedback="direct"))
            direct = catalog.resolve_stances(REPO, cfg)
            self.assertEqual(direct["feedback"].read_text(), "Use direct feedback.\n")
            self.assertIn("Use direct feedback.", harness.render_codex_agents(direct, None))
            cfg["stances"]["feedback"] = "gentle"
            gentle = catalog.resolve_stances(REPO, cfg)
            self.assertEqual(gentle["feedback"].read_text(), "Use gentle feedback.\n")
            self.assertIn("Use gentle feedback.", harness.render_codex_agents(gentle, None))
            (root / "constraints.json").write_text(json.dumps({"stances": [
                {"when": {"feedback": "gentle"}, "excludes": {"testing": CFG["stances"]["testing"]},
                 "reason": "fixture conflict"}]}))
            with self.assertRaisesRegex(ValueError, "fixture conflict"):
                catalog.resolve_stances(REPO, cfg)

    def test_unknown_and_traversal_choices_are_rejected(self):
        for name, variant in (("../escape", "x"), ("testing", "../x"), ("missing", "off")):
            cfg = dict(CFG, stances=dict(CFG["stances"], **{name: variant}))
            with self.assertRaises(ValueError):
                catalog.resolve_stances(REPO, cfg)

    def test_duplicate_custom_authority_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "stances/testing").mkdir(parents=True)
            (root / "stances/testing/off.md").write_text("different policy")
            with self.assertRaisesRegex(ValueError, "duplicate stance authority"):
                catalog.resolve_stances(REPO, dict(CFG, primitive_roots=[temp]))

    def test_project_selection_precedes_session_and_cannot_change_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project.json"
            project.write_text(json.dumps({"stances": {"testing": "off"}}))
            with patch.object(harness, "config_path", return_value=Path(temp) / "user.json"):
                env = {"HARNESS_PROJECT_CONFIG": str(project)}
                self.assertEqual(harness.load_config(env)["stances"]["testing"], "off")
                env["HARNESS_STANCE_TESTING"] = "required"
                self.assertEqual(harness.load_config(env)["stances"]["testing"], "required")
                project.write_text(json.dumps({"permissions": "bypass"}))
                with self.assertRaisesRegex(SystemExit, "selection keys only"):
                    harness.load_config(env)
