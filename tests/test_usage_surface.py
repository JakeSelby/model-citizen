# SPDX-License-Identifier: MIT
"""`citizen usage --surface [--json]` and `citizen scorecard [--results] [--json]` (#514), run as
the CLI in an isolated home: every owner listed with its total, MCP and hooks unmeasured, every
module in the default selection a scorecard row, and no row reading "no effect". `usage --rules`
keeps its own output.

Run: python3 -m unittest discover -s tests -p 'test_usage_surface.py'
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
sys.path.insert(0, str(REPO / "lib"))
from harness_core import rule_coverage  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("harness_cli_surface", str(REPO / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness_cli_surface", _loader))
_loader.exec_module(harness)


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home, self.project = root / "home", root / "project"
        (self.home / ".claude" / "rules").mkdir(parents=True)
        (self.home / ".claude" / "rules" / "mine.md").write_text("m" * 400, encoding="utf-8")
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {"github": {}}}), encoding="utf-8")
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}), encoding="utf-8")
        (self.project / ".git").mkdir(parents=True)
        (self.project / "CLAUDE.md").write_text("p" * 800, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        env = dict(without_harness_vars(), HOME=str(self.home))
        done = subprocess.run([sys.executable, str(REPO / "bin" / "harness")] + list(args), cwd=str(self.project),
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=300)
        return done.returncode, done.stdout, done.stderr


class UsageSurfaceTests(CliCase):
    def test_every_owner_is_listed_with_its_sources(self):
        status, out, err = self.cli("usage", "--surface")
        self.assertEqual(status, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^surface: [\d,]+ est\. tokens \(soft estimate, chars/4\); \d+ source\(s\) unmeasured$")
        for owner in ("harness", "own", "project", "plugin", "mcp", "hooks", "runtime"):
            self.assertTrue(any(line.startswith(owner + ": ") for line in lines), owner)
        self.assertTrue(any(line.strip().startswith("rules/secrets") for line in lines))
        self.assertIn("  ~/.claude/rules/mine.md", out)
        self.assertIn("100", [line for line in lines if "mine.md" in line][0])
        self.assertRegex(out, r"\n  github \(user scope\) +unmeasured: tool definitions")
        self.assertRegex(out, r"\n  Stop \(own settings\) +unmeasured: hook output")

    def test_json_lists_mcp_and_hooks_as_unmeasured_never_zero(self):
        status, out, err = self.cli("usage", "--surface", "--json")
        self.assertEqual(status, 0, err)
        document = json.loads(out)
        for source in document["sources"]:
            if source["owner"] in ("mcp", "hooks"):
                self.assertIsNone(source["tokens"])
                self.assertEqual(source["estimand"], "unmeasured")
        self.assertEqual(document["owners"]["project"]["tokens"], 200)

    def test_a_ledger_report_flag_with_surface_is_refused(self):
        status, _out, err = self.cli("usage", "--surface", "--rules")
        self.assertEqual(status, 2)
        self.assertIn("usage --surface lists the loaded instruction surface", err)

    def test_usage_rules_composes_the_same_classification(self):
        """`usage --rules`'s block is `rule_coverage.summary` over the classification the surface
        report joins, so extracting the classification changed no line of it."""
        cwd = REPO
        rules, findings = harness.classified_rules(cwd)
        self.assertEqual(harness.rule_coverage_lines(cwd),
                         rule_coverage.summary(rules, findings, relative_to=str(cwd)))
        states = harness.coverage_states()
        self.assertEqual(states.get("rules/secrets"), "measured")


class ScorecardTests(CliCase):
    def test_every_module_in_the_default_selection_has_a_row_and_none_reads_no_effect(self):
        status, out, err = self.cli("scorecard", "--json")
        self.assertEqual(status, 0, err)
        document = json.loads(out)
        module = harness.load_posture()
        selection = module.selection({"HOME": str(self.home)}, strict=False, config={}, root=REPO)
        expected = sorted("%s/%s" % (kind, unit) for kind in module.selection_kinds(REPO)
                          for unit in selection.get(kind) or {})
        self.assertEqual(sorted(row["module"] for row in document["rows"]), expected)
        self.assertNotIn("no effect", out)
        for row in document["rows"]:
            self.assertEqual(row["effect"], "unmeasured")
            if not row["instruments"]:
                self.assertEqual(row["measurement"], "unmeasured")
        hooks = [row for row in document["rows"] if row["module"].startswith("hooks/")]
        self.assertTrue(hooks and all(row["tokens"] == "unmeasured" for row in hooks))
        secrets = [row for row in document["rows"] if row["module"] == "rules/secrets"][0]
        self.assertEqual(secrets["tokens"]["estimand"], "soft estimate")

    def test_the_text_report_opens_with_the_instrumented_share(self):
        status, out, err = self.cli("scorecard")
        self.assertEqual(status, 0, err)
        self.assertRegex(out.splitlines()[0], r"^scorecard: \d+ of \d+ module\(s\) have an instrument \(\d+%\)$")
        self.assertNotIn("no effect", out)

    def test_ablation_results_fill_the_removed_modules_effect(self):
        rows = []
        for task in range(6):
            for rep in range(1, 6):
                for arm, cost in (("bare", 0.5), ("harness", 1.0 + 0.1 * task), ("no-secrets", 0.7 + 0.1 * task)):
                    rows.append({"task": "t%d" % task, "arm": arm, "rep": rep, "cost_usd": cost + 0.01 * rep,
                                 "passed": True, "error": False, "output_tokens": 10, "turns": 2,
                                 "first_call_context": 100, "tool_counts": {}, "date": "2026-10-01",
                                 "ablation_removes": "rules/secrets" if arm == "no-secrets" else None,
                                 "context_attribution": {"modules": {}}})
        results = Path(self.tmp.name) / "results.jsonl"
        results.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        status, out, err = self.cli("scorecard", "--json", "--results", str(results))
        self.assertEqual(status, 0, err)
        rows_out = {row["module"]: row for row in json.loads(out)["rows"]}
        effect = rows_out["rules/secrets"]["effect"]
        self.assertEqual((effect["estimand"], effect["arm"], effect["reading"], effect["n"]),
                         ("measured", "no-secrets", "lower", 30))
        self.assertEqual(rows_out["rules/verification"]["effect"], "unmeasured")
        status, out, _err = self.cli("scorecard", "--results", str(results))
        self.assertIn("effect cost ", [line for line in out.splitlines() if "rules/secrets" in line][0])

    def test_results_without_an_ablation_arm_are_refused(self):
        results = Path(self.tmp.name) / "results.jsonl"
        results.write_text(json.dumps({"task": "t", "arm": "bare", "rep": 1}) + "\n", encoding="utf-8")
        status, _out, err = self.cli("scorecard", "--results", str(results))
        self.assertNotEqual(status, 0)
        self.assertIn("holds no ablation arm", err)


if __name__ == "__main__":
    unittest.main()
