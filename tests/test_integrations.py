"""Implementation selection is independent of runtime and never silently falls back."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from isolation import without_config_dir
from unittest.mock import Mock, patch
from test_harness import harness, REPO
from harness_core import integrations as api


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="viewer fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.script = self.root / "adapter fixture.py"
        self.script.write_text('''import json, sys
r = json.load(sys.stdin)
payload = r["input"]
result = {"echo": payload, "arguments": sys.argv[1:]}
if r["operation"] == "open":
    result["session"] = {"id": "fixture", "epoch": "fixture-epoch"}
result["capabilities"] = ["describe", "validate", "open", "replace-document", "status", "close"]
result.update(revision=1, document_id="fixture", epoch="fixture-epoch", sha256="0"*64)
print(json.dumps({"contract_version": 1, "request_id": r["request_id"],
                 "operation": r["operation"], "implementation": r["implementation"],
                 "status": "ok", "result": result}))
''')
        self.desc = {"capability": api.CAPABILITY, "contract_version": 1,
                     "argv": [sys.executable, str(self.script)], "settings": {"literal": "$(never-run)"}}
        self.cfg = {"integrations": {api.CAPABILITY: {"implementation": "custom", "adapter": "fixture"}},
                    "integration_adapters": {"fixture": self.desc}}

    def binding(self):
        return api.resolve(self.root, self.cfg)

    def test_default_is_builtin_unavailable_without_execution(self):
        with patch.object(api.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            result = api.resolve(self.root, {}, source="distribution")
        self.assertEqual(result["implementation"], "builtin")
        self.assertEqual(result["availability"], "unavailable")
        self.assertEqual(result["selection_source"], "distribution")
        with self.assertRaisesRegex(api.IntegrationError, "no installed adapter"):
            api.viewer(self.root, {}, self.root / "sessions", "open")

    def test_malformed_status_is_a_structured_indeterminate_result(self):
        self.script.write_text(self.script.read_text().replace('"status": "ok"', '"status": []'))
        with self.assertRaises(api.IntegrationError) as error:
            api.invoke(self.binding(), "open")
        self.assertEqual(error.exception.code, "invalid-response")
        self.assertTrue(error.exception.indeterminate)

    def test_required_capability_must_exist_in_actual_opened_session(self):
        self.script.write_text(self.script.read_text().replace('result.update(revision=1',
            'if r["operation"] == "describe": result["capabilities"].append("regeneration-request")\nresult.update(revision=1'))
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "open",
                       {"required_capabilities": ["regeneration-request"]})
        self.assertEqual(error.exception.code, "unsupported-capability")
        record = json.loads(next((self.root / "sessions").glob("*.json")).read_text())
        self.assertTrue(record["session"])
        self.assertEqual(api.viewer(self.root, self.cfg, self.root / "sessions", "close",
                                   reference=record["reference"])["status"], "ok")

    def test_actual_builtin_descriptor_uses_same_contract(self):
        directory = self.root / "integrations" / api.CAPABILITY
        directory.mkdir(parents=True)
        (directory / "builtin.json").write_text(json.dumps(dict(self.desc, id="default-fixture")))
        result = api.viewer(self.root, {}, self.root / "sessions", "open")
        self.assertEqual(result["implementation"], "default-fixture")
        self.assertEqual(result["status"], "ok")

    def test_explicit_custom_never_falls_back(self):
        for cfg in ({"integrations": {api.CAPABILITY: {"implementation": "custom"}}},
                    {"integrations": {api.CAPABILITY: {"implementation": "custom", "adapter": "unknown"}}}):
            with self.assertRaises(api.IntegrationError):
                api.resolve(self.root, cfg)
        self.desc["argv"][0] = str(self.root / "missing")
        self.assertEqual(self.binding()["availability"], "unavailable")
        with self.assertRaises(api.IntegrationError):
            api.invoke(self.binding(), "describe")

    def test_descriptor_rejects_invalid_versions_commands_and_settings(self):
        for change in ({"contract_version": True}, {"contract_version": 2}, {"argv": "echo bad"},
                       {"argv": ["relative"]}, {"argv": [sys.executable, "\0"]},
                       {"settings": []}, {"capabilities": "open"}, {"capability": "../escape"},
                       {"unknown": True}, {"settings": {"number": float("nan")}}):
            with self.subTest(change=change), self.assertRaises(api.IntegrationError):
                api.descriptor(dict(self.desc, **change))
        with self.assertRaises(api.IntegrationError):
            api.resolve(self.root, {"integrations": {api.CAPABILITY: {"implementation": "other"}}})
        self.desc["capability"] = "different-capability"
        with self.assertRaises(api.IntegrationError):
            self.binding()

    def test_registration_preserves_unmanaged_settings_and_selection(self):
        original = {"unmanaged": {"keep": 3}, "integrations": {api.CAPABILITY: {"implementation": "builtin"}}}
        self.config_path.write_text(json.dumps(original))
        with patch.object(api.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            api.register(self.config_path, dict(self.desc, id="fixture"))
        current = json.loads(self.config_path.read_text())
        self.assertEqual(current["unmanaged"], original["unmanaged"])
        self.assertEqual(current["integrations"], original["integrations"])
        self.assertEqual(current["integration_adapters"]["fixture"], self.desc)
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)

    def test_nested_config_set_is_atomic_and_unknown_custom_is_rejected(self):
        api.register(self.config_path, dict(self.desc, id="fixture"))
        with patch.object(harness, "config_path", return_value=self.config_path):
            harness.config_set("integrations.architecture-viewer.adapter", "fixture")
            harness.config_set("integrations.architecture-viewer.implementation", "custom")
            self.assertEqual(harness.load_config(env={})["integrations"][api.CAPABILITY]["implementation"], "custom")
            before = self.config_path.read_bytes()
            with self.assertRaises(SystemExit):
                harness.config_set("integrations.architecture-viewer.adapter", "missing")
            self.assertEqual(before, self.config_path.read_bytes())
            harness.config_set("integrations.architecture-viewer.implementation", "builtin")
            harness.config_set("identity.name", "Fixture User")
        value = json.loads(self.config_path.read_text())
        self.assertEqual(value["integrations"][api.CAPABILITY]["adapter"], "fixture")
        self.assertNotIn("architecture-viewer.implementation", value["integrations"])
        self.assertEqual(value["identity"]["name"], "Fixture User")

    def test_slot_merge_preserves_distribution_defaults_and_project_is_stance_only(self):
        merged = api.merge_slots({api.CAPABILITY: {"implementation": "builtin"}}, {api.CAPABILITY: {"adapter": "fixture"}})
        self.assertEqual(merged[api.CAPABILITY], {"implementation": "builtin", "adapter": "fixture"})
        project = self.root / "project.json"
        project.write_text(json.dumps(self.cfg))
        with patch.object(harness, "config_path", return_value=self.config_path):
            with self.assertRaisesRegex(SystemExit, "selection keys only"):
                harness.load_config({"HARNESS_PROJECT_CONFIG": str(project)})

    def test_argv_with_spaces_and_shell_metacharacters_is_literal(self):
        sentinel = self.root / "should-not-exist"
        self.desc["argv"].append("$(touch " + str(sentinel) + ")")
        response = api.invoke(self.binding(), "validate", {"text": "`exit 1`"})
        self.assertEqual(response["result"]["arguments"], self.desc["argv"][2:])
        self.assertFalse(sentinel.exists())

    def test_session_is_pinned_after_selection_and_registration_change(self):
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        ref = opened["session_reference"]
        self.cfg["integrations"][api.CAPABILITY] = {"implementation": "builtin"}
        self.cfg["integration_adapters"]["fixture"] = {"argv": ["missing"]}
        response = api.viewer(self.root, self.cfg, self.root / "sessions", "status", reference=ref)
        self.assertEqual(response["implementation"], "fixture")
        self.assertEqual(response["result"]["echo"]["session"]["id"], "fixture")
        with self.assertRaises(api.IntegrationError):
            api.viewer(self.root, self.cfg, self.root / "sessions", "close", reference=ref, implementation="custom")
        self.assertEqual(api.viewer(self.root, self.cfg, self.root / "sessions", "close", reference=ref)["status"], "ok")

    def test_invocation_override_does_not_write_config(self):
        self.cfg["integrations"][api.CAPABILITY]["implementation"] = "builtin"
        before = json.dumps(self.cfg)
        response = api.viewer(self.root, self.cfg, self.root / "sessions", "open", implementation="custom", adapter="fixture")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(json.dumps(self.cfg), before)

    def test_response_identity_and_false_success_are_rejected(self):
        for code in ("print('{}')", "print('not json')", "raise SystemExit(3)"):
            self.script.write_text(code)
            with self.assertRaises(api.IntegrationError):
                api.invoke(self.binding(), "describe")
        self.script.write_text('import json,sys\nr=json.load(sys.stdin)\nr.update(status="ok",result={})\nprint(json.dumps(r))\nsys.exit(2)')
        with self.assertRaises(api.IntegrationError):
            api.invoke(self.binding(), "open")

    def test_timeout_is_indeterminate_for_mutation_and_never_retried(self):
        operations = []
        children = []
        timed_out = "describe"

        def launch(argv, stdin, stdout, **kwargs):
            request = json.load(stdin)
            operations.append(request["operation"])
            child = Mock(returncode=0)
            children.append(child)
            if request["operation"] != timed_out:
                request.update(status="ok", result={"capabilities": ["open"]})
                stdout.write(json.dumps(request).encode())
                stdout.flush()
                child.poll.return_value = 0
            else:
                child.poll.return_value = None
            return child

        with patch.object(api.subprocess, "Popen", side_effect=launch), \
                patch.object(api.time, "monotonic", side_effect=[0, 1]):
            with self.assertRaises(api.IntegrationError) as error:
                api.viewer(self.root, self.cfg, self.root / "sessions", "open", timeout=0.2)
        self.assertFalse(error.exception.indeterminate)
        self.assertEqual(operations, ["describe"])
        children[-1].kill.assert_called_once_with()
        children[-1].wait.assert_called_once_with()

        operations.clear()
        children.clear()
        timed_out = "open"
        with patch.object(api.subprocess, "Popen", side_effect=launch), \
                patch.object(api.time, "monotonic", side_effect=[0, 0, 1]):
            with self.assertRaises(api.IntegrationError) as error:
                api.viewer(self.root, self.cfg, self.root / "sessions", "open", timeout=0.2)
        self.assertTrue(error.exception.indeterminate)
        self.assertEqual(operations, ["describe", "open"])
        children[0].kill.assert_not_called()
        children[0].wait.assert_not_called()
        children[-1].kill.assert_called_once_with()
        children[-1].wait.assert_called_once_with()
        records = list((self.root / "sessions").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["open_error"]["code"], "adapter-timeout")

    def test_output_and_input_are_bounded(self):
        self.script.write_text("print('x' * (1024 * 1024 + 1))\n")
        with self.assertRaises(api.IntegrationError) as error:
            api.invoke(self.binding(), "describe")
        self.assertEqual(error.exception.code, "resource-limit")
        with self.assertRaises(api.IntegrationError):
            api.invoke(self.binding(), "validate", {"big": "x" * api.LIMIT})

    def test_large_responses_keep_a_minimal_usable_session_record(self):
        self.script.write_text(self.script.read_text().replace('result.update(revision=1',
                                                               'result["bulky"] = "x" * 600000\nresult.update(revision=1'))
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        path = self.root / "sessions" / (opened["session_reference"] + ".json")
        self.assertLess(path.stat().st_size, 10000)
        self.assertEqual(json.loads(path.read_text())["session"]["id"], "fixture")
        self.assertEqual(api.viewer(self.root, self.cfg, path.parent, "close",
                                   reference=opened["session_reference"])["status"], "ok")

    def test_large_pinned_binding_and_session_fit_bounded_storage(self):
        self.desc["name"] = "x" * 600000
        self.script.write_text(self.script.read_text().replace('result["session"] = {"id": "fixture", "epoch": "fixture-epoch"}',
            'result["session"] = {"id": "fixture", "opaque": "x" * 600000}').replace('result = {"echo": payload, "arguments": sys.argv[1:]}', 'result = {}'))
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        path = self.root / "sessions" / (opened["session_reference"] + ".json")
        self.assertGreater(path.stat().st_size, api.LIMIT)
        self.assertLess(path.stat().st_size, api.SESSION_LIMIT)
        self.assertEqual(api.viewer(self.root, self.cfg, path.parent, "close",
                                   reference=opened["session_reference"])["status"], "ok")

    def test_mutation_timeout_keeps_generated_request_id_for_retry(self):
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        self.script.write_text(self.script.read_text().replace('payload = r["input"]',
            'payload = r["input"]\nif r["operation"] == "close":\n import time\n time.sleep(20)'))
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "close",
                       reference=opened["session_reference"], timeout=0.1)
        request_id = error.exception.request_id
        self.assertEqual(str(uuid.UUID(request_id)), request_id)
        record = api.read_json(self.root / "sessions" / (opened["session_reference"] + ".json"))
        self.assertEqual(record["last_attempt"]["request_id"], request_id)
        self.assertEqual(record["last_observation"]["request_id"], request_id)
        self.assertEqual(record["last_observation"]["status"], "indeterminate")

    def test_cli_indeterminate_result_exposes_request_identity(self):
        import contextlib
        import io
        error = api.IntegrationError("adapter-timeout", "outcome unknown", True)
        error.request_id = str(uuid.uuid4())
        args = harness.argparse.Namespace(input=None, action="close", session=str(uuid.uuid4()),
                    implementation=None, adapter=None, request_id=None, timeout=1)
        output = io.StringIO()
        with patch.object(harness, "load_config", return_value=self.cfg), \
                patch.object(api, "viewer", side_effect=error), contextlib.redirect_stdout(output):
            self.assertEqual(harness.cmd_viewer(args), 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value["status"], "indeterminate")
        self.assertEqual(value["request_id"], error.request_id)

    def test_observation_failure_preserves_successful_acknowledgement(self):
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        save = api.save_record
        for failure in (ValueError("lock busy"), OSError("disk unavailable")):
            calls = []
            def fail_observation(path, record):
                calls.append(record)
                if len(calls) == 2:
                    raise failure
                return save(path, record)
            with patch.object(api, "save_record", side_effect=fail_observation):
                result = api.viewer(self.root, self.cfg, self.root / "sessions", "close",
                                    reference=opened["session_reference"])
            self.assertEqual(result["status"], "ok")
            self.assertIn(str(failure), result["persistence_warning"])

    def test_open_persistence_failure_returns_known_session_acknowledgement(self):
        save = api.save_record
        calls = []
        def fail_ack(path, record):
            calls.append(record)
            if len(calls) == 2:
                raise OSError("disk unavailable")
            return save(path, record)
        with patch.object(api, "save_record", side_effect=fail_ack):
            result = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["reference_persisted"])
        self.assertEqual(result["result"]["session"]["id"], "fixture")

    def test_session_traversal_symlink_and_missing_revision_rejected(self):
        with self.assertRaises(api.IntegrationError):
            api.session_path(self.root, "../escape")
        ref = str(uuid.uuid4())
        (self.root / (ref + ".json")).symlink_to(self.script)
        with self.assertRaises(api.IntegrationError):
            api.session_path(self.root, ref)
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        with self.assertRaises(api.IntegrationError):
            api.viewer(self.root, self.cfg, self.root / "sessions", "replace-document", reference=opened["session_reference"])

    def test_cli_inspection_is_json_and_cannot_launch(self):
        env = dict(without_config_dir(), HARNESS_HOME=str(self.root))
        completed = subprocess.run([sys.executable, str(REPO / "bin/harness"), "integrations", "show", "--json"],
                                   env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["implementation"], "builtin")


    def test_corrupt_record_and_falsey_nonobjects_are_structured_errors(self):
        for payload in ([], False, "", 0):
            with self.assertRaises(api.IntegrationError):
                api.viewer(self.root, self.cfg, self.root / "sessions", "validate", payload=payload)
        ref = str(uuid.uuid4())
        path = api.session_path(self.root / "sessions", ref)
        path.write_text(json.dumps({"schema_version": 1, "reference": ref}))
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "status", reference=ref)
        self.assertEqual(error.exception.code, "invalid-session")

    def test_pinned_record_cannot_change_capability(self):
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        path = self.root / "sessions" / (opened["session_reference"] + ".json")
        record = json.loads(path.read_text())
        record["binding"]["descriptor"]["capability"] = "different-capability"
        path.write_text(json.dumps(record))
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "status",
                       reference=opened["session_reference"])
        self.assertEqual(error.exception.code, "invalid-session")

    def test_missing_required_capability_does_not_open(self):
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "open",
                       {"required_capabilities": ["review-questions"]})
        self.assertEqual(error.exception.code, "unsupported-capability")
        self.assertFalse((self.root / "sessions").exists())

    def test_builtin_switch_survives_a_removed_custom_adapter(self):
        self.config_path.write_text(json.dumps({"integrations": {api.CAPABILITY: {"implementation": "custom", "adapter": "removed"}}}))
        with patch.object(harness, "config_path", return_value=self.config_path):
            harness.config_set("integrations.architecture-viewer.implementation", "builtin")
        self.assertEqual(json.loads(self.config_path.read_text())["integrations"][api.CAPABILITY]["adapter"], "removed")

    def test_invocation_cannot_silently_ignore_adapter(self):
        with self.assertRaises(api.IntegrationError):
            api.resolve(self.root, self.cfg, implementation="builtin", adapter="fixture")

    def test_incomplete_open_acknowledgement_is_indeterminate(self):
        self.script.write_text('import json,sys\nr=json.load(sys.stdin)\nr.update(status="ok",result={"capabilities":["open"],"session":{"id":"s"}})\nprint(json.dumps(r))')
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        self.assertTrue(error.exception.indeterminate)

    def test_config_lock_blocks_concurrent_registration_without_lost_updates(self):
        from harness_core import reconcile
        self.config_path.write_text(json.dumps({"preserve": 1}))
        with reconcile.lock(self.root):
            with self.assertRaises(ValueError):
                api.register(self.config_path, dict(self.desc, id="fixture"))
        self.assertEqual(json.loads(self.config_path.read_text()), {"preserve": 1})

    def test_session_capabilities_override_static_description(self):
        opened = api.viewer(self.root, self.cfg, self.root / "sessions", "open")
        with self.assertRaises(api.IntegrationError) as error:
            api.viewer(self.root, self.cfg, self.root / "sessions", "request-regeneration",
                       reference=opened["session_reference"])
        self.assertEqual(error.exception.code, "unsupported-capability")
