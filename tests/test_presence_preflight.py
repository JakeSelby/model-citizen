# SPDX-License-Identifier: MIT
"""`RunSupervisor.check_start`: a paid start's request is checked before the person is asked.

The Studio and the CLI ask for Touch ID or the login password only after this passes, and show
what it returns; the token is checked against the exact request and left unused for `start`.

Run: python3 -m unittest tests.test_presence_preflight
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import harness  # noqa: F401  (puts lib/ on the path)
from harness_core import presence
from harness_core.studio import runs
from studio_target_support import FixtureTargetService


class CheckStart(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.catalog = root / "suites.json"
        suite = {"version": 1, "argv": [sys.executable, "-c", "pass"], "parameters": {},
                 "expected_duration_seconds": 1, "timeout_seconds": 20, "targets": ["installed"]}
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": [
            dict(suite, id="paid-suite", cost_class="spends_usage", cases=["one", "two"]),
            dict(suite, id="free-suite", cost_class="free", cases=["one"])]}), encoding="utf-8")
        self.supervisor = runs.RunSupervisor(root / "studio", self.catalog, 1,
                                             target_service=FixtureTargetService())

    def preview(self, maximum="0.20", cap="1.00"):
        return self.supervisor.spend_preview("paid-suite", {}, "installed", "current", maximum,
                                             cap, "api_credit")

    def check(self, token, maximum="0.20", cap="1.00", suite="paid-suite"):
        return self.supervisor.check_start(suite, {}, "installed", "current", confirmed=token,
                                           max_budget_usd=maximum, spend_cap_usd=cap,
                                           pricing_source="api_credit")

    def test_a_matching_token_returns_what_the_dialog_names_and_stays_usable(self):
        token = self.preview()["confirmation_token"]
        checked = self.check(token)
        self.assertEqual({key: checked[key] for key in ("suite_id", "target_kind", "target_ref",
                                                        "case_count", "max_budget_usd",
                                                        "spend_cap_usd", "pricing_source")},
                         {"suite_id": "paid-suite", "target_kind": "installed",
                          "target_ref": "current", "case_count": 2, "max_budget_usd": "0.20",
                          "spend_cap_usd": "1.00", "pricing_source": "api_credit"})
        self.assertEqual(self.check(token)["case_count"], 2)  # not consumed
        reason = presence.spend_reason(checked)
        for part in ("paid-suite", "2 case(s)", "installed current", "$0.20", "$1.00"):
            self.assertIn(part, reason)

    def test_a_wrong_missing_or_changed_request_is_refused(self):
        token = self.preview()["confirmation_token"]
        for case in (("f" * 64,), (None,), (token, "0.30"), (token, "0.20", "2.00")):
            with self.subTest(case=case), self.assertRaises(runs.RunError):
                self.check(*case)

    def test_a_free_suite_needs_no_person(self):
        with self.assertRaises(runs.RunError):
            self.check("a" * 64, suite="free-suite")


if __name__ == "__main__":
    unittest.main()
