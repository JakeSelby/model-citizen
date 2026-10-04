# SPDX-License-Identifier: MIT
"""Effective-selection Studio provider, parity, provenance and route tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import selection, server  # noqa: E402


class StudioSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.repository = Path(self.temp.name) / "project"
        self.repository.mkdir(parents=True)
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "stances": {"testing": "pragmatic", "voice": "answer-card"},
            "claude": {"manage": True}, "codex": {"manage": True},
        }), encoding="utf-8")
        self.config = config
        self.project = Path(self.temp.name) / "project-selection.json"
        self.project.write_text(json.dumps({
            "stances": {"testing": "off", "voice": "scannable"},
        }), encoding="utf-8")
        self.config = self.config.resolve()
        self.project = self.project.resolve()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("HARNESS_")}
        self.env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})

    @staticmethod
    def row(report, kind, unit):
        group = next(group for group in report["groups"] if group["kind"] == kind)
        return next(row for row in group["rows"] if row["unit"] == unit)

    def test_project_override_keeps_user_value_and_both_files(self):
        report = selection.report(ROOT, str(self.repository), str(self.project), self.env)
        row = self.row(report, "stances", "testing")
        self.assertEqual((row["value"], row["source"], row["source_file"]),
                         ("off", "project", str(self.project)))
        user = next(item for item in row["overridden"] if item["source"] == "user")
        self.assertEqual((user["value"], user["source_file"], user["saved"]),
                         ("pragmatic", str(self.config), True))

    def test_environment_stance_is_an_unsaved_session_value(self):
        env = dict(self.env, HARNESS_STANCE_VOICE="off")
        report = selection.report(ROOT, str(self.repository), str(self.project), env)
        row = self.row(report, "stances", "voice")
        self.assertEqual((row["value"], row["source"], row["saved"], row["source_file"]),
                         ("off", "session", False, "HARNESS_STANCE_VOICE"))
        project = next(item for item in row["overridden"] if item["source"] == "project")
        self.assertEqual((project["value"], project["source_file"]),
                         ("scannable", str(self.project)))

    def test_embedded_selection_equals_cli_json_for_the_same_inputs(self):
        report = selection.report(ROOT, str(self.repository), str(self.project), self.env)
        env = dict(self.env, HARNESS_PROJECT_CONFIG=str(self.project))
        completed = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "harness"), "selection", "--json"],
            cwd=self.repository, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report["selection"], json.loads(completed.stdout))
        self.assertEqual(report["commands"]["selection"],
                         "HARNESS_PROJECT_CONFIG=%s citizen selection --json" % self.project)

    def test_budget_rows_use_the_lint_caps_for_each_runtime(self):
        report = selection.report(ROOT, str(self.repository), str(self.project), self.env)
        self.assertEqual([row["runtime"] for row in report["budgets"]],
                         ["claude-code", "codex"])
        first, second = report["budgets"]
        for field in ("used_tokens", "token_cap", "used_lines", "line_cap", "selected_lines"):
            self.assertEqual(first[field], second[field])
        harness = selection._harness_module(ROOT)
        self.assertEqual(first["used_tokens"], harness.always_loaded_tokens(ROOT)[0])
        self.assertEqual(first["token_cap"], harness.ALWAYS_LOADED_TOKEN_CAP)
        self.assertEqual(first["used_lines"], harness.always_loaded_lines(ROOT)[0])
        self.assertEqual(first["line_cap"], harness.ALWAYS_LOADED_CAP)

    def test_selection_route_is_registered_with_cli_parity_and_csrf_transport(self):
        route = server.ROUTES.resolve("POST", "/api/selection")
        self.assertIsNotNone(route)
        self.assertIsNone(route.parity_exemption)
        self.assertEqual(route.cli_command, ("citizen", "selection", "--report", "--repository",
                                             "{repository}", "--project-file", "{project_file}",
                                             "--json"))
        self.assertEqual(route.request_media_type, "application/json")

        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.request_json = {"repository": str(self.repository),
                                "project_file": str(self.project)}
        payload = selection.report(ROOT, str(self.repository), str(self.project), self.env)
        with mock.patch.object(selection, "report", return_value=payload) as called:
            server._selection_read(handler, route)
        called.assert_called_once_with(ROOT, str(self.repository), str(self.project))
        handler._json.assert_called_once_with(200, payload)

    def test_invalid_selection_request_is_a_bounded_error(self):
        route = server.ROUTES.resolve("POST", "/api/selection")
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.request_json = {"repository": [], "project_file": ""}
        server._selection_read(handler, route)
        handler._error.assert_called_once_with(400, "invalid_request")


if __name__ == "__main__":
    unittest.main()
