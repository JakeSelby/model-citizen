# SPDX-License-Identifier: MIT
"""Unit tests for the cost stance and the cache-hygiene rule.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

COST = REPO / "primitives" / "stances" / "cost"

sys.path.insert(0, str(REPO / "tests"))
from context_budget import LINE_BUDGET, LINE_CAP, TOKEN_CAP, breakdown, measured  # noqa: E402


class TempHome(unittest.TestCase):
    """A home with no real config, so a machine's own stance selection cannot mask the defaults."""

    config = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        if self.config is not None:
            d = Path(self.tmp.name) / ".config" / "agent-harness"
            d.mkdir(parents=True)
            (d / "config.json").write_text(json.dumps(self.config))

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def scrubbed_env(self):
        env = without_harness_vars()
        env["HOME"] = self.tmp.name
        return env


class CostStanceTests(TempHome):
    def test_dimension_is_registered_with_four_variants(self):
        self.assertIn("cost", harness.STANCE_NAMES)
        self.assertEqual(sorted(p.stem for p in COST.glob("*.md")), ["balanced", "frugal", "max", "off"])

    def test_off_keeps_balanceds_role_table_and_turns_the_feed_off(self):
        sidecar = json.loads((COST / "off.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["extends"], "balanced")
        self.assertEqual(sidecar["switches"], {"turn_feed": "off"})
        self.assertNotIn("rows", sidecar)

    def test_default_is_balanced(self):
        cfg = harness.load_config(env={})
        self.assertEqual(cfg["stances"]["cost"], "balanced")
        self.assertEqual(harness.resolve_stances(cfg)["cost"], COST / "balanced.md")

    def test_env_override_selects_a_variant(self):
        cfg = harness.load_config(env={"HARNESS_STANCE_COST": "frugal"})
        self.assertEqual(cfg["stances"]["cost"], "frugal")
        self.assertEqual(harness.resolve_stances(cfg)["cost"], COST / "frugal.md")

    def test_unknown_variant_fails_loudly(self):
        with self.assertRaises(SystemExit):
            harness.resolve_stances(harness.load_config(env={"HARNESS_STANCE_COST": "cheap"}))


class CostConfigFileTests(TempHome):
    config = {"stances": {"cost": "max"}}

    def test_config_file_selects_a_variant_and_env_beats_it(self):
        self.assertEqual(harness.load_config(env={})["stances"]["cost"], "max")
        cfg = harness.load_config(env={"HARNESS_STANCE_COST": "frugal"})
        self.assertEqual(cfg["stances"]["cost"], "frugal")

    def test_config_get_resolves_the_dimension(self):
        out = subprocess.run(
            [sys.executable, str(REPO / "bin" / "harness"), "config", "get", "stances.cost"],
            capture_output=True, text=True, env=self.scrubbed_env(),
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "max", out.stderr)


class CostBudgetTests(unittest.TestCase):
    def test_always_loaded_total_leaves_room_under_the_caps(self):
        lines, tokens = measured()
        self.assertLessEqual(lines, LINE_BUDGET, msg=breakdown())
        self.assertLessEqual(tokens, TOKEN_CAP, msg=breakdown())
        self.assertLessEqual(LINE_BUDGET, LINE_CAP)

    def test_every_cost_variant_stays_short(self):
        for path in sorted(COST.glob("*.md")):
            with self.subTest(variant=path.stem):
                self.assertLessEqual(len(path.read_text().splitlines()), 8)

    def test_cache_hygiene_rule_stays_short(self):
        rule = REPO / "claude" / "rules" / "cache-hygiene.md"
        self.assertLessEqual(len(rule.read_text().splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
