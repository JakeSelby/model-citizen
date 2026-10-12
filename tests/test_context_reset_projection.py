# SPDX-License-Identifier: MIT
"""The cost stance's context reset point, resolved and projected into Claude Code settings.

`switches.context_reset_at` names the context size at which a session resets its context. The
resolver derives the hand-off point from it when a variant names none, and `sync` writes the
auto-compaction pair into settings `env`, owned per variable: a value the harness did not write
is left with a warning, and a variant naming no reset point takes back only what sync wrote.
Every sync runs under a temporary HOME. Run: python3 -m unittest discover -s tests -p test_context_reset_projection.py
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isolation import isolate_home
from test_harness import harness

REPO = Path(__file__).resolve().parent.parent
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())
WINDOW = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
PCT = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"


def load_posture():
    spec = importlib.util.spec_from_file_location(
        "harness_posture_context_reset_projection", str(REPO / "policy" / "hooks" / "posture.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POSTURE = load_posture()


class Resolution(unittest.TestCase):
    """`context_reset_at` and the hand-off point it implies, per shipped variant and by rule."""

    def table(self, variant):
        table = POSTURE.table_for({"cost": variant}, {}, strict=True)
        self.assertEqual(table["warnings"], [], msg=variant)
        return table

    def test_each_shipped_variant_resolves_its_reset_and_handoff_point(self):
        for variant, reset, handoff in (("balanced", 400000, 360000), ("frugal", 200000, 180000),
                                        ("max", None, None), ("off", None, None)):
            table = self.table(variant)
            self.assertEqual(POSTURE.context_reset_at(table), reset, msg=variant)
            self.assertEqual(POSTURE.handoff_at(table), handoff, msg=variant)
            # The resolved table carries the effective value, so the usage feed reads the same.
            self.assertEqual(table["switches"].get("session_handoff_at"), handoff, msg=variant)

    def test_the_handoff_point_is_nine_tenths_of_the_reset_point(self):
        self.assertEqual(POSTURE.handoff_at({"switches": {"context_reset_at": 250000}}), 225000)
        self.assertEqual(POSTURE.handoff_at({"switches": {"context_reset_at": 100001}}), 90000)

    def test_an_explicit_handoff_point_below_the_reset_point_wins(self):
        self.assertEqual(POSTURE.handoff_at(
            {"switches": {"context_reset_at": 400000, "session_handoff_at": 300000}}), 300000)
        self.assertEqual(POSTURE.handoff_at({"switches": {"session_handoff_at": 300000}}), 300000)

    def test_a_null_or_late_handoff_point_takes_the_derived_one(self):
        for handoff in (None, 400000, 500000):
            self.assertEqual(POSTURE.handoff_at(
                {"switches": {"context_reset_at": 400000, "session_handoff_at": handoff}}),
                360000, msg=handoff)

    def test_a_sidecar_handing_off_at_or_past_its_reset_point_is_a_finding_naming_both(self):
        for handoff in (400000, 450000):
            clean, findings = POSTURE.validate_sidecar({"schema_version": 1, "switches": {
                "context_reset_at": 400000, "session_handoff_at": handoff}})
            self.assertEqual(len(findings), 1, findings)
            self.assertIn("session_handoff_at", findings[0])
            self.assertIn("context_reset_at", findings[0])
            self.assertNotIn("session_handoff_at", clean["switches"])
            self.assertEqual(clean["switches"]["context_reset_at"], 400000)

    def test_no_reset_point_means_no_derived_handoff(self):
        for table in ({}, {"switches": {}}, {"switches": {"context_reset_at": None}}, None):
            self.assertIsNone(POSTURE.handoff_at(table))
            self.assertIsNone(POSTURE.context_reset_at(table))

    def test_the_sidecar_schema_accepts_a_size_or_null_and_drops_anything_else(self):
        for bad in (0, -1, 1.5, True, "400000", [400000], 79999, 980001):
            clean, findings = POSTURE.validate_sidecar(
                {"schema_version": 1, "switches": {"context_reset_at": bad}})
            self.assertTrue(any("context_reset_at" in f for f in findings), bad)
            self.assertNotIn("context_reset_at", clean.get("switches", {}))
        for good in (None, 80000, 400000, 980000):
            clean, findings = POSTURE.validate_sidecar(
                {"schema_version": 1, "switches": {"context_reset_at": good}})
            self.assertEqual(findings, [])
            self.assertEqual(clean["switches"]["context_reset_at"], good)


class CustomVariants(unittest.TestCase):
    """A user's own variant on a primitive root, resolved through its `extends` chain."""

    def resolve(self, sidecar):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "primitives" / "stances" / "cost"
            root.mkdir(parents=True)
            (root / "mine.json").write_text(json.dumps(dict({"schema_version": 1}, **sidecar)))
            table = POSTURE.table_for({"cost": "mine"},
                                      {"primitive_roots": [str(Path(tmp) / "primitives")]},
                                      strict=False)
        self.assertEqual([c["variant"] for c in table["extends_chain"]][0], "mine")
        return table

    def test_extending_max_with_a_reset_point_derives_the_handoff(self):
        # max says null; the reset point this variant adds still gets a hand-off before it.
        table = self.resolve({"extends": "max", "switches": {"context_reset_at": 300000}})
        self.assertEqual(table["warnings"], [])
        self.assertEqual(POSTURE.handoff_at(table), 270000)
        self.assertEqual(table["switches"]["session_handoff_at"], 270000)

    def test_extending_balanced_with_an_earlier_handoff_keeps_it(self):
        table = self.resolve({"extends": "balanced", "switches": {"session_handoff_at": 300000}})
        self.assertEqual(table["warnings"], [])
        self.assertEqual(table["switches"]["session_handoff_at"], 300000)

    def test_extending_balanced_with_a_handoff_past_its_reset_point_warns_and_derives(self):
        table = self.resolve({"extends": "balanced", "switches": {"session_handoff_at": 450000}})
        self.assertEqual(len(table["warnings"]), 1, table["warnings"])
        self.assertIn("context_reset_at 400000", table["warnings"][0])
        self.assertEqual(table["switches"]["session_handoff_at"], 360000)


class Values(unittest.TestCase):
    """The env pair for one reset point: window = reset + reserve, clamped; percentage 100."""

    def values(self, reset):
        return harness.claude_context_reset_values({"switches": {"context_reset_at": reset}})

    def test_the_window_carries_the_reserve_and_the_percentage_is_whole(self):
        self.assertEqual(self.values(400000), {("env", WINDOW): "420000", ("env", PCT): "100"})

    def test_the_bounded_reset_points_land_on_the_documented_range_edges(self):
        self.assertEqual(self.values(POSTURE.CONTEXT_RESET_MIN)[("env", WINDOW)], "100000")
        self.assertEqual(self.values(POSTURE.CONTEXT_RESET_MAX)[("env", WINDOW)], "1000000")

    def test_an_unavailable_resolver_is_unknown_not_empty(self):
        with patch.object(harness, "load_posture", return_value=None):
            self.assertIsNone(self.values(400000))
        self.assertIsNone(harness.claude_context_reset_values({"rows": {}}))

    def test_no_reset_point_writes_nothing(self):
        self.assertEqual(harness.claude_context_reset_values({"switches": {}}), {})

    def test_the_manifest_owns_both_variables(self):
        self.assertEqual(OWNERSHIP["claude"]["context_reset"]["env_keys"], [WINDOW, PCT])


class Sync(unittest.TestCase):
    """The round trip through `sync`, per variant, with the journal deciding what is taken back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        self.addCleanup(self.tmp.cleanup)
        isolate_home(self.home)
        self.settings_file = self.home / ".claude" / "settings.json"

    def configure(self, cost):
        config = json.loads((REPO / "config.example.json").read_text())
        config.pop("workspaces_dir", None)
        config.setdefault("stances", {})["cost"] = cost
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def seed(self, env):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"env": env}))

    def sync(self, dry=False):
        out = io.StringIO()
        os.environ.pop("HARNESS_QUIET", None)
        with contextlib.redirect_stdout(out):
            code = harness.cmd_sync(harness.argparse.Namespace(
                dry_run=dry, adopt=False, adopt_codex=False, print_only=False))
        return code, out.getvalue()

    def warnings(self, out):
        return [line for line in out.splitlines() if "warning:" in line]

    def edit(self, **changes):
        live = json.loads(self.settings_file.read_text())
        for key, value in changes.items():
            live.setdefault("env", {})[key] = value
        self.settings_file.write_text(json.dumps(live))

    def env(self):
        return json.loads(self.settings_file.read_text()).get("env") or {}

    def reset_env(self):
        return {k: v for k, v in self.env().items() if k in (WINDOW, PCT)}

    def test_each_variant_projects_its_reset_point(self):
        for cost, wanted in (("balanced", {WINDOW: "420000", PCT: "100"}),
                             ("frugal", {WINDOW: "220000", PCT: "100"}),
                             ("max", {}), ("off", {})):
            with self.subTest(cost=cost), tempfile.TemporaryDirectory() as fresh:
                self.home = Path(fresh)
                self.settings_file = self.home / ".claude" / "settings.json"
                isolate_home(self.home)
                self.configure(cost)
                code, _ = self.sync()
                self.assertEqual(code, 0)
                self.assertEqual(self.reset_env(), wanted)

    def test_switching_to_a_variant_with_no_reset_point_takes_the_pair_back_out(self):
        self.seed({"MY_VAR": "keep"})
        self.configure("balanced")
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(self.reset_env(), {WINDOW: "420000", PCT: "100"})
        self.configure("max")
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(self.reset_env(), {})
        self.assertEqual(self.env().get("MY_VAR"), "keep")

    def test_switching_between_reset_points_rewrites_the_window(self):
        self.configure("balanced")
        self.sync()
        self.configure("frugal")
        self.assertEqual(self.sync()[0], 0)
        self.assertEqual(self.reset_env(), {WINDOW: "220000", PCT: "100"})

    def test_a_users_own_value_is_kept_with_one_warning(self):
        self.seed({WINDOW: "300000"})
        self.configure("balanced")
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.reset_env(), {WINDOW: "300000"})
        warnings = [line for line in out.splitlines() if "warning:" in line]
        self.assertEqual(len(warnings), 1, out)
        self.assertIn(WINDOW, warnings[0])
        # A variant with no reset point leaves a value the harness never wrote.
        self.configure("max")
        self.sync()
        self.assertEqual(self.reset_env(), {WINDOW: "300000"})

    def test_a_value_equal_to_the_projection_is_adopted_silently(self):
        self.seed({WINDOW: "420000", PCT: "100"})
        self.configure("balanced")
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertNotIn("warning:", out)
        self.assertEqual(self.reset_env(), {WINDOW: "420000", PCT: "100"})


    def test_a_users_own_percentage_alone_stops_the_whole_pair(self):
        self.seed({PCT: "80"})
        self.configure("balanced")
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.reset_env(), {PCT: "80"})
        self.assertEqual(len(self.warnings(out)), 1, out)

    def test_auto_compact_window_in_settings_stops_the_pair(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps({"autoCompactWindow": 300000}))
        self.configure("balanced")
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.reset_env(), {})
        self.assertEqual(len(self.warnings(out)), 1, out)
        self.assertIn("autoCompactWindow", self.warnings(out)[0])

    def test_auto_compact_window_added_later_takes_the_written_pair_back_out(self):
        self.configure("balanced")
        self.sync()
        live = json.loads(self.settings_file.read_text())
        live["autoCompactWindow"] = 300000
        self.settings_file.write_text(json.dumps(live))
        code, out = self.sync()
        self.assertEqual(code, 0)
        self.assertEqual(self.reset_env(), {})
        self.assertEqual(len(self.warnings(out)), 1, out)

    def test_a_window_set_after_the_pair_was_taken_back_out_is_never_half_overwritten(self):
        # balanced writes, max takes back, the user sets a window, balanced comes back.
        self.configure("balanced")
        self.sync()
        self.configure("max")
        self.sync()
        self.assertEqual(self.reset_env(), {})
        self.edit(**{WINDOW: "300000"})
        self.configure("balanced")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.reset_env(), {WINDOW: "300000"})
        self.assertEqual(len(self.warnings(out)), 1, out)

    def test_a_written_window_the_user_changed_takes_the_percentage_back_out(self):
        self.configure("balanced")
        self.sync()
        self.edit(**{WINDOW: "300000"})
        code, out = self.sync()
        self.assertEqual(self.reset_env(), {WINDOW: "300000"})
        self.assertEqual(len(self.warnings(out)), 1, out)

    def test_a_missing_or_failing_resolver_keeps_the_written_pair_and_warns(self):
        self.configure("balanced")
        self.sync()
        module = harness.load_posture()
        # What `catalog.cost_table` returns when the resolver is missing or raises.
        missing = patch.object(harness, "cost_table", return_value={
            "rows": {}, "class_applies": False, "warnings": ["no posture resolver"]})
        failing = patch.object(module, "context_reset_at", side_effect=RuntimeError("failed"))
        for name, stub in (("missing", missing), ("failing", failing)):
            with self.subTest(name), stub:
                code, out = self.sync()
                self.assertEqual(self.reset_env(), {WINDOW: "420000", PCT: "100"})
                self.assertEqual(len(self.warnings(out)), 1, out)
                self.assertIn("left as it is", self.warnings(out)[0])
        # The journal still owns the pair: a variant with no reset point takes it back out.
        self.configure("max")
        self.sync()
        self.assertEqual(self.reset_env(), {})

    def test_a_dry_run_shows_the_pair_and_writes_nothing(self):
        self.seed({"MY_VAR": "keep"})
        before = self.settings_file.read_text()
        self.configure("balanced")
        code, out = self.sync(dry=True)
        self.assertIn(WINDOW + "=420000", out)
        self.assertIn(PCT + "=100", out)
        self.assertEqual(self.settings_file.read_text(), before)


if __name__ == "__main__":
    unittest.main()
