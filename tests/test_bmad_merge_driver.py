# SPDX-License-Identifier: MIT
"""Tests for the git merge drivers of the BMad issue map and its derived sprint status."""

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import bmad_issue_sync as sync  # noqa: E402
import bmad_merge_driver as driver  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

REPOSITORY = "JakeSelby/agent-harness"
MAP = sync.MAP_RELATIVE_PATH
SPRINT = sync.SPRINT_STATUS_RELATIVE_PATH


def item_for(kind, sequence, number, parent=None):
    bmad_id = "AH-{}{:03d}".format(sync.PREFIX[kind], sequence)
    return {
        "bmad_id": bmad_id,
        "github_number": number,
        "github_url": "https://github.com/{}/issues/{}".format(REPOSITORY, number),
        "title": "A {} for issue {}".format(kind, number),
        "type": kind,
        "native_type": sync.NATIVE_TYPE[kind],
        "artifact_path": "_bmad-output/implementation-artifacts/{}.md".format(bmad_id),
        "parent_bmad_id": parent["bmad_id"] if parent else None,
        "parent_github_number": parent["github_number"] if parent else None,
        "lifecycle": "active",
        "provenance": "authored",
    }


def manifest_for(items, next_ids=None):
    counters = {kind: 1 for kind in sync.KINDS}
    counters.update(next_ids or {})
    return {
        "schema_version": 2,
        "repository": REPOSITORY,
        "native_type_projection": "labels-only",
        "generated_at": "2026-09-01",
        "next_ids": counters,
        "items": sorted(items, key=lambda value: value["github_number"]),
    }


def filled(item):
    """A typed story that passes the depth check, so the item renders ready-for-dev."""
    text = sync.render_artifact(item)
    head_end = sync.sync_layout(text)
    body = re.sub(r"<!-- fill: .*?-->", "Written content.", text[head_end:], flags=re.DOTALL)
    return text[:head_end] + body


def repository_local_variables():
    """The variables git treats as local to a repository, which a hook sets for its own."""
    listed = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True)
    names = set(listed.stdout.split()) if listed.returncode == 0 else set()
    return names | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR"}


LOCAL_VARIABLES = repository_local_variables()


def isolated_environment():
    """This environment without the variables a git hook sets, which would point git elsewhere."""
    return {key: value for key, value in os.environ.items() if key not in LOCAL_VARIABLES}


EPIC = item_for("epic", 1, 1)
STORY = item_for("story", 1, 2, parent=EPIC)
BASE = manifest_for([EPIC, STORY], {"epic": 2, "story": 2})


class MergeMapTests(unittest.TestCase):
    def merge(self, ours, theirs, base=BASE, main_is_ours=False):
        return driver.merge_maps(json.loads(json.dumps(base)), ours, theirs, main_is_ours)

    def reserved(self, *items, **next_ids):
        manifest = json.loads(json.dumps(BASE))
        manifest["items"] = sorted(manifest["items"] + list(items), key=lambda value: value["github_number"])
        manifest["next_ids"].update(next_ids)
        return manifest

    def test_each_side_keeps_its_reservation_and_the_larger_counter(self):
        mine = item_for("story", 3, 11, parent=EPIC)
        main = item_for("story", 2, 10, parent=EPIC)
        chore = item_for("chore", 1, 12)
        merged, conflicts, notices = self.merge(
            self.reserved(mine, story=4), self.reserved(main, chore, story=3, chore=2)
        )
        self.assertEqual((conflicts, notices), ([], []))
        self.assertEqual([item["bmad_id"] for item in merged["items"]],
                         ["AH-E001", "AH-S001", "AH-S002", "AH-S003", "AH-C001"])
        self.assertEqual(merged["next_ids"]["story"], 4)
        self.assertEqual(merged["next_ids"]["chore"], 2)
        self.assertEqual(list(merged), list(BASE))
        self.assertEqual(list(merged["next_ids"]), list(BASE["next_ids"]))

    def test_serialization_is_the_format_write_manifest_uses(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            manifest = self.reserved(item_for("story", 2, 10, parent=EPIC), story=3)
            with mock.patch.object(sync, "write_sprint_status"):
                sync.write_manifest(manifest)
            written = (Path(temp) / MAP).read_text(encoding="utf-8")
        self.assertEqual(driver.serialize(json.loads(written)), written)

    def test_main_wins_when_both_sides_mapped_one_issue(self):
        mine = item_for("story", 3, 10, parent=EPIC)
        main = item_for("story", 2, 10, parent=EPIC)
        merged, conflicts, notices = self.merge(self.reserved(mine, story=4), self.reserved(main, story=3))
        self.assertEqual(conflicts, [])
        self.assertEqual([item["bmad_id"] for item in merged["items"]], ["AH-E001", "AH-S001", "AH-S002"])
        self.assertIn("AH-S003 dropped", notices[0])
        self.assertEqual(merged["next_ids"]["story"], 4)

    def test_in_a_rebase_the_upstream_side_is_main(self):
        mine = item_for("story", 3, 10, parent=EPIC)
        main = item_for("story", 2, 10, parent=EPIC)
        merged, _, _ = self.merge(self.reserved(main, story=3), self.reserved(mine, story=4), main_is_ours=True)
        self.assertIn("AH-S002", [item["bmad_id"] for item in merged["items"]])
        self.assertNotIn("AH-S003", [item["bmad_id"] for item in merged["items"]])

    def test_one_id_reserved_for_two_issues_conflicts_and_keeps_both_for_the_audit(self):
        mine = item_for("story", 2, 11, parent=EPIC)
        main = item_for("story", 2, 10, parent=EPIC)
        merged, conflicts, _ = self.merge(self.reserved(mine, story=3), self.reserved(main, story=3))
        self.assertEqual(conflicts, ["AH-S002 was reserved on both sides"])
        self.assertEqual([item["github_number"] for item in merged["items"]], [1, 2, 10, 11])
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            self.assertIn("duplicate BMad ID AH-S002", sync.audit_manifest(merged))

    def test_a_field_changed_on_one_side_is_kept(self):
        mine = self.reserved()
        mine["items"][1]["title"] = "Retitled on the branch"
        main = self.reserved()
        main["items"][1]["lifecycle"] = "completed"
        merged, conflicts, _ = self.merge(mine, main)
        self.assertEqual(conflicts, [])
        self.assertEqual((merged["items"][1]["title"], merged["items"][1]["lifecycle"]),
                         ("Retitled on the branch", "completed"))

    def test_a_field_changed_differently_on_both_sides_conflicts(self):
        mine = self.reserved()
        mine["items"][1]["title"] = "Branch title"
        main = self.reserved()
        main["items"][1]["title"] = "Main title"
        merged, conflicts, _ = self.merge(mine, main)
        self.assertEqual(conflicts, ["AH-S001 title"])
        self.assertEqual(merged["items"][1]["title"], "Main title")

    def test_an_item_removed_on_the_branch_and_changed_on_main_conflicts(self):
        mine = self.reserved()
        del mine["items"][1]
        main = self.reserved()
        main["items"][1]["lifecycle"] = "completed"
        merged, conflicts, _ = self.merge(mine, main)
        self.assertEqual(conflicts, ["AH-S001 was removed on one side and changed on the other"])
        self.assertEqual(merged["items"][1]["lifecycle"], "completed")

    def test_a_top_level_field_changed_differently_on_both_sides_conflicts(self):
        mine = self.reserved()
        mine["repository"] = "someone/fork"
        main = self.reserved()
        main["repository"] = "someone/else"
        merged, conflicts, _ = self.merge(mine, main)
        self.assertEqual(conflicts, ["repository"])
        self.assertEqual(merged["repository"], "someone/else")

    def test_a_counter_that_is_not_an_integer_is_a_conflict(self):
        mine = self.reserved()
        mine["next_ids"]["story"] = True
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / name for name in ("base", "ours", "theirs")]
            for path, manifest in zip(paths, (BASE, mine, self.reserved())):
                path.write_text(driver.serialize(manifest))
            with mock.patch("sys.stderr"), mock.patch.object(driver, "rebasing", return_value=False):
                self.assertEqual(driver.main(["map"] + [str(path) for path in paths]), 1)
            self.assertEqual(json.loads(paths[1].read_text())["next_ids"]["story"], True)

    def test_an_unparsable_side_exits_as_a_conflict_and_leaves_ours(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / name for name in ("base", "ours", "theirs")]
            paths[0].write_text(driver.serialize(BASE))
            paths[1].write_text("<<<<<<< not json\n")
            paths[2].write_text(driver.serialize(BASE))
            with mock.patch("sys.stderr"):
                self.assertEqual(driver.main(["map"] + [str(path) for path in paths]), 1)
            self.assertEqual(paths[1].read_text(), "<<<<<<< not json\n")

    def test_sprint_status_outside_a_merge_is_a_conflict(self):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GITHEAD_")}
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, environment, clear=True):
            paths = [Path(temp) / name for name in ("base", "ours", "theirs")]
            for path in paths:
                path.write_text("development_status: {}\n")
            with mock.patch("sys.stderr"), mock.patch.object(driver, "rebasing", return_value=False):
                self.assertEqual(driver.main(["sprint-status"] + [str(path) for path in paths]), 1)
            self.assertEqual(paths[1].read_text(), "development_status: {}\n")


class IsolatedEnvironmentTests(unittest.TestCase):
    def test_every_repository_override_a_hook_could_set_is_dropped(self):
        names = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR")
        with mock.patch.dict(os.environ, {name: "/elsewhere" for name in names}):
            env = isolated_environment()
        for name in names:
            self.assertNotIn(name, env)


class RegistrationDocsTests(unittest.TestCase):
    def test_the_documented_commands_register_what_worktree_create_does(self):
        text = (REPO / "docs" / "bmad-governance.md").read_text(encoding="utf-8")
        for name, (description, command) in harness.BMAD_MERGE_DRIVERS.items():
            self.assertIn('git config merge.{}.name "{}"'.format(name, description), text)
            self.assertIn('git config merge.{}.driver "{}"'.format(name, command), text)


class MergeInRepositoryTests(unittest.TestCase):
    """Two branches reserve an issue each, then main merges into the branch, as agents sync."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "project"
        (self.repo / "scripts").mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test" + "@" + "example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        for name in ("bmad_issue_sync.py", "bmad_merge_driver.py"):
            shutil.copy(REPO / "scripts" / name, self.repo / "scripts" / name)
        shutil.copytree(REPO / "scripts" / "bmad_story_templates", self.repo / "scripts" / "bmad_story_templates")
        shutil.copy(REPO / ".gitattributes", self.repo / ".gitattributes")
        (self.repo / ".gitignore").write_text("__pycache__/\n")
        (self.repo / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
        self.reserve(BASE, [])
        self.commit("base")

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", str(self.repo)] + list(args), check=check,
                              capture_output=True, text=True, env=isolated_environment())

    def tool(self, *args):
        return subprocess.run([sys.executable, "scripts/bmad_issue_sync.py"] + list(args),
                              cwd=str(self.repo), capture_output=True, text=True, env=isolated_environment())

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def reserve(self, manifest, items):
        """What `reserve` writes, less the GitHub call: the story, the map, then sprint status."""
        manifest = json.loads(json.dumps(manifest))
        for item in items:
            manifest["items"].append(item)
            manifest["next_ids"][item["type"]] = max(
                manifest["next_ids"][item["type"]], int(item["bmad_id"][-3:]) + 1
            )
        manifest["items"].sort(key=lambda value: value["github_number"])
        for item in manifest["items"]:
            path = self.repo / item["artifact_path"]
            if not path.exists():
                path.write_text(filled(item), encoding="utf-8")
        (self.repo / MAP).write_text(driver.serialize(manifest), encoding="utf-8")
        result = self.tool("sprint-status")
        self.assertEqual(result.returncode, 0, result.stderr)
        return manifest

    def branches_that_each_reserve(self):
        self.git("checkout", "-qb", "feature")
        self.reserve(BASE, [item_for("story", 3, 11, parent=EPIC)])
        self.commit("feature reserves AH-S003")
        self.git("checkout", "-q", "main")
        self.reserve(BASE, [item_for("story", 2, 10, parent=EPIC), item_for("chore", 1, 12)])
        self.commit("main reserves AH-S002 and AH-C001")
        self.git("checkout", "-q", "feature")

    def test_without_the_drivers_the_same_merge_conflicts(self):
        self.branches_that_each_reserve()
        merged = self.git("merge", "--no-edit", "main", check=False)
        self.assertNotEqual(merged.returncode, 0)
        self.assertIn(MAP, self.git("diff", "--name-only", "--diff-filter=U").stdout)

    def test_registered_drivers_merge_cleanly_into_a_fresh_regeneration(self):
        self.assertTrue(harness.register_bmad_merge_drivers(self.repo, self.repo))
        self.branches_that_each_reserve()
        merged = self.git("merge", "--no-edit", "main", check=False)
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        committed = json.loads(self.git("show", "HEAD:" + MAP).stdout)
        self.assertEqual([item["bmad_id"] for item in committed["items"]],
                         ["AH-E001", "AH-S001", "AH-S002", "AH-S003", "AH-C001"])
        self.assertEqual(committed["next_ids"]["story"], 4)
        self.assertEqual(committed["next_ids"]["chore"], 2)
        self.assertEqual(self.git("show", "HEAD:" + MAP).stdout, driver.serialize(committed))
        audit = self.tool("audit")
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        # The merge commit itself carries the regeneration, not only the working tree.
        committed_status = self.git("show", "HEAD:" + SPRINT).stdout
        regenerated = self.tool("sprint-status")
        self.assertIn("unchanged", regenerated.stdout)
        self.assertEqual(committed_status, (self.repo / SPRINT).read_text(encoding="utf-8"))
        for key in ("ah-s002-a-story-for-issue-10: ready-for-dev", "ah-s003-a-story-for-issue-11: ready-for-dev",
                    "ah-c001-a-chore-for-issue-12: ready-for-dev"):
            self.assertIn(key, committed_status)

    def test_a_story_edited_on_both_sides_renders_from_its_merged_text(self):
        harness.register_bmad_merge_drivers(self.repo, self.repo)
        story = self.repo / STORY["artifact_path"]
        self.git("checkout", "-qb", "feature")
        story.write_text(story.read_text(encoding="utf-8") + "\nBranch note.\n", encoding="utf-8")
        self.reserve(BASE, [item_for("story", 3, 11, parent=EPIC)])
        self.commit("feature")
        self.git("checkout", "-q", "main")
        text = story.read_text(encoding="utf-8")
        story.write_text(re.sub(r'(?m)^updated: .*$', 'updated: "2999-12-31"', text, count=1), encoding="utf-8")
        self.reserve(BASE, [item_for("story", 2, 10, parent=EPIC)])
        self.commit("main")
        self.git("checkout", "-q", "feature")
        merged = self.git("merge", "--no-edit", "main", check=False)
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn('generated: "2999-12-31"', self.git("show", "HEAD:" + SPRINT).stdout)
        self.assertEqual(self.tool("sprint-status", "--check").returncode, 0)
        self.assertEqual(self.tool("audit").returncode, 0)


if __name__ == "__main__":
    unittest.main()
