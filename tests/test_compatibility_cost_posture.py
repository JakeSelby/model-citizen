"""The cost posture layer must be exercised natively, without rewriting earlier releases.

A client could pass every case that predates the layer while the release's main change never ran
natively, so `cost-posture` is required from 0.11.0. Evidence is scoped to the harness version it
records, which is what keeps 0.9.0 and 0.10.0 records valid as history under their own catalogs.
"""
import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch
from test_harness import REPO
from harness_core import compatibility

HISTORICAL = REPO / "compatibility" / "evidence" / "claude-code-cli-macos-0.10.0.json"


def linked(path, record):
    """A client entry that links one evidence file, matching its native identity."""
    return {"id": record["client"], "runtime_version": record["runtime_version"],
            "client_version": record["client_version"], "platform": record["platform"],
            "evidence": [{"path": str(path.relative_to(REPO)),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}


class RequiredCaseTests(unittest.TestCase):
    def setUp(self):
        self.cases = compatibility.catalog(REPO)["required_cases"]

    def test_the_cost_posture_case_is_required_after_role_confinement(self):
        self.assertIn("cost-posture", self.cases)
        self.assertEqual(self.cases[self.cases.index("role-confinement") + 1], "cost-posture")

    def test_the_procedure_names_the_layer_observables(self):
        procedure = (REPO / "docs" / "compatibility.md").read_text()
        step = procedure[procedure.index("\n8. "):procedure.index("\nStore a redacted")]
        step = " ".join(step.split())  # wrapping is not stable; the observables are.
        for needle in ("cost variant", "keeps its link", "default band worker", "effort",
                       "budget sentence", "usage feed", "citizen usage --rescan --by role",
                       "started before the workers were installed",
                       "keeps them out of its session record", "announced band worker", "feed off",
                       "does not route native spawns", "not applicable"):
            self.assertIn(needle, step, msg=needle)


class EvidenceScopeTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(HISTORICAL.read_text())
        self.client = linked(HISTORICAL, self.record)
        run = compatibility.subprocess.run

        def historical_source(command, **kwargs):
            if "merge-base" in command or "diff" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            return run(command, **kwargs)

        self.git = patch.object(compatibility.subprocess, "run", side_effect=historical_source)
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_the_cases_added_since_the_historical_record_are_the_ones_it_lacks(self):
        """Explicit, so adding a required case is a deliberate edit here rather than a surprise."""
        required = compatibility.catalog(REPO)["required_cases"]
        self.assertEqual(set(required) - set(self.record["cases"]),
                         {"cost-posture", "spawn-confinement", "framework-spawn-routing"})

    def test_a_release_evidence_record_still_validates_under_its_own_catalog(self):
        before = HISTORICAL.read_bytes()
        data = {"harness_version": self.record["harness_version"],
                "release_state": "released",
                "qualification_source_commit": self.record["source_commit"],
                "required_cases": sorted(self.record["cases"])}
        self.assertEqual(compatibility.evidence_errors(REPO, data, self.client), [])
        self.assertEqual(HISTORICAL.read_bytes(), before)

    def test_the_current_catalog_rejects_it_by_version_rather_than_by_the_new_case(self):
        before = HISTORICAL.read_bytes()
        errors = compatibility.evidence_errors(REPO, compatibility.catalog(REPO), self.client)
        self.assertIn("evidence version or client mismatch", errors)
        self.assertNotIn("unknown acceptance case or result", errors)
        self.assertEqual(HISTORICAL.read_bytes(), before)

    def test_current_evidence_without_the_case_cannot_qualify_a_client(self):
        data = compatibility.catalog(REPO)
        record = dict(self.record, harness_version=data["harness_version"])
        record["cases"] = {case: "passed" for case in sorted(record["cases"])}
        with patch.object(compatibility.Path, "read_bytes",
                          return_value=json.dumps(record).encode()):
            client = dict(self.client)
            client["evidence"] = [dict(client["evidence"][0],
                                       sha256=hashlib.sha256(json.dumps(record).encode()).hexdigest())]
            errors = compatibility.evidence_errors(REPO, data, client)
        missing = [line for line in errors if line.startswith("missing acceptance cases: ")]
        self.assertEqual(len(missing), 1)
        self.assertIn("cost-posture", missing[0].split(": ", 1)[1].split(", "))


if __name__ == "__main__":
    unittest.main()
