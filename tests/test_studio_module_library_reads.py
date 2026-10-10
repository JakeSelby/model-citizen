# SPDX-License-Identifier: MIT
"""The library inventory reads each module file and each root's manifests once per call."""
from __future__ import annotations

import collections
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import module_library  # noqa: E402


class InventoryReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(os.path.realpath(self.temporary.name))
        self.home = base / "home"
        self.custom = base / "team-primitives"
        (self.custom / "rules").mkdir(parents=True)
        for index in range(3):
            (self.custom / "rules" / ("team-%d.md" % index)).write_text(
                "# Team rule %d\n\nText.\n" % index, encoding="utf-8")
        (self.custom / "stances" / "tone").mkdir(parents=True)
        for variant in ("brief", "warm"):
            (self.custom / "stances" / "tone" / (variant + ".md")).write_text(
                "# Tone: %s\n" % variant, encoding="utf-8")
        (self.custom / "manifests.json").write_text(json.dumps({
            "schema_version": 1,
            "rules": {"team-%d" % index: {"summary": "rule %d" % index} for index in range(3)},
            "stances": {"tone": {"summary": "tone", "tags": ["voice"]}},
        }), encoding="utf-8")
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = [str(self.custom)]
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def inventory(self):
        with mock.patch.dict(os.environ, {"HARNESS_HOME": str(self.home)}):
            return module_library.inventory(ROOT)

    def test_each_module_file_and_each_manifests_file_is_read_once(self):
        reads = collections.Counter()
        original = Path.read_text

        def counted(path, *args, **kwargs):
            reads[str(path)] += 1
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", counted):
            payload = self.inventory()
        team = [item for item in payload["modules"]
                if item["root"]["path"] == str(self.custom) and item["kind"] == "rules"]
        self.assertEqual(len(team), 3)
        for item in team:
            self.assertEqual(reads[item["source"]["path"]], 1, item["source"]["path"])
            self.assertEqual(item["source"]["text"], item["rendered"]["text"])
            self.assertEqual(item["context_cost"]["tokens"],
                             round(len(item["source"]["text"]) / 4))
        self.assertEqual(reads[str(self.custom / "manifests.json")], 1)
        self.assertEqual(reads[str(ROOT / "primitives" / "manifests.json")], 1)
        # Skills, workflows and roles read their frontmatter, and roles their projection, from
        # the same cached text.
        for kind in ("skills", "workflows", "roles"):
            paths = [item["source"]["path"] for item in payload["modules"] if item["kind"] == kind]
            self.assertTrue(paths, kind)
            for path in paths:
                self.assertEqual(reads[path], 1, path)

    def test_modules_sharing_a_manifest_entry_receive_their_own_copies(self):
        shared = [item["manifest"] for item in self.inventory()["modules"]
                  if item["kind"] == "stances" and item["name"].startswith("tone/")]
        self.assertEqual(len(shared), 2)
        self.assertEqual(shared[0], {"summary": "tone", "tags": ["voice"]})
        self.assertEqual(shared[0], shared[1])
        self.assertIsNot(shared[0], shared[1])
        self.assertIsNot(shared[0]["tags"], shared[1]["tags"])

    def test_a_module_that_cannot_be_read_is_empty_in_the_same_call(self):
        broken = self.custom / "rules" / "team-1.md"
        original = Path.read_text

        def refusing(path, *args, **kwargs):
            if path == broken:
                raise PermissionError("refused")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", refusing):
            payload = self.inventory()
        item = next(item for item in payload["modules"]
                    if item["source"]["path"] == str(broken))
        self.assertEqual((item["source"]["text"], item["rendered"]["text"]), ("", ""))
        self.assertEqual(item["context_cost"]["tokens"], 0)

    def test_the_helpers_still_read_without_a_memo(self):
        path = self.custom / "rules" / "team-0.md"
        self.assertEqual(module_library._rendered(ROOT, "rules", path, "team-0"),
                         path.read_text(encoding="utf-8"))
        self.assertEqual(module_library._manifest(self.custom, "rules", "team-2"),
                         {"summary": "rule 2"})
        self.assertIsNone(module_library._manifest(self.custom, "rules", "missing"))


if __name__ == "__main__":
    unittest.main()
