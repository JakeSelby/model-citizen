# SPDX-License-Identifier: MIT
"""Unit tests for the shipped stance constraints and the conflict engine that reads them.

The frontier rule is the fixture: `delegation/tiered` says never to reach the frontier class, so
`primitives/constraints.json` reports any role declaring `tier: frontier` unless its contract
records a `frontier_exception` reason. No shipped role declares it, so synthetic roles stand in.

Synthetic constraints are written into temporary roots, never into a real primitive root.

Run: python3 -m unittest discover tests
"""
import copy
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONSTRAINTS = REPO / "primitives" / "constraints.json"
ROLES = REPO / "primitives" / "roles"

sys.path.insert(0, str(REPO / "lib"))
from harness_core import catalog  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness", loader))
loader.exec_module(harness)

DEFAULTS = json.loads((REPO / "config.example.json").read_text())["stances"]


def config(**stances):
    return {"stances": dict(DEFAULTS, **stances), "primitive_roots": []}


def shipped():
    return json.loads(CONSTRAINTS.read_text())


class ShippedConstraintsTests(unittest.TestCase):
    def test_the_shipped_file_loads_validates_and_states_three_constraints(self):
        rules = catalog.stance_constraints(REPO, config())
        self.assertGreaterEqual(len(rules), 3)
        for path, rule in rules:
            self.assertEqual(path, CONSTRAINTS)
            self.assertTrue(rule["reason"].strip().endswith("."))
            self.assertLessEqual(set(rule), catalog.CONSTRAINT_KEYS)

    def test_every_constraint_names_a_real_dimension_and_variant(self):
        for _, rule in catalog.stance_constraints(REPO, config()):
            for section in ("when", "requires", "excludes"):
                for dimension, variant in rule.get(section, {}).items():
                    path = REPO / "primitives" / "stances" / dimension / (variant + ".md")
                    self.assertTrue(path.is_file(), f"{section} names {dimension}/{variant}")

    def test_the_default_selection_violates_nothing(self):
        self.assertEqual(catalog.stance_conflicts(REPO, config()), [])
        self.assertEqual(harness.check_stance_constraints(REPO), [])


class FrontierRoleFixtureTests(unittest.TestCase):
    """The delegation/frontier rule, which only a recorded `frontier_exception` settles."""

    def frontier_rule(self):
        rules = [r for _, r in catalog.stance_constraints(REPO, config()) if "excludes_roles" in r]
        self.assertEqual(len(rules), 1)
        return rules[0]

    def roles_root(self, temp, **headers):
        """A root whose only roles are `headers`: name -> the frontmatter lines after `name:`."""
        roles = Path(temp) / "primitives" / "roles"
        roles.mkdir(parents=True)
        for name, lines in headers.items():
            (roles / (name + ".md")).write_text("---\nname: " + name + "\n" + lines + "---\n\nBody.\n")
        return Path(temp)

    def test_no_shipped_role_declares_frontier_and_none_is_exempted_by_name(self):
        declared = sorted(p.stem for p in ROLES.glob("*.md")
                          if catalog.frontmatter(p)[0].get("tier") == "frontier")
        self.assertEqual(declared, [])
        self.assertEqual(self.frontier_rule()["excludes_roles"], {"tier": "frontier"})
        self.assertEqual(catalog.excluded_roles(REPO, self.frontier_rule()["excludes_roles"]), [])

    def test_a_frontier_role_without_an_exception_is_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.roles_root(temp, plain="tier: frontier\n", blank="tier: frontier\nfrontier_exception:\n",
                                   other="tier: strong\n")
            self.assertEqual(catalog.excluded_roles(root, self.frontier_rule()["excludes_roles"]), ["blank", "plain"])

    def test_a_recorded_frontier_exception_exempts_the_role(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.roles_root(temp, special="tier: frontier\nfrontier_exception: measured win on X\n")
            self.assertEqual(catalog.excluded_roles(root, self.frontier_rule()["excludes_roles"]), [])
            # The exception is about the frontier class only; another condition still matches.
            self.assertEqual(catalog.excluded_roles(root, {"name": "special"}), ["special"])

    def test_an_excluded_role_is_reported_with_the_reason(self):
        rule = copy.deepcopy(self.frontier_rule())
        rule["excludes_roles"] = {"name": "designer"}
        with tempfile.TemporaryDirectory() as temp:
            conflicts = self.conflicts_for(Path(temp), rule)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("delegation: tiered excludes the role designer", conflicts[0])
        self.assertIn(rule["reason"], conflicts[0])

    def test_the_exception_does_not_widen_to_every_role(self):
        rule = copy.deepcopy(self.frontier_rule())
        rule["excludes_roles"] = {"authority": "workspace-write"}
        with tempfile.TemporaryDirectory() as temp:
            names = [c.split(" excludes the role ")[1].split(" — ")[0]
                     for c in self.conflicts_for(Path(temp), rule)]
        self.assertIn("builder", names)
        self.assertNotIn("planner", names)

    def test_excludes_roles_without_a_field_is_an_authoring_error(self):
        with self.assertRaisesRegex(ValueError, "at least one frontmatter field"):
            catalog.excluded_roles(REPO, {"allow": ["designer"]})

    def conflicts_for(self, root, rule):
        """Evaluate one rule against the shipped roles, from a primitive root of its own."""
        (root / "stances").mkdir()
        (root / "constraints.json").write_text(json.dumps({"stances": [rule]}))
        cfg = dict(config(), primitive_roots=[str(root)])
        return [c for c in catalog.stance_conflicts(REPO, cfg) if "the role" in c]


class SyntheticViolationTests(unittest.TestCase):
    def rule(self):
        return {"when": {"testing": DEFAULTS["testing"]}, "excludes": {"voice": DEFAULTS["voice"]},
                "reason": "Fixture: these two cannot both hold."}

    def test_a_violation_is_reported_with_its_reason_and_refused_by_the_resolver(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "stances").mkdir()
            (root / "constraints.json").write_text(json.dumps({"stances": [self.rule()]}))
            cfg = dict(config(), primitive_roots=[temp])
            conflicts = catalog.stance_conflicts(REPO, cfg)
            self.assertEqual(len(conflicts), 1)
            self.assertIn(self.rule()["reason"], conflicts[0])
            with self.assertRaisesRegex(ValueError, "Fixture: these two cannot both hold"):
                catalog.resolve_stances(REPO, cfg)
            self.assertEqual(set(catalog.resolve_stances(REPO, cfg, strict=False)), set(DEFAULTS))

    def test_lint_reports_a_violated_constraint_as_a_finding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "primitives").mkdir()
            (root / "config.example.json").write_text(json.dumps({"stances": DEFAULTS}))
            (root / "primitives" / "constraints.json").write_text(
                json.dumps({"stances": [self.rule()]}))
            hits = harness.check_stance_constraints(root)
        self.assertTrue(hits[0].startswith("stance-constraints: the default selection violates"))
        self.assertIn(self.rule()["reason"], hits[0])
        self.assertIn("no longer holds", hits[-1])

    def test_lint_reports_an_unreadable_or_underspecified_constraint(self):
        for rule, expected in (({"reason": "no when"}, "nonempty when selection"),
                               ({"when": {"testing": "off"}}, "states its reason"),
                               ({"when": {"testing": "off"}, "reason": "r"}, "rules something out"),
                               ({"when": {"testing": "off"}, "reason": "r", "forbids": {}},
                                "unknown stance constraint field")):
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "primitives").mkdir()
                (root / "config.example.json").write_text(json.dumps({"stances": DEFAULTS}))
                (root / "primitives" / "constraints.json").write_text(
                    json.dumps({"stances": [rule]}))
                hits = harness.check_stance_constraints(root)
                self.assertEqual(len(hits), 1)
                self.assertIn(expected, hits[0])


class CommandSurfaceTests(unittest.TestCase):
    def run_stances(self, **stances):
        env = dict(os.environ, HARNESS_QUIET="")
        env.pop("HARNESS_PROJECT_CONFIG", None)
        for name, variant in stances.items():
            env["HARNESS_STANCE_" + name.upper().replace("-", "_")] = variant
        out = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "stances", "--json"],
                             capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_stances_json_carries_no_conflict_for_the_shipped_defaults(self):
        self.assertEqual(self.run_stances(**DEFAULTS)["conflicts"], [])

    def test_stances_json_reports_a_violated_constraint_with_its_reason(self):
        reasons = [r["reason"] for r in shipped()["stances"]
                   if r.get("when") == {"cost": "max"}]
        self.assertEqual(len(reasons), 1)
        data = self.run_stances(**dict(DEFAULTS, cost="max", delegation="off"))
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertIn(reasons[0], data["conflicts"][0])
        self.assertEqual(data["stances"]["cost"]["variant"], "max")


if __name__ == "__main__":
    unittest.main()
