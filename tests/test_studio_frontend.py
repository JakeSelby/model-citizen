# SPDX-License-Identifier: MIT
"""Committed Studio workspace and browser bundle contract tests."""

import json
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
STUDIO = REPO / "studio"


class StudioFrontendTests(unittest.TestCase):
    def test_bundle_is_committed_with_content_hashed_assets(self):
        html = (STUDIO / "dist" / "index.html").read_text(encoding="utf-8")
        references = re.findall(r'["\']\./(assets/[^"\']+)["\']', html)
        self.assertGreaterEqual(len(references), 2)
        for relative in references:
            self.assertRegex(Path(relative).name, r"-[A-Za-z0-9_-]{8}\.(css|js)$")
            self.assertTrue((STUDIO / "dist" / relative).is_file(), relative)

    def test_distributed_dependency_evidence_matches_public_source(self):
        for name in ("third-party.json", "THIRD_PARTY_NOTICES.txt"):
            self.assertEqual(
                (STUDIO / "dist" / name).read_bytes(),
                (STUDIO / "public" / name).read_bytes(),
            )
        inventory = json.loads((STUDIO / "dist" / "third-party.json").read_text(encoding="utf-8"))
        self.assertEqual(inventory["package_count"], len(inventory["packages"]))
        self.assertTrue(all(package["license"] for package in inventory["packages"]))

    def test_generated_bundle_is_marked_for_review_tools(self):
        attributes = (REPO / ".gitattributes").read_text(encoding="utf-8")
        reviewer = (REPO / ".coderabbit.yaml").read_text(encoding="utf-8")
        self.assertIn("studio/dist/** linguist-generated=true", attributes)
        self.assertIn('"!studio/dist/**"', reviewer)

    def test_operational_theme_uses_the_approved_semantic_tokens(self):
        theme = (STUDIO / "src" / "theme.ts").read_text(encoding="utf-8")
        styles = (STUDIO / "src" / "styles.css").read_text(encoding="utf-8")
        app = (STUDIO / "src" / "StudioApp.tsx").read_text(encoding="utf-8")
        for token in (
            "#FFFFFF", "#F8F9FA", "#212529", "#646C73", "#E9ECEF", "#868E96",
            "#087F5B", "#E6FCF5", "#237032", "#2B8A3E", "#EBFBEE", "#A85500",
            "#E67700", "#FFF4E6", "#C92A2A", "#FFF5F5", "#16181B", "#1F2226",
            "#E9ECEF", "#A6A7AB", "#2C2F34", "#7C8189", "#63E6BE", "#0F2A22",
            "#8CE99A", "#17301D", "#FFC078", "#33260F", "#FF8787", "#3A1A1A",
        ):
            self.assertIn(token, styles + theme)
        # The faces are named first and never bundled: the Studio makes no network requests.
        self.assertIn('const SANS = "Inter, system-ui, -apple-system', theme)
        self.assertIn('const MONO = "JetBrains Mono, ui-monospace', theme)
        self.assertNotRegex(styles + theme, r"@font-face|url\(|\.woff2?")
        self.assertFalse([path for path in (STUDIO / "dist").rglob("*")
                          if path.suffix in {".woff", ".woff2", ".ttf", ".otf"}])
        self.assertIn("<AppShell", app)
        self.assertIn("navbar={{ width: 220,", app)
        self.assertIn("header={{ height: 52 }}", app)
        self.assertNotIn("Nothing needs attention", app)
        self.assertNotIn("No active work", app)

    def test_dependabot_watches_the_pinned_workspace(self):
        dependabot = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "npm"', dependabot)
        self.assertIn('directory: "/studio"', dependabot)

    def test_node_version_has_one_exact_authority(self):
        node = (REPO / ".node-version").read_text(encoding="utf-8").strip()
        package = json.loads((STUDIO / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((STUDIO / "package-lock.json").read_text(encoding="utf-8"))
        workflow = (REPO / ".github" / "workflows" / "studio-bundle-repro.yml").read_text(
            encoding="utf-8"
        )

        self.assertRegex(node, r"^\d+\.\d+\.\d+$")
        self.assertNotIn("engines", package)
        self.assertNotIn("engines", lock["packages"][""])
        self.assertIn("node-version-file: .node-version", workflow)


if __name__ == "__main__":
    unittest.main()
