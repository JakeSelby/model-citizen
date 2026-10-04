# SPDX-License-Identifier: MIT
"""Tests for BMad/GitHub traceability."""

import importlib.util
import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("bmad_issue_sync", REPO / "scripts" / "bmad_issue_sync.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def issue(number, title, state="open", labels=None):
    return {
        "id": number * 10,
        "number": number,
        "title": title,
        "state": state,
        "body": "Original body",
        "html_url": "https://github.com/JakeSelby/agent-harness/issues/{}".format(number),
        "labels": [{"name": label} for label in (labels or [])],
        "type": None,
        "parent_issue_url": None,
    }


class ClassificationTests(unittest.TestCase):
    def test_types_are_stable_and_hierarchy_is_separate(self):
        manifest = sync.build_manifest(
            [
                issue(52, "docs(bmad): decide the builder-routing follow-up", state="closed", labels=["docs"]),
                issue(80, "fix(installer): install crashes", state="closed", labels=["bug"]),
                issue(93, "Deliver a provider-agnostic harness"),
                issue(94, "Unify primitive contracts"),
                issue(141, "Preserve a deferred experiment"),
            ],
            "JakeSelby/agent-harness",
        )
        items = {item["github_number"]: item for item in manifest["items"]}
        self.assertEqual(items[52]["bmad_id"], "AH-D001")
        self.assertEqual(items[80]["bmad_id"], "AH-B001")
        self.assertEqual(items[93]["bmad_id"], "AH-E001")
        self.assertEqual(items[94]["parent_bmad_id"], "AH-E001")
        self.assertEqual(items[141]["bmad_id"], "AH-SP001")

    def test_completed_preexisting_issue_is_marked_reconstructed(self):
        item = sync.build_manifest([issue(1, "feat: first", state="closed")], "owner/repo")["items"][0]
        self.assertEqual(item["lifecycle"], "completed")
        self.assertEqual(item["provenance"], "reconstructed")

    def test_mapping_is_deterministic_when_live_issues_are_unsorted(self):
        forward = sync.build_manifest([issue(1, "first"), issue(2, "second")], "owner/repo")
        reverse = sync.build_manifest([issue(2, "second"), issue(1, "first")], "owner/repo")
        self.assertEqual(forward, reverse)


class PlanningBlockTests(unittest.TestCase):
    def setUp(self):
        self.item = sync.build_manifest([issue(1, "feat: first")], "JakeSelby/agent-harness")["items"][0]

    def test_upsert_preserves_original_body_and_is_idempotent(self):
        block = sync.planning_block(self.item, "JakeSelby/agent-harness")
        first = sync.upsert_planning_block("Original body", block)
        second = sync.upsert_planning_block(first, block)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("Original body"))
        self.assertEqual(first.count(sync.BEGIN), 1)
        self.assertIn("blob/main/" + self.item["artifact_path"], first)

    def test_upsert_collapses_complete_duplicates_without_deleting_prose(self):
        block = sync.planning_block(self.item, "JakeSelby/agent-harness")
        body = "Before\n\n{}\n\nBetween\n\n{}\n\nAfter".format(block, block)
        result = sync.upsert_planning_block(body, block)
        self.assertEqual(result.count(sync.BEGIN), 1)
        self.assertEqual(result.count(sync.END), 1)
        self.assertIn("Before", result)
        self.assertIn("Between", result)
        self.assertIn("After", result)

    def test_upsert_refuses_unmatched_or_nested_fences(self):
        block = sync.planning_block(self.item, "JakeSelby/agent-harness")
        malformed = [
            "Prose\n{}".format(sync.BEGIN),
            "Prose\n{}".format(sync.END),
            "{}\n{}\n{}\n{}".format(sync.BEGIN, sync.BEGIN, sync.END, sync.END),
        ]
        for body in malformed:
            with self.subTest(body=body):
                with self.assertRaisesRegex(RuntimeError, "malformed BMad Planning fences"):
                    sync.upsert_planning_block(body, block)


class AuditTests(unittest.TestCase):
    def test_audit_requires_an_explicit_native_type_projection_mode(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        manifest["native_type_projection"] = "unknown"
        findings = sync.audit_manifest(manifest)
        self.assertIn(
            "native_type_projection must be labels-only or native-and-labels",
            findings,
        )

    def test_audit_accepts_bidirectional_mapping_and_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
            old_root = sync.ROOT
            try:
                sync.ROOT = root
                path = root / manifest["items"][0]["artifact_path"]
                path.parent.mkdir(parents=True)
                path.write_text(sync.render_artifact(manifest["items"][0]), encoding="utf-8")
                self.assertEqual(sync.audit_manifest(manifest), [])
                manifest["items"].append(dict(manifest["items"][0]))
                self.assertTrue(any("duplicate BMad ID" in finding for finding in sync.audit_manifest(manifest)))
            finally:
                sync.ROOT = old_root

    def test_audit_names_missing_broken_parent_and_reverse_link_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = sync.build_manifest(
                [issue(93, "Epic"), issue(94, "Child"), issue(101, "Missing")], "owner/repo"
            )
            old_root = sync.ROOT
            try:
                sync.ROOT = root
                first = root / manifest["items"][0]["artifact_path"]
                first.parent.mkdir(parents=True)
                first.write_text(sync.render_artifact(manifest["items"][0]), encoding="utf-8")
                child = root / manifest["items"][1]["artifact_path"]
                child.write_text(sync.render_artifact(manifest["items"][1]).replace(
                    'github_issue_url: "https://github.com/JakeSelby/agent-harness/issues/94"',
                    'github_issue_url: "https://github.com/JakeSelby/agent-harness/issues/999"',
                ), encoding="utf-8")
                manifest["items"][1]["parent_bmad_id"] = None
                findings = sync.audit_manifest(manifest)
            finally:
                sync.ROOT = old_root
        self.assertTrue(any("parent mapping is incomplete" in finding for finding in findings))
        self.assertTrue(any("artifact github_issue_url does not match" in finding for finding in findings))
        self.assertTrue(any("missing artifact" in finding for finding in findings))

    def test_audit_checks_every_manifest_owned_artifact_field(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        fields = (
            "bmad_id", "type", "title", "lifecycle", "provenance", "github_issue",
            "github_issue_url", "parent_bmad_id", "parent_github_issue",
        )
        with tempfile.TemporaryDirectory() as temp:
            old_root = sync.ROOT
            try:
                sync.ROOT = Path(temp)
                path = sync.ROOT / manifest["items"][0]["artifact_path"]
                path.parent.mkdir(parents=True)
                rendered = sync.render_artifact(manifest["items"][0])
                for field in fields:
                    with self.subTest(field=field):
                        changed = re.sub(
                            r"^{}: .*$".format(field),
                            '{}: "wrong"'.format(field),
                            rendered,
                            count=1,
                            flags=re.MULTILINE,
                        )
                        path.write_text(changed, encoding="utf-8")
                        findings = sync.audit_manifest(manifest)
                        self.assertTrue(any(
                            "artifact {} does not match".format(field) in finding
                            for finding in findings
                        ))
                path.write_text(rendered.replace("parent_bmad_id: null\n", ""), encoding="utf-8")
                findings = sync.audit_manifest(manifest)
                self.assertTrue(any(
                    "artifact parent_bmad_id does not match" in finding for finding in findings
                ))
            finally:
                sync.ROOT = old_root

    def test_plan_reports_each_missing_remote_projection(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        actions = sync.planned_actions(manifest, [issue(1, "feat: first")])
        self.assertEqual(actions[0]["changes"], ["planning-block", "type-label"])

    def test_plan_includes_native_type_only_when_the_repository_supports_it(self):
        manifest = sync.build_manifest(
            [issue(1, "feat: first")], "org/repo", native_type_projection="native-and-labels"
        )
        actions = sync.planned_actions(manifest, [issue(1, "feat: first")])
        self.assertEqual(actions[0]["changes"], ["planning-block", "native-type", "type-label"])

    def test_plan_replaces_stale_owned_type_labels(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        live = issue(1, "feat: first", labels=["keep-me", "type::bug", "type::story"])
        live["body"] = sync.planning_block(manifest["items"][0], "owner/repo")
        live["type"] = {"name": "Feature"}
        actions = sync.planned_actions(manifest, [live])
        self.assertEqual(actions, [{"issue": 1, "changes": ["type-label"]}])

    def test_plan_reports_missing_issue_without_mutation(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        self.assertEqual(sync.planned_actions(manifest, []), [{"issue": 1, "action": "missing"}])


class ApplyTests(unittest.TestCase):
    def test_apply_preserves_prose_and_skips_an_already_projected_issue(self):
        manifest = sync.build_manifest(
            [issue(1, "feat: first", labels=["keep-me", "type::bug"])], "owner/repo"
        )
        original = issue(1, "feat: first", labels=["keep-me", "type::bug"])
        projected = issue(1, "feat: first", labels=["keep-me", "type::story"])
        projected["body"] = sync.upsert_planning_block(
            original["body"], sync.planning_block(manifest["items"][0], "owner/repo")
        )
        projected["type"] = None
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", side_effect=[[original], [projected]]), mock.patch.object(
            sync, "gh_command"
        ), mock.patch.object(sync, "gh_json") as gh:
            sync.apply_manifest(manifest, "owner/repo")
        payload = gh.call_args.args[1]
        self.assertTrue(payload["body"].startswith("Original body"))
        self.assertEqual(payload["labels"], ["keep-me", "type::story"])
        self.assertNotIn("type", payload)

        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", side_effect=[[projected], [projected]]), mock.patch.object(
            sync, "gh_command"
        ), mock.patch.object(sync, "gh_json") as gh:
            sync.apply_manifest(manifest, "owner/repo")
        gh.assert_not_called()

    def test_apply_sets_native_type_when_the_manifest_enables_projection(self):
        manifest = sync.build_manifest(
            [issue(1, "feat: first")], "org/repo", native_type_projection="native-and-labels"
        )
        original = issue(1, "feat: first")
        projected = issue(1, "feat: first", labels=["type::story"])
        projected["body"] = sync.upsert_planning_block(
            original["body"], sync.planning_block(manifest["items"][0], "org/repo")
        )
        projected["type"] = {"name": "Feature"}
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", side_effect=[[original], [projected]]), mock.patch.object(
            sync, "gh_command"
        ), mock.patch.object(sync, "gh_json") as gh:
            sync.apply_manifest(manifest, "org/repo")
        self.assertEqual(gh.call_args.args[1]["type"], "Feature")

    def test_apply_refuses_missing_remote_artifact_before_mutation(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[manifest["items"][0]["artifact_path"]]
        ), mock.patch.object(sync, "fetch_issues") as fetch, mock.patch.object(
            sync.subprocess, "run"
        ) as run, mock.patch.object(sync, "gh_json") as gh:
            with self.assertRaisesRegex(RuntimeError, "artifacts are not on main"):
                sync.apply_manifest(manifest, "owner/repo")
        fetch.assert_not_called()
        run.assert_not_called()
        gh.assert_not_called()

    def test_apply_refuses_missing_issue_before_mutation(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", return_value=[]), mock.patch.object(
            sync.subprocess, "run"
        ) as run, mock.patch.object(sync, "gh_json") as gh:
            with self.assertRaisesRegex(RuntimeError, "mapped issues were not found"):
                sync.apply_manifest(manifest, "owner/repo")
        run.assert_not_called()
        gh.assert_not_called()

    def test_apply_refuses_malformed_fences_before_mutation(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        malformed = issue(1, "feat: first")
        malformed["body"] = "Original body\n\n{}".format(sync.BEGIN)
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", return_value=[malformed]), mock.patch.object(
            sync, "gh_command"
        ) as gh:
            with self.assertRaisesRegex(RuntimeError, "malformed BMad Planning fences"):
                sync.apply_manifest(manifest, "owner/repo")
        gh.assert_not_called()

    def test_apply_restores_prior_parent_when_reparenting_fails(self):
        manifest = sync.build_manifest([issue(93, "Epic"), issue(94, "Child")], "owner/repo")
        live = []
        for item in manifest["items"]:
            current = issue(item["github_number"], item["title"], labels=["type::{}".format(item["type"])])
            current["body"] = sync.planning_block(item, "owner/repo")
            current["type"] = {"name": item["native_type"]}
            live.append(current)
        reparented = [dict(value) for value in live]
        reparented[1]["parent_issue_url"] = "https://api.github.com/repos/owner/repo/issues/52"
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", side_effect=[live, reparented]), mock.patch.object(
            sync, "gh_command"
        ), mock.patch.object(
            sync, "gh_json", side_effect=[None, RuntimeError("add failed"), None]
        ) as gh:
            with self.assertRaisesRegex(RuntimeError, "add failed"):
                sync.apply_manifest(manifest, "owner/repo")
        endpoints = [call.args[0][-3] for call in gh.call_args_list]
        self.assertIn("repos/owner/repo/issues/52/sub_issue", endpoints[0])
        self.assertIn("repos/owner/repo/issues/93/sub_issues", endpoints[1])
        self.assertIn("repos/owner/repo/issues/52/sub_issues", endpoints[2])

    def test_label_creation_failure_uses_runtime_error_boundary(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        failure = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="label failed")
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "verify_remote_artifacts", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", return_value=[issue(1, "feat: first")]), mock.patch.object(
            sync.subprocess, "run", return_value=failure
        ):
            with self.assertRaisesRegex(RuntimeError, "label failed"):
                sync.apply_manifest(manifest, "owner/repo")


class LiveAuditTests(unittest.TestCase):
    def setUp(self):
        self.live = [issue(1, "feat: first"), issue(2, "feat: second", state="closed")]
        self.manifest = sync.build_manifest(self.live, "owner/repo")

    def projected(self, live_issue):
        item = next(i for i in self.manifest["items"] if i["github_number"] == live_issue["number"])
        live_issue["body"] = sync.upsert_planning_block(live_issue["body"], sync.planning_block(item, "owner/repo"))
        live_issue["labels"] = [{"name": "type::{}".format(item["type"])}]
        return live_issue

    def test_projected_and_current_mapping_has_no_findings(self):
        live = [self.projected(entry) for entry in self.live]
        self.assertEqual(sync.live_findings(self.manifest, live), ([], []))

    def test_open_to_completed_completed_to_active_and_title_drift(self):
        live = [self.projected(entry) for entry in self.live]
        live[0]["state"] = "closed"
        live[1]["state"] = "open"
        live[1]["title"] = "feat: renamed"
        findings, notices = sync.live_findings(self.manifest, live)
        self.assertEqual(notices, [])
        self.assertEqual(len(findings), 3)
        self.assertRegex(findings[0], r"#1: GitHub is closed but the manifest says active")
        self.assertRegex(findings[1], r"#2: title drift")
        self.assertRegex(findings[2], r"#2: GitHub is open but the manifest says completed")

    def test_lifecycle_drift_can_be_reported_without_failing(self):
        live = [self.projected(entry) for entry in self.live]
        live[0]["state"] = "closed"
        findings, notices = sync.live_findings(self.manifest, live, check_lifecycle=False)
        self.assertEqual(findings, [])
        self.assertEqual(len(notices), 1)

    def test_missing_issue_and_unprojected_issue_are_findings(self):
        findings, _ = sync.live_findings(self.manifest, [self.live[0]])
        self.assertIn("#1: projection drift (planning-block, type-label); run apply", findings)
        self.assertIn("#2: mapped issue was not found on GitHub", findings)

    def test_unmapped_issue_fails_once_accepted_and_waits_while_untriaged(self):
        live = [self.projected(entry) for entry in self.live]
        community = issue(3, "idea from a visitor")
        community["author_association"] = "NONE"
        owner = issue(4, "feat: maintainer work")
        owner["author_association"] = "OWNER"
        triaged = issue(5, "triaged idea", labels=["type::story"])
        triaged["author_association"] = "NONE"
        scheduled = issue(6, "scheduled idea")
        scheduled["milestone"] = {"title": "v1.0.0"}
        declined = issue(7, "declined", state="closed")
        declined.update(author_association="OWNER", state_reason="not_planned")
        findings, notices = sync.live_findings(self.manifest, live + [community, owner, triaged, scheduled, declined])
        self.assertEqual([finding.split(":")[0] for finding in findings], ["#4", "#5", "#6"])
        self.assertEqual([notice.split(":")[0] for notice in notices], ["#3", "#7"])

    def test_grace_period_defers_a_new_accepted_issue_only_until_it_lapses(self):
        live = [self.projected(entry) for entry in self.live]
        fresh = issue(3, "feat: filed today")
        fresh.update(author_association="OWNER", created_at="2026-01-10T12:00:00Z")
        now = sync.dt.datetime(2026, 1, 11, 12, 0, 0)
        findings, notices = sync.live_findings(self.manifest, live + [fresh], grace_days=2, now=now)
        self.assertEqual((findings, len(notices)), ([], 1))
        later = now + sync.dt.timedelta(days=2)
        findings, notices = sync.live_findings(self.manifest, live + [fresh], grace_days=2, now=later)
        self.assertEqual((len(findings), notices), (1, []))
        findings, _ = sync.live_findings(self.manifest, live + [fresh], now=now)
        self.assertEqual(len(findings), 1)

    def test_live_audit_is_read_only(self):
        with mock.patch.object(sync, "load_manifest", return_value=self.manifest), mock.patch.object(
            sync, "audit_manifest", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", return_value=self.live), mock.patch.object(
            sync.subprocess, "run"
        ) as run, mock.patch.object(Path, "write_text") as write, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sync.main(["audit", "--live"]), 1)
        run.assert_not_called()
        write.assert_not_called()
        self.assertIn("2 finding(s)", out.getvalue())

    def test_malformed_planning_fences_are_one_finding_not_the_end_of_the_audit(self):
        live = [self.projected(entry) for entry in self.live]
        live[0]["body"] += "\n" + sync.BEGIN
        findings, _ = sync.live_findings(self.manifest, live + [])
        self.assertEqual(findings, ["#1: issue body has malformed BMad Planning fences"])
        live[1]["labels"] = []
        findings, _ = sync.live_findings(self.manifest, live)
        self.assertEqual(len(findings), 2)
        self.assertIn("#2: projection drift (type-label); run apply", findings)

    def test_live_comparison_is_reported_as_skipped_when_the_local_audit_fails(self):
        with mock.patch.object(sync, "load_manifest", return_value=self.manifest), mock.patch.object(
            sync, "audit_manifest", return_value=["broken mapping"]
        ), mock.patch.object(sync, "fetch_issues") as fetch, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sync.main(["audit", "--live"]), 1)
        fetch.assert_not_called()
        self.assertIn("notice: live comparison skipped", out.getvalue())

    def test_offline_audit_never_fetches(self):
        with mock.patch.object(sync, "load_manifest", return_value=self.manifest), mock.patch.object(
            sync, "audit_manifest", return_value=[]
        ), mock.patch.object(sync, "fetch_issues") as fetch, redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(["audit"]), 0)
        fetch.assert_not_called()


class ParentDriftTests(unittest.TestCase):
    def setUp(self):
        self.live = [issue(1, "Deliver the epic"), issue(2, "feat: a child")]
        self.manifest = sync.build_manifest(self.live, "owner/repo")
        for item, entry in zip(self.manifest["items"], self.live):
            entry["body"] = sync.upsert_planning_block(entry["body"], sync.planning_block(item, "owner/repo"))
            entry["labels"] = [{"name": "type::{}".format(item["type"])}]

    def parent_in_manifest(self, number):
        parent = next(i for i in self.manifest["items"] if i["github_number"] == number)
        child = self.manifest["items"][1]
        child["parent_github_number"], child["parent_bmad_id"] = number, parent["bmad_id"]
        body = sync.upsert_planning_block(self.live[1]["body"], sync.planning_block(child, "owner/repo"))
        self.live[1]["body"] = body

    def parent_on_github(self, number):
        self.live[1]["parent_issue_url"] = "https://api.github.com/repos/owner/repo/issues/{}".format(number)

    def test_a_parent_only_github_records_is_reported_as_refresh_not_apply(self):
        self.parent_on_github(1)
        findings, _ = sync.live_findings(self.manifest, self.live)
        self.assertEqual(findings, ["AH-S002 #2: GitHub records parent #1 and the manifest records none; run refresh"])

    def test_a_parent_only_the_manifest_records_is_still_applied(self):
        self.parent_in_manifest(1)
        findings, _ = sync.live_findings(self.manifest, self.live)
        self.assertEqual(findings, ["#2: projection drift (parent); run apply"])

    def test_a_parent_that_differs_on_both_sides_is_a_conflict_for_a_human(self):
        self.parent_in_manifest(1)
        self.parent_on_github(99)
        findings, _ = sync.live_findings(self.manifest, self.live)
        self.assertEqual(
            findings, ["AH-S002 #2: parent conflict, GitHub #99 against manifest #1; decide which is right"]
        )

    def test_refresh_adopts_a_parent_github_records_and_leaves_the_rest_alone(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            sync.write_manifest(self.manifest)
            self.parent_on_github(1)
            self.assertEqual(sync.refresh(self.manifest, self.live), ["AH-S002"])
            saved = json.loads((Path(temp) / "_bmad-output" / "issue-map.json").read_text(encoding="utf-8"))
        child = saved["items"][1]
        self.assertEqual((child["parent_github_number"], child["parent_bmad_id"]), (1, "AH-S001"))
        # The parent finding is gone; the body still needs the parent line, which apply owns.
        self.assertEqual(
            sync.live_findings(self.manifest, self.live),
            (["#2: projection drift (planning-block); run apply"], []),
        )

    def test_refresh_never_overwrites_a_parent_the_manifest_already_records(self):
        self.parent_in_manifest(1)
        self.parent_on_github(99)
        with mock.patch.object(sync, "write_manifest") as write:
            self.assertEqual(sync.refresh(self.manifest, self.live), [])
        write.assert_not_called()
        self.assertEqual(self.manifest["items"][1]["parent_github_number"], 1)

    def test_an_unmapped_parent_is_reported_and_never_adopted(self):
        self.parent_on_github(99)
        findings, _ = sync.live_findings(self.manifest, self.live)
        self.assertEqual(
            findings, ["AH-S002 #2: GitHub records parent #99, which has no BMad ID; run reserve for it first"]
        )
        with mock.patch.object(sync, "write_manifest") as write:
            self.assertEqual(sync.refresh(self.manifest, self.live), [])
        write.assert_not_called()
        self.assertIsNone(self.manifest["items"][1]["parent_github_number"])


class RefreshTests(unittest.TestCase):
    def test_refresh_copies_title_and_state_into_manifest_and_artifact(self):
        live = [issue(1, "feat: first")]
        manifest = sync.build_manifest(live, "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            sync.write_manifest(manifest)
            live[0].update(state="closed", title="feat: renamed")
            self.assertEqual(sync.refresh(manifest, live), ["AH-S001"])
            saved = json.loads((Path(temp) / "_bmad-output" / "issue-map.json").read_text(encoding="utf-8"))
            self.assertEqual(sync.audit_manifest(saved), [])
            self.assertEqual(saved["items"][0]["title"], "feat: renamed")
            text = (Path(temp) / manifest["items"][0]["artifact_path"]).read_text(encoding="utf-8")
        self.assertEqual(manifest["items"][0]["lifecycle"], "completed")
        self.assertIn("# AH-S001 — feat: renamed", text)
        self.assertIn("- **State:** completed", text)

    def test_refresh_preserves_a_typed_story_body_byte_for_byte(self):
        live = [issue(1, "feat: first")]
        manifest = sync.build_manifest(live, "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            sync.write_manifest(manifest)
            path = Path(temp) / manifest["items"][0]["artifact_path"]
            text = path.read_text(encoding="utf-8")
            head, body = text.split(sync.SYNC_END, 1)
            body = body.replace("<!-- fill: As a <role>", "As a maintainer, I want \u00e9 \r\n kept.\n<!-- fill:")
            body += "\n## Amendment \u2014 kept\n\nTrailing text without a newline"
            path.write_bytes((head + sync.SYNC_END + body).encode("utf-8"))
            live[0].update(state="closed", title="feat: renamed")
            self.assertEqual(sync.refresh(manifest, live), ["AH-S001"])
            after = path.read_bytes().decode("utf-8")
            self.assertEqual(sync.audit_manifest(manifest), [])
        new_head, new_body = after.split(sync.SYNC_END, 1)
        self.assertEqual(new_body, body)
        self.assertIn("# AH-S001 \u2014 feat: renamed", new_head)
        self.assertIn("- **State:** completed", new_head)
        self.assertIn('title: "feat: renamed"', new_head)

    def test_refresh_still_refuses_an_amended_legacy_stub_before_writing_anything(self):
        live = [issue(1, "feat: first"), issue(2, "feat: second")]
        manifest = sync.build_manifest(live, "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            sync.write_manifest(manifest)
            first, second = (Path(temp) / item["artifact_path"] for item in manifest["items"])
            for path, item in zip((first, second), manifest["items"]):
                path.write_text(sync.render_legacy_stub(item), encoding="utf-8")
            second.write_text(second.read_text(encoding="utf-8") + "\n## Amendment\n\nContext.\n", encoding="utf-8")
            before = first.read_text(encoding="utf-8")
            for entry in live:
                entry["state"] = "closed"
            with self.assertRaisesRegex(RuntimeError, "carries amendments.*AH-S002"):
                sync.refresh(manifest, live)
            self.assertEqual(first.read_text(encoding="utf-8"), before)
            self.assertEqual(sync.audit_manifest(manifest), [])
        self.assertEqual([item["lifecycle"] for item in manifest["items"]], ["active", "active"])

    def test_refresh_renders_an_unamended_legacy_stub_the_old_way(self):
        live = [issue(1, "feat: first")]
        manifest = sync.build_manifest(live, "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)):
            sync.write_manifest(manifest)
            path = Path(temp) / manifest["items"][0]["artifact_path"]
            path.write_text(sync.render_legacy_stub(manifest["items"][0]), encoding="utf-8")
            live[0]["state"] = "closed"
            self.assertEqual(sync.refresh(manifest, live), ["AH-S001"])
            text = path.read_text(encoding="utf-8")
        self.assertEqual(text, sync.render_legacy_stub(manifest["items"][0]))
        self.assertNotIn(sync.SYNC_BEGIN, text)

    def test_refresh_without_drift_writes_nothing(self):
        live = [issue(1, "feat: first")]
        manifest = sync.build_manifest(live, "owner/repo")
        with mock.patch.object(sync, "write_manifest") as write:
            self.assertEqual(sync.refresh(manifest, live), [])
        write.assert_not_called()


class RemoteArtifactTests(unittest.TestCase):
    def test_remote_tree_distinguishes_present_and_missing_artifacts(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        artifact = manifest["items"][0]["artifact_path"]
        with mock.patch.object(sync, "gh_json", return_value={
            "truncated": False, "tree": [{"path": artifact}],
        }):
            self.assertEqual(sync.verify_remote_artifacts(manifest, "owner/repo"), [])
        with mock.patch.object(sync, "gh_json", return_value={"truncated": False, "tree": []}):
            self.assertEqual(sync.verify_remote_artifacts(manifest, "owner/repo"), [artifact])

    def test_remote_tree_refuses_truncated_response(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with mock.patch.object(sync, "gh_json", return_value={"truncated": True, "tree": []}):
            with self.assertRaisesRegex(RuntimeError, "truncated main tree"):
                sync.verify_remote_artifacts(manifest, "owner/repo")


class GitHubCommandTests(unittest.TestCase):
    def test_timeout_is_converted_to_runtime_error(self):
        timeout = subprocess.TimeoutExpired(["gh", "api"], sync.GH_TIMEOUT_SECONDS)
        with mock.patch.object(sync.subprocess, "run", side_effect=timeout) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out after 30 seconds"):
                sync.gh_command(["api", "repos/owner/repo"])
        self.assertEqual(run.call_args.kwargs["timeout"], sync.GH_TIMEOUT_SECONDS)


class CliTests(unittest.TestCase):
    def test_repo_override_belongs_to_bootstrap_only(self):
        map_path = mock.Mock()
        map_path.exists.return_value = False
        with mock.patch.object(sync, "MAP_PATH", map_path), mock.patch.object(
            sync, "fetch_issues", return_value=[]
        ) as fetch, mock.patch.object(sync, "write_manifest") as write:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main([
                    "bootstrap", "--repo", "owner/repo",
                    "--native-type-projection", "native-and-labels",
                ]), 0)
        fetch.assert_called_once_with("owner/repo")
        self.assertEqual(write.call_args.args[0]["native_type_projection"], "native-and-labels")
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                sync.main(["plan", "--repo", "owner/repo"])

    def test_plan_rejects_a_malformed_projection_before_fetching(self):
        manifest = sync.build_manifest([], "owner/repo")
        manifest["native_type_projection"] = "unknown"
        with mock.patch.object(sync, "load_manifest", return_value=manifest), mock.patch.object(
            sync, "audit_manifest", return_value=["invalid projection"]
        ), mock.patch.object(sync, "fetch_issues") as fetch, redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "invalid projection"):
                sync.main(["plan"])
        fetch.assert_not_called()

    def test_schema_one_manifest_defaults_to_the_original_native_projection(self):
        manifest = sync.build_manifest([], "owner/repo")
        manifest["schema_version"] = 1
        manifest.pop("native_type_projection")
        self.assertEqual(sync.projection_mode(manifest), "native-and-labels")


class ReserveTests(unittest.TestCase):
    def test_reserve_uses_next_id_without_recycling(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)), mock.patch.object(
            sync, "audit_manifest", return_value=[]
        ), mock.patch.object(
            sync, "fetch_issues", return_value=[issue(1, "feat: first"), issue(2, "feat: second")]
        ), mock.patch.object(sync, "write_manifest"):
            item = sync.reserve(manifest, "owner/repo", 2, "story", None)
        self.assertEqual(item["bmad_id"], "AH-S002")
        self.assertEqual(manifest["next_ids"]["story"], 3)

    def test_reserve_refuses_duplicate_and_missing_issue(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with self.assertRaisesRegex(RuntimeError, "already has a BMad ID"):
            sync.reserve(manifest, "owner/repo", 1, "story", None)
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "fetch_issues", return_value=[]
        ):
            with self.assertRaisesRegex(RuntimeError, "was not found"):
                sync.reserve(manifest, "owner/repo", 2, "story", None)

    def test_reserve_refuses_parent_without_bmad_id(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "fetch_issues", return_value=[issue(2, "feat: second")]
        ):
            with self.assertRaisesRegex(RuntimeError, "parent issue #99 does not have a BMad ID"):
                sync.reserve(manifest, "owner/repo", 2, "story", 99)

    def test_reserve_audits_before_fetching_or_allocating(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with mock.patch.object(sync, "audit_manifest", return_value=["broken mapping"]), mock.patch.object(
            sync, "fetch_issues"
        ) as fetch:
            with self.assertRaisesRegex(RuntimeError, "broken mapping"):
                sync.reserve(manifest, "owner/repo", 2, "story", None)
        fetch.assert_not_called()
        self.assertEqual(manifest["next_ids"]["story"], 2)

    def test_reserve_refuses_invalid_or_exhausted_counter(self):
        for sequence in (0, 1000, "2"):
            manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
            manifest["next_ids"]["story"] = sequence
            with self.subTest(sequence=sequence), mock.patch.object(
                sync, "audit_manifest", return_value=[]
            ), mock.patch.object(sync, "fetch_issues", return_value=[issue(2, "feat: second")]):
                with self.assertRaisesRegex(RuntimeError, "ID sequence is invalid"):
                    sync.reserve(manifest, "owner/repo", 2, "story", None)

    def test_reserve_refuses_preexisting_target_artifact(self):
        manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(sync, "ROOT", Path(temp)), mock.patch.object(
            sync, "audit_manifest", return_value=[]
        ), mock.patch.object(sync, "fetch_issues", return_value=[issue(2, "feat: second")]):
            target = sync.ROOT / "_bmad-output/implementation-artifacts/AH-S002.md"
            target.parent.mkdir(parents=True)
            target.write_text("unrelated\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "target artifact already exists"):
                sync.reserve(manifest, "owner/repo", 2, "story", None)

    def test_manifest_write_preserves_existing_artifact_amendments(self):
        with tempfile.TemporaryDirectory() as temp:
            old_root = sync.ROOT
            try:
                sync.ROOT = Path(temp)
                manifest = sync.build_manifest([issue(1, "feat: first")], "owner/repo")
                path = sync.ROOT / manifest["items"][0]["artifact_path"]
                path.parent.mkdir(parents=True)
                path.write_text("durable amendment\n", encoding="utf-8")
                sync.write_manifest(manifest)
                self.assertEqual(path.read_text(encoding="utf-8"), "durable amendment\n")
            finally:
                sync.ROOT = old_root



class NewIssueTests(unittest.TestCase):
    def setUp(self):
        self.manifest = sync.build_manifest([issue(1, "Deliver the program")], "owner/repo")

    def test_new_files_a_typed_issue_then_reserves_it(self):
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "gh_json", return_value={"number": 2, "labels": [{"name": "type::bug"}]}
        ) as gh, mock.patch.object(sync, "reserve", return_value={"bmad_id": "AH-B001"}) as reserve:
            item = sync.create_issue(self.manifest, "owner/repo", "A defect", "Body", "bug", 1, 7)
        self.assertEqual(item["bmad_id"], "AH-B001")
        self.assertEqual(gh.call_args.args[0], ["api", "--method", "POST", "repos/owner/repo/issues", "--input", "-"])
        self.assertEqual(
            gh.call_args.kwargs["input_data"],
            {"title": "A defect", "body": "Body", "labels": ["type::bug"], "milestone": 7},
        )
        reserve.assert_called_once_with(self.manifest, "owner/repo", 2, "bug", 1, False)

    def test_new_files_nothing_when_the_map_or_parent_is_invalid(self):
        with mock.patch.object(sync, "audit_manifest", return_value=["broken mapping"]), mock.patch.object(
            sync, "gh_json"
        ) as gh:
            with self.assertRaisesRegex(RuntimeError, "broken mapping"):
                sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", None, None)
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(sync, "gh_json") as gh2:
            with self.assertRaisesRegex(RuntimeError, "parent issue #99 does not have a BMad ID"):
                sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", 99, None)
        gh.assert_not_called()
        gh2.assert_not_called()

    def test_failed_creation_reserves_nothing(self):
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "gh_json", side_effect=RuntimeError("HTTP 403")
        ), mock.patch.object(sync, "reserve") as reserve:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", None, None)
        reserve.assert_not_called()

    def test_any_failure_after_filing_names_the_filed_issue(self):
        created = {"number": 2, "labels": [{"name": "type::story"}]}
        for failure in (RuntimeError("target artifact already exists"), KeyError("story"), OSError("disk full")):
            with self.subTest(failure=failure), mock.patch.object(
                sync, "audit_manifest", return_value=[]
            ), mock.patch.object(sync, "gh_json", return_value=created), mock.patch.object(
                sync, "reserve", side_effect=failure
            ):
                with self.assertRaisesRegex(RuntimeError, r"issue #2 was filed but not reserved.*reserve --issue 2"):
                    sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", None, None)

    def test_a_dropped_type_label_is_reported_and_reserves_nothing(self):
        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "gh_json", return_value={"number": 2, "labels": []}
        ), mock.patch.object(sync, "reserve") as reserve:
            with self.assertRaisesRegex(RuntimeError, r"issue #2 was filed.*did not apply type::story"):
                sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", None, None)
        reserve.assert_not_called()

    def test_an_empty_github_response_is_reported(self):
        for created in (None, {}, {"number": "2"}):
            with self.subTest(created=created), mock.patch.object(
                sync, "audit_manifest", return_value=[]
            ), mock.patch.object(sync, "gh_json", return_value=created), mock.patch.object(sync, "reserve") as reserve:
                with self.assertRaisesRegex(RuntimeError, "did not return the new issue's number"):
                    sync.create_issue(self.manifest, "owner/repo", "Title", "Body", "story", None, None)
            reserve.assert_not_called()

    def test_cli_reads_the_body_from_a_file(self):
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "body.md"
            body.write_text("## Problem\n", encoding="utf-8")
            with mock.patch.object(sync, "load_manifest", return_value=self.manifest), mock.patch.object(
                sync, "create_issue", return_value={"github_number": 2, "bmad_id": "AH-S002"}
            ) as create, redirect_stdout(io.StringIO()) as out:
                self.assertEqual(
                    sync.main(["new", "--title", "T", "--kind", "story", "--body-file", str(body), "--parent", "1"]), 0
                )
        create.assert_called_once_with(
            self.manifest, sync.API_REPOSITORY, "T", "## Problem\n", "story", 1, None, False
        )
        self.assertIn("filed #2 and reserved AH-S002", out.getvalue())


class ApiRepositoryTests(unittest.TestCase):
    """API calls go to the current slug while planning blocks keep the slug the map stores."""

    def setUp(self):
        self.manifest = sync.build_manifest([issue(93, "Epic"), issue(94, "Child")], "owner/old")

    def test_apply_sends_every_call_to_the_given_slug_and_links_the_stored_one(self):
        live = [issue(93, "Epic"), issue(94, "Child")]
        reparented = [dict(value) for value in live]
        reparented[1]["parent_issue_url"] = "https://api.github.com/repos/owner/new/issues/52"
        tree = {"truncated": False, "tree": [{"path": item["artifact_path"]} for item in self.manifest["items"]]}

        def answer(args, input_data=None):
            return tree if "git/trees" in " ".join(args) else None

        with mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
            sync, "fetch_issues", side_effect=[live, reparented]
        ) as fetch, mock.patch.object(sync, "gh_command") as command, mock.patch.object(
            sync, "gh_json", side_effect=answer
        ) as gh:
            sync.apply_manifest(self.manifest, "owner/new")
        self.assertEqual([call.args for call in fetch.call_args_list], [("owner/new",), ("owner/new",)])
        for call in command.call_args_list:
            args = call.args[0]
            self.assertEqual(args[args.index("-R") + 1], "owner/new")
        self.assertTrue(command.call_args_list)
        endpoints = [next(arg for arg in call.args[0] if arg.startswith("repos/")) for call in gh.call_args_list]
        methods = [call.args[0][2] if call.args[0][1] == "--method" else "GET" for call in gh.call_args_list]
        self.assertEqual(methods, ["GET", "PATCH", "PATCH", "DELETE", "POST"])
        for endpoint in endpoints:
            self.assertTrue(endpoint.startswith("repos/owner/new/"), endpoint)
        self.assertIn("repos/owner/new/issues/52/sub_issue", endpoints)
        self.assertIn("repos/owner/new/issues/93/sub_issues", endpoints)
        for call in gh.call_args_list[1:3]:
            body = call.args[1]["body"]
            self.assertIn("https://github.com/owner/old/blob/main/", body)
            self.assertNotIn("owner/new", body)

    def test_every_github_command_receives_the_api_slug(self):
        with tempfile.TemporaryDirectory() as temp:
            body = Path(temp) / "body.md"
            body.write_text("## Problem\n", encoding="utf-8")
            cases = [
                (["new", "--title", "T", "--kind", "story", "--body-file", str(body)], "create_issue"),
                (["reserve", "--issue", "95", "--kind", "story"], "reserve"),
                (["plan"], "fetch_issues"),
                (["refresh"], "fetch_issues"),
                (["audit", "--live"], "fetch_issues"),
                (["apply"], "apply_manifest"),
                (["apply"], "fetch_issues"),
            ]
            for argv, target in cases:
                with self.subTest(argv=argv, target=target), mock.patch.object(
                    sync, "load_manifest", return_value=self.manifest
                ), mock.patch.object(sync, "audit_manifest", return_value=[]), mock.patch.object(
                    sync, "fetch_issues", return_value=[]
                ) as fetch, mock.patch.object(
                    sync, "create_issue", return_value={"github_number": 95, "bmad_id": "AH-S001"}
                ) as create, mock.patch.object(
                    sync, "reserve", return_value={"github_number": 95, "bmad_id": "AH-S001"}
                ) as reserve, mock.patch.object(sync, "apply_manifest") as apply, mock.patch.object(
                    sync, "refresh", return_value=[]
                ), mock.patch.object(sync, "planned_actions", return_value=[]), mock.patch.object(
                    sync, "live_findings", return_value=([], [])
                ), mock.patch.object(sync.subprocess, "run") as run, redirect_stdout(io.StringIO()):
                    self.assertEqual(sync.main(argv), 0)
                    called = {"fetch_issues": fetch, "create_issue": create, "reserve": reserve,
                              "apply_manifest": apply}[target]
                    self.assertTrue(called.call_args_list)
                    for call in called.call_args_list:
                        self.assertEqual(call.args[1] if target != "fetch_issues" else call.args[0],
                                         sync.API_REPOSITORY)
                        self.assertNotIn("owner/old", call.args)
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class SiblingReservationTests(unittest.TestCase):
    """The counter alone cannot see a reservation made in another checkout of this repository."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.shared = Path(temp.name) / "shared"
        self.shared.mkdir()
        self.git("init", "--quiet", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t")
        with mock.patch.object(sync, "ROOT", self.shared):
            sync.write_manifest(sync.build_manifest([issue(1, "feat: first")], "owner/repo"))
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "initial")
        self.sibling = Path(temp.name) / "sibling"
        self.git("worktree", "add", "--quiet", "-b", "side", str(self.sibling))

    def git(self, *args, **kwargs):
        subprocess.run(
            ["git", "-C", str(kwargs.get("cwd", self.shared))] + list(args),
            check=True,
            capture_output=True,
        )

    def reserve(self, root, number, **kwargs):
        with mock.patch.object(sync, "ROOT", root), mock.patch.object(
            sync, "fetch_issues", return_value=[issue(number, "fix: something")]
        ), redirect_stdout(io.StringIO()) as out:
            item = sync.reserve(sync.load_manifest(), "owner/repo", number, "bug", None, **kwargs)
        return item, out.getvalue()

    def test_a_second_reservation_from_the_same_counter_is_refused(self):
        first, _ = self.reserve(self.shared, 2)
        self.assertEqual(first["bmad_id"], "AH-B001")
        with self.assertRaises(RuntimeError) as caught:
            self.reserve(self.sibling, 3)
        message = str(caught.exception)
        self.assertIn("AH-B001 is not free", message)
        self.assertIn("AH-B001 is already taken in worktree {}".format(self.shared.resolve()), message)
        self.assertIn("--advance", message)

    def test_advance_skips_the_taken_ids_and_says_which(self):
        self.reserve(self.shared, 2)
        item, printed = self.reserve(self.sibling, 3, advance=True)
        self.assertEqual(item["bmad_id"], "AH-B002")
        self.assertIn("notice: --advance left AH-B001..AH-B001 unused", printed)
        with mock.patch.object(sync, "ROOT", self.sibling):
            self.assertEqual(sync.load_manifest()["next_ids"]["bug"], 3)

    def test_a_reservation_committed_on_another_branch_is_seen(self):
        self.reserve(self.shared, 2)
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "reserve")
        self.git("checkout", "--quiet", "-b", "parked")
        self.git("reset", "--quiet", "--hard", "HEAD~1")
        with self.assertRaisesRegex(RuntimeError, r"AH-B001 is already taken in branch (main|side)"):
            self.reserve(self.sibling, 3)

    def test_a_counter_a_sibling_has_already_run_past_is_refused(self):
        with mock.patch.object(sync, "ROOT", self.sibling):
            manifest = sync.load_manifest()
            manifest["next_ids"]["bug"] = 4
            sync.write_manifest(manifest)
        with self.assertRaisesRegex(RuntimeError, r"already counted bug up to 4"):
            self.reserve(self.shared, 2)

    def test_an_unrelated_kind_is_untouched_by_the_survey(self):
        self.reserve(self.shared, 2)
        with mock.patch.object(sync, "ROOT", self.sibling), mock.patch.object(
            sync, "fetch_issues", return_value=[issue(3, "chore: something")]
        ):
            item = sync.reserve(sync.load_manifest(), "owner/repo", 3, "chore", None)
        self.assertEqual(item["bmad_id"], "AH-C001")


class SiblingSurveyUnitTests(unittest.TestCase):
    def test_refs_without_the_map_are_skipped_without_desynchronising_the_batch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        subprocess.run(["git", "-C", str(root), "init", "--quiet", "-b", "main"], check=True)
        for name in ("user.name", "user.email"):
            subprocess.run(["git", "-C", str(root), "config", name, "t"], check=True)
        (root / "README.md").write_text("first\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "no map"], check=True)
        subprocess.run(["git", "-C", str(root), "branch", "mapped"], check=True)
        with mock.patch.object(sync, "ROOT", root):
            sync.write_manifest(sync.build_manifest([issue(1, "feat: first")], "owner/repo"))
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet", "-m", "map"], check=True
            )
            blobs = sync.blobs_at_refs(["refs/heads/mapped", "refs/heads/main"])
        self.assertEqual(list(blobs), ["refs/heads/main"])
        self.assertEqual(json.loads(blobs["refs/heads/main"])["next_ids"]["story"], 2)

    def test_the_survey_degrades_to_nothing_outside_a_git_repository(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(sync, "ROOT", Path(temp.name)):
            used, claimed, unreadable = sync.survey_ids_elsewhere("bug")
        self.assertEqual((dict(used), claimed, unreadable), ({}, 1, []))

    def test_a_remote_head_this_clone_cannot_read_is_reported(self):
        answers = {
            ("remote",): "origin\n",
            ("fetch", "--quiet", "origin"): "",
            ("ls-remote", "--heads", "origin"): "beef refs/heads/here\ndead refs/heads/gone\n",
        }
        def fake(args, input_data=None, text=True):
            if args[0] == "cat-file":
                return "beef commit 12\ndead missing\n"
            return answers.get(tuple(args))
        with mock.patch.object(sync, "git_output", side_effect=fake):
            self.assertEqual(sync.unreadable_remote_heads(), ["refs/heads/gone"])

    def test_an_exhausted_range_is_not_offered_as_something_to_skip_into(self):
        report, floor = sync.collision_report("bug", 998, {999: {"branch side"}}, 1, [])
        self.assertEqual(floor, 1000)
        self.assertIn("the bug range is exhausted", report)
        self.assertNotIn("--advance", report)

    def test_an_unreadable_remote_head_is_named_in_the_refusal(self):
        report, _ = sync.collision_report("bug", 1, {1: {"branch side"}}, 1, ["refs/heads/gone"])
        self.assertIn("warning: refs/heads/gone could not be scanned", report)

    def test_a_clear_counter_produces_no_report(self):
        report, floor = sync.collision_report("bug", 7, {3: {"branch side"}}, 4, [])
        self.assertIsNone(report)
        self.assertEqual(floor, 4)
