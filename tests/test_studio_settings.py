# SPDX-License-Identifier: MIT
"""Schema-driven Studio settings and draft-save tests."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import server, settings  # noqa: E402
from harness_core.studio.mutations import MutationExecutor  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness_studio_settings_test", str(ROOT / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness_studio_settings_test", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)


class SettingsDescriptorTests(unittest.TestCase):
    def test_descriptor_is_the_complete_generic_form_authority(self):
        described = settings.descriptor(ROOT)
        fields = [field for section in described["sections"] for field in section["fields"]]
        paths = {field["path"] for field in fields}
        self.assertEqual(described["schema_version"], 1)
        self.assertEqual(described["commands"]["schema"],
                         "citizen draft settings schema --json")
        self.assertIn("identity.name", paths)
        self.assertIn("permissions", paths)
        self.assertIn("primitive_roots", paths)
        self.assertIn("telemetry.native", paths)
        self.assertIn("governance.jev.state_fields", paths)
        self.assertIn("integrations.architecture-viewer.implementation", paths)
        self.assertTrue(all(field["kind"] and field["help"] and field["provenance"]
                            for field in fields))
        by_path = dict((field["path"], field) for field in fields)
        self.assertEqual(by_path["telemetry.endpoint"]["default"], "http://localhost:4318")
        self.assertEqual(by_path["telemetry.allow_sample_rate"]["default"], 20)
        self.assertEqual(by_path["governance.jev.max_requests"]["default"], 50)
        self.assertEqual(by_path["identity.expertise"]["options"], ["expert", "beginner"])
        self.assertEqual(by_path["governance.jev.modes"]["constraints"]["keys"],
                         list(settings.controls.POINTS))
        self.assertEqual(by_path["governance.jev.modes"]["constraints"]["values"],
                         list(settings.controls.MODES))
        telemetry = next(section for section in described["sections"]
                         if section["id"] == "telemetry")
        self.assertIn("account", telemetry["description"])
        self.assertIn("Codex", telemetry["description"])

    def test_routes_are_registered_under_the_existing_security_boundary(self):
        routes = {(route.method, route.path): route for route in server.ROUTES.entries}
        for key in (("GET", "/api/configure/schema"),
                    ("POST", "/api/configure/read"),
                    ("POST", "/api/configure/preview"),
                    ("POST", "/api/configure/save")):
            self.assertIn(key, routes)
            self.assertIsNone(routes[key].parity_exemption)
            self.assertEqual(routes[key].cli_command[:3], ("citizen", "draft", "settings"))
        self.assertEqual(routes[("POST", "/api/configure/save")].request_media_type,
                         "application/json")

    def test_domain_route_without_a_cli_mapping_is_refused(self):
        route = server.ROUTES.resolve("GET", "/api/configure/schema")
        self.assertIsNotNone(route)
        unmapped = server.Route(route.method, "/api/unmapped", route.media_type,
                                route.response_schema, route.handler, None)
        with self.assertRaisesRegex(ValueError, "must name its citizen command"):
            server.RouteRegistry((unmapped,))

    def test_preview_route_returns_domain_validation_without_bypassing_registry_schema(self):
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        handler.request_json = {"draft": "test", "changes": {"identity.name": "Operator"}}
        payload = {"valid": True, "errors": [], "warnings": [],
                   "changed": ["identity.name"], "preview": {}, "base_revision": "a" * 40}
        route = server.ROUTES.resolve("POST", "/api/configure/preview")
        self.assertIsNotNone(route)
        with mock.patch.object(settings, "preview", return_value=payload):
            server._configure_preview(handler, route)
        handler._json.assert_called_once_with(200, payload)

    def test_read_route_has_an_explicit_unavailable_state(self):
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        handler.request_json = {"draft": "missing"}
        route = server.ROUTES.resolve("POST", "/api/configure/read")
        self.assertIsNotNone(route)
        unavailable = {"status": "unavailable", "message": "draft is absent", "draft": {},
                       "values": {}, "warnings": []}
        with mock.patch.object(settings, "read", return_value=unavailable):
            server._configure_read(handler, route)
        payload = handler._json.call_args.args[1]
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["values"], {})

    def test_save_route_submits_the_domain_mutation_to_the_single_executor(self):
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        handler.request_json = {"draft": "test", "base_revision": "a" * 40,
                                "idempotency_key": "save-1", "changes": {}}
        payload = {"valid": True, "errors": [], "warnings": [], "changed": [],
                   "preview": {}, "base_revision": "a" * 40, "saved": True, "result": {}}
        handler.server.mutations.call.side_effect = lambda operation: operation()
        route = server.ROUTES.resolve("POST", "/api/configure/save")
        self.assertIsNotNone(route)
        with mock.patch.object(settings, "save", return_value=payload):
            server._configure_save(handler, route)
        handler.server.mutations.call.assert_called_once()
        handler._json.assert_called_once_with(200, payload)

    def test_cli_and_http_preview_delegate_to_the_same_core_payload(self):
        payload = {"valid": True, "errors": [], "warnings": [],
                   "changed": ["identity.name"], "preview": {"identity.name": "Operator"},
                   "base_revision": "a" * 40}
        changes = {"identity.name": "Operator"}
        handler = mock.Mock()
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        handler.request_json = {"draft": "test", "changes": changes}
        route = server.ROUTES.resolve("POST", "/api/configure/preview")
        self.assertIsNotNone(route)
        with mock.patch.object(settings, "preview", return_value=payload):
            server._configure_preview(handler, route)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as source:
            json.dump(changes, source)
            source.flush()
            output = StringIO()
            with mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                    mock.patch.object(harness, "git_root", return_value=ROOT), \
                    mock.patch.object(harness.studio_settings, "preview", return_value=payload) as preview, \
                    redirect_stdout(output):
                code = harness.main([
                    "draft", "settings", "preview", "test", "--changes", source.name, "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), handler._json.call_args.args[1])
        preview.assert_called_once_with(ROOT, "test", changes)

    def test_all_cli_settings_adapters_emit_json_and_preserve_domain_errors(self):
        operations = (
            (["draft", "settings", "schema", "--json"], "descriptor", (ROOT,)),
            (["draft", "settings", "read", "test", "--json"], "read", (ROOT, "test")),
        )
        for argv, operation, expected in operations:
            with self.subTest(operation=operation):
                output = StringIO()
                with mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                        mock.patch.object(harness, "git_root", return_value=ROOT), \
                        mock.patch.object(harness.studio_settings, operation,
                                          return_value={"operation": operation}) as called, \
                        redirect_stdout(output):
                    self.assertEqual(harness.main(argv), 0)
                self.assertEqual(json.loads(output.getvalue()), {"operation": operation})
                called.assert_called_once_with(*expected)

        domain_error = {"valid": False, "errors": [{"path": "draft", "message": "reload the draft"}],
                        "warnings": [], "changed": [], "preview": {}, "base_revision": "",
                        "saved": False, "result": None}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as source:
            json.dump({}, source)
            source.flush()
            output = StringIO()
            with mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                    mock.patch.object(harness, "git_root", return_value=ROOT), \
                    mock.patch.object(harness.studio_settings, "save", return_value=domain_error), \
                    redirect_stdout(output):
                code = harness.main([
                    "draft", "settings", "save", "test", "--base-revision", "a" * 40,
                    "--idempotency-key", "save-1", "--changes", source.name, "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), domain_error)

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json") as source:
            source.write("not json")
            source.flush()
            output = StringIO()
            with mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                    mock.patch.object(harness, "git_root", return_value=ROOT), redirect_stdout(output):
                code = harness.main([
                    "draft", "settings", "preview", "test", "--changes", source.name, "--json",
                ])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid-input")


class SettingsValidationTests(unittest.TestCase):
    def setUp(self):
        self.integration_health = mock.patch.object(
            settings.integrations, "invoke",
            return_value={"status": "ok", "result": {"capabilities": []}},
        )
        self.integration_health.start()
        self.addCleanup(self.integration_health.stop)

    def draft(self, config=None):
        base = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

        def merge(target, changes):
            for key, value in changes.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = copy.deepcopy(value)

        if config is not None:
            merge(base, config)
        return {"draft": {"name": "test", "revision": "a" * 40},
                "config": base}

    def raw_draft(self, config):
        return {"draft": {"name": "test", "revision": "a" * 40},
                "config": config}

    def available(self):
        return {"availability": "available"}

    def test_invalid_value_saves_nothing_and_names_the_field(self):
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
            result = settings.save(ROOT, "test", "a" * 40, "save-1",
                                   {"permissions": "anything"})
        self.assertFalse(result["saved"])
        self.assertEqual(result["errors"][0]["path"], "permissions")
        checkpoint.assert_not_called()

    def test_draft_failures_have_stable_read_preview_and_save_payloads(self):
        failure = settings.drafts.DraftError("not-found", "draft is absent")
        with mock.patch.object(settings.drafts, "read_config", side_effect=failure):
            read = settings.read(ROOT, "missing")
            preview = settings.preview(ROOT, "missing", {})
            saved = settings.save(ROOT, "missing", "a" * 40, "save-1", {})
        self.assertEqual(read["status"], "unavailable")
        self.assertEqual(preview["errors"], [{"path": "draft", "message": "draft is absent"}])
        self.assertEqual(saved["errors"], preview["errors"])
        self.assertFalse(saved["saved"])

    def test_permission_dependency_is_checked_before_checkpoint(self):
        config = {"permissions": "inherit", "permissions_bypass_acknowledged": False}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(config)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            result = settings.preview(ROOT, "test", {"permissions": "bypass"})
        self.assertFalse(result["valid"])
        self.assertIn("permissions_bypass_acknowledged",
                      [error["path"] for error in result["errors"]])

    def test_every_present_modeled_field_and_required_field_are_validated(self):
        base = self.draft()["config"]
        cases = []
        missing_name = copy.deepcopy(base)
        missing_name["identity"].pop("name")
        cases.append(("missing required identity", missing_name, "identity.name"))
        for label, path, value in (
            ("scalar", "permissions", 7),
            ("container", "primitive_roots", {}),
            ("nested object", "telemetry.labels", []),
            ("number", "governance.jev.timeout", "slow"),
        ):
            candidate = copy.deepcopy(base)
            settings._set(candidate, path, value)
            cases.append((label, candidate, path))

        for label, candidate, path in cases:
            with self.subTest(label=label), \
                    mock.patch.object(settings.drafts, "read_config",
                                      return_value=self.raw_draft(candidate)), \
                    mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                    mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
                read = settings.read(ROOT, "test")
                preview = settings.preview(ROOT, "test", {"identity.pronouns": "she/her"})
                saved = settings.save(
                    ROOT, "test", "a" * 40, "unrelated-change", {"identity.pronouns": "she/her"},
                )
            self.assertEqual(read["status"], "error")
            self.assertFalse(preview["valid"])
            self.assertFalse(saved["saved"])
            self.assertIn(path, [error["path"] for error in preview["errors"]])
            checkpoint.assert_not_called()

    def test_every_modeled_container_must_be_an_object(self):
        self.assertEqual(set(settings.MODELED_CONTAINERS), {
            "identity", "telemetry", "governance", "governance.jev",
            "integrations", "integrations.architecture-viewer",
        })
        for path in settings.MODELED_CONTAINERS:
            with self.subTest(path=path):
                candidate = self.draft()["config"]
                settings._set(candidate, path, [])
                with mock.patch.object(settings.drafts, "read_config",
                                       return_value=self.raw_draft(candidate)), \
                        mock.patch.object(settings.integrations, "resolve",
                                          return_value=self.available()), \
                        mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
                    read = settings.read(ROOT, "test")
                    preview = settings.preview(ROOT, "test", {"permissions": "auto"})
                    saved = settings.save(
                        ROOT, "test", "a" * 40, "invalid-container", {"permissions": "auto"},
                    )
                self.assertEqual(read["status"], "error")
                self.assertFalse(preview["valid"])
                self.assertFalse(saved["saved"])
                self.assertIn(path, [error["path"] for error in preview["errors"]])
                checkpoint.assert_not_called()

    def test_every_present_falsy_reference_is_invalid_and_redacted(self):
        for path in ("telemetry.headers_env", "telemetry.headers_file"):
            for value in (False, 0, [], {}):
                with self.subTest(path=path, value=value):
                    candidate = self.draft()["config"]
                    settings._set(candidate, path, value)
                    with mock.patch.object(settings.drafts, "read_config",
                                           return_value=self.raw_draft(candidate)), \
                            mock.patch.object(settings.integrations, "resolve",
                                              return_value=self.available()), \
                            mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
                        read = settings.read(ROOT, "test")
                        preview = settings.preview(ROOT, "test", {"identity.pronouns": "she/her"})
                        saved = settings.save(
                            ROOT, "test", "a" * 40, "invalid-ref",
                            {"identity.pronouns": "she/her"},
                        )
                    self.assertEqual(read["status"], "error")
                    self.assertEqual(read["values"][path], {"configured": True})
                    self.assertFalse(preview["valid"])
                    self.assertFalse(saved["saved"])
                    self.assertIn(path, [error["path"] for error in preview["errors"]])
                    checkpoint.assert_not_called()

    def test_primitive_root_dependencies_use_the_existing_stance_resolver(self):
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary)
            source = ROOT / "primitives" / "stances" / "voice" / "scannable.md"
            target = duplicate / "stances" / "voice" / "scannable.md"
            target.parent.mkdir(parents=True)
            target.write_bytes(source.read_bytes())
            with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                    mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
                result = settings.preview(ROOT, "test", {"primitive_roots": [str(duplicate)]})
        self.assertFalse(result["valid"])
        self.assertIn("duplicate stance authority",
                      next(error["message"] for error in result["errors"]
                           if error["path"] == "primitive_roots"))

    def test_header_reference_is_saved_as_a_name_and_never_resolved(self):
        marker = "HARNESS_OTLP_HEADERS"
        captured = {}

        def checkpoint(_root, _name, _revision, _key, config, check_command=None):
            captured.update(config)
            return {"revision": "b" * 40, "replayed": False}

        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config", side_effect=checkpoint):
            result = settings.save(
                ROOT, "test", "a" * 40, "save-2",
                {"telemetry.headers_env": {"source": "environment", "name": marker}},
            )
        self.assertTrue(result["saved"])
        self.assertEqual(captured["telemetry"]["headers_env"], marker)
        self.assertEqual(result["preview"]["telemetry.headers_env"],
                         {"source": "environment", "name": marker})
        self.assertNotIn("value", json.dumps(result))

    def test_empty_reference_objects_remove_existing_header_references(self):
        captured = {}
        existing = {"telemetry": {
            "headers_env": "HARNESS_OTLP_HEADERS",
            "headers_file": "/private/header-reference",
        }}

        def checkpoint(_root, _name, _revision, _key, config, check_command=None):
            captured.update(config)
            return {"revision": "b" * 40, "replayed": False}

        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(existing)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config", side_effect=checkpoint):
            result = settings.save(ROOT, "test", "a" * 40, "clear-refs", {
                "telemetry.headers_env": {"source": "environment", "name": ""},
                "telemetry.headers_file": {"source": "file", "name": ""},
            })
        self.assertTrue(result["saved"])
        self.assertEqual(result["changed"], ["telemetry.headers_env", "telemetry.headers_file"])
        self.assertNotIn("headers_env", captured.get("telemetry", {}))
        self.assertNotIn("headers_file", captured.get("telemetry", {}))
        self.assertIsNone(result["preview"]["telemetry.headers_env"])
        self.assertIsNone(result["preview"]["telemetry.headers_file"])

    def test_reference_rejects_inline_or_malformed_values(self):
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            inline = settings.preview(ROOT, "test", {"telemetry.headers_env": "secret=value"})
            malformed = settings.preview(ROOT, "test", {
                "telemetry.headers_env": {"source": "environment", "name": "mixed-case"},
            })
        self.assertFalse(inline["valid"])
        self.assertFalse(malformed["valid"])

    def test_nonfinite_numbers_are_refused_before_preview_or_checkpoint(self):
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
            result = settings.save(ROOT, "test", "a" * 40, "save-nan", {
                "telemetry.labels": {"invalid": float("nan")},
                "governance.jev.timeout": float("inf"),
            })
        self.assertFalse(result["saved"])
        self.assertEqual(
            {error["path"] for error in result["errors"]},
            {"telemetry.labels", "governance.jev.timeout"},
        )
        self.assertNotIn("NaN", json.dumps(result))
        checkpoint.assert_not_called()

    def test_existing_inline_header_secret_is_refused_without_echoing_it(self):
        marker = "not-a-real-inline-secret"
        config = {"telemetry": {"headers": {"authorization": marker}}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(config)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            result = settings.read(ROOT, "test")
        self.assertEqual(result["status"], "error")
        self.assertNotIn(marker, json.dumps(result))
        self.assertIn("headers_env or headers_file", result["message"])

    def test_existing_malformed_header_reference_is_redacted_in_read_and_preview(self):
        marker = "Authorization=not-a-real-secret"
        config = {"telemetry": {"headers_env": marker}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(config)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            read = settings.read(ROOT, "test")
            preview = settings.preview(ROOT, "test", {"identity.name": "Operator"})
        self.assertEqual(read["status"], "error")
        self.assertFalse(preview["valid"])
        self.assertEqual(read["values"]["telemetry.headers_env"], {"configured": True})
        self.assertEqual(preview["preview"]["telemetry.headers_env"], {"configured": True})
        self.assertNotIn(marker, json.dumps(read))
        self.assertNotIn(marker, json.dumps(preview))

    def test_malformed_persisted_references_block_checkpoint_until_replaced_or_cleared(self):
        marker = "not-a-real-keychain-secret"
        malformed = {"telemetry": {
            "headers_env": "lowercase_name",
            "headers_file": {"source": "keychain", "name": marker},
        }}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(malformed)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config") as checkpoint:
            read = settings.read(ROOT, "test")
            preview = settings.preview(ROOT, "test", {"identity.name": "Unrelated"})
            refused = settings.save(
                ROOT, "test", "a" * 40, "invalid-existing", {"identity.name": "Unrelated"},
            )
            repaired = settings.save(ROOT, "test", "a" * 40, "repair-existing", {
                "telemetry.headers_env": {
                    "source": "environment", "name": "HARNESS_OTLP_HEADERS",
                },
                "telemetry.headers_file": {"source": "file", "name": ""},
            })
        self.assertEqual(read["status"], "error")
        self.assertFalse(preview["valid"])
        self.assertFalse(refused["saved"])
        self.assertEqual(
            {error["path"] for error in preview["errors"] if error["path"].startswith("telemetry.headers_")},
            {"telemetry.headers_env", "telemetry.headers_file"},
        )
        self.assertEqual(read["values"]["telemetry.headers_env"], {"configured": True})
        self.assertEqual(read["values"]["telemetry.headers_file"], {"configured": True})
        self.assertNotIn(marker, json.dumps(read))
        self.assertNotIn(marker, json.dumps(preview))
        self.assertTrue(repaired["saved"])
        checkpoint.assert_called_once()

    def test_persisted_file_reference_with_line_break_is_invalid_and_redacted(self):
        marker = "/private/header\nnot-a-real-secret"
        config = {"telemetry": {"headers_file": marker}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(config)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            result = settings.preview(ROOT, "test", {})
        self.assertFalse(result["valid"])
        self.assertIn("telemetry.headers_file", [error["path"] for error in result["errors"]])
        self.assertEqual(result["preview"]["telemetry.headers_file"], {"configured": True})
        self.assertNotIn(marker, json.dumps(result))

    def test_available_integration_runs_describe_health_check_without_echoing_failures(self):
        marker = "not-a-real-adapter-secret"
        binding = {"availability": "available", "adapter": "fixture", "descriptor": {}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=binding), \
                mock.patch.object(settings.integrations, "invoke", return_value={
                    "status": "error", "error": {"code": marker, "message": marker},
                }) as invoke:
            result = settings.preview(ROOT, "test", {})
        self.assertFalse(result["valid"])
        self.assertNotIn(marker, json.dumps(result))
        self.assertIn("health check failed", result["errors"][0]["message"])
        invoke.assert_called_once_with(binding, "describe", timeout=30)

    def test_integration_dependency_and_unavailable_state_are_explicit(self):
        unavailable = {"availability": "unavailable", "reason": "adapter executable is missing"}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()), \
                mock.patch.object(settings.integrations, "resolve", return_value=unavailable):
            preview = settings.preview(ROOT, "test", {})
        self.assertTrue(preview["valid"])
        self.assertIn("unavailable", preview["warnings"][0].lower())

        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft()):
            missing = settings.preview(ROOT, "test", {
                "integrations.architecture-viewer.implementation": "custom",
                "integrations.architecture-viewer.adapter": "missing",
            })
        self.assertFalse(missing["valid"])
        self.assertIn("integrations.architecture-viewer",
                      [error["path"] for error in missing["errors"]])

    def test_legacy_native_boolean_is_normalized_for_the_multi_select(self):
        config = {"telemetry": {"native": True}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(config)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()):
            result = settings.read(ROOT, "test")
        self.assertEqual(result["values"]["telemetry.native"], ["claude-code", "codex"])

    def test_valid_save_uses_draft_checkpoint_and_keeps_unmodelled_config(self):
        existing = {"identity": {"name": "Before"}, "future_key": {"kept": True}}
        with mock.patch.object(settings.drafts, "read_config", return_value=self.draft(existing)), \
                mock.patch.object(settings.integrations, "resolve", return_value=self.available()), \
                mock.patch.object(settings.drafts, "checkpoint_config",
                                  return_value={"revision": "b" * 40, "replayed": False}) as checkpoint:
            result = settings.save(ROOT, "test", "a" * 40, "save-3",
                                   {"identity.name": "After"})
        self.assertTrue(result["saved"])
        written = checkpoint.call_args.args[4]
        self.assertEqual(written["identity"]["name"], "After")
        self.assertEqual(written["future_key"], {"kept": True})


class DraftConfigurationOperationTests(unittest.TestCase):
    def test_read_config_returns_structured_data_under_the_writer_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"identity":{"name":"Operator"}}\n', encoding="utf-8")
            state = {"schema_version": 1, "revision": "a" * 40}
            with mock.patch.object(settings.drafts, "find", return_value=(Path(temporary), state)), \
                    mock.patch.object(settings.drafts, "_locked", return_value=nullcontext()), \
                    mock.patch.object(settings.drafts, "_read_state", return_value=state), \
                    mock.patch.object(settings.drafts, "_recover", return_value=state), \
                    mock.patch.object(settings.drafts, "_paths", return_value={"config": path}), \
                    mock.patch.object(settings.drafts, "describe", return_value={"revision": "a" * 40}):
                result = settings.drafts.read_config(ROOT, "test")
        self.assertEqual(result["config"]["identity"]["name"], "Operator")
        self.assertEqual(result["draft"]["revision"], "a" * 40)

    def test_checkpoint_config_serializes_one_canonical_draft_transaction(self):
        with mock.patch.object(settings.drafts, "checkpoint",
                               return_value={"revision": "b" * 40}) as checkpoint:
            result = settings.drafts.checkpoint_config(
                ROOT, "test", "a" * 40, "save-1", {"future": {"kept": True}},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
        self.assertEqual(result["revision"], "b" * 40)
        content = checkpoint.call_args.kwargs["config"]
        self.assertEqual(json.loads(content), {"future": {"kept": True}})
        self.assertTrue(content.endswith(b"\n"))

    def test_structured_config_operations_refuse_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"invalid":NaN}\n', encoding="utf-8")
            state = {"schema_version": 1, "revision": "a" * 40}
            with mock.patch.object(settings.drafts, "find", return_value=(Path(temporary), state)), \
                    mock.patch.object(settings.drafts, "_locked", return_value=nullcontext()), \
                    mock.patch.object(settings.drafts, "_read_state", return_value=state), \
                    mock.patch.object(settings.drafts, "_recover", return_value=state), \
                    mock.patch.object(settings.drafts, "_paths", return_value={"config": path}):
                with self.assertRaisesRegex(settings.drafts.DraftError, "unavailable"):
                    settings.drafts.read_config(ROOT, "test")
        with self.assertRaisesRegex(settings.drafts.DraftError, "finite JSON"):
            settings.drafts.checkpoint_config(
                ROOT, "test", "a" * 40, "save-nan", {"invalid": float("nan")},
            )


class MutationExecutorTests(unittest.TestCase):
    def test_recovery_capable_read_and_save_routes_share_serial_execution(self):
        executor = MutationExecutor()
        entered = threading.Event()
        release = threading.Event()
        order = []
        read_payload = {"status": "ready", "message": "ready", "draft": {},
                        "values": {}, "warnings": []}
        save_payload = {"valid": True, "errors": [], "warnings": [], "changed": [],
                        "preview": {}, "base_revision": "a" * 40,
                        "saved": True, "result": {}}

        def read(_root, _name):
            order.append("recovery-read-start")
            entered.set()
            release.wait(1)
            order.append("recovery-read-end")
            return read_payload

        def saved(*_args):
            order.append("save")
            return save_payload

        read_handler = mock.Mock()
        read_handler.server.repo_root = ROOT
        read_handler.server.mutations = executor
        read_handler.request_json = {"draft": "test"}
        save_handler = mock.Mock()
        save_handler.server.repo_root = ROOT
        save_handler.server.mutations = executor
        save_handler.request_json = {"draft": "test", "base_revision": "a" * 40,
                                     "idempotency_key": "save-1", "changes": {}}
        with mock.patch.object(settings, "read", side_effect=read), \
                mock.patch.object(settings, "save", side_effect=saved):
            first = threading.Thread(target=server._configure_read, args=(
                read_handler, server.ROUTES.resolve("POST", "/api/configure/read"),
            ))
            second = threading.Thread(target=server._configure_save, args=(
                save_handler, server.ROUTES.resolve("POST", "/api/configure/save"),
            ))
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertEqual(order, ["recovery-read-start"])
            release.set()
            first.join(1)
            second.join(1)
        self.assertEqual(order, ["recovery-read-start", "recovery-read-end", "save"])
        executor.close()

    def test_mutations_are_serialized_and_errors_return_to_the_caller(self):
        executor = MutationExecutor()
        entered = threading.Event()
        release = threading.Event()
        order = []

        def first():
            order.append("first-start")
            entered.set()
            release.wait(1)
            order.append("first-end")

        first_thread = threading.Thread(target=lambda: executor.call(first))
        first_thread.start()
        self.assertTrue(entered.wait(1))
        second_started = threading.Event()

        def second():
            second_started.set()
            executor.call(lambda: order.append("second"))

        second_thread = threading.Thread(target=second)
        second_thread.start()
        self.assertTrue(second_started.wait(1))
        self.assertEqual(order, ["first-start"])
        release.set()
        first_thread.join(1)
        second_thread.join(1)
        self.assertEqual(order, ["first-start", "first-end", "second"])
        with self.assertRaisesRegex(ValueError, "injected"):
            executor.call(lambda: (_ for _ in ()).throw(ValueError("injected")))
        executor.close()

    def test_closed_and_recursive_execution_are_refused(self):
        executor = MutationExecutor()
        with self.assertRaisesRegex(RuntimeError, "recursively"):
            executor.call(lambda: executor.call(lambda: None))
        executor.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            executor.call(lambda: None)


if __name__ == "__main__":
    unittest.main()
