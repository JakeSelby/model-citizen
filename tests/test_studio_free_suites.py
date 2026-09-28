"""Free Studio suites stay discoverable, allowlisted and command-equivalent."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import free_suites, runs
import test_studio_security as studio_security
from studio_target_support import FixtureTargetService


class FreeSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "checkout"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "bin").mkdir()
        (self.root / "bin" / "harness").write_text("#!/usr/bin/env python3\n")
        self.module = "test_sample_" + self.root.parent.name.replace("-", "_")
        (self.root / "tests" / (self.module + ".py")).write_text(
            "import unittest\n\n"
            "class SampleTests(unittest.TestCase):\n"
            "    def test_passes(self):\n"
            "        self.assertEqual(2 + 2, 4)\n\n"
            "    def test_fails(self):\n"
            "        self.assertEqual('actual', 'expected')\n"
        )
        self.catalog = runs.SuiteCatalog.load(REPO / "policy" / "studio" / "suites.json")

    def supervisor(self):
        return runs.RunSupervisor(Path(self.temporary.name).resolve() / "state",
                                  REPO / "policy" / "studio" / "suites.json",
                                  target_service=FixtureTargetService())

    def test_discovery_lists_module_class_and_single_test_scopes(self):
        cases = free_suites.discover_unit_tests(self.root)
        self.assertNotIn(self.module, sys.modules)
        self.assertEqual([case["id"] for case in cases], [
            self.module + ".SampleTests.test_fails",
            self.module + ".SampleTests.test_passes",
        ])
        scopes = free_suites.unit_scopes(cases)
        self.assertEqual(scopes[self.module], scopes["all"])
        self.assertEqual(scopes[self.module + ".SampleTests"], scopes["all"])
        self.assertEqual(scopes[self.module + ".SampleTests.test_passes"],
                         [self.module + ".SampleTests.test_passes"])

    def test_catalog_contains_only_free_fixed_commands_and_exact_cli_equivalents(self):
        payload = free_suites.catalog_payload(self.catalog, self.root)
        self.assertEqual({item["id"] for item in payload["suites"]},
                         free_suites.FREE_SUITE_IDS)
        self.assertTrue(all(item["cost_class"] == "free" for item in payload["suites"]))
        unit = next(item for item in payload["suites"] if item["id"] == "unit-tests")
        self.assertEqual(unit["case_count"], 2)
        self.assertEqual(unit["command_argv"], shlex.split(unit["command"]))
        self.assertEqual(shlex.split(unit["command"]), [
            "citizen", "runs", "start", "unit-tests", "--target-kind", "installed",
            "--target-ref", str(self.root), "--param", "case=all", "--param",
            "root=" + str(self.root), "--json",
        ])

    def test_a_selection_absent_from_discovery_is_refused_before_launch(self):
        supervisor = self.supervisor()
        with mock.patch.object(supervisor, "_admit_locked") as admit:
            with self.assertRaisesRegex(runs.RunError, "not returned by discovery"):
                supervisor.start("unit-tests", {"root": str(self.root), "case": "missing.Test.test"},
                                 "installed", str(self.root))
        admit.assert_not_called()

    def test_one_discovered_test_runs_alone_and_the_displayed_command_repeats_it(self):
        selected = self.module + ".SampleTests.test_passes"
        supervisor = self.supervisor()
        self.addCleanup(supervisor.close)
        with mock.patch.object(supervisor, "_admit_locked"):
            started = supervisor.start("unit-tests", {"root": str(self.root), "case": selected},
                                       "installed", str(self.root))
        self.assertEqual(started["case_identities"], [selected])
        manual = shlex.split(started["exact_command"])
        self.assertEqual(manual[:4], ["citizen", "runs", "start", "unit-tests"])
        self.assertIn("case=" + selected, manual)

        cli_output = io.StringIO()
        cli_state = Path(self.temporary.name).resolve() / "cli-state"
        with mock.patch.object(harness, "state_dir", return_value=cli_state), \
                mock.patch.object(harness, "REPO", self.root), \
                mock.patch.object(runs, "default_catalog_path",
                                  return_value=REPO / "policy" / "studio" / "suites.json"), \
                mock.patch.object(runs.targets, "TargetService",
                                  return_value=FixtureTargetService()), \
                mock.patch.object(runs.RunSupervisor, "_admit_locked"), \
                contextlib.redirect_stdout(cli_output):
            self.assertEqual(harness.main(manual[1:]), 0)
        cli_started = json.loads(cli_output.getvalue())
        for name in ("suite_id", "case_identities", "target", "exact_command", "parameters",
                     "status", "cost_class", "command"):
            self.assertEqual(cli_started[name], started[name])

        rendered = self.catalog.get("unit-tests").render(
            {"root": str(self.root), "case": selected}, "installed", str(self.root))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO / "lib")
        completed = subprocess.run(rendered, cwd=str(REPO), env=environment,
                                   capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(selected, completed.stderr)
        self.assertNotIn("test_fails", completed.stderr)

    def test_progress_parser_preserves_result_and_traceback_detail(self):
        output = "\n".join((
            "test_fails (test_sample.SampleTests.test_fails) ... FAIL",
            "test_passes (test_sample.SampleTests.test_passes) ... ok",
            "", "======================================================================",
            "FAIL: test_fails (test_sample.SampleTests.test_fails)",
            "----------------------------------------------------------------------",
            "Traceback (most recent call last):",
            "  File \"tests/test_sample.py\", line 9, in test_fails",
            "AssertionError: 'actual' != 'expected'",
            "----------------------------------------------------------------------",
        ))
        parsed = {item["id"]: item for item in free_suites.parse_unit_output(output)}
        self.assertEqual(parsed["test_sample.SampleTests.test_passes"]["status"], "passed")
        failure = parsed["test_sample.SampleTests.test_fails"]
        self.assertEqual(failure["status"], "failed")
        self.assertIn("AssertionError", failure["detail"])

    def test_progress_parser_carries_split_results_and_late_tracebacks_across_chunks(self):
        parser = free_suites.UnitOutputState()
        first = parser.feed("test_fails (test_sample.SampleTests.test_fa")
        self.assertEqual(first, [])
        second = parser.feed(
            "ils) ... FAIL\n========================================"
            "==============================\nFAIL: test_fails (test_sample.SampleTests.test_fails)\n"
            "------------------------------------")
        self.assertEqual(second[0]["status"], "failed")
        self.assertEqual(second[0]["detail"], "")
        final = parser.feed(
            "----------------------------------\nTraceback (most recent call last):\n"
            "AssertionError: split detail\n----------------------------------------------------------------------\n",
            eof=True)
        self.assertIn("split detail", final[0]["detail"])

    def test_lint_findings_link_to_their_library_file_and_line(self):
        findings = free_suites.parse_lint_findings(
            "primitives/rules/example.md:17:3: forbidden personal value\n"
            "/private/hidden:2: must not escape\n../outside:4: ignored\n")
        self.assertEqual(findings, [{
            "path": "primitives/rules/example.md", "line": 17, "column": 3,
            "message": "forbidden personal value",
            "library_href": None,
        }])

    def test_lint_links_only_files_present_in_the_library_inventory(self):
        known = REPO / "primitives" / "rules" / "secrets.md"
        progress = {"lint_findings": [
            {"path": "primitives/rules/secrets.md", "line": 4, "library_href": None},
            {"path": "README.md", "line": 2, "library_href": None},
        ]}
        studio = mock.Mock(repo_root=REPO)
        inventory = {"modules": [{"source": {"path": str(known)}}]}
        with mock.patch.object(studio_security.server.module_library, "inventory",
                               return_value=inventory):
            studio_security.server._link_lint_findings(studio, progress)
        self.assertEqual(progress["lint_findings"][0]["library_href"],
                         "/library?path=primitives/rules/secrets.md&line=4")
        self.assertIsNone(progress["lint_findings"][1]["library_href"])

    def test_lint_link_inventory_refreshes_when_modules_change(self):
        source = REPO / "primitives" / "rules" / "secrets.md"
        studio = mock.Mock(repo_root=REPO)
        first = {"lint_findings": [{
            "path": "primitives/rules/secrets.md", "line": 4, "library_href": None,
        }]}
        second = json.loads(json.dumps(first))
        inventories = ({"modules": []}, {"modules": [{"source": {"path": str(source)}}]})
        with mock.patch.object(studio_security.server.module_library, "inventory",
                               side_effect=inventories) as inventory:
            studio_security.server._link_lint_findings(studio, first)
            studio_security.server._link_lint_findings(studio, second)
        self.assertIsNone(first["lint_findings"][0]["library_href"])
        self.assertEqual(second["lint_findings"][0]["library_href"],
                         "/library?path=primitives/rules/secrets.md&line=4")
        self.assertEqual(inventory.call_count, 2)

    def test_abandoned_run_progress_is_ttl_and_lru_bounded(self):
        studio = mock.Mock(run_progress=OrderedDict())
        for index in range(studio_security.server.RUN_PROGRESS_MAX_ENTRIES + 20):
            studio_security.server._save_run_progress(
                studio, "running-%d" % index, {"cursor": index}, False, now=float(index))
        self.assertEqual(len(studio.run_progress),
                         studio_security.server.RUN_PROGRESS_MAX_ENTRIES)
        self.assertNotIn("running-0", studio.run_progress)

        studio_security.server._save_run_progress(
            studio, "terminal", {"cursor": 0}, True, now=500.0)
        studio_security.server._prune_run_progress(
            studio, 500.0 + studio_security.server.RUN_PROGRESS_TERMINAL_TTL_SECONDS)
        self.assertNotIn("terminal", studio.run_progress)

    def test_cli_catalog_is_the_same_core_payload(self):
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=Path(self.temporary.name).resolve() / "state"), \
                mock.patch.object(harness, "REPO", self.root), \
                mock.patch.object(runs, "default_catalog_path",
                                  return_value=REPO / "policy" / "studio" / "suites.json"), \
                contextlib.redirect_stdout(output):
            self.assertEqual(harness.main(["runs", "catalog", "--json"]), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(len(payload["unit_tests"]["cases"]), 2)
        self.assertEqual(payload["commands"]["catalog"], "citizen runs catalog --json")


class StudioRunApiTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.session_cookie = self.cookie(headers)
        status, _headers, body = self.request(
            "GET", "/api/session", {"Cookie": self.session_cookie})
        self.assertEqual(status, 200, body)
        self.csrf = json.loads(body)["csrf_token"]

    def api_post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        return self.request("POST", path, {
            "Cookie": self.session_cookie,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Origin": self.record["url"].rstrip("/"),
            "X-Studio-CSRF": self.csrf,
        }, body)

    def test_authenticated_routes_launch_stream_show_and_cancel_one_discovered_test(self):
        status, _headers, body = self.request(
            "GET", "/api/runs/catalog", {"Cookie": self.session_cookie})
        self.assertEqual(status, 200)
        catalog = json.loads(body)
        self.assertEqual({item["id"] for item in catalog["suites"]},
                         free_suites.FREE_SUITE_IDS)
        unit = next(item for item in catalog["suites"] if item["id"] == "unit-tests")
        selected = "test_studio_free_suites.FreeSuiteTests.test_lint_findings_link_to_their_library_file_and_line"
        self.assertIn(selected, catalog["unit_tests"]["scopes"])

        invalid = {"suite_id": "unit-tests",
                   "parameters": {"root": unit["parameters"]["root"], "case": "absent.Test.test"},
                   "target_kind": "installed", "target_ref": unit["parameters"]["root"]}
        outside = dict(invalid)
        outside["parameters"] = {"root": "/tmp", "case": selected}
        outside["target_ref"] = "/tmp"
        status, _headers, body = self.api_post("/api/runs/start", outside)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_run"})

        status, _headers, body = self.api_post("/api/runs/start", invalid)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_run"})

        request = dict(invalid)
        request["parameters"] = {"root": unit["parameters"]["root"], "case": selected}
        status, _headers, body = self.api_post("/api/runs/start", request)
        self.assertEqual(status, 200, body)
        started = json.loads(body)
        self.assertEqual(started["case_identities"], [selected])
        self.assertIn("case=" + selected, shlex.split(started["exact_command"]))

        stdout_cursor = stderr_cursor = 0
        update = None
        case_results = {}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, headers, body = self.api_post("/api/runs/stream", {
                "run_id": started["run_id"], "stdout_cursor": stdout_cursor,
                "stderr_cursor": stderr_cursor,
            })
            self.assertEqual(status, 200)
            self.assertEqual(headers.get_content_type(), "text/event-stream")
            data = next(line[6:] for line in body.decode().splitlines()
                        if line.startswith("data: "))
            update = json.loads(data)
            stdout_cursor = update["stdout"]["cursor"]
            stderr_cursor = update["stderr"]["cursor"]
            case_results.update({item["id"]: item for item in update["progress"]["cases"]})
            if update["run"]["status"] in runs.TERMINAL:
                break
            time.sleep(0.05)
        self.assertIsNotNone(update)
        self.assertEqual(update["run"]["status"], "succeeded")
        self.assertEqual(list(case_results.values()), [{
            "id": selected, "status": "passed", "detail": "",
        }])

        status, _headers, body = self.api_post(
            "/api/runs/show", {"run_id": started["run_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "succeeded")
        status, _headers, body = self.api_post(
            "/api/runs/cancel", {"run_id": started["run_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
