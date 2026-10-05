"""`claude plugin eval` aggregate results imported into the Studio run store."""
import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO  # noqa: F401 - adds lib/ to the test import path
from harness_core.studio import plugin_evals, run_store, runs
from harness_core.studio import auth
from harness_core.studio import server as studio_server
from harness_core.studio.state import Store
import test_studio_security as studio_security

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "plugin-eval" / "aggregate-result.v1.json"
REPORT = b"<!doctype html>\n<html><body><script>document.title='report'</script></body></html>\n"
STAMP = "2026-09-29T16-48-37-679Z"


def fixture():
    return json.loads(FIXTURE.read_text())


class PluginEvalFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.repository = root / "repository"
        self.repository.mkdir()
        catalog = root / "suites.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1, "argv": ["python3", "-c", "print('ok')"],
            "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["branch"]}]}))
        self.supervisor = runs.RunSupervisor(root / "state", catalog, repository=self.repository)
        self.addCleanup(self.supervisor.close)

    def write_result(self, value, stamp=STAMP, eval_dir="evals", report=True):
        directory = self.repository / eval_dir / "results" / stamp
        directory.mkdir(parents=True)
        (directory / "aggregate-result.json").write_text(json.dumps(value))
        if report:
            (directory / "report.html").write_bytes(REPORT)
        return directory

    def only_run(self):
        items = self.supervisor.history.history(limit=10)["items"]
        self.assertEqual(len(items), 1, items)
        return self.supervisor.history.get(items[0]["run_id"])


class PluginEvalImportTests(PluginEvalFixture):
    def test_a_result_is_imported_with_its_cases_two_arms_and_scores(self):
        self.write_result(fixture())

        result = self.supervisor.reindex(self.repository)

        self.assertEqual((result["imported_runs"], result["skipped"]), (1, []))
        record = self.only_run()
        self.assertEqual(record["suite"], {"id": "claude-plugin-eval", "version": 1})
        self.assertEqual(record["source"]["path"],
                         "evals/results/%s/aggregate-result.json" % STAMP)
        self.assertEqual(record["arms"], ["with", "without"])
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["target"]["kind"], "plugin")
        self.assertEqual(record["target"]["ref"], "probe-plugin")
        self.assertEqual(record["cost"]["amount_usd"], 0.032029)
        self.assertEqual(record["scores"]["overall"], 1)
        self.assertEqual(record["scores"]["mean_delta"], 1)
        case = record["cases"]["plugin:probe-plugin/greets"]
        self.assertEqual((case["score"], case["score_without"], case["delta"]), (1, 0, 1))
        self.assertEqual([run["score"] for run in case["arms"]["with"]], [1])
        self.assertEqual([run["score"] for run in case["arms"]["without"]], [0])
        self.assertEqual(case["arms"]["without"][0]["graders"][0]["passed"], False)
        detail = self.supervisor.history.detail(record["run_id"])
        self.assertEqual(detail["duration_ms"], 6000)
        self.assertEqual([(item["id"], item["outcome"]) for item in detail["cases"]],
                         [("plugin:probe-plugin/greets", "passed")])

    def test_reindex_is_stable_across_repeated_imports(self):
        self.write_result(fixture())
        self.supervisor.reindex(self.repository)
        first = self.only_run()["run_id"]
        self.supervisor.reindex(self.repository)
        self.assertEqual(self.only_run()["run_id"], first)

    def test_an_unknown_format_version_is_skipped_and_reported_never_guessed(self):
        for stamp, version in (("v2", 2), ("v-string", "1"), ("v-bool", True)):
            value = fixture()
            value["schemaVersion"] = version
            self.write_result(value, stamp=stamp)
        missing = fixture()
        del missing["schemaVersion"]
        self.write_result(missing, stamp="v-missing")
        self.write_result(fixture())

        result = self.supervisor.reindex(self.repository)

        self.assertEqual(result["imported_runs"], 1)
        reasons = {item["path"].split("/")[2]: item["reason"] for item in result["skipped"]}
        self.assertEqual(reasons, {
            "v2": "unsupported plugin eval schema version: 2",
            "v-string": "unsupported plugin eval schema version: 1",
            "v-bool": "unsupported plugin eval schema version: True",
            "v-missing": "unsupported plugin eval schema version: None",
        })
        self.only_run()

    def test_a_version_one_document_with_an_unrecognized_shape_is_skipped(self):
        value = fixture()
        value["cases"][0]["arms"]["other"] = []
        self.write_result(value)
        result = self.supervisor.reindex(self.repository)
        self.assertEqual(result["imported_runs"], 0)
        self.assertIn("unrecognized arms", result["skipped"][0]["reason"])

    def test_unknown_fields_are_ignored_as_the_format_asks(self):
        value = fixture()
        value["futureField"] = {"anything": [1, 2]}
        value["cases"][0]["arms"]["with"][0]["futureRunField"] = "x"
        self.write_result(value)
        self.assertEqual(self.supervisor.reindex(self.repository)["skipped"], [])

    def test_status_follows_threshold_and_partial_reason(self):
        failing = fixture()
        failing["aggregates"]["casesPassed"] = 0
        self.assertEqual(plugin_evals.plugin_eval_record("a.json", failing)["status"], "failed")
        for reason, status in (("cost_ceiling", "capped"), ("interrupted", "cancelled"),
                               ("auth_failed", "failed")):
            partial = fixture()
            partial.update({"partial": True, "partialReason": reason})
            self.assertEqual(plugin_evals.plugin_eval_record("a.json", partial)["status"], status)

    def test_a_case_below_threshold_fails_and_a_case_without_runs_is_unknown(self):
        value = fixture()
        second = copy.deepcopy(value["cases"][0])
        second["name"] = "idle"
        second["arms"] = {"with": []}
        second["aggregates"] = {"score": None}
        value["cases"].append(second)
        value["cases"][0]["aggregates"]["score"] = 0.5
        record = plugin_evals.plugin_eval_record("a.json", value)
        self.assertEqual(run_store._case_rows(record), [
            ("plugin:probe-plugin/greets", "failed"), ("plugin:probe-plugin/idle", "unknown")])

    def test_a_single_arm_run_records_one_arm(self):
        value = fixture()
        value["suite"]["ablation"] = "none"
        del value["cases"][0]["arms"]["without"]
        self.assertEqual(plugin_evals.plugin_eval_record("a.json", value)["arms"], ["with"])

    def test_plugin_manifests_name_their_eval_directory(self):
        plugin = self.repository / "plugins" / "probe"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": "probe", "experimental": {"evals": "checks"}}))
        self.write_result(fixture(), eval_dir="plugins/probe/checks")
        self.supervisor.reindex(self.repository)
        self.assertEqual(self.only_run()["source"]["path"],
                         "plugins/probe/checks/results/%s/aggregate-result.json" % STAMP)

    def test_a_result_reached_through_a_symlink_outside_the_repository_is_ignored(self):
        outside = Path(self.temporary.name).resolve() / "outside" / "results" / STAMP
        outside.mkdir(parents=True)
        (outside / "aggregate-result.json").write_text(json.dumps(fixture()))
        (self.repository / "evals").symlink_to(outside.parent.parent)
        result = self.supervisor.reindex(self.repository)
        self.assertEqual((result["imported_runs"], result["skipped"]), (0, []))

    def test_an_out_of_range_time_skips_only_that_file(self):
        for stamp, started, duration in (("huge", "2026-09-29T00:00:00Z", 1e300),
                                         ("edge", "9999-12-31T23:59:59Z", 10)):
            value = fixture()
            value.update({"startedAt": started, "durationSeconds": duration})
            self.write_result(value, stamp=stamp)
        self.write_result(fixture())

        result = self.supervisor.reindex(self.repository)

        self.assertEqual(result["imported_runs"], 1)
        self.assertEqual(sorted(item["path"].split("/")[2] for item in result["skipped"]),
                         ["edge", "huge"])

    def test_the_file_cap_applies_before_reading_and_keeps_benchmark_rows(self):
        benchmark = self.repository / "benchmarks" / "v1"
        benchmark.mkdir(parents=True)
        (benchmark / "results.jsonl").write_text(json.dumps({
            "task": "case", "arm": "harness", "rep": 1, "harness_sha": "a" * 40,
            "model": "model", "passed": True, "error": False}) + "\n")
        for stamp in ("one", "two"):
            self.write_result(fixture(), stamp=stamp)

        with mock.patch.object(run_store, "MAX_RESULTS_FILES", 2), \
                mock.patch.object(plugin_evals, "_read_json",
                                  side_effect=AssertionError("read past the cap")):
            result = self.supervisor.reindex(self.repository)

        self.assertEqual(result["imported_runs"], 1)
        self.assertEqual(result["skipped"], [
            {"path": "evals", "reason": "too many plugin eval result files"}])
        items = self.supervisor.history.history(limit=10)["items"]
        self.assertEqual([item["suite_id"] for item in items], ["cost-benchmark"])

    def test_stored_raw_document_carries_no_local_absolute_paths(self):
        self.write_result(fixture())
        self.supervisor.reindex(self.repository)
        stored = json.dumps(self.only_run())
        for marker in ("/work/probe-plugin", "/tmp/eval-", "tracePath"):
            self.assertNotIn(marker, stored)


class PluginEvalReportTests(PluginEvalFixture):
    def imported_run_id(self, report=True):
        self.write_result(fixture(), report=report)
        self.supervisor.reindex(self.repository)
        return self.only_run()["run_id"]

    def test_detail_links_the_original_html_report(self):
        run_id = self.imported_run_id()
        detail = self.supervisor.run_detail(run_id)
        self.assertEqual(detail["artifacts"], [
            {"id": "imported-0", "label": "Result JSON", "available": True},
            {"id": "imported-1", "label": "HTML report", "available": True,
             "kind": "html-report", "href": "/api/runs/plugin-eval-report/" + run_id},
        ])
        self.assertEqual(self.supervisor.plugin_eval_report(run_id), REPORT)

    def test_a_result_without_its_report_declares_no_link(self):
        run_id = self.imported_run_id(report=False)
        self.assertEqual([item["label"] for item in self.supervisor.run_detail(run_id)["artifacts"]],
                         ["Result JSON"])
        with self.assertRaises(runs.RunError):
            self.supervisor.plugin_eval_report(run_id)

    def test_only_plugin_eval_runs_serve_a_report(self):
        run_id = str(uuid.uuid4())
        (self.repository / "page.html").write_bytes(REPORT)
        self.supervisor.history.upsert({
            "schema_version": 1, "run_id": run_id,
            "source": {"kind": "native", "path": "page.html", "record_identity": run_id},
            "suite": {"id": "native-acceptance", "version": 1},
            "target": {"kind": "commit", "ref": None, "commit": None, "draft": None,
                       "config_digest": None},
            "status": "succeeded", "times": {}, "cases": [], "artifacts": ["page.html"],
            "report": "page.html", "raw": {}})
        with self.assertRaises(runs.RunError):
            self.supervisor.plugin_eval_report(run_id)

    def test_route_serves_the_report_in_an_opaque_sandbox(self):
        run_id = self.imported_run_id()
        path = "/api/runs/plugin-eval-report/" + run_id
        route = studio_server.ROUTES.resolve("GET", path)
        self.assertIsNotNone(route)
        self.assertEqual(route.cli_command,
                         ("citizen", "runs", "evidence", "{run_id}", "{artifact}", "--json"))
        handler = mock.Mock()
        handler.path = path
        handler.server.run_supervisor = self.supervisor
        handler.server.mutations.call.side_effect = lambda function: function()
        studio_server._plugin_eval_report(handler, route)
        status, body, media_type = handler._send.call_args.args
        self.assertEqual((status, body, media_type), (200, REPORT, "text/html; charset=utf-8"))
        policy = handler._send.call_args.kwargs["content_security_policy"]
        self.assertTrue(policy.startswith("sandbox allow-scripts;"))
        self.assertNotIn("allow-same-origin", policy)
        self.assertIn("default-src 'none'", policy)

    def test_route_refuses_unknown_runs_and_malformed_paths(self):
        for path in ("/api/runs/plugin-eval-report/" + str(uuid.uuid4()),):
            route = studio_server.ROUTES.resolve("GET", path)
            handler = mock.Mock()
            handler.path = path
            handler.server.run_supervisor = self.supervisor
            handler.server.mutations.call.side_effect = lambda function: function()
            studio_server._plugin_eval_report(handler, route)
            handler._error.assert_called_once_with(404, "evidence_unavailable")
        for method, path in (("POST", "/api/runs/plugin-eval-report/" + str(uuid.uuid4())),
                             ("GET", "/api/runs/plugin-eval-report/not-a-run"),
                             ("GET", "/api/runs/plugin-eval-report/")):
            self.assertIsNone(studio_server.ROUTES.resolve(method, path), path)


class PluginEvalReportHttpTests(studio_security.StudioSecurityFixture):
    def test_report_route_requires_a_session(self):
        path = "/api/runs/plugin-eval-report/" + str(uuid.uuid4())
        status, _headers, _body = self.request("GET", path)
        self.assertEqual(status, 401)
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        status, _headers, body = self.request("GET", path, {"Cookie": self.cookie(headers)})
        self.assertEqual(status, 404, body)
        self.assertEqual(json.loads(body), {"error": "evidence_unavailable"})


class PluginEvalReportServerTests(unittest.TestCase):
    """The report route over real HTTP against an in-process server rooted in a fixture repo."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(os.path.realpath(temporary.name))
        self.repository = base / "repository"
        static_root = self.repository / "studio" / "dist"
        static_root.mkdir(parents=True)
        catalog = runs.default_catalog_path(self.repository)
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1, "argv": ["python3", "-c", "print('ok')"],
            "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["branch"]}]}))
        self.results = self.repository / "evals" / "results" / STAMP
        self.results.mkdir(parents=True)
        (self.results / "aggregate-result.json").write_text(json.dumps(fixture()))
        (self.results / "report.html").write_bytes(REPORT)
        store = Store(base / "state").__enter__()
        self.addCleanup(store.__exit__, None, None, None)
        self.instance = studio_server.Server(("127.0.0.1", 0), static_root, "control", store)
        self.addCleanup(self.instance.server_close)
        supervisor = self.instance.run_supervisor
        self.instance.mutations.call(lambda: supervisor.reindex(self.repository))
        items = self.instance.mutations.call(lambda: supervisor.history.history(limit=5))["items"]
        self.path = "/api/runs/plugin-eval-report/" + items[0]["run_id"]
        token, _form_name = self.instance.sessions.issue()
        session, _form_name = self.instance.sessions.consume(token)
        self.cookie = auth.SESSION_COOKIE + "=" + session.cookie
        thread = threading.Thread(target=self.instance.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.instance.shutdown)

    def get(self, host=None, cookie=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.instance.server_address[1],
                                                timeout=5)
        headers = {"Host": host or self.instance.host}
        if cookie:
            headers["Cookie"] = self.cookie
        connection.request("GET", self.path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.headers, response.read()
        connection.close()
        return result

    def test_the_report_is_served_with_the_sandbox_policy_on_the_response(self):
        status, headers, body = self.get()
        self.assertEqual((status, body), (200, REPORT))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        policy = headers["Content-Security-Policy"]
        self.assertTrue(policy.startswith("sandbox allow-scripts;"), policy)
        self.assertNotIn("allow-same-origin", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_a_wrong_host_or_missing_session_is_refused(self):
        self.assertEqual(self.get(host="127.0.0.1:%d" % self.instance.server_address[1])[0], 403)
        self.assertEqual(self.get(cookie=False)[0], 401)

    def test_a_report_replaced_by_a_symlink_is_refused(self):
        outside = self.repository.parent / "outside.html"
        outside.write_bytes(b"<!doctype html><title>outside-marker</title>")
        (self.results / "report.html").unlink()
        (self.results / "report.html").symlink_to(outside)
        status, _headers, body = self.get()
        self.assertEqual(status, 404)
        self.assertNotIn(b"outside-marker", body)

    def test_an_oversized_report_is_refused(self):
        with mock.patch.object(runs, "MAX_REPORT_BYTES", len(REPORT) - 1):
            status, _headers, body = self.get()
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "evidence_unavailable"})


if __name__ == "__main__":
    unittest.main()
