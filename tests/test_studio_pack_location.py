"""Evaluator pack discovery searches a configured location, and skips a malformed pack (#1211).

`HARNESS_STUDIO_PACKS` names the folders discovery searches instead of the ones beside the
checkout, so a test runs real discovery against its own fixture packs and never against the
user's repositories. A sibling whose `pack.json` or `task.json` is JSON but not an object is
listed under `skipped`, never a crash of the catalog.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import packs, replay  # noqa: E402
from test_replay_pack import make_pack  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
                   cwd=str(cwd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class PackLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve() / "packs"
        self.folder.mkdir()

    def located(self):
        return mock.patch.dict(os.environ, {packs.PACKS_ENV: str(self.folder)})

    def test_the_configured_folder_replaces_the_folders_beside_the_checkout(self):
        make_pack(self.folder / "fixture-pack")
        with self.located():
            self.assertEqual(packs.search_folders(REPO), [self.folder])
            found = packs.discover(REPO)
            catalog = replay.task_catalog(REPO)
        self.assertEqual([item["name"] for item in found["packs"]], ["test-pack"])
        self.assertEqual(found["packs"][0]["source"], str(self.folder / "fixture-pack"))
        self.assertEqual([item["name"] for item in catalog["packs"]], ["test-pack"])
        self.assertEqual(catalog["default_pack"], found["packs"][0]["digest"])

    def test_an_unset_location_searches_beside_the_checkout(self):
        with mock.patch.dict(os.environ, {packs.PACKS_ENV: ""}):
            self.assertEqual(packs.search_folders(self.folder / "checkout"), [self.folder])

    def test_a_pack_or_task_that_is_json_but_not_an_object_is_skipped_not_a_crash(self):
        good = make_pack(self.folder / "good-pack")
        for name, edit in (("array-pack", "pack.json"), ("null-task-pack", "task.json")):
            root = make_pack(self.folder / name, name=name)
            target = root / "pack.json" if edit == "pack.json" else next(
                (root / "tasks").iterdir()) / "task.json"
            target.write_text("[]" if edit == "pack.json" else "null", encoding="utf-8")
            git(root, "commit", "-qam", "chore: break it")
        with self.located():
            found = packs.discover(REPO)
        self.assertEqual([item["source"] for item in found["packs"]], [str(good)])
        self.assertEqual(sorted(Path(item["source"]).name for item in found["skipped"]),
                         ["array-pack", "null-task-pack"])


if __name__ == "__main__":
    unittest.main()
