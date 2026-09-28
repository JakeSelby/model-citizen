# SPDX-License-Identifier: MIT
"""Activity keeps immutable provenance while paging large ledgers in bounded reads."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core import decision as decision_contract  # noqa: E402
from harness_core.studio import activity, server  # noqa: E402


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.state = self.home / ".local" / "state" / "agent-harness"
        self.state.mkdir(parents=True)
        self.log = self.state / "decisions.jsonl"

    def write(self, rows):
        with self.log.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")

    @staticmethod
    def decision(index, answer="deny", session="session-a"):
        return {
            "kind": "decision", "decision_id": "decision-%06d" % index,
            "point": "grade-bash", "module": "hooks/grade-bash",
            "deterministic_answer": answer, "input": "command-%d" % index,
            "grade": 3,
            "session_id": session, "runtime": "codex",
            "repository": "repo:agent-harness/feature", "ts": "2026-09-28T12:%02d:%02dZ" % (
                (index // 60) % 60, index % 60),
        }

    def query(self, **values):
        request = {"limit": 25, "cursor": "", "session": "", "repository": "",
                   "hook": "", "outcome": ""}
        request.update(values)
        return activity.query(self.state, request)

    def record_real_decision(self, answer="deny", session="session-a"):
        writer = decision_contract._ledger()
        self.assertIsNotNone(writer)
        with mock.patch.object(writer, "enabled", return_value=True):
            identity = writer.record(
                "grade-bash", answer, "<script>globalThis.bad = true</script>",
                {"session_id": session}, runtime="codex", key="activity-real-row",
                target=self.log, now=1_780_000_000)
        self.assertIsNotNone(identity)
        return identity

    def test_refused_decision_names_hook_reason_session_and_library_source(self):
        identity = self.record_real_decision()
        result = self.query()
        entry = result["entries"][0]
        self.assertTrue(entry["id"].startswith("decision:" + identity + "@"))
        self.assertEqual(entry["title"], "Command refused")
        self.assertEqual(entry["outcome"], "refused")
        self.assertIn("grade-bash refused", entry["reason"])
        self.assertEqual(entry["session"], "session-a")
        self.assertEqual(entry["grade"], "unknown")
        self.assertEqual(entry["repository"], "")
        self.assertEqual(entry["command"], "<script>globalThis.bad = true</script>")
        self.assertEqual(entry["evidence_href"], "/library?path=policy/hooks/grade-bash.py")
        self.assertEqual(result["command"], "citizen activity --json --limit 25")
        self.assertEqual(self.query(repository="agent-harness")["entries"], [])

    def test_studio_apply_keeps_draft_and_changed_files(self):
        self.write([{
            "kind": "event", "event": "studio.apply", "id": "apply-1",
            "ts": "2026-09-28T12:00:00Z", "detail": {
                "draft": "tighten-guard", "files": ["primitives/rules/guard.md", "policy/hooks/guard.py"],
                "outcome": "completed", "repository": "repo:agent-harness/feature",
                "command": "citizen draft apply tighten-guard",
            },
        }])
        entry = self.query()["entries"][0]
        self.assertEqual(entry["title"], "Draft applied")
        self.assertEqual(entry["draft"], "tighten-guard")
        self.assertEqual(entry["files"], ["primitives/rules/guard.md", "policy/hooks/guard.py"])
        self.assertEqual(entry["command"], "citizen draft apply tighten-guard")

    def test_ownership_provenance_never_exposes_recorded_content(self):
        marker = "sensitive-config-content"
        (self.state / "ownership.json").write_text(json.dumps({
            "schema_version": 1,
            "files": {str(self.home / ".config" / "agent-harness" / "config.json"): {
                "kind": "generated", "applied": marker,
            }},
        }), encoding="utf-8")
        result = self.query(limit=1)
        source = next(item for item in result["sources"] if item["id"] == "ownership-journal")
        self.assertEqual(result["entries"], [])
        self.assertEqual(source["status"], "current")
        self.assertEqual(source["message"], "Current ownership snapshot records 1 owned file.")
        serialized = json.dumps(result)
        self.assertNotIn(marker, serialized)
        self.assertNotIn("ownership:", serialized)
        self.assertNotIn("citizen diff", serialized)
        self.assertNotIn("config.json", serialized)

    def test_decision_links_require_recorded_canonical_existing_hook_provenance(self):
        recorded = self.decision(1)
        recorded["point"] = "legacy-point-name-is-irrelevant"
        recorded["module"] = "hooks/intent-overlap"
        legacy = self.decision(2)
        del legacy["module"]
        traversal = self.decision(3)
        traversal["module"] = "hooks/../grade-bash"
        nonexistent = self.decision(4)
        nonexistent["module"] = "hooks/not-a-real-hook"
        self.write([recorded, legacy, traversal, nonexistent])

        by_id = {entry["id"].split("@", 1)[0]: entry for entry in self.query()["entries"]}
        self.assertEqual(by_id["decision:decision-000002"]["hook"],
                         "")
        self.assertEqual(by_id["decision:decision-000001"]["hook"], "hooks/intent-overlap")
        self.assertEqual(by_id["decision:decision-000001"]["evidence_href"],
                         "/library?path=policy/hooks/intent-overlap.py")
        self.assertEqual(by_id["decision:decision-000003"]["hook"], "")
        self.assertEqual(by_id["decision:decision-000003"]["evidence_href"], "")
        self.assertEqual(by_id["decision:decision-000004"]["hook"], "")
        self.assertEqual(by_id["decision:decision-000004"]["evidence_href"], "")

    def test_repeated_reproducible_decision_ids_remain_distinct_across_pages(self):
        first_identity = self.record_real_decision()
        second_identity = self.record_real_decision()
        self.assertEqual(first_identity, second_identity)

        newest = self.query(limit=1)
        older = self.query(limit=1, cursor=newest["next_cursor"])
        self.assertNotEqual(newest["entries"][0]["id"], older["entries"][0]["id"])
        self.assertTrue(newest["entries"][0]["id"].startswith("decision:" + first_identity + "@"))
        self.assertTrue(older["entries"][0]["id"].startswith("decision:" + first_identity + "@"))

    def test_one_hundred_thousand_rows_page_without_loading_the_file_whole(self):
        with self.log.open("w", encoding="utf-8") as stream:
            for index in range(100_000):
                stream.write(json.dumps(self.decision(index), sort_keys=True) + "\n")
        reads = []
        original_open = Path.open

        class TrackingReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read(self, amount=-1):
                reads.append(amount)
                if amount < 0:
                    raise AssertionError("decision ledger was read whole")
                return self.stream.read(amount)

        def tracked(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return TrackingReader(stream) if Path(path) == self.log else stream

        with mock.patch.object(Path, "open", tracked):
            first = self.query(limit=20)
            second = self.query(limit=20, cursor=first["next_cursor"])
        self.assertEqual(len(first["entries"]), 20)
        self.assertEqual(len(second["entries"]), 20)
        self.assertTrue(first["next_cursor"].startswith("v1:"))
        self.assertFalse({item["id"] for item in first["entries"]}
                         & {item["id"] for item in second["entries"]})
        self.assertTrue(reads)
        self.assertLessEqual(max(reads), activity.READ_CHUNK)

        with mock.patch.object(activity, "_entry", wraps=activity._entry) as normalized:
            no_match = self.query(limit=20, session="never-recorded")
        self.assertEqual(no_match["entries"], [])
        self.assertTrue(no_match["next_cursor"].startswith("v1:"))
        self.assertEqual(normalized.call_count, activity.MAX_SCAN_ROWS)
        source = next(item for item in no_match["sources"] if item["id"] == "decision-log")
        self.assertIn("scan limit reached", source["message"])
        continued = self.query(limit=20, cursor=no_match["next_cursor"],
                               session="never-recorded")
        self.assertNotEqual(continued["next_cursor"], no_match["next_cursor"])

    def test_selective_scan_is_also_bounded_by_total_row_bytes(self):
        rows = []
        for index in range(400):
            row = self.decision(index)
            row["input"] = "x" * (16 * 1024)
            rows.append(row)
        self.write(rows)
        with mock.patch.object(activity, "_entry", wraps=activity._entry) as normalized:
            result = self.query(session="never-recorded")
        self.assertEqual(result["entries"], [])
        self.assertTrue(result["next_cursor"].startswith("v1:"))
        self.assertGreater(normalized.call_count, 0)
        self.assertLess(normalized.call_count, len(rows))
        self.assertLess(normalized.call_count, activity.MAX_SCAN_ROWS)

    def test_filters_skip_nonmatching_rows_and_bad_cursors_fail_closed(self):
        self.write([self.decision(1, answer="ask", session="other"), self.decision(2)])
        result = self.query(session="session-a", hook="grade-bash", outcome="refused")
        self.assertEqual(len(result["entries"]), 1)
        self.assertTrue(result["entries"][0]["id"].startswith("decision:decision-000002@"))
        self.assertIn("--session session-a", result["command"])
        with self.assertRaisesRegex(activity.ActivityError, "cursor"):
            self.query(cursor="v1:not-a-number")

    def test_cli_equivalents_preserve_cursor_limit_and_every_filter(self):
        self.write([self.decision(1), self.decision(2)])
        filters = {"session": "session-a", "repository": "repo:agent-harness",
                   "hook": "grade-bash", "outcome": "refused"}
        first = self.query(limit=1, **filters)
        self.assertEqual(shlex.split(first["command"]), [
            "citizen", "activity", "--json", "--limit", "1",
            "--session", "session-a", "--repository", "repo:agent-harness",
            "--hook", "grade-bash", "--outcome", "refused",
        ])
        self.assertEqual(shlex.split(first["next_command"]), [
            "citizen", "activity", "--limit", "1", "--cursor", first["next_cursor"],
            "--session", "session-a", "--repository", "repo:agent-harness",
            "--hook", "grade-bash", "--outcome", "refused",
        ])
        second = self.query(limit=1, cursor=first["next_cursor"], **filters)
        self.assertIn("--cursor", shlex.split(second["command"]))

        env = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home))
        env.pop("HARNESS_QUIET", None)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "harness"), "activity", "--limit", "1",
             "--session", filters["session"], "--repository", filters["repository"],
             "--hook", filters["hook"], "--outcome", filters["outcome"]],
            env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("more: " + first["next_command"], completed.stdout)

    def test_malformed_rows_are_partial_evidence_not_empty_results(self):
        self.log.write_text("not json\n" + json.dumps(self.decision(1)) + "\n", encoding="utf-8")
        result = self.query()
        self.assertEqual(len(result["entries"]), 1)
        source = next(item for item in result["sources"] if item["id"] == "decision-log")
        self.assertEqual(source["status"], "partial")
        self.assertIn("1 malformed row", source["message"])

    def test_an_oversized_unterminated_row_fails_the_source_with_a_bounded_buffer(self):
        self.log.write_bytes(b"x" * (activity.MAX_LINE_BYTES + activity.READ_CHUNK + 1))
        result = self.query()
        source = next(item for item in result["sources"] if item["id"] == "decision-log")
        self.assertEqual(source["status"], "failed")
        self.assertIn("safe read limit", source["message"])
        self.assertEqual(result["entries"], [])

    def test_route_and_cli_share_the_activity_contract(self):
        route = server.ROUTES.resolve("POST", "/api/activity")
        self.assertIsNotNone(route)
        self.assertEqual(route.cli_command, ("citizen", "activity", "--json"))
        self.write([self.decision(1)])
        handler = mock.Mock()
        handler.request_json = {"limit": 5, "cursor": "", "session": "",
                                "repository": "", "hook": "", "outcome": ""}
        handler.server.store.path = self.state / "studio"
        server._activity(handler, route)
        payload = handler._json.call_args.args[1]
        self.assertEqual(payload["entries"][0]["outcome"], "refused")

        refused = mock.Mock()
        refused.request_json = {"limit": 0}
        refused.server.store.path = self.state / "studio"
        server._activity(refused, route)
        refused._error.assert_called_once_with(400, "invalid_activity_query")

        env = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home))
        completed = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "harness"), "activity", "--json",
             "--outcome", "refused"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["entries"][0]["outcome"], "refused")


if __name__ == "__main__":
    unittest.main()
