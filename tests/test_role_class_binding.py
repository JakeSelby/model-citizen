# SPDX-License-Identifier: MIT
"""Every role's class resolves to the model its adapter's `tiers` table maps that class to.

A role's class is its cost row's `class` under `delegation: tiered`, or its frontmatter `tier`
when the row is absent or the role is `posture: fixed`. `adapters/<runtime>/bindings.json` is
the one place a class becomes a native model, so the bound model must equal `tiers[class]` for
every shipped cost variant and both runtimes. An adapter that maps no model for the class is a
finding of its own: `native_model` would resolve it upward, or to nothing so the agent inherits
the session model, and neither should pass silently. `test_role_frontier_tier.py` covers the
frontier case; this covers every class.

Run: python3 -m unittest discover -s tests -p "test_role_class_binding.py"
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO
from harness_core import catalog

RUNTIMES = ("claude-code", "codex")


def variants(root):
    return sorted(p.stem for p in (root / "primitives" / "stances" / "cost").glob("*.json"))


def config_for(variant):
    return {"stances": {"cost": variant, "delegation": "tiered"}}


def role_class(root, table, fields):
    """The class a role is bound to under `table`: its cost row's, else its own tier."""
    row = catalog.cost_row(root, table, fields["name"]) or {}
    return row.get("class") if table.get("class_applies") and row.get("class") else fields["tier"]


def class_findings(root):
    """(variant, runtime, role, problem) for each role whose bound model is not its class's."""
    findings = []
    for variant in variants(root):
        config = config_for(variant)
        table = catalog.cost_table(root, config)
        for warning in table.get("warnings", []):
            findings.append((variant, None, None, "cost table warning: " + warning))
        for path in sorted((root / "primitives" / "roles").glob("*.md")):
            fields, _ = catalog.role_contract(root, path.stem)
            klass = role_class(root, table, fields)
            for runtime in RUNTIMES:
                tiers = catalog.adapter_tiers(root, runtime)[1]
                overrides = catalog.cost_overrides(root, config, table, runtime, path.stem)
                model = catalog.role_binding(root, runtime, fields, overrides).get("model")
                if klass not in tiers:
                    findings.append((variant, runtime, path.stem, "the adapter maps no %s class, so it "
                                     "resolves to %s" % (klass, model or "the session model")))
                elif model != tiers[klass]:
                    findings.append((variant, runtime, path.stem, "class %s maps to %s but the role is "
                                     "bound to %s" % (klass, tiers[klass], model or "the session model")))
    return findings


class ShippedRoles(unittest.TestCase):
    def test_every_role_resolves_to_its_class_model_in_every_variant(self):
        self.assertEqual(class_findings(REPO), [])

    def test_every_adapter_maps_every_class(self):
        for runtime in RUNTIMES:
            self.assertEqual(sorted(catalog.adapter_tiers(REPO, runtime)[1]), sorted(catalog.TIER_CLASSES),
                             runtime)

    def test_the_committed_claude_agents_carry_the_balanced_class_model(self):
        table = catalog.cost_table(REPO, config_for("balanced"))
        tiers = catalog.adapter_tiers(REPO, "claude-code")[1]
        for path in sorted((REPO / "primitives" / "roles").glob("*.md")):
            fields, _ = catalog.role_contract(REPO, path.stem)
            agent, _ = catalog.frontmatter(REPO / "claude" / "agents" / path.name)
            self.assertEqual(agent.get("model"), tiers[role_class(REPO, table, fields)], path.stem)


class BrokenBindings(unittest.TestCase):
    """A copy of the shipped adapters, roles, stances and resolver, with one thing broken."""

    def root(self, temp, edit):
        root = Path(temp)
        for part in ("adapters", "primitives/roles", "primitives/stances", "primitives/skills", "policy/hooks"):
            shutil.copytree(REPO / part, root / part, symlinks=True)
        path = root / "adapters" / "claude-code" / "bindings.json"
        data = json.loads(path.read_text())
        edit(data)
        path.write_text(json.dumps(data))
        return root

    def roles_on(self, findings, problem):
        return sorted({(variant, role) for variant, runtime, role, text in findings
                       if runtime == "claude-code" and problem in text})

    def test_a_role_pinned_off_its_class_is_reported(self):
        def pin(data):
            # A cost row's class overrides an adapter pin, so the pin bites on a `posture: fixed` role.
            data["roles"]["reviewer"]["model"] = data["tiers"]["light"]
        with tempfile.TemporaryDirectory() as temp:
            findings = class_findings(self.root(temp, pin))
        self.assertEqual(self.roles_on(findings, "bound to haiku"),
                         [(variant, "reviewer") for variant in variants(REPO)])
        self.assertTrue(all(runtime == "claude-code" for _, runtime, _, _ in findings), findings)

    def test_an_unmapped_class_is_reported_with_the_model_it_falls_back_to(self):
        with tempfile.TemporaryDirectory() as temp:
            findings = class_findings(self.root(temp, lambda data: data["tiers"].pop("standard")))
        reported = self.roles_on(findings, "maps no standard class, so it resolves to opus")
        self.assertIn(("balanced", "spec-reviewer"), reported)
        self.assertIn(("frugal", "gatherer"), reported)
        self.assertNotIn(("balanced", "gatherer"), reported)

    def test_an_unmapped_class_with_nothing_above_it_reports_the_session_model(self):
        def empty(data):
            data["tiers"] = {"light": data["tiers"]["light"]}
        with tempfile.TemporaryDirectory() as temp:
            findings = class_findings(self.root(temp, empty))
        self.assertIn(("balanced", "builder"),
                      self.roles_on(findings, "maps no strong class, so it resolves to the session model"))


if __name__ == "__main__":
    unittest.main()
