# SPDX-License-Identifier: MIT
"""Studio's context-cap figures agree with `citizen lint` once a rule is switched off."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from isolation import isolate_home

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import module_editing, selection  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("harness", str(ROOT / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness", _loader))
_loader.exec_module(harness)


def _lines(n):
    return "\n".join(["line"] * n) + "\n"


class Environment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._environ = dict(os.environ)
        isolate_home(self.home)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environ)
        self.tmp.cleanup()


class DraftCheckTests(Environment):
    def _forked(self):
        cap = harness.ALWAYS_LOADED_CAP
        candidate = self.home / "draft"
        (candidate / "claude" / "rules").mkdir(parents=True)
        (candidate / "claude" / "CLAUDE.md").write_text("# Global instructions\n")
        (candidate / "claude" / "rules" / "secrets.md").write_text(_lines(cap - 50))
        personal = self.home / "personal"
        (personal / "rules").mkdir(parents=True)
        (personal / "rules" / "secrets-fork.md").write_text(_lines(cap - 50))
        return candidate, {"primitive_roots": [str(personal)]}

    @staticmethod
    def _cap(found):
        return [item["message"] for item in found if item["message"].startswith("context-cap:")]

    def test_the_draft_check_discounts_a_rule_its_configuration_switches_off(self):
        candidate, config = self._forked()
        self.assertTrue(self._cap(module_editing._diagnostics(harness, candidate, "x.md", config)))
        config["rules"] = {"secrets": "off"}
        self.assertEqual(self._cap(module_editing._diagnostics(harness, candidate, "x.md", config)), [])
        self.assertEqual(harness.check_context_cap(candidate, config), [])  # what the lint says


class SelectionBudgetTests(Environment):
    def _report(self, user):
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(user), encoding="utf-8")
        repository = self.home / "project"
        repository.mkdir(exist_ok=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        return selection.report(ROOT, str(repository), "", env)["budgets"]

    def test_the_lint_figure_is_discounted_and_the_worst_case_is_not(self):
        rule = (ROOT / "claude" / "rules" / "secrets.md").read_text(encoding="utf-8")
        worst_lines, worst_tokens = harness.always_loaded_lines(ROOT)[0], harness.always_loaded_tokens(ROOT)[0]
        for budget in self._report({"rules": {"secrets": "off"}}):
            self.assertEqual((budget["used_lines"], budget["used_tokens"]), (worst_lines, worst_tokens))
            self.assertEqual(budget["lint_lines"], worst_lines - len(rule.splitlines()))
            self.assertEqual(budget["lint_tokens"],
                             harness.always_loaded_tokens(ROOT, {"rules": {"secrets": "off"}})[0])
            self.assertLess(budget["lint_tokens"], worst_tokens)
            self.assertEqual((budget["line_cap"], budget["token_cap"]),
                             (harness.ALWAYS_LOADED_CAP, harness.ALWAYS_LOADED_TOKEN_CAP))

    def test_with_nothing_switched_off_the_lint_figure_is_the_worst_case(self):
        for budget in self._report({}):
            self.assertEqual((budget["lint_lines"], budget["lint_tokens"]),
                             (budget["used_lines"], budget["used_tokens"]))


if __name__ == "__main__":
    unittest.main()
