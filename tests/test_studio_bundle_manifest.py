# SPDX-License-Identifier: MIT
"""Tests for deterministic Studio bundle manifests."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "studio_bundle_manifest", REPO / "scripts" / "studio_bundle_manifest.py"
)
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        return target

    def manifest_file(self, name, data):
        target = self.root / name
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def evidence_file(self, packages=None):
        return self.manifest_file(
            "license-evidence.json", {"schema_version": 1, "packages": packages or []}
        )

    def test_manifest_is_canonical_and_path_sorted(self):
        self.write("z.txt", b"last")
        self.write("nested/a.txt", b"first")
        first = manifest.build_manifest(self.root)
        second = manifest.build_manifest(self.root)
        self.assertEqual(first, second)
        self.assertEqual([item["path"] for item in first["files"]], ["nested/a.txt", "z.txt"])
        self.assertEqual(first["files"][0]["sha256"], manifest.sha256_file(self.root / "nested/a.txt"))

    def test_compare_reports_differing_files(self):
        a = self.manifest_file("a.json", {
            "schema_version": 1,
            "files": [{"path": "app.js", "sha256": "a" * 64, "size": 1}],
        })
        b = self.manifest_file("b.json", {
            "schema_version": 1,
            "files": [{"path": "app.js", "sha256": "b" * 64, "size": 1}],
        })
        report = manifest.compare_manifests([a, b])
        self.assertFalse(report["identical"])
        self.assertEqual(report["comparisons"][0]["differing"], ["app.js"])

    def test_compare_reports_missing_and_extra_files(self):
        a = self.manifest_file("a.json", {
            "schema_version": 1,
            "files": [{"path": "only-a.js", "sha256": "a" * 64, "size": 1}],
        })
        b = self.manifest_file("b.json", {
            "schema_version": 1,
            "files": [{"path": "only-b.js", "sha256": "b" * 64, "size": 1}],
        })
        comparison = manifest.compare_manifests([a, b])["comparisons"][0]
        self.assertEqual(comparison["missing"], ["only-a.js"])
        self.assertEqual(comparison["extra"], ["only-b.js"])

    def test_group_comparison_checks_every_reference_candidate_pair(self):
        matching = {
            "schema_version": 1,
            "files": [{"path": "app.js", "sha256": "a" * 64, "size": 1}],
        }
        differing = {
            "schema_version": 1,
            "files": [{"path": "app.js", "sha256": "b" * 64, "size": 1}],
        }
        references = [self.manifest_file("mac-1.json", matching),
                      self.manifest_file("mac-2.json", matching)]
        candidates = [self.manifest_file("linux-1.json", matching),
                      self.manifest_file("linux-2.json", differing)]
        report = manifest.compare_manifest_groups(references, candidates)
        self.assertFalse(report["identical"])
        self.assertEqual(len(report["comparisons"]), 4)
        self.assertEqual(
            [item["differing"] for item in report["comparisons"]],
            [[], ["app.js"], [], ["app.js"]],
        )

    def test_environment_evidence_does_not_change_file_comparison(self):
        shared = [{"path": "app.js", "sha256": "a" * 64, "size": 1}]
        mac = self.manifest_file("mac.json", {
            "schema_version": 1, "environment": {"os": "darwin"}, "files": shared,
        })
        linux = self.manifest_file("linux.json", {
            "schema_version": 1, "environment": {"os": "linux"}, "files": shared,
        })
        self.assertTrue(manifest.compare_manifests([mac, linux])["identical"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_manifest_rejects_symlinked_output(self):
        outside = self.root.parent / "outside-studio-bundle.txt"
        outside.write_bytes(b"outside")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        os.symlink(str(outside), str(self.root / "escape.txt"))
        with self.assertRaisesRegex(manifest.UnsafeOutput, "unsafe file"):
            manifest.build_manifest(self.root)

    def test_manifest_rejects_escaping_manifest_path(self):
        unsafe = self.manifest_file("unsafe.json", {
            "schema_version": 1,
            "files": [{"path": "../escape.js", "sha256": "a" * 64, "size": 1}],
        })
        with self.assertRaisesRegex(manifest.UnsafeOutput, "unsafe relative path"):
            manifest.load_manifest(unsafe)

    def test_license_inventory_is_sorted_and_counts_qualifying_licenses(self):
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "": {"name": "root"},
                "node_modules/z": {
                    "version": "2.0.0", "license": "MIT", "resolved": "https://example/z.tgz",
                    "integrity": "sha512-z", "dev": True,
                },
                "node_modules/a": {
                    "version": "1.0.0", "license": "ISC", "resolved": "https://example/a.tgz",
                    "integrity": "sha512-a",
                },
            }
        })
        inventory = manifest.build_license_inventory(lock)
        self.assertEqual(inventory["package_count"], 2)
        self.assertEqual([item["name"] for item in inventory["packages"]], ["a", "z"])
        self.assertEqual(inventory["license_counts"], {"ISC": 1, "MIT": 1})
        self.assertEqual([item["scope"] for item in inventory["packages"]], ["runtime", "development"])

    def test_license_inventory_rejects_unreviewed_license(self):
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/nope": {
                    "version": "1.0.0", "license": "MPL-2.0", "resolved": "https://example/nope.tgz",
                    "integrity": "sha512-nope",
                }
            }
        })
        with self.assertRaisesRegex(ValueError, "unreviewed or disallowed"):
            manifest.build_license_inventory(lock)

    def test_license_inventory_rejects_unsafe_package_location(self):
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/../../escape": {
                    "version": "1.0.0", "license": "MIT", "resolved": "https://example/x.tgz",
                    "integrity": "sha512-x",
                }
            }
        })
        with self.assertRaisesRegex(manifest.UnsafeOutput, "unsafe relative path"):
            manifest.build_license_inventory(lock)

    def test_runtime_notices_fail_closed_without_license_text(self):
        package = self.root / "node_modules" / "example"
        package.mkdir(parents=True)
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/example": {"version": "1.0.0", "license": "MIT"},
            }
        })
        with self.assertRaisesRegex(ValueError, "no bundled or external license evidence"):
            manifest.build_runtime_notices(lock, self.evidence_file())

    def test_external_license_evidence_is_exact_and_hash_verified(self):
        package = self.root / "node_modules" / "example"
        package.mkdir(parents=True)
        license_file = self.write("licenses/example-LICENSE", b"verbatim upstream license\n")
        digest = manifest.sha256_file(license_file)
        evidence = self.evidence_file([{
                "basis": "metadata declares MIT", "license": "MIT",
                "name": "example", "version": "1.0.0",
                "path": "licenses/example-LICENSE", "sha256": digest,
                "source_commit": "abc123", "source_url": "https://example.invalid/LICENSE",
            }])
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/example": {
                    "version": "1.0.0", "license": "MIT",
                    "resolved": "https://example.invalid/example.tgz", "integrity": "sha512-example",
                },
            }
        })
        notices = manifest.build_runtime_notices(lock, evidence)
        self.assertIn("verbatim upstream license", notices)
        self.assertIn("https://example.invalid/LICENSE @ abc123", notices)
        inventory = manifest.build_license_inventory(lock, evidence)
        self.assertEqual(
            inventory["packages"][0]["external_license_evidence"]["source_commit"], "abc123"
        )
        data = json.loads(evidence.read_text(encoding="utf-8"))
        data["packages"][0]["sha256"] = "0" * 64
        evidence.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            manifest.build_runtime_notices(lock, evidence)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_runtime_notices_reject_symlinked_package_location(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "LICENSE").write_text("upstream", encoding="utf-8")
        packages = self.root / "node_modules"
        packages.mkdir()
        os.symlink(str(outside), str(packages / "example"))
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/example": {"version": "1.0.0", "license": "MIT"},
            }
        })
        with self.assertRaisesRegex(manifest.UnsafeOutput, "symlinked package location"):
            manifest.build_runtime_notices(lock, self.evidence_file())

    def test_runtime_notices_preserve_upstream_bytes(self):
        package = self.root / "node_modules" / "example"
        package.mkdir(parents=True)
        upstream = b"Copyright upstream" + b"@" + b"example.com\r\nTrailing spaces  \r\n"
        (package / "LICENSE").write_bytes(upstream)
        lock = self.manifest_file("package-lock.json", {
            "packages": {
                "node_modules/example": {"version": "1.0.0", "license": "MIT"},
            }
        })
        notices = manifest.build_runtime_notices(lock, self.evidence_file()).encode("utf-8")
        self.assertIn(upstream, notices)

    def test_runtime_pins_are_read_and_mismatches_fail(self):
        source = self.root / "studio"
        source.mkdir()
        npm_version = ".".join(("10", "9", "8"))
        (self.root / ".node-version").write_text("22.22.3\n", encoding="utf-8")
        (source / "package.json").write_text(
            json.dumps({"packageManager": "npm@" + npm_version}), encoding="utf-8"
        )
        expected = manifest.expected_runtime(source)
        self.assertEqual(expected, {"node": "22.22.3", "npm": npm_version})
        manifest.enforce_runtime(expected, {"node": "22.22.3", "npm": npm_version})
        self.assertEqual(
            manifest.runtime_evidence({"node": "22.22.3", "npm": npm_version, "os": "darwin"})["npm"],
            ["10", "9", "8"],
        )
        with self.assertRaisesRegex(ValueError, "npm version mismatch"):
            manifest.enforce_runtime(expected, {"node": "22.22.3", "npm": ".".join(("10", "9", "7"))})

    def test_workflow_compares_all_committed_and_linux_manifests(self):
        workflow = (REPO / ".github" / "workflows" / "studio-bundle-repro.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("compare-groups", workflow)
        self.assertIn("studio/reproducibility/macos-arm64/run-{1,2,3}.json", workflow)
        self.assertIn("evidence/linux-x64/run-{1,2,3}.json", workflow)
        self.assertIn("evidence/cross-platform-comparison.json", workflow)
        self.assertIn('test "$CROSS_PLATFORM_STATUS" = "0"', workflow)
        self.assertIn('npm install --global "npm@$required_npm"', workflow)
        self.assertIn("cmp \"$evidence_dir/third-party.json\" studio/public/third-party.json", workflow)
        self.assertIn("cmp \"$evidence_dir/THIRD_PARTY_NOTICES.txt\" studio/public/THIRD_PARTY_NOTICES.txt", workflow)
        self.assertIn("npm audit --prefix studio --audit-level=low", workflow)
        self.assertIn("--license-evidence studio/third-party/license-evidence.json", workflow)


if __name__ == "__main__":
    unittest.main()
