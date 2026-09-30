"""CLI coverage for offline evidence-bundle verification."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_evidence_bundle as bundle_tests  # a module import keeps its tests out of this module

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/harness"


class EvidenceCliTest(unittest.TestCase):
    def setUp(self):
        self.fixture = bundle_tests.EvidenceBundleTest(methodName=
                                          "test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def invoke(self, *args):
        return subprocess.run([str(CLI), "evidence", "verify"] + list(args), cwd=str(ROOT),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_text_mode_reports_twelve_verified_items(self):
        self.fixture._mutate_rows(lambda row: row.update(observed_effort="high"))
        done = self.invoke(str(self.fixture.root))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("evidence bundle proof-1: verified; 12/12 evidence-standard items pass\n",
                         done.stdout)
        self.assertEqual("", done.stderr)

    def test_text_mode_names_unobserved_effort_as_unknown(self):
        done = self.invoke(str(self.fixture.root))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(
            "evidence bundle proof-1: verified; 12/12 evidence-standard items pass\n"
            "  unknown, not verified: bare effort: requested high, observed on 0 of 10 planned attempts\n"
            "  unknown, not verified: harness effort: requested high, observed on 0 of 10 planned attempts\n",
            done.stdout)

    def test_json_mode_emits_the_machine_result(self):
        done = self.invoke(str(self.fixture.root), "--json")
        self.assertEqual(0, done.returncode, done.stderr)
        result = json.loads(done.stdout)
        self.assertTrue(result["ok"])
        self.assertTrue(all(result["checks"].values()))

    def test_failure_uses_exit_one_and_names_each_reason(self):
        (self.fixture.root / "bundle.json").write_text('{"schema_version": NaN}')
        done = self.invoke(str(self.fixture.root))
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn("verification failed", done.stderr)
        self.assertIn("non-finite", done.stderr)

    def test_missing_bundle_is_a_deterministic_verification_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            first = self.invoke(str(missing), "--json")
            second = self.invoke(str(missing), "--json")
        self.assertEqual(1, first.returncode)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, second.stderr)


if __name__ == "__main__":
    unittest.main()
