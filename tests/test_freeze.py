"""A qualification round is frozen at a commit; source drift invalidates its evidence."""
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO, harness
from harness_core import compatibility


class FreezeRecordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "compatibility").mkdir()

    def write(self, data):
        (self.root / "compatibility" / "freeze.json").write_text(json.dumps(data))

    def test_the_repository_record_is_readable_and_consistent_with_its_state(self):
        data = compatibility.freeze_record(REPO)
        self.assertIn(data["state"], compatibility.FREEZE_STATES)
        if data["state"] == "frozen":
            self.assertTrue(data["branch"].startswith("release/"))
            self.assertRegex(data["commit"], r"^[0-9a-f]{40,64}$")

    def test_the_repository_record_names_no_released_branch(self):
        data = json.loads((REPO / "compatibility" / "catalog.json").read_text())
        tagged = subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify",
                                 "refs/tags/v" + data["harness_version"]],
                                capture_output=True).returncode == 0
        self.assertEqual(compatibility.stale_freeze_errors(
            compatibility.freeze_record(REPO), data, tagged), [])

    def test_a_record_frozen_at_a_released_branch_is_refused(self):
        released = {"harness_version": "0.14.2", "release_state": "released"}
        frozen = {"schema_version": 1, "state": "frozen", "branch": "release/v0.14.2",
                  "commit": "040462636205d8edc766775ebe2cc9571ac6b217"}
        errors = compatibility.stale_freeze_errors(frozen, released, True)
        self.assertEqual(len(errors), 1)
        self.assertIn("release/v0.14.2", errors[0])
        candidate = dict(released, release_state="candidate")
        self.assertEqual(compatibility.stale_freeze_errors(frozen, candidate, True), [])
        self.assertEqual(compatibility.stale_freeze_errors(
            dict(frozen, branch="release/v0.14.3"), released, True), [])
        self.assertEqual(compatibility.stale_freeze_errors({"state": "open"}, released, True), [])
        # The release commit precedes its tag and may still be frozen.
        self.assertEqual(compatibility.stale_freeze_errors(frozen, released, False), [])

    def test_a_missing_record_means_no_branch_is_frozen(self):
        self.assertEqual(compatibility.freeze_record(self.root)["state"], "open")

    def test_an_open_record_needs_no_branch_or_commit(self):
        self.write({"schema_version": 1, "state": "open", "branch": None, "commit": None})
        self.assertEqual(compatibility.freeze_record(self.root)["state"], "open")

    def test_a_frozen_record_requires_a_branch_and_a_full_commit(self):
        cases = [
            ({"schema_version": 2, "state": "open"}, "unsupported freeze schema"),
            ({"schema_version": 1, "state": "thawed"}, "freeze state must be one of"),
            ({"schema_version": 1, "state": "frozen", "commit": "a" * 40}, "requires its branch name"),
            ({"schema_version": 1, "state": "frozen", "branch": "  ", "commit": "a" * 40},
             "requires its branch name"),
            ({"schema_version": 1, "state": "frozen", "branch": "release/v1.0.0", "commit": "abc"},
             "full commit identity"),
            ({"schema_version": 1, "state": "frozen", "branch": "release/v1.0.0", "commit": None},
             "full commit identity"),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaisesRegex(ValueError, message):
                    compatibility.freeze_record(self.root)


class FreezeDriftTests(unittest.TestCase):
    """The drift check a release handoff otherwise runs by hand, on a real repository."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name) / "work"
        self.work.mkdir(parents=True)
        self.git("init", "--quiet", "-b", "main")
        (self.work / "docs").mkdir()
        (self.work / "bin").mkdir()
        self.write("VERSION", "1.0.0\n")
        self.write("bin/harness", "print('one')\n")
        self.write("docs/releasing.md", "first\n")
        self.frozen = self.commit("freeze commit")
        self.data = {"schema_version": 1, "state": "frozen", "branch": "release/v1.0.0",
                     "commit": self.frozen}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.work), "-c", "user.name=t",
                                        "-c", "user.email=t", *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def write(self, name, text):
        (self.work / name).write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_an_unchanged_tip_reports_no_drift_and_no_merge_refusal(self):
        self.write("docs/releasing.md", "second\n")
        self.commit("docs only")
        result = compatibility.freeze_drift(self.work, self.data, "main")
        self.assertEqual(result["paths"], [])
        self.assertEqual(result["stat"], "")
        self.assertEqual(result["ref"], "main")
        self.assertEqual(compatibility.merge_refusal(self.work, self.data, "main"), [])

    def test_a_runtime_source_change_is_named_path_by_path_with_a_stat(self):
        self.write("bin/harness", "print('two')\n")
        self.write("VERSION", "1.0.1\n")
        self.write("docs/releasing.md", "second\n")
        self.commit("source change")
        result = compatibility.freeze_drift(self.work, self.data, "main")
        self.assertEqual(result["paths"], ["VERSION", "bin/harness"])
        self.assertIn("bin/harness", result["stat"])
        self.assertNotIn("docs/releasing.md", result["stat"])

    def test_a_merge_into_the_frozen_branch_is_refused_for_source_paths(self):
        self.git("checkout", "--quiet", "-b", "feature")
        self.write("bin/harness", "print('three')\n")
        self.commit("feature source change")
        refusals = compatibility.merge_refusal(self.work, self.data, "feature")
        self.assertEqual(len(refusals), 1)
        self.assertIn("release/v1.0.0 is frozen at " + self.frozen[:12], refusals[0])
        self.assertIn("bin/harness", refusals[0])

    def test_an_open_record_compares_nothing_and_refuses_nothing(self):
        data = {"schema_version": 1, "state": "open"}
        self.write("bin/harness", "print('two')\n")
        self.commit("source change")
        self.assertEqual(compatibility.freeze_drift(self.work, data, "main")["paths"], [])
        self.assertEqual(compatibility.merge_refusal(self.work, data, "main"), [])

    def test_an_unknown_revision_fails_closed_rather_than_reporting_no_drift(self):
        with self.assertRaisesRegex(ValueError, "git diff failed"):
            compatibility.freeze_drift(self.work, self.data, "no-such-ref")

    def test_the_subcommand_reports_drift_as_json_and_a_nonzero_status(self):
        self.write("bin/harness", "print('two')\n")
        self.commit("source change")
        with patch.object(harness, "REPO", self.work), \
                patch.object(compatibility, "freeze_record", return_value=self.data):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                status = harness.main(["freeze", "--against", "main", "--json"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(payload["paths"], ["bin/harness"])
        self.assertEqual(payload["branch"], "release/v1.0.0")

    def test_the_subcommand_merge_check_fails_the_command(self):
        self.git("checkout", "--quiet", "-b", "feature")
        (self.work / "primitives").mkdir()
        self.write("primitives/rule.md", "x\n")
        self.commit("feature primitive")
        with patch.object(harness, "REPO", self.work), \
                patch.object(compatibility, "freeze_record", return_value=self.data), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(harness.main(["freeze", "--merge-check", "feature",
                                           "--against", "main"]), 1)
            self.assertEqual(harness.main(["freeze", "--merge-check", "main",
                                           "--against", "main"]), 0)
        self.assertIn("merge refused", errors.getvalue())

    def test_the_subcommand_refuses_an_unreadable_record_rather_than_passing(self):
        with patch.object(harness, "REPO", self.work), \
                patch.object(compatibility, "freeze_record",
                             side_effect=ValueError("unsupported freeze schema")), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(harness.main(["freeze"]), 1)
        self.assertIn("unsupported freeze schema", errors.getvalue())


class ReleaseDocTests(unittest.TestCase):
    def test_the_release_doc_names_the_freeze_and_the_mid_round_triage_rule(self):
        text = (REPO / "docs" / "releasing.md").read_text()
        self.assertIn("compatibility/freeze.json", text)
        self.assertIn("citizen freeze --merge-check", text)
        self.assertIn("Fix no defect mid-round", text)


if __name__ == "__main__":
    unittest.main()
