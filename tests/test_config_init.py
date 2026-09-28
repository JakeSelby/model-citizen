# SPDX-License-Identifier: MIT
"""Unit tests for `harness init`, `harness config set`, and placeholder identity reporting."""
import argparse
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from isolation import isolate_home

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

EXAMPLE = json.loads((REPO / "config.example.json").read_text())


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


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_home = os.environ.get("HOME")
        isolate_home(self.home)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()


class PlaceholderIdentityTests(unittest.TestCase):
    def test_the_example_config_is_all_placeholder(self):
        self.assertEqual(harness.placeholder_identity(EXAMPLE),
                         harness.PLACEHOLDER_IDENTITY_KEYS)

    def test_a_filled_in_config_is_clean(self):
        cfg = {"identity": {"name": "A Name", "role": "Does a thing", "github": "a-handle"}}
        self.assertEqual(harness.placeholder_identity(cfg), [])

    def test_a_missing_field_counts_as_placeholder_because_load_config_backfills_it(self):
        self.assertIn("name", harness.placeholder_identity({"identity": {}}))

    def test_pronouns_and_timezone_are_never_flagged(self):
        flagged = harness.placeholder_identity({"identity": {}})
        self.assertNotIn("pronouns", flagged)
        self.assertNotIn("timezone", flagged)

    def test_whitespace_around_a_real_value_still_counts_as_filled_in(self):
        cfg = {"identity": dict(EXAMPLE["identity"], name="  A Name  ")}
        self.assertNotIn("name", harness.placeholder_identity(cfg))


class CoerceTests(unittest.TestCase):
    def test_a_known_stance_variant_passes(self):
        self.assertEqual(harness.coerce_config_value("stances.testing", "off"), "off")

    def test_an_unknown_stance_variant_names_the_options(self):
        with self.assertRaises(SystemExit) as cm:
            harness.coerce_config_value("stances.testing", "sometimes")
        self.assertIn("required", str(cm.exception))

    def test_an_unknown_stance_is_refused(self):
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("stances.vibes", "on")

    def test_an_unknown_identity_field_is_refused(self):
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("identity.favourite_colour", "blue")

    def test_permissions_is_checked_against_the_postures(self):
        self.assertEqual(harness.coerce_config_value("permissions", "auto"), "auto")
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("permissions", "yolo")

    def test_bool_keys_coerce(self):
        self.assertIs(harness.coerce_config_value("vscode.manage", "false"), False)
        self.assertIs(harness.coerce_config_value("codex.manage", "TRUE"), True)
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("vscode.manage", "maybe")

    def test_an_unknown_top_level_key_is_refused(self):
        with self.assertRaises(SystemExit):
            harness.coerce_config_value("colour_scheme", "dark")


class ConfigSetTests(TempHome):
    def test_set_creates_the_file_from_the_example_and_writes_the_value(self):
        harness.config_set("identity.name", "A Name")
        written = json.loads(harness.config_path().read_text())
        self.assertEqual(written["identity"]["name"], "A Name")
        self.assertEqual(written["stances"], EXAMPLE["stances"])

    def test_set_keeps_the_rest_of_an_existing_file(self):
        harness.config_set("identity.name", "A Name")
        harness.config_set("stances.testing", "off")
        written = json.loads(harness.config_path().read_text())
        self.assertEqual(written["identity"]["name"], "A Name")
        self.assertEqual(written["stances"]["testing"], "off")

    def test_the_written_file_still_resolves(self):
        harness.config_set("stances.commits", "off")
        cfg = harness.load_config(env={})
        self.assertEqual(cfg["stances"]["commits"], "off")
        harness.resolve_stances(cfg)

    def test_a_rejected_value_writes_nothing(self):
        with self.assertRaises(SystemExit):
            harness.config_set("stances.testing", "sometimes")
        self.assertFalse(harness.config_path().exists())

    def test_set_without_a_value_is_refused(self):
        with self.assertRaises(SystemExit):
            harness.config_set("identity.name", None)

    def test_the_cli_routes_set(self):
        args = argparse.Namespace(action="set", key="identity.name", value="A Name")
        self.assertEqual(harness.cmd_config(args), 0)
        self.assertEqual(json.loads(harness.config_path().read_text())["identity"]["name"], "A Name")

    def test_set_suggests_the_citizen_command_to_apply_it(self):
        with loud() as out:
            harness.config_set("stances.commits", "off")
        self.assertIn("run `citizen sync` to apply it", out.getvalue())


class InitTests(TempHome):
    def _answers(self, *values):
        it = iter(values)
        return lambda _prompt: next(it)

    def test_init_writes_a_config_that_resolves(self):
        answers = (["A Name", "", "Does a thing", "a-handle", "Europe/Lisbon", "", ""]
                   + [""] * len(harness.STANCE_NAMES))
        with unittest.mock.patch("builtins.input", self._answers(*answers)), \
             unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
             unittest.mock.patch.object(harness, "_detect_github", return_value=""):
            with loud() as out:
                rc = harness.cmd_init(argparse.Namespace(force=False))
        self.assertEqual(rc, 0)
        cfg = json.loads(harness.config_path().read_text())
        self.assertEqual(cfg["identity"]["name"], "A Name")
        self.assertEqual(cfg["identity"]["timezone"], "Europe/Lisbon")
        self.assertEqual(cfg["identity"]["pronouns"], EXAMPLE["identity"]["pronouns"])
        self.assertEqual(cfg["stances"], EXAMPLE["stances"])
        self.assertEqual(harness.placeholder_identity(cfg), [])
        self.assertIn("next: bin/citizen sync", out.getvalue())
        harness.resolve_stances(harness.load_config(env={}))

    def test_a_blank_required_answer_is_asked_again(self):
        answers = (["", "A Name", "", "Does a thing", "", "UTC", "", ""]
                   + [""] * len(harness.STANCE_NAMES))
        with unittest.mock.patch("builtins.input", self._answers(*answers)), \
             unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
             unittest.mock.patch.object(harness, "_detect_github", return_value=""):
            with loud():
                harness.cmd_init(argparse.Namespace(force=False))
        self.assertEqual(json.loads(harness.config_path().read_text())["identity"]["name"], "A Name")

    def test_an_unknown_stance_answer_is_asked_again(self):
        answers = (["A Name", "", "Does a thing", "", "UTC", "", "", "nonsense", "off"]
                   + [""] * (len(harness.STANCE_NAMES) - 1))
        with unittest.mock.patch("builtins.input", self._answers(*answers)), \
             unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
             unittest.mock.patch.object(harness, "_detect_github", return_value=""):
            with loud():
                harness.cmd_init(argparse.Namespace(force=False))
        first = harness.STANCE_NAMES[0]
        self.assertEqual(json.loads(harness.config_path().read_text())["stances"][first], "off")

    def test_init_refuses_to_clobber_without_force(self):
        harness.config_set("identity.name", "A Name")
        with loud() as out:
            rc = harness.cmd_init(argparse.Namespace(force=False))
        self.assertEqual(rc, 1)
        self.assertIn("config set", out.getvalue())
        self.assertEqual(json.loads(harness.config_path().read_text())["identity"]["name"], "A Name")

    def test_init_without_a_terminal_names_the_alternative(self):
        with unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=False):
            with loud() as out:
                rc = harness.cmd_init(argparse.Namespace(force=False))
        self.assertEqual(rc, 1)
        self.assertIn("config set", out.getvalue())
        self.assertFalse(harness.config_path().exists())


class ReportingTests(TempHome):
    def test_sync_reports_placeholder_identity_and_still_succeeds(self):
        with loud() as out:
            rc = harness.cmd_sync(argparse.Namespace(dry_run=True, adopt=False, adopt_codex=False,
                                                     print_only=False))
        self.assertEqual(rc, 0)
        self.assertIn("still at the example value", out.getvalue())

    def test_sync_is_quiet_once_identity_is_filled_in(self):
        for key, value in (("identity.name", "A Name"), ("identity.role", "Does a thing"),
                           ("identity.github", "a-handle")):
            harness.config_set(key, value)
        with loud() as out:
            harness.cmd_sync(argparse.Namespace(dry_run=True, adopt=False, adopt_codex=False,
                                                print_only=False))
        self.assertNotIn("still at the example value", out.getvalue())


if __name__ == "__main__":
    unittest.main()
