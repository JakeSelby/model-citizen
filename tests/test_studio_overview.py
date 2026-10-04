# SPDX-License-Identifier: MIT
"""The Studio Overview is a read-only view over the CLI's existing queries."""
from __future__ import annotations

import importlib.machinery
import os
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_studio_security as security_support

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core import overview  # noqa: E402
from harness_core.studio import server  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness_studio_overview_test",
                                              str(ROOT / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness_studio_overview_test", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)


class OverviewDomainTests(unittest.TestCase):
    def sources(self):
        return dict(
            catalog=lambda: {"version": "1.2.3"},
            doctor=lambda: ["model-citizen 1.2.3", "  hooks: none; run `citizen sync`",
                            "  identity: set"],
            drift=lambda: ["missing link /tmp/rules"],
            selection=lambda: {"mode": "focused", "sources": {"mode": "user"}},
            runs=lambda: [{"run_id": "run-1", "suite_id": "smoke", "status": "succeeded",
                           "created_at": "2026-09-27T10:00:00Z",
                           "target": {"kind": "installed"}}],
        )

    def test_failing_doctor_keeps_its_message_and_exact_fix(self):
        result = overview.snapshot(ROOT, **self.sources())
        failing = result["doctor"]["checks"][0]
        self.assertEqual(failing["message"], "hooks: none; run `citizen sync`")
        self.assertEqual(failing["fix"], "citizen sync")
        self.assertEqual(failing["status"], "attention")
        self.assertEqual(result["drift"]["command"], "citizen sync")
        self.assertEqual(result["mode"], {"status": "current", "value": "focused",
                                           "source": "user"})

    def test_doctor_repairs_preserve_installer_and_plugin_alternatives(self):
        checks = overview.doctor_checks([
            "plugin: neither; run `bin/harness install` or `/plugin install model-citizen@market`",
        ])
        self.assertEqual(checks[0]["fix"], "bin/harness install")
        self.assertEqual(checks[0]["fixes"], [
            "bin/harness install", "/plugin install model-citizen@market",
        ])

    def test_the_constrained_roles_pointer_is_informational_not_a_repair(self):
        """Regression: doctor prints it on every machine, so counting it kept every home amber."""
        with tempfile.TemporaryDirectory() as home:
            env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
            env.update(HOME=home, HARNESS_HOME=home)
            done = subprocess.run([sys.executable, str(ROOT / "bin" / "harness"), "doctor"],
                                  env=env, capture_output=True, text=True, timeout=300)
        lines = [line for line in done.stdout.splitlines() if "constrained roles:" in line]
        self.assertEqual(len(lines), 1, done.stdout)
        check = overview.doctor_checks(lines)[0]
        self.assertEqual((check["status"], check["fixes"], check["fix"]), ("informational", [], None))
        self.assertEqual(overview.doctor_checks(["hooks: none; run `citizen sync`"])[0]["status"],
                         "attention")
        # The pointer prefix never hides a real warning.
        self.assertEqual(overview.doctor_checks(["constrained roles: worker registry unreadable"])[0]["status"],
                         "attention")

    def test_each_source_failure_stays_distinct_from_empty_or_healthy(self):
        sources = self.sources()
        sources["doctor"] = mock.Mock(side_effect=OSError("private detail"))
        sources["runs"] = mock.Mock(side_effect=ValueError("private detail"))
        result = overview.snapshot(ROOT, **sources)
        self.assertEqual(result["doctor"]["status"], "failed")
        self.assertEqual(result["runs"]["status"], "failed")
        self.assertNotIn("private detail", str(result))
        self.assertEqual(result["drift"]["status"], "drift")

    def test_newer_stable_release_links_changelog_without_fetching_or_updating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email",
                            "test" + "@" + "example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / "VERSION").write_text("1.4.0\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "VERSION"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "stable"], check=True)
            subprocess.run(["git", "-C", str(root), "branch", "stable"], check=True)
            subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                            "https://github.com/example/project.git"], check=True)
            result = overview.stable_release(root, "1.3.9")
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["version"], "1.4.0")
        self.assertEqual(result["changelog_url"],
                         "https://github.com/example/project/blob/stable/CHANGELOG.md")

    def test_equal_stable_release_is_current_and_has_no_action_link(self):
        with mock.patch.object(overview, "_git", side_effect=["1.2.3\n"]):
            self.assertEqual(overview.stable_release(ROOT, "1.2.3"), {
                "status": "current", "version": "1.2.3", "changelog_url": None,
            })

    def test_cli_adapter_calls_the_same_doctor_diff_catalog_selection_and_run_functions(self):
        with mock.patch.object(harness, "cmd_doctor", side_effect=lambda _args: print(
                "model-citizen 1.2.3\n  identity: set") or 0) as doctor_call, \
             mock.patch.object(harness, "_diff_lines", return_value=[]) as diff_call, \
             mock.patch.object(harness.primitive_catalog, "catalog",
                               return_value={"version": "1.2.3"}) as catalog_call, \
             mock.patch.object(harness, "load_selection",
                               return_value={"mode": None, "sources": {"mode": "defaults"}}) as selection_call, \
             mock.patch.object(harness.studio.runs, "read_only_list",
                               return_value=[]) as runs_read, \
             mock.patch.object(harness.studio.runs, "RunSupervisor",
                               side_effect=AssertionError("Hub must not construct the mutating supervisor")) as supervisor_factory, \
             mock.patch.object(overview, "stable_release", return_value={
                 "status": "current", "version": "1.2.3", "changelog_url": None,
             }):
            result = harness._studio_overview()
        doctor_call.assert_called_once()
        diff_call.assert_called_once_with()
        catalog_call.assert_called_once_with(ROOT)
        selection_call.assert_called_once_with()
        runs_read.assert_called_once_with(harness.state_dir() / "studio")
        supervisor_factory.assert_not_called()
        self.assertEqual(result["installed"]["version"], "1.2.3")
        self.assertEqual(
            [check["message"] for check in result["doctor"]["checks"]],
            ["identity: set"],
        )
        self.assertEqual(result["drift"]["items"], [])

    def test_overview_values_match_the_cli_doctor_diff_and_catalog_outputs(self):
        doctor_lines = harness._studio_doctor_lines()
        drift_lines = harness._diff_lines()
        catalog = harness.primitive_catalog.catalog(ROOT)
        with mock.patch.object(harness, "_studio_doctor_lines", return_value=doctor_lines), \
             mock.patch.object(harness, "_diff_lines", return_value=drift_lines), \
             mock.patch.object(harness.primitive_catalog, "catalog", return_value=catalog), \
             mock.patch.object(harness.studio.runs, "read_only_list", return_value=[]), \
             mock.patch.object(overview, "stable_release", return_value={
                 "status": "current", "version": catalog.get("version"),
                 "changelog_url": None,
             }):
            result = harness._studio_overview()

        expected_doctor = [line.strip() for line in doctor_lines[1:] if line.strip()]
        self.assertEqual(
            [check["message"] for check in result["doctor"]["checks"]], expected_doctor,
        )
        self.assertEqual(result["drift"]["items"], drift_lines)
        self.assertEqual(result["installed"]["version"], catalog["version"])


class OverviewRouteTests(unittest.TestCase):
    def test_route_is_registered_read_only_with_cli_parity(self):
        route = server.ROUTES.resolve("GET", "/api/overview")
        self.assertIsNotNone(route)
        self.assertIsNone(route.parity_exemption)
        self.assertEqual(route.cli_command, ("citizen", "doctor", "--json"))
        self.assertIsNone(server.ROUTES.resolve("POST", "/api/overview"))

    def test_route_validates_and_returns_the_installed_source(self):
        payload = overview.unavailable("test")
        handler = mock.Mock()
        route = server.ROUTES.resolve("GET", "/api/overview")
        with mock.patch.object(overview, "current", return_value=payload):
            server._overview(handler, route)
        handler._json.assert_called_once_with(200, payload)


class OverviewHttpBoundaryTests(security_support.StudioSecurityFixture):
    def test_overview_refuses_anonymous_access_and_answers_the_launcher_session(self):
        status, _headers, body = self.request("GET", "/api/overview")
        self.assertEqual(status, 401)
        self.assertEqual(body, b'{"error":"unauthorized"}\n')

        _issued, status, bootstrap_headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        status, headers, body = self.request(
            "GET", "/api/overview", {"Cookie": self.cookie(bootstrap_headers)})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        payload = security_support.json.loads(body)
        self.assertEqual(payload["commands"]["doctor"], "citizen doctor")
        self.assertEqual(payload["commands"]["diff"], "citizen diff")
        self.assertEqual(payload["commands"]["catalog"], "citizen catalog")


if __name__ == "__main__":
    unittest.main()
