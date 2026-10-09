"""The micro `micro-stop-gate` task scores the edit its stop gate accepts as a pass (#1170).

The rename leaves the tests that call the old name red, the stop gate refuses that tree, and the
only way past it is to update those tests. The check must pass that tree and fail the one that
leaves the tests red, and the prompt must allow the edit."""
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH

REPO = BENCH.ROOT
ORACLE = BENCH._oracle(REPO, "micro_rename")
# The test modules that call the renamed function at the task's parent sha.
CALLERS = ("tests/test_harness.py", "tests/test_neutralize.py", "tests/test_usage.py")


def stop_gate_task():
    tasks = json.loads((REPO / "benchmarks" / "micro" / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    return [t for t in tasks if t["id"] == "micro-stop-gate"][0]


class MicroStopGateOracleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        archive = subprocess.check_output(["git", "archive", stop_gate_task()["parent_sha"]], cwd=str(REPO))
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(str(self.root))

    def rename(self, name):
        path = self.root / name
        path.write_text(path.read_text(encoding="utf-8").replace(ORACLE.OLD, ORACLE.NEW), encoding="utf-8")

    def test_the_parent_tree_fails(self):
        self.assertIn("%s is not defined" % ORACLE.NEW, ORACLE.check(self.root))

    def test_the_rename_with_its_tests_updated_passes(self):
        ORACLE.solve(self.root)
        self.assertEqual(ORACLE.check(self.root), [])

    def test_the_rename_that_leaves_the_tests_red_fails(self):
        self.rename(ORACLE.SCRIPT.as_posix())
        errors = ORACLE.check(self.root)
        self.assertEqual(errors, ["%s still calls %s" % (name, ORACLE.OLD) for name in sorted(CALLERS)])

    def test_the_rename_whose_tests_still_fail_fails(self):
        ORACLE.solve(self.root)
        path = self.root / CALLERS[2]
        path.write_text(path.read_text(encoding="utf-8") + "\n\nclass Broken(unittest.TestCase):\n"
                        "    def test_red(self):\n        self.fail(%r)\n" % ORACLE.NEW, encoding="utf-8")
        errors = ORACLE.check(self.root)
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith("the visible tests fail: exit 1, FAILED"), errors)

    def test_a_test_whose_call_was_deleted_instead_of_renamed_fails(self):
        ORACLE.solve(self.root)
        path = self.root / CALLERS[0]
        path.write_text(path.read_text(encoding="utf-8").replace(ORACLE.NEW, "dict"), encoding="utf-8")
        self.assertEqual(ORACLE.check(self.root), ["%s no longer calls %s" % (CALLERS[0], ORACLE.NEW)])

    def test_a_removed_caller_fails(self):
        ORACLE.solve(self.root)
        (self.root / CALLERS[1]).unlink()
        self.assertEqual(ORACLE.check(self.root), ["%s is missing" % CALLERS[1]])

    def test_the_oracle_names_every_test_that_calls_it_at_the_parent_sha(self):
        listed = subprocess.check_output(["git", "grep", "-l", ORACLE.OLD, stop_gate_task()["parent_sha"], "--", "tests"],
                                         cwd=str(REPO)).decode("utf-8").split()
        self.assertEqual(sorted(name.split(":", 1)[1] for name in listed), sorted(CALLERS))
        self.assertEqual(sorted(getattr(ORACLE, "CALLERS", ())), sorted(CALLERS))

    def test_the_prompt_allows_updating_the_tests(self):
        prompt = " ".join(stop_gate_task()["prompt"])
        self.assertIn("the tests that call the function", prompt)
        self.assertNotIn("Edit only `bin/harness`, run nothing", prompt)


if __name__ == "__main__":
    unittest.main()
