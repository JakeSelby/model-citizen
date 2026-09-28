# SPDX-License-Identifier: MIT
"""Studio qualification stays wired into release and platform surfaces."""
import json
import unittest
from pathlib import Path

from test_harness import REPO


class StudioReleaseQualificationTests(unittest.TestCase):
    def test_matrix_names_every_supported_tuple_and_its_evidence(self):
        matrix = json.loads((REPO / "compatibility/studio.json").read_text())
        self.assertEqual(matrix["schema_version"], 1)
        self.assertEqual({item["platform"] for item in matrix["supported"]}, {"macos", "linux"})
        for item in matrix["supported"]:
            self.assertEqual(item["browser"], "chrome")
            self.assertEqual(item["version_policy"], "stable-channel")
            self.assertTrue((REPO / item["flow_evidence"]).is_file())
            self.assertIn("studio-{platform}-{version}.json", item["release_evidence"])

    def test_both_platform_jobs_run_the_blocking_lifecycle(self):
        workflow = (REPO / ".github/workflows/studio-qualification.yml").read_text()
        self.assertIn("[ubuntu-latest, macos-15-intel]", workflow)
        self.assertIn("scripts/studio_lifecycle_acceptance.py", workflow)
        self.assertNotIn("continue-on-error", workflow)

    def test_release_preflight_rebuilds_the_bundle(self):
        preflight = (REPO / "scripts/release_preflight.py").read_text()
        self.assertIn("studio_bundle_manifest.verify_committed", preflight)
        self.assertIn("Studio committed bundle differs from source", preflight)

    def test_tag_release_provisions_the_exact_node_and_npm_pins_before_preflight(self):
        workflow = (REPO / ".github/workflows/release.yml").read_text()
        setup = workflow.index("actions/setup-node@")
        version = workflow.index("node-version-file: .node-version", setup)
        npm = workflow.index("Install pinned npm for Studio preflight", version)
        preflight = workflow.index("python3 scripts/release_preflight.py", npm)
        self.assertLess(setup, version)
        self.assertLess(version, npm)
        self.assertLess(npm, preflight)


if __name__ == "__main__":
    unittest.main()
