# SPDX-License-Identifier: MIT
"""No generated role resolves to the frontier class unless its contract documents why.

`delegation/tiered` says never `frontier`. A role reaches it either by declaring `tier: frontier`
or by declaring a class its adapter leaves unmapped, which resolves upward. Either way the
generated agent would run on the frontier model, so the check reads the resolved model, not the
declared class. The one way through is a `frontier_exception: <reason>` line in the role's
contract; no shipped role carries one.

Run: python3 -m unittest discover -s tests -p "test_role_frontier_tier.py"
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO
from harness_core import catalog

RUNTIMES = ("claude-code", "codex")


def frontier_roles(root):
    """(role, runtime) for every role whose generated binding lands on the frontier model."""
    hits = []
    for path in sorted((root / "primitives" / "roles").glob("*.md")):
        fields, _ = catalog.role_contract(root, path.stem)
        if str(fields.get("frontier_exception", "")).strip():
            continue
        for runtime in RUNTIMES:
            _, tiers = catalog.adapter_tiers(root, runtime)
            top = tiers.get(catalog.TIER_CLASSES[0])
            if top and catalog.role_binding(root, runtime, fields).get("model") == top:
                hits.append((path.stem, runtime))
    return hits


class ShippedRoles(unittest.TestCase):
    def test_no_shipped_role_resolves_to_the_frontier_class(self):
        self.assertEqual(frontier_roles(REPO), [])

    def test_no_shipped_role_records_a_frontier_exception(self):
        for path in (REPO / "primitives" / "roles").glob("*.md"):
            self.assertNotIn("frontier_exception", catalog.frontmatter(path)[0], path.stem)

    def test_no_generated_claude_agent_names_the_frontier_model(self):
        top = catalog.adapter_tiers(REPO, "claude-code")[1]["frontier"]
        for path in (REPO / "claude" / "agents").glob("*.md"):
            fields, _ = catalog.frontmatter(path)
            self.assertNotEqual(fields.get("model"), top, path.name)

    def test_the_design_roles_run_on_strong_at_high_effort(self):
        for name in ("designer", "design-judge"):
            fields, _ = catalog.frontmatter(REPO / "claude" / "agents" / (name + ".md"))
            self.assertEqual(fields["model"], catalog.adapter_tiers(REPO, "claude-code")[1]["strong"])
            self.assertEqual(fields["effort"], "high")


class SyntheticRoles(unittest.TestCase):
    """A root of its own: the shipped adapters plus one role, `odd`, written per test."""

    def root(self, temp, header, unmap=None):
        root = Path(temp)
        shutil.copytree(REPO / "adapters", root / "adapters")
        roles = root / "primitives" / "roles"
        roles.mkdir(parents=True)
        (roles / "odd.md").write_text("---\nname: odd\n" + header + "authority: read-only\ncontext: fresh\n"
                                      "delegation: none\n---\n\nBody.\n")
        for runtime in RUNTIMES:
            path = root / "adapters" / runtime / "bindings.json"
            data = json.loads(path.read_text())
            data["roles"]["odd"] = dict(data["roles"]["reviewer"])
            if unmap:
                data["tiers"].pop(unmap)
            path.write_text(json.dumps(data))
        return root

    def test_a_frontier_role_without_an_exception_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp, "tier: frontier\n")
            self.assertEqual(frontier_roles(root), [("odd", "claude-code"), ("odd", "codex")])

    def test_a_strong_role_whose_class_is_unmapped_resolves_upward_and_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp, "tier: strong\n", unmap="strong")
            self.assertEqual(frontier_roles(root), [("odd", "claude-code"), ("odd", "codex")])

    def test_a_documented_frontier_exception_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp, "tier: frontier\nfrontier_exception: measured gain on this work\n")
            self.assertEqual(frontier_roles(root), [])

    def test_an_exception_must_state_a_reason_on_a_frontier_role(self):
        for header in ("tier: frontier\nfrontier_exception:\n", "tier: strong\nfrontier_exception: why\n"):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as temp:
                root = self.root(temp, header)
                with self.assertRaisesRegex(ValueError, "frontier_exception"):
                    catalog.role_contract(root, "odd")


if __name__ == "__main__":
    unittest.main()
