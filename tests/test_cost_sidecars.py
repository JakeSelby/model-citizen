# SPDX-License-Identifier: MIT
"""Unit tests for cost-variant sidecars: the schema, the `extends` chain and `posture: fixed`.

The resolver is loaded by path, the way every hook loads it. Custom variants are written into a
temporary primitive root, never into a real home.

Run: python3 -m unittest discover tests
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
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "claude" / "hooks"
COST = REPO / "primitives" / "stances" / "cost"
ROLES = REPO / "primitives" / "roles"

sys.path.insert(0, str(REPO / "lib"))
from harness_core import catalog  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness", loader))
loader.exec_module(harness)

spec = importlib.util.spec_from_file_location("harness_posture", HOOKS / "posture.py")
posture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(posture)


def sidecar(name):
    return json.loads((COST / (name + ".json")).read_text(encoding="utf-8"))


class CustomRoot(unittest.TestCase):
    """A primitive root outside the checkout, the way a user's own variant is discovered."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "primitives"
        (self.root / "stances" / "cost").mkdir(parents=True)

    def write(self, name, data, prose="# Cost stance: custom\n"):
        path = self.root / "stances" / "cost" / (name + ".json")
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        path.with_suffix(".md").write_text(prose, encoding="utf-8")
        return path

    def resolve(self, variant, strict=True, delegation="tiered"):
        stances = dict(posture.DEFAULT_STANCES, cost=variant, delegation=delegation)
        return posture.table_for(stances, {"primitive_roots": [str(self.root)]}, strict=strict)


class Extends(CustomRoot):
    def test_one_switch_overrides_and_every_other_value_is_inherited(self):
        self.write("careful", {"schema_version": 1, "extends": "balanced",
                               "switches": {"session_effort": "high"}})
        table = self.resolve("careful")
        base = self.resolve("balanced")
        self.assertEqual(table["switches"]["session_effort"], "high")
        self.assertEqual(base["switches"]["session_effort"], "medium")
        self.assertEqual({k: v for k, v in table["switches"].items() if k != "session_effort"},
                         {k: v for k, v in base["switches"].items() if k != "session_effort"})
        self.assertEqual(table["rows"], base["rows"])
        self.assertEqual(table["default_band"], base["default_band"])
        self.assertEqual([c["variant"] for c in table["extends_chain"]], ["careful", "balanced"])
        self.assertEqual(table["warnings"], [])

    def test_a_row_cell_merges_rather_than_replacing_the_row(self):
        self.write("careful", {"schema_version": 1, "extends": "balanced",
                               "rows": {"gatherer": {"effort": "medium"}}})
        row = self.resolve("careful")["rows"]["gatherer"]
        self.assertEqual(row["effort"], "medium")
        self.assertEqual(row["class"], sidecar("balanced")["rows"]["gatherer"]["class"])
        self.assertEqual(row["budget_output_tokens"],
                         sidecar("balanced")["rows"]["gatherer"]["budget_output_tokens"])

    def test_a_cycle_stops_resolution_and_warns_instead_of_hanging(self):
        self.write("ping", {"schema_version": 1, "extends": "pong"})
        self.write("pong", {"schema_version": 1, "extends": "ping"})
        table = self.resolve("ping")
        self.assertEqual([c["variant"] for c in table["extends_chain"]], ["ping", "pong"])
        self.assertTrue(any("cycle" in w for w in table["warnings"]), table["warnings"])

    def test_a_chain_longer_than_the_cap_stops_at_the_cap(self):
        names = ["deep%d" % n for n in range(posture.MAX_EXTENDS_DEPTH + 2)]
        for name, parent in zip(names, names[1:] + [None]):
            self.write(name, {"schema_version": 1, "extends": parent})
        table = self.resolve(names[0])
        self.assertEqual(len(table["extends_chain"]), posture.MAX_EXTENDS_DEPTH)
        self.assertTrue(any("deeper than" in w for w in table["warnings"]), table["warnings"])

    def test_a_variant_with_no_sidecar_resolves_to_the_base_variants_table(self):
        (self.root / "stances" / "cost" / "prose-only.md").write_text("# prose\n", encoding="utf-8")
        table = self.resolve("prose-only")
        self.assertEqual(table["cost_variant"], "prose-only")
        self.assertEqual([c["variant"] for c in table["extends_chain"]],
                         [posture.BASE_COST_VARIANT])
        self.assertEqual(table["rows"], self.resolve("balanced")["rows"])


class Schema(CustomRoot):
    def test_an_unknown_key_warns_and_is_ignored_rather_than_failing(self):
        self.write("odd", {"schema_version": 1, "extends": "balanced", "invented": True,
                           "switches": {"invented_switch": 1},
                           "rows": {"gatherer": {"invented_cell": 1, "effort": "medium"}}})
        for strict in (True, False):
            with self.subTest(strict=strict):
                table = self.resolve("odd", strict=strict)
                self.assertEqual(len(table["warnings"]), 3, table["warnings"])
                self.assertTrue(all("unknown" in w for w in table["warnings"]), table["warnings"])
                self.assertEqual(table["rows"]["gatherer"]["effort"], "medium")

    def test_a_sidecar_that_is_not_readable_json_is_strict_only(self):
        for body in ("{not json", '"a string"', "[]"):
            with self.subTest(body=body):
                self.write("broken", body)
                with self.assertRaises(ValueError):
                    self.resolve("broken")
                table = self.resolve("broken", strict=False)
                self.assertEqual([c["variant"] for c in table["extends_chain"]],
                                 [posture.BASE_COST_VARIANT])
                self.assertTrue(table["warnings"])

    def test_a_row_may_not_reach_the_top_class_or_pass_high_effort(self):
        self.write("greedy", {"schema_version": 1, "extends": "balanced",
                              "rows": {"builder": {"class": "frontier", "effort": "maximum"}}})
        table = self.resolve("greedy")
        self.assertEqual(len(table["warnings"]), 2, table["warnings"])
        self.assertEqual(table["rows"]["builder"]["class"],
                         sidecar("balanced")["rows"]["builder"]["class"])
        self.assertEqual(table["rows"]["builder"]["effort"],
                         sidecar("balanced")["rows"]["builder"]["effort"])

    def test_the_same_cells_are_lint_findings_for_a_shipped_sidecar(self):
        data = {"schema_version": 1, "rows": {"builder": {"class": "frontier", "effort": "maximum"}}}
        findings = posture.validate_sidecar(data)[1]
        self.assertEqual(len(findings), 2, findings)
        self.assertTrue(all("builder" in f for f in findings), findings)

    def test_class_applies_only_when_delegation_resolves_to_tiered(self):
        for variant, applies in (("tiered", True), ("session-model", False), ("off", False)):
            with self.subTest(delegation=variant):
                table = self.resolve("balanced", delegation=variant)
                self.assertIs(table["class_applies"], applies)
                # The class is still reported; whether it applies is the reader's business.
                self.assertEqual(table["rows"]["builder"]["class"], "strong")


class Budgets(CustomRoot):
    def test_the_multiplier_scales_both_figures_and_the_base_is_still_reported(self):
        self.write("half", {"schema_version": 1, "extends": "balanced",
                            "switches": {"budget_multiplier": 0.5}})
        row = self.resolve("half")["rows"]["gatherer"]
        base = sidecar("balanced")["rows"]["gatherer"]
        self.assertEqual(row["base_budget_output_tokens"], base["budget_output_tokens"])
        self.assertEqual(row["budget_output_tokens"], 4300)  # 4250 rounds to the nearer 100
        self.assertEqual(row["budget_tool_calls"], 8)  # 7.5 tool calls rounds up to a whole one
        self.assertEqual(row["base_budget_tool_calls"], base["budget_tool_calls"])

    def test_an_unbudgeted_row_stays_unbudgeted_under_any_multiplier(self):
        self.write("half", {"schema_version": 1, "extends": "balanced",
                            "switches": {"budget_multiplier": 0.5}})
        row = self.resolve("half")["rows"]["planner"]
        self.assertIsNone(row["budget_output_tokens"])
        self.assertIsNone(row["base_budget_output_tokens"])

    def test_the_shipped_frugal_multiplier_is_applied_to_every_shipped_row(self):
        frugal = posture.table_for(dict(posture.DEFAULT_STANCES, cost="frugal"))
        multiplier = sidecar("frugal")["switches"]["budget_multiplier"]
        for name, row in frugal["rows"].items():
            with self.subTest(row=name):
                if row["base_budget_output_tokens"] is None:
                    self.assertIsNone(row["budget_output_tokens"])
                    continue
                self.assertEqual(row["budget_output_tokens"],
                                 round(row["base_budget_output_tokens"] * multiplier / 100) * 100)


class FixedRoles(unittest.TestCase):
    def test_the_fixed_roles_are_the_ones_whose_frontmatter_says_so(self):
        self.assertEqual(posture.fixed_roles(),
                         {"reviewer", "spec-reviewer", "design-judge", "log-compressor"})

    def test_a_fixed_role_keeps_its_own_class_and_effort_and_takes_the_budgets(self):
        rows = posture.table_for(dict(posture.DEFAULT_STANCES, cost="balanced"))["rows"]
        self.assertEqual(rows["reviewer"]["posture"], "fixed")
        self.assertIsNone(rows["reviewer"]["class"])
        self.assertIsNone(rows["reviewer"]["effort"])
        self.assertEqual(rows["reviewer"]["budget_output_tokens"],
                         sidecar("balanced")["rows"]["reviewer"]["budget_output_tokens"])
        self.assertEqual(rows["reviewer"]["budget_tool_calls"],
                         sidecar("balanced")["rows"]["reviewer"]["budget_tool_calls"])
        self.assertNotIn("posture", rows["builder"])
        self.assertEqual(rows["builder"]["class"], "strong")

    def test_the_frontmatter_field_is_validated_and_projections_do_not_carry_it(self):
        for name in sorted(p.stem for p in ROLES.glob("*.md")):
            fields, _ = catalog.role_contract(REPO, name)
            with self.subTest(role=name):
                self.assertIn(fields.get("posture", "fixed"), ("fixed",))
                self.assertNotIn("posture:", catalog.role_projection(REPO, "claude-code",
                                                                    ROLES / (name + ".md")))

    def test_an_unsupported_posture_value_is_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "primitives" / "roles").mkdir(parents=True)
        text = (ROLES / "reviewer.md").read_text(encoding="utf-8").replace(
            "posture: fixed", "posture: flexible")
        (root / "primitives" / "roles" / "reviewer.md").write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
            catalog.role_contract(root, "reviewer")


class ShippedValues(unittest.TestCase):
    def test_every_shipped_variant_has_a_sidecar_and_every_sidecar_has_prose(self):
        self.assertEqual(sorted(p.stem for p in COST.glob("*.json")),
                         sorted(p.stem for p in COST.glob("*.md")))

    def test_the_base_variant_reproduces_todays_class_and_effort_for_every_role_it_names(self):
        # A role with no row is unbudgeted and takes its own class and effort, which is what a
        # role added after this variant was written does until somebody measures it.
        rows = sidecar("balanced")["rows"]
        efforts = json.loads((REPO / "adapters" / "claude-code" / "bindings.json")
                             .read_text(encoding="utf-8"))["roles"]
        roles = sorted(p.stem for p in ROLES.glob("*.md"))
        self.assertTrue(set(rows) - set(posture.BANDS) <= set(roles), sorted(rows))
        for name in roles:
            fields, _ = catalog.role_contract(REPO, name)
            if name not in rows:
                continue
            with self.subTest(role=name):
                row = rows[name]
                if fields["tier"] == catalog.TIER_CLASSES[0]:
                    # A row may never name the top class, so the role's own tier is all there is.
                    self.assertNotIn("class", row)
                    self.assertNotIn("effort", row)
                    continue
                self.assertEqual(row["class"], fields["tier"])
                self.assertEqual(row["effort"], efforts[name]["effort"])

    def test_the_designer_takes_no_row_and_no_fixed_marking(self):
        self.assertNotIn("designer", sidecar("balanced")["rows"])
        self.assertNotIn("designer", posture.fixed_roles())

    def test_the_shipped_sidecars_are_valid_against_the_schema(self):
        roles = posture.role_catalog()[0]
        for path in sorted(COST.glob("*.json")):
            with self.subTest(variant=path.stem):
                self.assertEqual(posture.validate_sidecar(sidecar(path.stem), roles)[1], [])

    def test_the_prose_files_did_not_grow(self):
        for path in sorted(COST.glob("*.md")):
            with self.subTest(variant=path.stem):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 8)


class LintAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "claude" / "hooks").mkdir(parents=True)
        shutil.copy2(HOOKS / "posture.py", self.root / "claude" / "hooks" / "posture.py")
        self.cost = self.root / "primitives" / "stances" / "cost"
        self.cost.mkdir(parents=True)

    def variant(self, prose, data):
        (self.cost / "thrifty.md").write_text(prose, encoding="utf-8")
        (self.cost / "thrifty.json").write_text(json.dumps(data), encoding="utf-8")

    def test_prose_and_data_must_agree_on_the_switches_a_reader_can_check(self):
        self.variant("Session effort runs low, fan-out stays at three.\n",
                     {"schema_version": 1, "switches": {"session_effort": "low",
                                                        "max_parallel": 3}})
        self.assertEqual(harness.check_cost_sidecars(self.root), [])
        self.variant("Session effort runs low, fan-out stays at three.\n",
                     {"schema_version": 1, "switches": {"session_effort": "high",
                                                        "max_parallel": 9}})
        hits = harness.check_cost_sidecars(self.root)
        self.assertEqual(len(hits), 2, hits)
        self.assertTrue(any("session_effort" in h for h in hits), hits)
        self.assertTrue(any("max_parallel" in h for h in hits), hits)

    def test_a_null_width_needs_no_number_in_the_prose(self):
        self.variant("Fan-out is as wide as the task needs; effort stays at the default.\n",
                     {"schema_version": 1, "switches": {"session_effort": "default",
                                                        "max_parallel": None}})
        self.assertEqual(harness.check_cost_sidecars(self.root), [])

    def test_lint_reports_an_unknown_key_in_a_shipped_sidecar(self):
        self.variant("Session effort runs low.\n",
                     {"schema_version": 1, "switches": {"session_effort": "low"}, "invented": 1})
        hits = harness.check_cost_sidecars(self.root)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("unknown key 'invented'", hits[0])

    def test_lint_reports_a_row_naming_neither_a_band_nor_a_role(self):
        (self.root / "primitives" / "roles").mkdir(parents=True)
        shutil.copy2(ROLES / "gatherer.md", self.root / "primitives" / "roles" / "gatherer.md")
        self.variant("Session effort runs low.\n",
                     {"schema_version": 1, "switches": {"session_effort": "low"},
                      "rows": {"gatherer": {"effort": "low"}, "gatherers": {"effort": "low"}}})
        hits = harness.check_cost_sidecars(self.root)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("'gatherers' names no role and no band", hits[0])

    def test_lint_reports_an_unusable_number_in_a_shipped_sidecar(self):
        self.variant("Session effort runs low.\n",
                     {"schema_version": 1, "switches": {"session_effort": "low",
                                                        "budget_multiplier": float("inf")}})
        hits = harness.check_cost_sidecars(self.root)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("budget_multiplier", hits[0])

    def test_a_schema_version_this_release_does_not_read_is_a_lint_finding(self):
        self.variant("Session effort runs low.\n",
                     {"schema_version": 2, "switches": {"session_effort": "low"}})
        hits = harness.check_cost_sidecars(self.root)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("schema_version", hits[0])

    def test_the_shipped_tree_passes_the_sidecar_lint(self):
        self.assertEqual(harness.check_cost_sidecars(REPO), [])

    def test_the_stances_command_carries_the_resolved_table(self):
        out = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "stances", "--json"],
                             capture_output=True, text=True, cwd=str(self.root),
                             env={"HOME": str(self.root), "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 0, out.stderr)
        cost = json.loads(out.stdout)["cost"]
        self.assertEqual(cost["cost_variant"], "balanced")
        self.assertEqual(cost["default_band"], sidecar("balanced")["default_band"])
        self.assertEqual(cost["warnings"], [])
        self.assertEqual(cost["extends_chain"][0]["source"], str(COST / "balanced.json"))
        self.assertEqual(cost["rows"]["gatherer"]["budget_output_tokens"],
                         sidecar("balanced")["rows"]["gatherer"]["budget_output_tokens"])


class NamesAndPaths(CustomRoot):
    """A variant name comes from a config file or the environment, so it is never a path."""

    def test_a_traversing_variant_name_reads_nothing_and_falls_back(self):
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text(json.dumps({"schema_version": 1, "switches": {"max_parallel": 99}}),
                           encoding="utf-8")
        for name in ("../../outside", "/etc/passwd", "cost/../../outside", "Balanced", "b a d"):
            with self.subTest(name=name):
                table = self.resolve(name)
                self.assertEqual([c["variant"] for c in table["extends_chain"]],
                                 [posture.BASE_COST_VARIANT])
                self.assertEqual(table["switches"]["max_parallel"], 6)
                self.assertTrue(table["warnings"])
        self.assertIsNone(posture.sidecar_path("../../outside",
                                               [self.root / "stances"]))

    def test_a_sidecar_symlinked_out_of_its_root_is_not_read(self):
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text(json.dumps({"schema_version": 1, "switches": {"max_parallel": 99}}),
                           encoding="utf-8")
        link = self.root / "stances" / "cost" / "escape.json"
        link.symlink_to(outside)
        link.with_suffix(".md").write_text("# escape\n", encoding="utf-8")
        self.assertIsNone(posture.sidecar_path("escape", [self.root / "stances"]))
        table = self.resolve("escape")
        self.assertEqual(table["switches"]["max_parallel"], 6)
        self.assertTrue(any("no sidecar" in w for w in table["warnings"]), table["warnings"])

    def test_an_extends_link_to_a_variant_with_no_sidecar_resolves_to_the_base(self):
        self.write("orphan", {"schema_version": 1, "extends": "absent",
                              "switches": {"session_effort": "high"}})
        table = self.resolve("orphan")
        self.assertEqual([c["variant"] for c in table["extends_chain"]],
                         ["orphan", posture.BASE_COST_VARIANT])
        self.assertEqual(table["switches"]["session_effort"], "high")
        self.assertEqual(table["rows"]["builder"]["budget_output_tokens"], 135000)
        self.assertTrue(any("absent" in w for w in table["warnings"]), table["warnings"])

    def test_a_schema_version_this_release_does_not_read_falls_back_to_the_base(self):
        self.write("future", {"schema_version": 2, "switches": {"max_parallel": 99}})
        table = self.resolve("future")
        self.assertEqual([c["variant"] for c in table["extends_chain"]],
                         [posture.BASE_COST_VARIANT])
        self.assertEqual(table["switches"]["max_parallel"], 6)
        self.assertTrue(any("schema_version" in w for w in table["warnings"]), table["warnings"])


class Numbers(CustomRoot):
    """No number from a file reaches the arithmetic without being finite and in range."""

    def unusable(self, switches=None, rows=None):
        data = {"schema_version": 1, "extends": "balanced"}
        if switches:
            data["switches"] = switches
        if rows:
            data["rows"] = rows
        self.write("odd", data)
        return self.resolve("odd")

    def test_a_multiplier_that_is_not_a_finite_number_warns_and_is_dropped(self):
        for value in (float("inf"), float("nan"), 1e308, 0, -1, True, "2", None):
            with self.subTest(multiplier=value):
                table = self.unusable({"budget_multiplier": value})
                self.assertEqual(len(table["warnings"]), 1, table["warnings"])
                self.assertIn("budget_multiplier", table["warnings"][0])
                # The base variant's multiplier still applies; nothing overflowed.
                self.assertEqual(table["rows"]["gatherer"]["budget_output_tokens"], 8500)

    def test_an_out_of_range_budget_or_nudge_list_warns_and_is_dropped(self):
        table = self.unusable({"nudge_at": [1.0, float("inf")]})
        self.assertIn("nudge_at", table["warnings"][0])
        table = self.unusable({"nudge_at": [1.0] * (posture.MAX_NUDGES + 1)})
        self.assertIn("nudge_at", table["warnings"][0])
        table = self.unusable(rows={"gatherer": {"budget_output_tokens": 10 ** 12}})
        self.assertIn("budget_output_tokens", table["warnings"][0])
        self.assertEqual(table["rows"]["gatherer"]["base_budget_output_tokens"], 8500)

    def test_a_large_but_usable_multiplier_still_resolves(self):
        table = self.unusable({"budget_multiplier": posture.MAX_MULTIPLIER})
        self.assertEqual(table["warnings"], [])
        self.assertEqual(table["rows"]["gatherer"]["budget_output_tokens"], 850000)


class FrontmatterSpacing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def role(self, directory, name, line):
        directory.mkdir(parents=True, exist_ok=True)
        text = (ROLES / "reviewer.md").read_text(encoding="utf-8").replace(
            "name: reviewer", "name: " + name).replace("posture: fixed", line)
        (directory / (name + ".md")).write_text(text, encoding="utf-8")

    def test_whitespace_around_the_value_does_not_lose_the_protection(self):
        self.role(self.root / "primitives" / "roles", "padded", "posture:   fixed  ")
        self.assertIn("padded", posture.fixed_roles(root=self.root))
        # The library accepts the same file, so the two cannot disagree about one role.
        self.assertEqual(catalog.role_contract(self.root, "padded")[0]["posture"], "fixed")

    def test_a_role_from_a_user_primitive_root_can_be_fixed_too(self):
        custom = self.root / "custom"
        self.role(custom / "roles", "auditor", "posture: fixed")
        names, fixed = posture.role_catalog({"primitive_roots": [str(custom)]}, self.root)
        self.assertIn("auditor", names)
        self.assertIn("auditor", fixed)
        self.assertNotIn("auditor", posture.role_catalog(None, self.root)[0])


class OffTheHotPath(unittest.TestCase):
    """The cost table is opt-in: a hook that never asked for it must never pay for it."""

    PAYLOADS = {
        "usage-log.py": {"hook_event_name": "SessionEnd", "session_id": "s1",
                         "transcript_path": "", "cwd": "."},
        "tier-agent-spawns.py": {"tool_name": "Agent", "tool_input": {"prompt": "do a thing"}},
        "brief-guard.py": {"tool_name": "Agent", "tool_input": {"prompt": "do a thing"}},
        "grade-bash.py": {"tool_name": "Bash", "cwd": ".", "permission_mode": "default",
                          "tool_input": {"command": "gh pr create --fill"}},
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.hooks = Path(self.tmp.name) / "policy" / "hooks"
        self.home.mkdir()
        shutil.copytree(HOOKS.resolve(), self.hooks)
        self.marker = Path(self.tmp.name) / "built"
        # A shim over the table builder, so the assertion is what the hook ran, not what it says.
        with (self.hooks / "posture.py").open("a", encoding="utf-8") as handle:
            handle.write("\n\n_real_table_for = table_for\n\n\n"
                         "def table_for(*args, **kwargs):\n"
                         "    Path(os.environ['HARNESS_TABLE_MARKER']).write_text('built')\n"
                         "    return _real_table_for(*args, **kwargs)\n")

    def run_hook(self, name):
        env = without_harness_vars()
        env.update(HOME=str(self.home), HARNESS_TABLE_MARKER=str(self.marker))
        out = subprocess.run([sys.executable, str(self.hooks / name)],
                             input=json.dumps(self.PAYLOADS[name]), capture_output=True,
                             text=True, env=env, cwd=str(self.home))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_no_hot_path_hook_builds_the_cost_table(self):
        for name in sorted(self.PAYLOADS):
            with self.subTest(hook=name):
                self.run_hook(name)
                self.assertFalse(self.marker.exists(),
                                 name + " built the cost table it does not read")

    def test_the_shim_would_have_caught_a_hook_that_did(self):
        spec = importlib.util.spec_from_file_location("shimmed", self.hooks / "posture.py")
        shimmed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shimmed)
        os.environ["HARNESS_TABLE_MARKER"] = str(self.marker)
        self.addCleanup(os.environ.pop, "HARNESS_TABLE_MARKER", None)
        shimmed.cost_table(env={"HOME": str(self.home)})
        self.assertTrue(self.marker.exists())

    def test_resolve_returns_stances_only_unless_the_table_is_asked_for(self):
        env = {"HOME": str(self.home)}
        self.assertEqual(set(posture.resolve(env)), {"stances"})
        self.assertIn("rows", posture.resolve(env, table=True))
        self.assertIn("rows", posture.cost_table(env))


class UnreadableCustomSidecar(unittest.TestCase):
    """A cost sidecar nobody can read is a warning on the report, never a traceback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        cost = self.home / "primitives" / "stances" / "cost"
        cost.mkdir(parents=True)
        (cost / "thrifty.md").write_text("# Cost stance: thrifty\n", encoding="utf-8")
        (cost / "thrifty.json").write_text("{not json", encoding="utf-8")
        config = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
        config["stances"]["cost"] = "thrifty"
        config["primitive_roots"] = [str(self.home / "primitives")]
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def test_the_stances_command_reports_it_and_still_exits_clean(self):
        out = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "stances", "--json"],
                             capture_output=True, text=True, cwd=str(self.home),
                             env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual(out.returncode, 0, out.stderr)
        cost = json.loads(out.stdout)["cost"]
        self.assertTrue(any("not readable JSON" in w for w in cost["warnings"]), cost["warnings"])
        self.assertEqual([c["variant"] for c in cost["extends_chain"]], ["balanced"])
        plain = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "stances"],
                               capture_output=True, text=True, cwd=str(self.home),
                               env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("cost warnings: 1", plain.stdout)


if __name__ == "__main__":
    unittest.main()
