# SPDX-License-Identifier: MIT
"""Studio spend rows remain distinct, idempotent, priced, and visible in usage reports."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = load("harness_usage_studio", REPO / "bin" / "harness")
usage_log = load("usage_log_studio", REPO / "claude" / "hooks" / "usage-log.py")
telemetry = load("telemetry_studio", REPO / "claude" / "hooks" / "telemetry.py")


class StudioUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = self.root / "usage.jsonl"
        self.ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def row(self, run_id, spend):
        return {
            "kind": "studio_run", "runtime": "studio", "run_id": run_id,
            "suite_id": "paid-suite", "pricing_source": "api_credit",
            "spend_usd": spend, "spend_cap_usd": "5.0", "ended": self.ended,
        }

    def rows(self):
        return [json.loads(line) for line in
                self.ledger.read_text(encoding="utf-8").splitlines()]

    def test_run_id_is_the_usage_identity_and_retry_replaces_the_same_run(self):
        usage_log.upsert(self.row("run-a", 1.25), path=self.ledger)
        usage_log.upsert(self.row("run-b", 2.5), path=self.ledger)
        usage_log.upsert(self.row("run-a", 1.5), path=self.ledger)
        rows = self.rows()
        self.assertEqual([(row["run_id"], row["spend_usd"]) for row in rows],
                         [("run-b", 2.5), ("run-a", 1.5)])
        self.assertEqual({row["schema_version"] for row in rows}, {2})

    def test_telemetry_prices_studio_spend_and_deduplicates_by_run_id(self):
        first, second = self.row("run-a", 1.25), self.row("run-b", 2.5)
        self.assertNotEqual(telemetry.row_key(first), telemetry.row_key(second))
        self.assertEqual(telemetry.row_prices([first], {})[0],
                         (1.25, self.ended[:10]))
        attrs = {item["key"]: item["value"]
                 for item in telemetry.attributes(first, price=(1.25, self.ended[:10]))}
        self.assertEqual(attrs["harness.usd"], {"doubleValue": 1.25})
        self.assertEqual(attrs["harness.row_key"],
                         {"stringValue": "run-a|studio|studio_run|"})

    def test_usage_report_counts_studio_spend_without_token_pricing(self):
        usage_log.upsert(self.row("run-a", 2.5), path=self.ledger)
        args = argparse.Namespace(action=None, days=30, by="day", rules=False,
                                  rescan=False, conflicts=False, stance=None)
        output = io.StringIO()
        with patch.object(harness, "state_dir", return_value=self.root), \
             contextlib.redirect_stdout(output):
            self.assertEqual(harness.cmd_usage(args), 0)
        shown = output.getvalue()
        self.assertRegex(shown, r"TOTAL\s+1(?:\s+0){4}\s+0%\s+2\.50")
        self.assertIn("unpriced: none", shown)


if __name__ == "__main__":
    unittest.main()
