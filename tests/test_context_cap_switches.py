"""The context-cap lint counts the rules a sync would project, so a switched-off rule is not counted.

Forking a core rule switches the original off and adds the fork to a personal root (#977); the
cap counts the fork alone, and with nothing switched off its figures are what they always were.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from isolation import isolate_home

REPO = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
harness = importlib.util.module_from_spec(importlib.util.spec_from_loader("harness", _loader))
_loader.exec_module(harness)
EXAMPLE = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))


def _lines(n):
    return "\n".join(["line"] * n) + "\n"


class ContextCapSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._environ = dict(os.environ)
        isolate_home(self.home)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environ)
        self.tmp.cleanup()

    def _forked(self, original, fork):
        """A tree whose core `secrets` rule is `original` lines, and a personal root holding its
        `fork`-line fork, as #977's fork leaves them."""
        root = self.home / "tree"
        (root / "claude" / "rules").mkdir(parents=True)
        (root / "claude" / "CLAUDE.md").write_text("# Global instructions\n\nshort.\n")
        (root / "claude" / "rules" / "secrets.md").write_text(_lines(original))
        (root / "claude" / "rules" / "short.md").write_text(_lines(5))
        personal = self.home / "personal"
        (personal / "rules").mkdir(parents=True)
        (personal / "rules" / "secrets-fork.md").write_text(_lines(fork))
        return root, {"primitive_roots": [str(personal)]}

    def test_a_switched_off_original_is_not_counted_and_its_fork_is(self):
        cap = harness.ALWAYS_LOADED_CAP
        root, config = self._forked(cap - 50, cap - 50)
        self.assertTrue(harness.check_context_cap(root, config))  # both counted: over the cap
        config["rules"] = {"secrets": "off"}
        self.assertEqual(harness.check_context_cap(root, config), [])
        lines, groups = harness.always_loaded_lines(root, config)
        self.assertEqual(lines, 3 + 5 + (cap - 50))  # CLAUDE.md, the other rule, the fork
        self.assertIn(("claude/rules/ (1 file, 1 switched off)", 5), groups)
        self.assertIn(("primitive root 1/rules/ (1 files)", cap - 50), groups)

    def test_a_fork_that_alone_exceeds_the_cap_still_fails(self):
        cap = harness.ALWAYS_LOADED_CAP
        root, config = self._forked(10, cap + 20)
        config["rules"] = {"secrets": "off"}
        hits = harness.check_context_cap(root, config)
        self.assertTrue(hits)
        self.assertIn(f"over the {cap}-line cap", hits[0])
        self.assertIn("primitive root 1/rules/", hits[1])
        self.assertIn(str(cap + 20), hits[1])

    def test_switching_off_a_rule_by_its_fork_name_withholds_the_fork(self):
        root, config = self._forked(10, harness.ALWAYS_LOADED_CAP + 20)
        config["rules"] = {"secrets-fork": "off"}
        self.assertEqual(harness.check_context_cap(root, config), [])
        _lines_total, groups = harness.always_loaded_lines(root, config)
        self.assertFalse(any(name.startswith("primitive root") for name, _ in groups))

    def test_a_rule_a_mode_switches_off_is_not_counted(self):
        cap = harness.ALWAYS_LOADED_CAP
        root, config = self._forked(cap - 50, cap - 50)
        personal = Path(config["primitive_roots"][0])
        (personal / "modes").mkdir()
        (personal / "modes" / "forked.json").write_text(json.dumps({
            "schema_version": 1, "description": "The fork loads in place of the core rule.",
            "rules": {"secrets": "off"}}))
        self.assertTrue(harness.check_context_cap(root, config))
        config["mode"] = "forked"
        self.assertEqual(harness.rules_projected_off(config), {"secrets"})
        self.assertEqual(harness.check_context_cap(root, config), [])

    def test_without_a_resolver_the_configuration_rules_are_read(self):
        cap = harness.ALWAYS_LOADED_CAP
        root, config = self._forked(cap - 50, cap - 50)
        config["rules"] = {"secrets": " off ", "short": "on", "other": 1}
        with unittest.mock.patch.object(harness, "load_posture", return_value=None):
            self.assertEqual(harness.rules_projected_off(config), {"secrets"})
            self.assertEqual(harness.check_context_cap(root, config), [])
            self.assertEqual(harness.rules_projected_off({"rules": ["secrets"]}), set())
            self.assertEqual(harness.rules_projected_off(None), set())

    def test_the_lint_command_counts_the_user_configuration(self):
        root, config = self._forked(harness.ALWAYS_LOADED_CAP - 50, harness.ALWAYS_LOADED_CAP - 50)
        path = harness.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        def lint():
            """The lint's context lines; the bare tree has other findings, such as no detectors."""
            out = io.StringIO()
            quiet = os.environ.pop("HARNESS_QUIET", None)  # `say` prints nothing while it is set
            try:
                with redirect_stdout(out):
                    harness.cmd_lint(harness.argparse.Namespace(path=str(root), staged=False))
            finally:
                if quiet is not None:
                    os.environ["HARNESS_QUIET"] = quiet
            return [line for line in out.getvalue().splitlines() if line.startswith("context")]

        path.write_text(json.dumps(config))
        self.assertTrue(any("over the" in line for line in lint() if line.startswith("context-cap:")))
        path.write_text(json.dumps(dict(config, rules={"secrets": "off"})))
        found = lint()
        self.assertFalse([line for line in found if line.startswith("context-cap:")], msg=str(found))
        self.assertIn(f"{3 + 5 + harness.ALWAYS_LOADED_CAP - 50} lines of {harness.ALWAYS_LOADED_CAP}",
                      found[-1])

    def test_with_nothing_switched_off_the_figures_are_unchanged(self):
        worst = harness.always_loaded_groups(REPO)
        self.assertEqual(harness.always_loaded_groups(REPO, EXAMPLE), worst)
        self.assertEqual(harness.always_loaded_groups(REPO, dict(EXAMPLE, rules={})), worst)
        # A switch naming no installed rule withholds nothing: the filter is keyed by the rule.
        self.assertEqual(harness.always_loaded_groups(REPO, {"rules": {"no-such-rule": "off"}}), worst)
        self.assertEqual(harness.always_loaded_lines(REPO, EXAMPLE), harness.always_loaded_lines(REPO))
        self.assertEqual(harness.always_loaded_tokens(REPO, EXAMPLE), harness.always_loaded_tokens(REPO))
        self.assertEqual(harness.check_context_cap(REPO, EXAMPLE), [])

    def test_a_switched_off_core_rule_leaves_by_exactly_its_size(self):
        rule = REPO / "claude" / "rules" / "secrets.md"
        body = rule.read_text(encoding="utf-8")
        before = dict((name, (n, chars)) for name, n, chars in harness.always_loaded_groups(REPO))
        after = dict((name, (n, chars)) for name, n, chars
                     in harness.always_loaded_groups(REPO, {"rules": {"secrets": "off"}}))
        count = len(list((REPO / "claude" / "rules").glob("*.md")))
        was = before.pop(f"claude/rules/ ({count} files)")
        now = after.pop(f"claude/rules/ ({count - 1} files, 1 switched off)")
        self.assertEqual((was[0] - now[0], was[1] - now[1]), (len(body.splitlines()), len(body)))
        self.assertEqual(before, after)  # every stance stays at its longest variant

    def test_a_rule_switched_on_is_counted(self):
        self.assertEqual(harness.always_loaded_groups(REPO, {"rules": {"secrets": "on"}}),
                         harness.always_loaded_groups(REPO))


if __name__ == "__main__":
    unittest.main()
