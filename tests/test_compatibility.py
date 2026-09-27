"""Published support cannot be inferred from generated files or empty evidence."""
import json
import hashlib
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO
from harness_core import compatibility


class CompatibilityTests(unittest.TestCase):
    def test_current_catalog_is_honest_and_blocks_release_only_for_its_state(self):
        data = compatibility.catalog(REPO)
        required = [row["id"] for row in data["clients"] if row.get("required_for_release")]
        # 0.14.0, like 0.13.x, requires the two Claude Code CLI targets. Codex CLI stays out: FR-12
        # admits it only once a scripted round agrees with a hand-driven one (#700), and no such
        # round has run. codex-cli-linux ships as a stated limitation for the same reason.
        self.assertEqual(required, ["claude-code-cli-macos", "claude-code-cli-linux"])
        # Released: exactly the required clients are qualified. Candidate: none is yet.
        qualified = [row["id"] for row in data["clients"] if row["status"] == "qualified"]
        self.assertEqual(qualified, required if data.get("release_state") == "released" else [])
        self.assertTrue(all(row["status"] in compatibility.STATES for row in data["clients"]))
        # The expectation follows the catalog's state so it survives a release without an edit.
        # Released: changed runtime source must block publication and nothing else, leaving the
        # released evidence intact. Candidate: exactly the required clients still unqualified.
        if data.get("release_state") == "released":
            drift = ["current runtime source differs from the released qualification source"]
            expected = drift if compatibility.source_drift(REPO, data) else []
        else:
            expected = [row["id"] + " is unqualified" for row in data["clients"]
                        if row.get("required_for_release") and row["status"] != "qualified"]
        self.assertEqual(compatibility.release_errors(REPO), expected)

    def test_qualified_claim_without_native_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO / "compatibility", root / "compatibility")
            shutil.copy(REPO / "VERSION", root / "VERSION")
            path = root / "compatibility" / "catalog.json"
            data = json.loads(path.read_text())
            data["release_state"] = "candidate"
            data.pop("qualification_source_commit", None)
            data["clients"][0]["status"] = "qualified"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "missing acceptance"):
                compatibility.catalog(root)

    def test_custom_stance_reports_advisory_coverage_for_both_adapters(self):
        result = compatibility.coverage(REPO, {"feedback": "direct"})
        for runtime in ("codex", "claude-code"):
            self.assertEqual(result[runtime]["feedback"]["mode"], "instruction")

    def test_released_catalog_stays_readable_while_changed_source_blocks_release(self):
        data = compatibility.catalog(REPO)
        data = dict(data, release_state="released", qualification_source_commit="a" * 40)
        with patch.object(compatibility, "catalog", return_value=data), \
                patch.object(compatibility, "source_drift", return_value=True):
            self.assertIn("current runtime source differs", compatibility.release_errors(REPO)[-1])

    def test_released_catalog_fails_when_qualification_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO / "compatibility", root / "compatibility")
            shutil.copy(REPO / "VERSION", root / "VERSION")
            path = root / "compatibility" / "catalog.json"
            data = json.loads(path.read_text())
            data.update(release_state="released", qualification_source_commit="a" * 40)
            path.write_text(json.dumps(data))
            with patch.object(compatibility.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaisesRegex(ValueError, "source commit is unavailable"):
                    compatibility.catalog(root)


class QualificationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {"harness_version": "0.9.0", "required_cases": ["read", "write-denial"]}
        self.client = {"id": "fixture-cli", "runtime_version": "1.2", "client_version": "3.4",
                       "platform": "fixture-os", "evidence": []}
        self.record = {"kind": "native", "client": self.client["id"], "harness_version": "0.9.0",
                       "runtime_version": "1.2", "client_version": "3.4", "platform": "fixture-os",
                       "source_commit": "a" * 40, "observations": ["Synthetic validation fixture"],
                       "cases": {"read": "passed", "write-denial": "passed"}}
        self.git = patch.object(compatibility.subprocess, "run", return_value=subprocess.CompletedProcess([], 0))
        self.git.start()
        self.addCleanup(self.git.stop)

    def add_record(self, record):
        path = self.root / ("evidence-" + str(len(self.client["evidence"])) + ".json")
        path.write_text(json.dumps(record))
        self.client["evidence"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def errors(self):
        return compatibility.evidence_errors(self.root, self.data, self.client)

    def test_matching_complete_records_can_qualify(self):
        self.add_record(self.record)
        self.assertEqual(self.errors(), [])

    def test_released_evidence_is_checked_against_its_pinned_source(self):
        target = "b" * 40
        self.data.update(release_state="released", qualification_source_commit=target)
        self.add_record(self.record)
        with patch.object(compatibility.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(self.errors(), [])
        calls = [call.args[0] for call in run.mock_calls]
        self.assertTrue(any(command[:5] == ["git", "-C", str(self.root), "merge-base", "--is-ancestor"]
                            and command[-1] == target for command in calls))

    def test_released_catalog_requires_a_full_source_commit(self):
        for value in (None, "short", "z" * 40):
            with self.subTest(value=value):
                data = dict(self.data, release_state="released", qualification_source_commit=value)
                with self.assertRaisesRegex(ValueError, "full qualification source commit"):
                    compatibility.qualification_source(data)

    def test_partial_passing_records_can_cover_distinct_cases(self):
        for case in self.data["required_cases"]:
            self.add_record(dict(self.record, cases={case: "passed"}))
        self.assertEqual(self.errors(), [])

    def test_failed_and_unverified_records_cannot_be_hidden_by_a_pass(self):
        self.add_record(self.record)
        for result in ("failed", "unverified"):
            with self.subTest(result=result):
                self.client["evidence"] = self.client["evidence"][:1]
                self.add_record(dict(self.record, cases={"read": result}))
                self.assertIn("read is " + result + " in linked evidence", self.errors())

    def test_native_versions_and_platform_must_match(self):
        for key in ("runtime_version", "client_version", "platform"):
            for value in (None, "different"):
                with self.subTest(key=key, value=value):
                    self.client["evidence"] = []
                    self.add_record(dict(self.record, **{key: value}))
                    self.assertTrue(any("mismatch" in error for error in self.errors()))

    def test_unknown_cases_results_and_invalid_records_fail_closed(self):
        for record in ([], dict(self.record, cases=[]), dict(self.record, cases={}),
                       dict(self.record, cases={"unknown": "passed"}),
                       dict(self.record, cases={"read": "skip"})):
            with self.subTest(record=record):
                self.client["evidence"] = []
                self.add_record(record)
                self.assertTrue(self.errors())

    def test_changed_evidence_cannot_reuse_a_digest(self):
        self.add_record(self.record)
        (self.root / self.client["evidence"][0]["path"]).write_text("{}")
        self.assertIn("evidence digest mismatch", self.errors())

    def test_evidence_replaced_after_read_cannot_change_verified_record(self):
        failed = dict(self.record, cases={"read": "failed", "write-denial": "passed"})
        self.add_record(failed)
        original_read = Path.read_bytes

        def replace_after_read(path):
            content = original_read(path)
            path.write_text(json.dumps(self.record))
            return content

        with patch.object(Path, "read_bytes", replace_after_read):
            self.assertIn("read is failed in linked evidence", self.errors())

    def test_unreadable_evidence_fails_closed(self):
        self.add_record(self.record)
        with patch.object(Path, "read_bytes", side_effect=OSError("fixture read failure")):
            self.assertIn("unreadable evidence record", self.errors())
