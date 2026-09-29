# SPDX-License-Identifier: MIT
"""Regression coverage for replay answer-contamination controls; no model call is made."""
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, Launch, REPO, TASK, git_repo, options


class SnapshotFailureTests(unittest.TestCase):
    def test_every_snapshot_git_operation_fails_closed(self):
        operations = ("rev-parse", "checkout", "for-each-ref", "update-ref", "remote", "reflog", "gc")
        for operation in operations:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                repo = git_repo(Path(tmp) / "source")
                subprocess.run(["git", "-C", str(repo), "tag", "ancestor"], check=True)
                subprocess.run(["git", "-C", str(repo), "branch", "extra"], check=True)
                sha = subprocess.check_output(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
                original = BENCH._git

                def failing(target, *args):
                    if args and args[0] == operation:
                        return types.SimpleNamespace(returncode=2, stdout="forced failure")
                    return original(target, *args)

                with mock.patch.object(BENCH, "_git", side_effect=failing):
                    with self.assertRaisesRegex(RuntimeError, "failed"):
                        BENCH.snapshot(repo, sha, Path(tmp) / "snapshot")

    def test_an_ancestry_error_is_distinct_from_not_ancestor(self):
        for code, expected in ((1, False), (0, True)):
            with self.subTest(code=code), mock.patch.object(
                    BENCH, "_git", return_value=types.SimpleNamespace(returncode=code, stdout="")):
                self.assertEqual(BENCH._git_is_ancestor("repo", "a", "b"), expected)
        with mock.patch.object(
                BENCH, "_git", return_value=types.SimpleNamespace(returncode=2, stdout="broken graph")):
            with self.assertRaisesRegex(RuntimeError, "ancestry query failed.*broken graph"):
                BENCH._git_is_ancestor("repo", "a", "b")


class InstalledAnswerTests(unittest.TestCase):
    def synthetic_repo(self, root, include_oracle=True):
        repo = git_repo(root)
        if include_oracle:
            oracle = repo / BENCH.ORACLES / "demo.py"
            oracle.parent.mkdir(parents=True)
            oracle.write_text("def check(root):\n    return ['unrelated drift']\n\n"
                              "def solve(root):\n    (root / 'answer').write_text('fixed')\n",
                              encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t",
                            "commit", "-qm", "test: oracle"], check=True)
        sha = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
        return repo, sha

    def test_a_failing_oracle_does_not_make_its_readable_solve_source_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, sha = self.synthetic_repo(Path(tmp) / "source")
            task = dict(TASK, tests={"oracle": "demo"})
            self.assertEqual(BENCH.contamination_errors([task], repo, sha), [
                "demo: installed checkout exposes the held-back oracle source and its reference solution"])

    def test_unknown_answer_absence_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, sha = self.synthetic_repo(Path(tmp) / "source", include_oracle=False)
            task = dict(TASK, tests={"oracle": "missing"})
            self.assertEqual(BENCH.contamination_errors([task], repo, sha), [
                "demo: answer absence cannot be established for this same-repository synthetic task"])

    def test_no_eligible_tasks_refuses_before_arm_checks_or_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([])
            with self.assertRaisesRegex(SystemExit, "no contamination-safe replay tasks"):
                BENCH.replay([], options(tmp), launch)
            self.assertEqual((launch.probes, launch.calls), ([], []))

    def test_the_command_refuses_an_empty_manifest_before_protocol_or_model_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "tasks.json"
            manifest.write_text('{"tasks": []}\n', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "no contamination-safe replay tasks"):
                BENCH.main(["replay", "--tasks", str(manifest)])


class TranscriptObservationTests(unittest.TestCase):
    def message(self, value):
        return [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": value}}]}}]

    def test_lexical_aliases_of_the_installed_root_are_observed(self):
        for path in ("/opt/./model-citizen/policy/x.py",
                     "/opt/cache/../model-citizen/policy/x.py"):
            with self.subTest(path=path):
                self.assertEqual(BENCH.installed_checkout_reads(self.message(path)),
                                 ["Read:/opt/model-citizen"])

    def test_an_unknown_symlink_name_is_not_claimed_as_observed(self):
        self.assertEqual(BENCH.installed_checkout_reads(self.message("/tmp/unknown-alias/x.py")), [])

    def test_incomplete_and_timed_out_attempts_preserve_observed_paths(self):
        stream = json.dumps(self.message("/opt/model-citizen/answer.py")[0]) + '\n{"truncated"'
        for output in (stream, subprocess.TimeoutExpired("docker", 1, output=stream.encode())):
            with self.subTest(timeout=isinstance(output, Exception)), tempfile.TemporaryDirectory() as tmp:
                opts = options(tmp)
                row = BENCH.run_one(TASK, 1, "harness", opts, Launch([output]))
                self.assertTrue(row["error"])
                self.assertIsNone(row["passed"])
                self.assertEqual(row["installed_checkout_reads"], ["Read:/opt/model-citizen"])


class ManifestExclusionTests(unittest.TestCase):
    def test_every_current_task_is_excluded_with_provenance(self):
        manifest = json.loads((REPO / BENCH.TASKS).read_text(encoding="utf-8"))
        self.assertEqual(manifest["tasks"], [])
        synthetic = [entry for entry in manifest["retired"]
                     if entry["task"]["kind"] == "synthetic"]
        self.assertEqual({entry["id"] for entry in synthetic}, {"hook-inventory", "hook-ids"})
        self.assertTrue(all("solve()" in entry["reason"] for entry in synthetic))


if __name__ == "__main__":
    unittest.main()
