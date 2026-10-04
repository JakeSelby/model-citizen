# SPDX-License-Identifier: MIT
"""Severity of the live BMad audit: what warns and what still fails."""

import importlib.util
import io
import re
import unittest
from contextlib import redirect_stdout
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("bmad_issue_sync", REPO / "scripts" / "bmad_issue_sync.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def issue(number, title, state="open"):
    return {
        "id": number * 10,
        "number": number,
        "title": title,
        "state": state,
        "body": "Original body",
        "html_url": "https://github.com/owner/repo/issues/{}".format(number),
        "labels": [],
        "type": None,
        "parent_issue_url": None,
    }


class UnreservedSeverityTests(unittest.TestCase):
    def setUp(self):
        self.mapped = [issue(1, "feat: first")]
        self.manifest = sync.build_manifest(self.mapped, "owner/repo")
        item = self.manifest["items"][0]
        self.mapped[0]["body"] = sync.upsert_planning_block("Original body", sync.planning_block(item, "owner/repo"))
        self.mapped[0]["labels"] = [{"name": "type::{}".format(item["type"])}]
        self.unreserved = issue(2, "fix: accepted, never reserved")
        self.unreserved["author_association"] = "OWNER"

    def test_unreserved_accepted_issue_is_a_finding_by_default(self):
        findings, notices = sync.live_findings(self.manifest, self.mapped + [self.unreserved])
        self.assertEqual(findings, ["#2: accepted issue has no BMad ID; run reserve"])
        self.assertEqual(notices, [])

    def test_unreserved_accepted_issue_is_a_warning_when_asked(self):
        findings, notices = sync.live_findings(
            self.manifest, self.mapped + [self.unreserved], check_unreserved=False
        )
        self.assertEqual(findings, [])
        self.assertEqual(notices, ["warning: #2: accepted issue has no BMad ID; run reserve"])

    def test_grace_period_still_wins_over_the_warning(self):
        self.unreserved["created_at"] = "2026-01-10T12:00:00Z"
        now = sync.dt.datetime(2026, 1, 11, 12, 0, 0)
        _, notices = sync.live_findings(
            self.manifest, self.mapped + [self.unreserved], grace_days=2, now=now, check_unreserved=False
        )
        self.assertEqual(notices, ["#2: accepted, no BMad ID yet, inside the grace period"])

    def test_other_finding_classes_stay_errors_when_unreserved_warns(self):
        live = [dict(self.mapped[0], title="feat: renamed", body="Original body", labels=[])]
        live[0]["parent_issue_url"] = "https://api.github.com/repos/owner/repo/issues/9"
        epic = issue(9, "Deliver the epic")
        epic["author_association"] = "OWNER"
        manifest = sync.build_manifest([self.mapped[0], issue(3, "feat: gone")], "owner/repo")
        findings, _ = sync.live_findings(manifest, live + [epic], check_unreserved=False)
        kinds = sorted({re.sub(r"^\S+ ?#\d+: |^#\d+: ", "", f).split(";")[0].split(",")[0] for f in findings})
        self.assertEqual(
            kinds,
            [
                "GitHub records parent #9",
                "mapped issue was not found on GitHub",
                "projection drift (planning-block",
                "title drift",
            ],
        )


class AuditCommandSeverityTests(unittest.TestCase):
    def setUp(self):
        self.case = UnreservedSeverityTests("test_unreserved_accepted_issue_is_a_finding_by_default")
        self.case.setUp()

    def run_audit(self, argv):
        with mock.patch.object(sync, "load_manifest", return_value=self.case.manifest), mock.patch.object(
            sync, "audit_manifest", return_value=[]
        ), mock.patch.object(
            sync, "fetch_issues", return_value=self.case.mapped + [self.case.unreserved]
        ), redirect_stdout(io.StringIO()) as out:
            code = sync.main(argv)
        return code, out.getvalue()

    def test_full_live_audit_still_fails_on_an_unreserved_issue(self):
        code, out = self.run_audit(["audit", "--live"])
        self.assertEqual(code, 1)
        self.assertIn("#2: accepted issue has no BMad ID; run reserve\n", out)
        self.assertIn("1 finding(s)\n", out)

    def test_warn_unreserved_passes_and_prints_a_warning_line(self):
        code, out = self.run_audit(["audit", "--live", "--warn-unreserved"])
        self.assertEqual(code, 0)
        self.assertIn("warning: #2: accepted issue has no BMad ID; run reserve\n", out.splitlines(True))
        self.assertNotIn("notice: warning:", out)
        self.assertIn("audit: 1 issue(s), 0 finding(s), 1 warning(s)\n", out)

    def test_workflow_runs_the_live_audit_with_unreserved_as_a_warning(self):
        workflow = (REPO / ".github" / "workflows" / "bmad-traceability.yml").read_text(encoding="utf-8")
        run = next(line for line in workflow.splitlines() if "bmad_issue_sync.py audit" in line)
        self.assertIn("--live", run)
        self.assertIn("--warn-unreserved", run)


if __name__ == "__main__":
    unittest.main()
