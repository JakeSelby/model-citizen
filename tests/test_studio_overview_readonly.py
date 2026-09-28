# SPDX-License-Identifier: MIT
"""Overview run summaries never reconcile state or admit queued work."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import runs  # noqa: E402


class OverviewRunReadTests(unittest.TestCase):
    def test_read_only_list_neither_changes_sidecars_nor_starts_queued_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            catalog = root / "suites.json"
            catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
                "id": "fixture", "version": 1,
                "argv": [sys.executable, "-c", "print('never started')"],
                "parameters": {}, "cost_class": "free",
                "expected_duration_seconds": 1, "timeout_seconds": 30,
                "targets": ["installed"],
            }]}), encoding="utf-8")
            supervisor = runs.RunSupervisor(root / "state", catalog)
            with mock.patch.object(supervisor, "_admit_locked"):
                queued = supervisor.start("fixture", {}, "installed", "current")
            before = {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in root.rglob("*") if path.is_file()}
            with mock.patch.object(runs.subprocess, "Popen") as spawn:
                records = runs.read_only_list(root / "state")
            after = {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in root.rglob("*") if path.is_file()}

            spawn.assert_not_called()
            self.assertEqual(before, after)
            self.assertEqual(records[0]["run_id"], queued["run_id"])
            self.assertEqual(records[0]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
