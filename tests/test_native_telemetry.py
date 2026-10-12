# SPDX-License-Identifier: MIT
"""Native telemetry pass-through: what `sync` writes, what it refuses to take over, and off.

`telemetry.native` asks each runtime to export its own telemetry to the endpoint the ledger
exporter already uses. The promises held here are the ones a user's settings file depends on:
a variable they set themselves is never touched, a managed key that already holds something the
harness did not write is reported rather than overwritten, turning the key off puts back exactly
what was there before, and no header value reaches a settings file, a config file or a printed
line. The Codex table is written knowing its metrics default drops token and cost metrics.

Every exercise runs under a temporary HOME. Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from isolation import isolate_home, without_config_dir

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

from harness_core import reconcile  # noqa: E402

HELPER = REPO / "policy" / "hooks" / "otel-headers.py"
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())
ENV_KEYS = OWNERSHIP["claude"]["native_telemetry"]["env_keys"]
# A placeholder header value, not a credential: the tests only assert where it does and does not
# appear. Named plainly so a static analyser does not read the fixture write as storing a secret.
HEADER_VALUE = "Bearer " + "placeholder-header-value"


def settings(**overrides):
    """A validated `telemetry` block, the way `telemetry_settings` hands one to `sync`."""
    block = {"export": "otlp", "endpoint": "http://collector.invalid:4318", "headers_env": "",
             "headers_file": "", "labels": {}, "native": True}
    block.update(overrides)
    return block


@contextlib.contextmanager
def loud():
    prior = os.environ.pop("HARNESS_QUIET", None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        if prior is not None:
            os.environ["HARNESS_QUIET"] = prior


class LabelTests(unittest.TestCase):
    def test_version_stances_and_configured_labels_are_stamped(self):
        cfg = {"stances": {"cost": "frugal", "testing": "required"}}
        attributes, omitted = harness.resource_attributes(
            cfg, settings(labels={"deployment.environment": "laptop"}))
        self.assertEqual(omitted, [])
        self.assertIn("harness.version=" + harness.VERSION, attributes)
        self.assertIn("harness.cost=frugal", attributes)
        self.assertIn("harness.testing=required", attributes)
        self.assertIn("deployment.environment=laptop", attributes)

    def test_a_label_the_variable_cannot_carry_is_left_out_and_named(self):
        cfg = {"stances": {"cost": "frugal"}}
        attributes, omitted = harness.resource_attributes(
            cfg, settings(labels={"host": "my laptop", "team": "a,b", "ok": "one-two"}))
        self.assertEqual(omitted, ["host", "team"])
        self.assertNotIn("my laptop", attributes)
        self.assertNotIn("a,b", attributes)
        self.assertIn("ok=one-two", attributes)

    def test_no_helper_key_without_a_header_source(self):
        values, _ = harness.claude_native_values(settings(), {"stances": {}})
        self.assertNotIn(("otelHeadersHelper",), values)
        self.assertEqual(values[("env", "OTEL_EXPORTER_OTLP_PROTOCOL")], "http/protobuf")
        self.assertEqual(sorted(k[1] for k in values), sorted(ENV_KEYS))
        with_file = harness.claude_native_values(settings(headers_file="~/h"), {"stances": {}})[0]
        self.assertTrue(with_file[("otelHeadersHelper",)].endswith("hooks/harness/otel-headers.py"))

    def test_off_asks_for_nothing(self):
        self.assertEqual(harness.claude_native_values(settings(native=False), {"stances": {}}),
                         ({}, []))


class ClaudeOwnershipTests(unittest.TestCase):
    """`apply_native_claude` decides, per variable, whether the harness may write it."""

    def cfg(self):
        return {"stances": {"cost": "frugal"}}

    def test_a_user_variable_is_untouched_and_ours_are_added(self):
        live = {"env": {"MY_VAR": "keep", "PATH": "/x"}}
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(
            merged, live, {}, settings(), self.cfg(), OWNERSHIP)
        self.assertEqual(notices, [])
        self.assertEqual(merged["env"]["MY_VAR"], "keep")
        self.assertEqual(merged["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertNotIn(["env", "MY_VAR"], paths)
        self.assertIn(["env", "OTEL_LOGS_EXPORTER"], paths)

    def test_a_managed_key_the_harness_never_wrote_is_reported_not_overwritten(self):
        live = {"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://mine.invalid:4318"}}
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(
            merged, live, {}, settings(), self.cfg(), OWNERSHIP)
        self.assertEqual(merged["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://mine.invalid:4318")
        self.assertNotIn(["env", "OTEL_EXPORTER_OTLP_ENDPOINT"], paths)
        self.assertEqual(len(notices), 1)
        self.assertIn("already holds a value the harness did not write", notices[0])

    def test_a_key_already_equal_to_ours_is_taken_over_silently(self):
        live = {"env": {"OTEL_LOGS_EXPORTER": "otlp"}}
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(
            merged, live, {}, settings(), self.cfg(), OWNERSHIP)
        self.assertEqual(notices, [])
        self.assertIn(["env", "OTEL_LOGS_EXPORTER"], paths)

    def test_off_restores_what_each_key_held_before_and_touches_nothing_else(self):
        held = {json.dumps(["env", "OTEL_LOGS_EXPORTER"]):
                {"prior": {"present": True, "value": "console"},
                 "applied": {"present": True, "value": "otlp"}},
                json.dumps(["env", "OTEL_METRICS_EXPORTER"]):
                {"prior": {"present": False, "value": None},
                 "applied": {"present": True, "value": "otlp"}}}
        live = {"env": {"OTEL_LOGS_EXPORTER": "otlp", "OTEL_METRICS_EXPORTER": "otlp",
                        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://mine.invalid:4318"}}
        merged = json.loads(json.dumps(live))
        paths, notices = harness.apply_native_claude(
            merged, live, held, settings(native=False), self.cfg(), OWNERSHIP)
        self.assertEqual(notices, [])
        self.assertEqual(merged["env"]["OTEL_LOGS_EXPORTER"], "console")
        self.assertNotIn("OTEL_METRICS_EXPORTER", merged["env"])
        # Never written by the harness, so not its to remove.
        self.assertEqual(merged["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://mine.invalid:4318")
        self.assertEqual(sorted(paths), [["env", "OTEL_LOGS_EXPORTER"], ["env", "OTEL_METRICS_EXPORTER"]])

    def test_a_finished_removal_is_not_repeated(self):
        held = {json.dumps(["env", "OTEL_LOGS_EXPORTER"]):
                {"prior": {"present": False, "value": None},
                 "applied": {"present": False, "value": None}}}
        merged = {}
        paths, notices = harness.apply_native_claude(
            merged, {}, held, settings(native=False), self.cfg(), OWNERSHIP)
        self.assertEqual((paths, notices, merged), ([], [], {}))


class CodexTableTests(unittest.TestCase):
    def test_both_exporters_are_explicit_and_signal_specific(self):
        table, notices = harness.codex_native_otel(settings(), None, False)
        self.assertEqual(notices, [])
        self.assertEqual(table["exporter"]["otlp-http"]["endpoint"],
                         "http://collector.invalid:4318/v1/logs")
        # The metrics exporter defaults to a first-party sink that drops token and cost metrics
        # client-side, so leaving it unset would send no token metrics anywhere.
        self.assertEqual(table["metrics_exporter"]["otlp-http"]["endpoint"],
                         "http://collector.invalid:4318/v1/metrics")
        self.assertEqual(table["metrics_exporter"]["otlp-http"]["protocol"], "binary")

    def test_no_header_is_ever_written(self):
        table, _ = harness.codex_native_otel(
            settings(headers_env="H", headers_file="/tmp/h"), None, False)
        self.assertNotIn("headers", table["exporter"]["otlp-http"])
        self.assertNotIn("headers", table["metrics_exporter"]["otlp-http"])

    def test_other_keys_in_the_table_are_carried_through(self):
        table, _ = harness.codex_native_otel(
            settings(), {"environment": "prod", "log_user_prompt": False}, False)
        self.assertEqual(table["environment"], "prod")
        self.assertIs(table["log_user_prompt"], False)

    def test_an_exporter_the_harness_never_wrote_is_reported_not_replaced(self):
        live = {"exporter": {"otlp-http": {"endpoint": "http://mine.invalid/v1/logs",
                                           "protocol": "json"}}}
        table, notices = harness.codex_native_otel(settings(), live, False)
        self.assertIsNone(table)
        self.assertEqual(len(notices), 1)
        self.assertIn("exporter", notices[0])

    def test_off_asks_for_the_table_to_come_back_out(self):
        self.assertEqual(harness.codex_native_otel(settings(native=False), {"a": 1}, True),
                         (None, []))

    def test_an_unmanaged_call_never_lists_the_key(self):
        self.assertNotIn("otel", harness.codex_wanted({"stances": {}, "permissions": "inherit"}))


class HeadersHelperTests(unittest.TestCase):
    """The helper prints headers and, on any failure, prints nothing at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".config" / "agent-harness").mkdir(parents=True)
        self.headers = self.home / "otlp-headers"
        self.headers.write_text("authorization=" + HEADER_VALUE + "\n")
        self.headers.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, **block):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.write_text(json.dumps({"telemetry": block}))

    def run_helper(self):
        env = dict(without_config_dir(), HOME=str(self.home), HARNESS_HOME=str(self.home))
        return subprocess.run([sys.executable, str(HELPER)], capture_output=True, text=True, env=env)

    def test_it_prints_one_json_object_of_headers(self):
        self.configure(export="otlp", native=True, headers_file=str(self.headers))
        done = self.run_helper()
        self.assertEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout), {"authorization": HEADER_VALUE})
        self.assertEqual(done.stderr, "")

    def test_an_unreadable_source_prints_nothing_and_fails(self):
        self.configure(export="otlp", native=True, headers_file=str(self.home / "missing"))
        done = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, "")

    def test_a_world_readable_file_is_refused_without_echoing_it(self):
        self.headers.chmod(0o644)
        self.configure(export="otlp", native=True, headers_file=str(self.headers))
        done = self.run_helper()
        self.assertEqual(done.returncode, 1)
        self.assertNotIn(HEADER_VALUE, done.stdout + done.stderr)

    def test_it_prints_nothing_when_native_is_off(self):
        self.configure(export="otlp", native=False, headers_file=str(self.headers))
        done = self.run_helper()
        self.assertEqual((done.returncode, done.stdout), (1, ""))

    def test_the_file_is_executable_so_the_runtime_can_run_it_by_path(self):
        self.assertTrue(os.access(str(HELPER), os.X_OK))
        self.assertTrue(HELPER.read_text().startswith("#!"))


class SyncTests(unittest.TestCase):
    """The whole round trip under a temporary HOME: on, refreshed, and back off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._environ = dict(os.environ)
        isolate_home(self.home)
        self.settings_file = self.home / ".claude" / "settings.json"
        self.config_toml = self.home / ".codex" / "config.toml"
        self.headers = self.home / "otlp-headers"
        self.headers.write_text("authorization=" + HEADER_VALUE + "\n")
        self.headers.chmod(0o600)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environ)
        self.tmp.cleanup()

    def configure(self, telemetry, **overrides):
        config = json.loads((REPO / "config.example.json").read_text())
        config["telemetry"] = telemetry
        config.update(overrides)
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def sync(self, dry=False):
        return harness.cmd_sync(harness.argparse.Namespace(
            dry_run=dry, adopt=False, adopt_codex=False, print_only=False))

    def on(self, **extra):
        block = {"export": "otlp", "endpoint": "http://collector.invalid:4318", "native": True}
        block.update(extra)
        self.configure(block)

    def env(self):
        # The cost stance's reset pair has its own owner and tests (test_context_reset_projection).
        found = json.loads(self.settings_file.read_text()).get("env") or {}
        return {k: v for k, v in found.items() if k not in harness.CONTEXT_RESET_ENV}

    def otel(self):
        return harness.codex_otel_in_config(self.config_toml.read_text()) or {}

    def test_on_writes_both_runtimes_and_off_removes_exactly_that(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"model": "mine", "env": {"MY_VAR": "keep"}}))
        self.on(headers_file=str(self.headers))
        self.assertEqual(self.sync(), 0)
        before = json.loads(self.settings_file.read_text())
        self.assertEqual(set(self.env()) - {"MY_VAR"}, set(ENV_KEYS))
        self.assertEqual(self.env()["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://collector.invalid:4318")
        self.assertIn("harness.version=" + harness.VERSION,
                      self.env()["OTEL_RESOURCE_ATTRIBUTES"])
        self.assertTrue(before["otelHeadersHelper"].endswith("otel-headers.py"))
        self.assertEqual(sorted(self.otel()), ["exporter", "metrics_exporter"])
        # No credential reaches either file, in any form.
        self.assertNotIn(HEADER_VALUE, self.settings_file.read_text())
        self.assertNotIn(HEADER_VALUE, self.config_toml.read_text())

        self.on(native=False, headers_file=str(self.headers))
        self.assertEqual(self.sync(), 0)
        after = json.loads(self.settings_file.read_text())
        self.assertEqual(self.env(), {"MY_VAR": "keep"})
        self.assertNotIn("otelHeadersHelper", after)
        self.assertEqual(after["model"], "mine")
        self.assertEqual(self.otel(), {})
        self.assertEqual({k: v for k, v in before.items() if k not in ("env", "otelHeadersHelper")},
                         {k: v for k, v in after.items() if k != "env"})

    def test_a_dry_run_shows_the_change_only_when_the_key_is_on(self):
        self.on()
        with loud() as out:
            self.sync(dry=True)
        self.assertIn("  native  claude-code exports its own telemetry", out.getvalue())
        self.assertFalse(self.settings_file.exists())
        self.on(native=False)
        with loud() as out:
            self.sync(dry=True)
        self.assertNotIn("  native ", out.getvalue())

    def test_labels_are_refreshed_on_every_sync(self):
        self.on()
        self.assertEqual(self.sync(), 0)
        self.assertIn("harness.cost=balanced", self.env()["OTEL_RESOURCE_ATTRIBUTES"])
        self.configure({"export": "otlp", "endpoint": "http://collector.invalid:4318",
                        "native": True}, stances={"cost": "frugal"})
        self.assertEqual(self.sync(), 0)
        self.assertIn("harness.cost=frugal", self.env()["OTEL_RESOURCE_ATTRIBUTES"])

    def test_a_user_value_under_a_managed_key_stops_the_write_and_is_reported(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(
            {"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://mine.invalid:4318"}}))
        self.on()
        with loud() as out:
            self.assertEqual(self.sync(), 2)
        self.assertIn("already holds a value the harness did not write", out.getvalue())
        self.assertEqual(self.env()["OTEL_EXPORTER_OTLP_ENDPOINT"], "http://mine.invalid:4318")
        self.assertEqual(self.env()["OTEL_LOGS_EXPORTER"], "otlp")

    def doctor(self):
        """Doctor's report without the installed clients: no test launches a real one, and
        `claude doctor` under a temporary HOME raises a macOS keychain dialog."""
        which = harness.shutil.which

        def hidden(name, *args, **kwargs):
            return None if name == "claude" else which(name, *args, **kwargs)

        with unittest.mock.patch.object(harness, "_version_of", return_value="stub"), \
                unittest.mock.patch.object(harness.shutil, "which", side_effect=hidden), \
                loud() as out:
            harness.cmd_doctor(harness.argparse.Namespace())
        return out.getvalue()

    def test_doctor_reports_the_state_the_endpoint_and_stale_labels(self):
        self.on()
        self.assertEqual(self.sync(), 0)
        report = self.doctor()
        self.assertIn("native telemetry: on -> http://collector.invalid:4318", report)
        self.assertIn("labels match this version and these stances", report)
        self.assertIn("user and organization identifiers", report)
        live = json.loads(self.settings_file.read_text())
        live["env"]["OTEL_RESOURCE_ATTRIBUTES"] = "harness.version=0.0.1"
        self.settings_file.write_text(json.dumps(live))
        self.assertIn("labels are from an earlier sync", self.doctor())

    def test_uninstall_puts_a_user_env_block_back_as_it_was(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": {"MY_VAR": "keep"}}))
        self.on()
        self.assertEqual(self.sync(), 0)
        store = reconcile.Store(harness.state_dir())
        store.uninstall()
        self.assertEqual(json.loads(self.settings_file.read_text()).get("env"), {"MY_VAR": "keep"})


if __name__ == "__main__":
    unittest.main()
