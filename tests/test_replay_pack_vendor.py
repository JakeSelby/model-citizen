"""A pack workspace that carries a third-party skill fetched at a pinned commit (#1216): validated,
placed into the materialized workspace only when its bytes match the pinned digest, and loaded by
both arms alike. The upstream is a small git repository built in a temporary directory; nothing is
fetched from the network and no container is started."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH
from test_replay_pack import CANARY, PACK, git, make_pack, task_spec

SPAWNS = BENCH.replay_spawns
SKILL = "---\nname: deep-recon\ndescription: Research a topic with parallel subagents.\n---\n\nFan out.\n"


def upstream(root):
    """A stand-in for the skill's upstream repository: `(path, commit)`."""
    root = Path(root)
    (root / "skills" / "deep-recon" / "references").mkdir(parents=True)
    (root / "skills" / "deep-recon" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "skills" / "deep-recon" / "references" / "run.md").write_text("Spawn one agent per question.\n",
                                                                         encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "memlog.py").write_text("print('memlog')\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License\n\nCopyright (c) upstream\n", encoding="utf-8")
    git(root.parent, "init", "-q", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "feat: skill")
    return root, git(root, "rev-parse", "HEAD")


PATHS = [["skills/deep-recon", ".claude/skills/deep-recon"], ["scripts/memlog.py", "_bmad/scripts/memlog.py"],
         ["LICENSE", "_bmad/LICENSE"]]


def entry(source, commit, digest="0" * 64, **over):
    spec = {"name": "upstream-skill", "source": str(source), "commit": commit, "license": "MIT",
            "paths": PATHS, "digest": digest}
    spec.update(over)
    return spec


def placed_digest(source, commit):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            PACK.place_vendored([entry(source, commit)], tmp)
        except SystemExit as exc:
            return str(exc).split("placed digest ")[1].split(",")[0]
    raise AssertionError("a zero digest was accepted")


def research_pack(tmp, vendor, **task_over):
    spec = task_spec("research-fanout", prompt=["Research vendor pricing with the deep-recon skill."],
                     first_wave=True, requires_skills=["deep-recon"], **task_over)
    root = make_pack(Path(tmp) / "pack", tasks=[spec])
    document = json.loads((root / "pack.json").read_text(encoding="utf-8"))
    document["workspaces"]["app"]["vendor"] = vendor
    (root / "pack.json").write_text(json.dumps(document), encoding="utf-8")
    git(root, "commit", "-qam", "feat: vendor")
    return PACK.open_pack(root, harness_root=Path(tmp) / "harness")


class VendorValidationTests(unittest.TestCase):
    def test_a_well_formed_entry_passes(self):
        self.assertEqual(PACK.vendor_errors("app", [entry("https://example.invalid/x.git", "a" * 40)]), [])

    def test_every_malformed_field_is_named(self):
        errors = PACK.vendor_errors("app", [entry("", "abc", digest="x", license="GPL-3.0",
                                                  paths=[["../up", "/abs"]])])
        joined = "\n".join(errors)
        for word in ("source", "commit", "license 'GPL-3.0'", "digest", "paths"):
            self.assertIn(word, joined)
        self.assertEqual(PACK.vendor_errors("app", {}), ["workspace app vendor is not a list"])

    def test_a_workspace_path_named_twice_or_nested_is_refused(self):
        paths = [["a", ".claude/skills/x"], ["b", ".claude/skills/x/y"]]
        self.assertTrue(PACK.vendor_errors("app", [entry("s", "a" * 40, paths=paths)]))

    def test_a_pack_naming_a_copyleft_vendor_is_refused_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, commit = upstream(Path(tmp) / "upstream")
            with self.assertRaises(SystemExit) as caught:
                research_pack(tmp, [entry(source, commit, license="AGPL-3.0")])
        self.assertIn("license 'AGPL-3.0'", str(caught.exception))


class MaterializeTests(unittest.TestCase):
    def test_the_skill_is_committed_into_the_workspace_at_the_pinned_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, commit = upstream(Path(tmp) / "upstream")
            pack = research_pack(tmp, [entry(source, commit, placed_digest(source, commit))])
            self.addCleanup(PACK.close_pack, pack)
            task = PACK.load_set(pack, "production", "production")[0][0]
            self.assertEqual((task["first_wave"], task["requires_skills"]), (True, ["deep-recon"]))
            dest = PACK.materialize(task, Path(tmp) / "work" / "repo")
            files = git(dest, "ls-files").splitlines()
            self.assertIn(".claude/skills/deep-recon/SKILL.md", files)
            self.assertIn(".claude/skills/deep-recon/references/run.md", files)
            self.assertIn("_bmad/LICENSE", files)
            self.assertEqual((dest / ".claude/skills/deep-recon/SKILL.md").read_text(encoding="utf-8"), SKILL)
            self.assertEqual(git(dest, "status", "--porcelain"), "")
            self.assertFalse(any(CANARY in p.read_text(encoding="utf-8") for p in dest.rglob("*.md")))

    def test_bytes_other_than_the_pinned_digest_are_refused_before_the_workspace_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, commit = upstream(Path(tmp) / "upstream")
            pack = research_pack(tmp, [entry(source, commit, "f" * 64)])
            self.addCleanup(PACK.close_pack, pack)
            task = PACK.load_set(pack, "production", "production")[0][0]
            with self.assertRaises(SystemExit) as caught:
                PACK.materialize(task, Path(tmp) / "work" / "repo")
            self.assertIn("not the %s the pack pins" % ("f" * 64), str(caught.exception))
            self.assertFalse((Path(tmp) / "work" / "repo" / ".claude").exists())

    def test_a_commit_the_upstream_does_not_hold_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = upstream(Path(tmp) / "upstream")
            with self.assertRaises(SystemExit) as caught:
                PACK.place_vendored([entry(source, "b" * 40)], Path(tmp) / "dest")
        self.assertIn("fetching upstream-skill", str(caught.exception))

    def test_a_vendored_file_never_replaces_a_workspace_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, commit = upstream(Path(tmp) / "upstream")
            dest = Path(tmp) / "dest"
            (dest / "_bmad").mkdir(parents=True)
            (dest / "_bmad" / "LICENSE").write_text("the workspace's own\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                PACK.place_vendored([entry(source, commit, placed_digest(source, commit))], dest)
            self.assertIn("would replace the workspace's _bmad/LICENSE", str(caught.exception))
            self.assertFalse((dest / ".claude").exists())

    def test_task_fields_of_the_wrong_type_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, commit = upstream(Path(tmp) / "upstream")
            spec = task_spec("bad", first_wave="yes", requires_skills=["../x"])
            root = make_pack(Path(tmp) / "pack", tasks=[spec])
            pack = PACK.open_pack(root, harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            with self.assertRaises(SystemExit) as caught:
                PACK.load_set(pack, "production", "production")
        self.assertIn("first_wave must be true or false", str(caught.exception))
        self.assertIn("requires_skills is not a list of skill names", str(caught.exception))


class BothArmsTests(unittest.TestCase):
    def test_both_arms_run_one_command_that_loads_project_skills(self):
        argv = BENCH.arm_command("claude", "claude-test", "Research it.", 2.0, 8)
        self.assertNotIn("--setting-sources", argv)
        self.assertNotIn("--bare", argv)
        self.assertNotIn("--disable-slash-commands", argv)
        self.assertIn("--include-hook-events", argv)

    def test_the_row_says_whether_the_skill_was_loaded(self):
        loaded = [{"type": "system", "subtype": "init", "skills": ["deep-recon", "update-config"]}]
        self.assertTrue(SPAWNS.skills_loaded(loaded, ["deep-recon"]))
        self.assertFalse(SPAWNS.skills_loaded([dict(loaded[0], skills=[])], ["deep-recon"]))


if __name__ == "__main__":
    unittest.main()
