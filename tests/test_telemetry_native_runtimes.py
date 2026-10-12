# SPDX-License-Identifier: MIT
"""`telemetry.native` names the runtimes it applies to, and `sync` honours exactly those.

Codex takes OTLP header values in `config.toml` only as literals, which the harness will not
write, so a collector that authenticates rejects every native Codex session while Claude Code
exports correctly. The fix is a per-runtime key: `true` is still both runtimes, and a list names
the ones a given endpoint can actually serve. What is held here is that a named runtime is
configured, an unnamed one is left byte-for-byte alone, and an unknown name stops the command
rather than silently configuring nothing.

Every exercise runs under a temporary HOME. Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

telemetry_hook = harness.load_telemetry()

CODEX_KEYS = ["exporter", "metrics_exporter"]
OTEL_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"


def block(native, **overrides):
    out = {"export": "otlp", "endpoint": "http://collector.invalid:4318", "native": native}
    out.update(overrides)
    return out


class ValidationTests(unittest.TestCase):
    """The validator turns the key into a list of runtimes, or refuses it."""

    def native(self, value):
        return telemetry_hook.settings({"telemetry": block(value)})["native"]

    def test_true_is_every_runtime_and_false_is_none(self):
        self.assertEqual(self.native(True), ["claude-code", "codex"])
        self.assertEqual(self.native(False), [])

    def test_a_list_names_the_runtimes_in_a_stable_order(self):
        self.assertEqual(self.native(["claude-code"]), ["claude-code"])
        self.assertEqual(self.native(["codex"]), ["codex"])
        self.assertEqual(self.native(["codex", "claude-code"]), ["claude-code", "codex"])
        self.assertEqual(self.native([]), [])

    def test_an_unknown_runtime_name_is_refused_and_the_known_ones_are_named(self):
        with self.assertRaises(ValueError) as caught:
            self.native(["claude-code", "codex-cli"])
        message = str(caught.exception)
        self.assertIn("'codex-cli'", message)
        self.assertIn("claude-code, codex", message)

    def test_a_value_that_is_neither_a_flag_nor_a_list_is_refused(self):
        for value in ("claude-code", 1, {"claude-code": True}, ["claude-code", 2]):
            with self.assertRaises(ValueError) as caught:
                self.native(value)
            self.assertIn("true, false, or a list of runtime names", str(caught.exception))


class ResolutionTests(unittest.TestCase):
    """What each writer asks for, given the resolved key."""

    def test_the_cli_reads_a_flag_and_a_list_the_same_way(self):
        self.assertEqual(harness.native_runtimes(block(True)), ["claude-code", "codex"])
        self.assertEqual(harness.native_runtimes(block(False)), [])
        self.assertEqual(harness.native_runtimes(block(["codex"])), ["codex"])
        self.assertEqual(harness.native_runtimes({}), [])

    def test_the_cli_names_the_runtimes_the_validator_names_and_no_others(self):
        self.assertEqual(harness.native_runtimes(block(True)),
                         list(telemetry_hook.NATIVE_RUNTIMES))

    def test_a_runtime_added_to_the_validator_is_resolved_without_a_second_edit(self):
        """One list, not two: a name the config accepts is a name the CLI acts on."""
        extended = harness.load_telemetry()
        extended.NATIVE_RUNTIMES = tuple(telemetry_hook.NATIVE_RUNTIMES) + ("another-runtime",)
        with unittest.mock.patch.object(harness, "load_telemetry", return_value=extended):
            self.assertEqual(harness.native_runtimes(block(["another-runtime"])),
                             ["another-runtime"])

    def test_the_cli_refuses_every_shape_the_validator_refuses(self):
        for value in ("codex", {"claude-code": 1}, 3, ["codex", None]):
            with self.assertRaises(ValueError):
                harness.native_runtimes(block(value))
            with self.assertRaises(ValueError):
                telemetry_hook.settings({"telemetry": block(value)})

    def test_claude_values_are_asked_for_only_when_claude_code_is_named(self):
        cfg = {"stances": {}}
        self.assertEqual(harness.claude_native_values(block(["codex"]), cfg), ({}, []))
        values, _ = harness.claude_native_values(block(["claude-code"]), cfg)
        self.assertEqual(values[("env", OTEL_ENV)], "http://collector.invalid:4318")

    def test_the_codex_table_is_asked_for_only_when_codex_is_named(self):
        self.assertEqual(harness.codex_native_otel(block(["claude-code"]), None, False), (None, []))
        table, notices = harness.codex_native_otel(block(["codex"]), None, False)
        self.assertEqual((sorted(table), notices), (CODEX_KEYS, []))


class SyncTests(unittest.TestCase):
    """The round trip under a temporary HOME: each runtime written only when it is named."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._environ = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        for key in list(os.environ):
            if key.startswith("HARNESS_"):
                del os.environ[key]
        os.environ["HARNESS_QUIET"] = "1"
        self.settings_file = self.home / ".claude" / "settings.json"
        self.config_toml = self.home / ".codex" / "config.toml"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environ)
        self.tmp.cleanup()

    def configure(self, native, **overrides):
        config = json.loads((REPO / "config.example.json").read_text())
        config["telemetry"] = block(native)
        config.update(overrides)
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def doctor(self):
        """Doctor's report without the installed clients: no test launches a real one, and
        `claude doctor` under a temporary HOME raises a macOS keychain dialog."""
        which = harness.shutil.which

        def hidden(name, *args, **kwargs):
            return None if name == "claude" else which(name, *args, **kwargs)

        prior = os.environ.pop("HARNESS_QUIET", None)
        buf = io.StringIO()
        try:
            with unittest.mock.patch.object(harness, "_version_of", return_value="stub"), \
                    unittest.mock.patch.object(harness.shutil, "which", side_effect=hidden), \
                    contextlib.redirect_stdout(buf):
                harness.cmd_doctor(harness.argparse.Namespace())
        finally:
            if prior is not None:
                os.environ["HARNESS_QUIET"] = prior
        return buf.getvalue()

    def sync(self):
        return harness.cmd_sync(harness.argparse.Namespace(
            dry_run=False, adopt=False, adopt_codex=False, print_only=False))

    def env(self):
        live = json.loads(self.settings_file.read_text()) if self.settings_file.exists() else {}
        # The cost stance's reset pair has its own owner and tests (test_context_reset_projection).
        return {k: v for k, v in (live.get("env") or {}).items() if k not in harness.CONTEXT_RESET_ENV}

    def otel(self):
        if not self.config_toml.exists():
            return {}
        return harness.codex_otel_in_config(self.config_toml.read_text()) or {}

    def test_claude_code_alone_leaves_the_codex_otel_table_untouched(self):
        self.configure(["claude-code"])
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env()[OTEL_ENV], "http://collector.invalid:4318")
        self.assertEqual(self.otel(), {})
        self.assertNotIn("[otel]", self.config_toml.read_text())

    def test_a_users_own_otel_table_survives_a_claude_code_only_sync(self):
        self.config_toml.parent.mkdir(parents=True, exist_ok=True)
        self.config_toml.write_text('[otel]\nenvironment = "mine"\n')
        self.configure(["claude-code"])
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.otel(), {"environment": "mine"})

    def test_codex_alone_leaves_the_claude_settings_untouched(self):
        self.configure(["codex"])
        self.assertEqual(self.sync(), 0)
        self.assertEqual(sorted(self.otel()), CODEX_KEYS)
        self.assertEqual([k for k in self.env() if k.startswith("OTEL_") or k.startswith("CLAUDE_")],
                         [])
        self.assertNotIn("otelHeadersHelper", json.loads(self.settings_file.read_text()))

    def test_true_still_writes_both_runtimes(self):
        self.configure(True)
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.env()[OTEL_ENV], "http://collector.invalid:4318")
        self.assertEqual(sorted(self.otel()), CODEX_KEYS)

    def test_dropping_a_runtime_takes_back_only_that_runtimes_keys(self):
        self.configure(True)
        self.assertEqual(self.sync(), 0)
        self.configure(["claude-code"])
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.otel(), {})
        self.assertEqual(self.env()[OTEL_ENV], "http://collector.invalid:4318")

    def test_dropping_claude_code_takes_back_its_env_and_leaves_codex_exporting(self):
        """The mirror of the case above: a gate narrowed back to `native` being merely truthy
        would keep writing the Claude variables under `["codex"]` and go unnoticed."""
        self.configure(True)
        self.assertEqual(self.sync(), 0)
        self.configure(["codex"])
        self.assertEqual(self.sync(), 0)
        self.assertEqual([k for k in self.env() if k.startswith(("OTEL_", "CLAUDE_"))], [])
        self.assertNotIn("otelHeadersHelper", json.loads(self.settings_file.read_text()))
        self.assertEqual(sorted(self.otel()), CODEX_KEYS)

    def test_doctor_reports_each_named_runtime_from_its_own_live_file(self):
        self.configure(["claude-code"])
        self.assertEqual(self.sync(), 0)
        report = self.doctor()
        self.assertIn("native telemetry: on -> http://collector.invalid:4318 (claude-code)",
                      report)
        self.assertIn("labels match this version and these stances", report)
        self.assertIn("codex: not named by telemetry.native", report)

    def test_doctor_does_not_call_codex_configured_when_sync_left_its_table_alone(self):
        self.config_toml.parent.mkdir(parents=True, exist_ok=True)
        self.config_toml.write_text(
            '[otel.exporter.otlp-http]\nendpoint = "http://mine.invalid:4318/v1/logs"\n')
        self.configure(["codex"])
        self.assertEqual(self.sync(), 2)
        self.assertEqual(self.otel()["exporter"],
                         {"otlp-http": {"endpoint": "http://mine.invalid:4318/v1/logs"}})
        report = self.doctor()
        self.assertIn("codex: [otel] holds exporters this endpoint did not ask for", report)

    def test_doctor_says_a_named_runtime_whose_block_is_unmanaged_is_written_nothing(self):
        self.configure(["codex"], codex={"manage": False})
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.otel(), {})
        self.assertIn("codex.manage is off, so `sync` writes nothing for it", self.doctor())

    def test_doctor_says_codex_is_configured_once_sync_has_written_the_table(self):
        self.configure(["codex"])
        self.assertEqual(self.sync(), 0)
        report = self.doctor()
        self.assertIn("codex: [otel] exports logs and metrics to this endpoint", report)
        self.assertIn("claude-code: not named by telemetry.native", report)

    def test_an_unknown_runtime_name_stops_the_sync(self):
        self.configure(["claude"])
        with self.assertRaises(SystemExit) as caught:
            self.sync()
        self.assertIn("telemetry.native does not know the runtime 'claude'", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
