# SPDX-License-Identifier: MIT
"""Unit tests for the stage-user-files PreToolUse hook.

A Remote Control client can open a file an agent sends only when the file is under the session's
working directories, so a file `SendUserFile` names from anywhere else is copied into
`.agent-harness/outbox/` and the path rewritten. Everything else passes through unchanged, and the
hook never denies.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "policy" / "hooks" / "stage-user-files.py"
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())

_spec = importlib.util.spec_from_file_location("stage_user_files", HOOK)
stage_user_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage_user_files)


class StageUserFilesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "session"
        self.elsewhere = self.base / "elsewhere"
        self.root.mkdir()
        self.elsewhere.mkdir()
        self.shot = self.elsewhere / "shot.png"
        self.shot.write_bytes(b"\x89PNG first")
        self.box = self.root / ".agent-harness" / "outbox"

    def send(self, *files, **extra):
        inputs = dict({"files": list(files), "status": "normal"}, **extra)
        return stage_user_files.decide({"tool_name": "SendUserFile", "cwd": str(self.root),
                                        "tool_input": inputs})

    @staticmethod
    def sent(result):
        return result["hookSpecificOutput"]["updatedInput"]["files"]

    def test_a_file_from_outside_is_copied_in_and_the_path_rewritten(self):
        result = self.send(str(self.shot), caption="the render", display="render")
        output = result["hookSpecificOutput"]
        copy = Path(self.sent(result)[0])
        self.assertEqual(copy.parent.parent, self.box)
        self.assertEqual(copy.name, "shot.png")
        self.assertEqual(copy.read_bytes(), b"\x89PNG first")
        self.assertEqual(output["updatedInput"]["caption"], "the render")
        self.assertEqual(output["updatedInput"]["display"], "render")
        self.assertEqual(output["updatedInput"]["status"], "normal")
        self.assertIn("1 file(s)", result["systemMessage"])

    def test_a_file_inside_the_working_directory_is_left_alone(self):
        inside = self.root / "docs" / "notes.md"
        inside.parent.mkdir()
        inside.write_text("notes")
        self.assertIsNone(self.send(str(inside)))
        self.assertIsNone(self.send("docs/notes.md"))
        self.assertFalse((self.root / ".agent-harness").exists())

    def test_a_symlinked_working_directory_still_contains_its_files(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        (self.root / "a.md").write_text("a")
        result = stage_user_files.decide({"tool_name": "SendUserFile", "cwd": str(alias),
                                          "tool_input": {"files": [str(self.root / "a.md")]}})
        self.assertIsNone(result)

    def test_a_relative_path_resolves_against_the_working_directory(self):
        copy = Path(self.sent(self.send("../elsewhere/shot.png"))[0])
        self.assertEqual(copy.read_bytes(), b"\x89PNG first")

    def test_a_link_inside_that_points_outside_is_copied(self):
        link = self.root / "link.png"
        link.symlink_to(self.shot)
        copy = Path(self.sent(self.send(str(link)))[0])
        self.assertFalse(copy.is_symlink())
        self.assertEqual(copy.read_bytes(), b"\x89PNG first")

    def test_only_outside_entries_change_and_the_order_is_kept(self):
        inside = self.root / "inside.md"
        inside.write_text("in")
        missing = str(self.elsewhere / "missing.png")
        files = self.sent(self.send(str(inside), str(self.shot), missing))
        self.assertEqual(files[0], str(inside))
        self.assertEqual(Path(files[1]).parent.parent, self.box)
        self.assertEqual(files[2], missing)

    def test_missing_paths_and_directories_pass_through(self):
        self.assertIsNone(self.send(str(self.elsewhere / "missing.png")))
        self.assertIsNone(self.send(str(self.elsewhere)))
        self.assertIsNone(self.send("", 7, None))
        self.assertFalse(self.box.exists())

    def test_a_file_over_the_cap_is_left_where_it_is_with_a_notice(self):
        with mock.patch.object(stage_user_files, "MAX_BYTES", 4):
            result = self.send(str(self.shot))
        self.assertNotIn("hookSpecificOutput", result)
        self.assertIn("shot.png", result["systemMessage"])
        self.assertFalse(self.box.exists())

    def test_the_byte_budget_covers_the_whole_call(self):
        small = [self.elsewhere / name for name in ("one.md", "two.md")]
        for path in small:
            path.write_bytes(b"abc")
        with mock.patch.object(stage_user_files, "MAX_BYTES", 4):
            result = self.send(*map(str, small))
        files = self.sent(result)
        self.assertEqual(Path(files[0]).parent.parent, self.box)
        self.assertEqual(files[1], str(small[1]))
        self.assertIn("two.md", result["systemMessage"])

    def test_nothing_is_copied_once_the_deadline_has_passed(self):
        with mock.patch.object(stage_user_files, "DEADLINE_SECONDS", -1):
            result = self.send(str(self.shot))
        self.assertNotIn("hookSpecificOutput", result)
        self.assertIn("shot.png", result["systemMessage"])

    def test_a_failed_copy_leaves_its_entry_and_keeps_the_others(self):
        other = self.elsewhere / "other.md"
        other.write_text("other")
        real_copy = shutil.copyfile

        def copy(source, target):
            if source.endswith("shot.png"):
                raise PermissionError("denied")
            return real_copy(source, target)

        with mock.patch.object(stage_user_files.shutil, "copyfile", copy):
            result = self.send(str(self.shot), "bad\0path", str(other))
        files = self.sent(result)
        self.assertEqual(files[0], str(self.shot))
        self.assertEqual(files[1], "bad\0path")
        self.assertEqual(Path(files[2]).read_text(), "other")
        self.assertIn("shot.png", result["systemMessage"])
        self.assertEqual([p.name for p in self.box.rglob("*.part")], [])

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_the_outbox_never_shows_in_git_status(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.send(str(self.shot))
        self.assertEqual((self.box / ".gitignore").read_text(), "*\n")
        status = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain",
                                 "--untracked-files=all"], capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_an_unchanged_file_reuses_its_copy_and_a_changed_one_gets_another(self):
        first = Path(self.sent(self.send(str(self.shot)))[0])
        inode = first.stat().st_ino
        self.assertEqual(Path(self.sent(self.send(str(self.shot)))[0]), first)
        self.assertEqual(first.stat().st_ino, inode)
        self.shot.write_bytes(b"\x89PNG second, longer")
        later = time.time() + 5
        os.utime(str(self.shot), (later, later))
        second = Path(self.sent(self.send(str(self.shot)))[0])
        self.assertNotEqual(second, first)
        self.assertEqual(first.read_bytes(), b"\x89PNG first")
        self.assertEqual(second.read_bytes(), b"\x89PNG second, longer")

    def test_a_same_size_rewrite_with_its_timestamps_restored_gets_a_new_copy(self):
        before = self.shot.stat()
        first = Path(self.sent(self.send(str(self.shot)))[0])
        time.sleep(0.05)  # past the coarsest change-time granularity a kernel keeps
        self.shot.write_bytes(b"\x89PNG fresh")
        os.utime(str(self.shot), ns=(before.st_atime_ns, before.st_mtime_ns))
        second = Path(self.sent(self.send(str(self.shot)))[0])
        self.assertNotEqual(second, first)
        self.assertEqual(first.read_bytes(), b"\x89PNG first")
        self.assertEqual(second.read_bytes(), b"\x89PNG fresh")

    def test_staging_prunes_old_copies_and_nothing_else(self):
        self.box.mkdir(parents=True)
        stale = time.time() - (stage_user_files.KEEP_DAYS + 1) * 86400
        old, fresh, other = self.box / ("a" * 16), self.box / ("b" * 16), self.box / "keep-me"
        for folder in (old, fresh, other):
            folder.mkdir()
            (folder / "x.md").write_text("x")
        for folder in (old, other):
            os.utime(str(folder), (stale, stale))
        self.send(str(self.shot))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())

    def test_an_outbox_that_resolves_elsewhere_is_never_written(self):
        (self.root / ".agent-harness").symlink_to(self.elsewhere, target_is_directory=True)
        result = self.send(str(self.shot))
        self.assertNotIn("hookSpecificOutput", result)
        self.assertIn("shot.png", result["systemMessage"])
        self.assertEqual(sorted(self.elsewhere.iterdir()), [self.shot])

    def test_links_planted_in_the_outbox_are_never_written_through(self):
        self.send(str(self.shot))
        (self.box / ".gitignore").unlink()
        (self.box / ".gitignore").symlink_to(self.elsewhere / "planted")
        self.assertNotIn("hookSpecificOutput", self.send(str(self.shot)))
        (self.box / ".gitignore").unlink()
        (self.box / ".gitignore").write_text("*\n")
        other = self.elsewhere / "other.md"
        other.write_text("other")
        info = other.resolve().stat()
        key = "%s\0%d\0%d\0%d" % (other.resolve(), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        digest = stage_user_files.hashlib.sha256(key.encode()).hexdigest()[:16]
        decoy = self.base / "decoy"
        decoy.mkdir()
        (self.box / digest).symlink_to(decoy, target_is_directory=True)
        self.assertNotIn("hookSpecificOutput", self.send(str(other)))
        self.assertEqual(list(decoy.iterdir()), [])
        self.assertFalse((self.elsewhere / "planted").exists())

    def test_other_tools_and_malformed_payloads_pass_through(self):
        good = {"files": [str(self.shot)]}
        for payload in (None, [], {"tool_name": "Read", "cwd": str(self.root), "tool_input": good},
                        {"tool_name": "SendUserFile", "tool_input": good},
                        {"tool_name": "SendUserFile", "cwd": ".", "tool_input": good},
                        {"tool_name": "SendUserFile", "cwd": str(self.base / "gone"), "tool_input": good},
                        {"tool_name": "SendUserFile", "cwd": str(self.root), "tool_input": "files"},
                        {"tool_name": "SendUserFile", "cwd": str(self.root),
                         "tool_input": {"files": str(self.shot)}}):
            with self.subTest(payload=payload):
                self.assertIsNone(stage_user_files.decide(payload))
        self.assertFalse(self.box.exists())

    def test_the_entrypoint_prints_the_rewrite_and_ignores_garbage(self):
        payload = {"tool_name": "SendUserFile", "cwd": str(self.root),
                   "tool_input": {"files": [str(self.shot)]}}
        for stdin, rewritten in ((json.dumps(payload), True), ("not json", False), ("", False)):
            with self.subTest(stdin=stdin):
                done = subprocess.run([sys.executable, str(HOOK)], input=stdin,
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 0)
                if rewritten:
                    output = json.loads(done.stdout)["hookSpecificOutput"]
                    self.assertEqual(Path(output["updatedInput"]["files"][0]).parent.parent, self.box)
                else:
                    self.assertEqual(done.stdout, "")

    def test_the_hook_never_denies(self):
        with mock.patch.object(stage_user_files, "MAX_BYTES", 4):
            capped = self.send(str(self.shot))
        with mock.patch.object(stage_user_files.shutil, "copyfile", side_effect=OSError("disk full")):
            failed = self.send(str(self.shot), "bad\0path")
        for result in (self.send(str(self.shot)), capped, failed):
            self.assertNotIn("permissionDecision", result.get("hookSpecificOutput", {}))

    def test_ownership_claims_the_hook_id(self):
        self.assertEqual(OWNERSHIP["claude"]["hook_ids"]["stage-files"],
                         {"event": "PreToolUse", "always": True})


if __name__ == "__main__":
    unittest.main()
