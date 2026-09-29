# SPDX-License-Identifier: MIT
"""`harness integration check|apply <name>`, and `harness bmad` kept as its alias.

The CLI names no framework: the template directory, the install destination, the presence probe
and the skill surface all come from the descriptor in `policy/integrations/`, and so does the
session hook's notice. Run: python3 -m unittest discover tests
"""
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_harness import REPO, harness
from harness_core import frameworks

try:
    from test_bmad_templates import install, SURFACE
except ImportError:  # discovery from the repository root
    from tests.test_bmad_templates import install, SURFACE

HOOK = REPO / "policy" / "hooks" / "harness-session.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("harness_session", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DescriptorInstallTests(unittest.TestCase):
    def test_every_installable_descriptor_declares_a_complete_install_block(self):
        found = [data for data in frameworks.descriptors() if data.get("install")]
        self.assertTrue(found)
        for data in found:
            with self.subTest(integration=data["id"]):
                self.assertEqual(frameworks.problems(data), [])
                self.assertTrue((REPO / data["install"]["templates"]).is_dir())
                self.assertEqual(frameworks.installable(data["id"]), data)

    def test_an_incomplete_install_block_is_refused_as_a_whole(self):
        data = json.loads((REPO / "policy" / "integrations" / "bmad.json").read_text())
        data["install"] = dict(data["install"], detect="", skill_roots=[])
        problems = frameworks.problems(data)
        self.assertIn("install.detect must be a non-empty string", problems)
        self.assertTrue(any("install.skill_roots" in p for p in problems))

    def test_an_unknown_integration_names_what_is_declared(self):
        with self.assertRaisesRegex(ValueError, "no installable integration `nope`.*bmad"):
            harness.integration_install("nope")


class Surface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self._quiet = os.environ.pop("HARNESS_QUIET", None)
        self.addCleanup(self.restore)
        install(self.base, SURFACE)

    def restore(self):
        if self._quiet is not None:
            os.environ["HARNESS_QUIET"] = self._quiet

    def cli(self, *args):
        """Through the real argument parser, so the alias is reached the way a user reaches it."""
        out = io.StringIO()
        with redirect_stdout(out):
            code = harness.main(list(args))
        return code, out.getvalue()


class CommandTests(Surface):
    def test_the_named_integration_checks_the_repository(self):
        code, _ = self.cli("integration", "check", "bmad", str(self.base))
        self.assertEqual(code, 0)

    def test_the_alias_and_the_named_form_do_the_same_thing(self):
        aliased = self.cli("bmad", "check", str(self.base))
        named = self.cli("integration", "check", "bmad", str(self.base))
        self.assertEqual(aliased, named)

    def test_apply_installs_through_either_spelling_and_is_then_idempotent(self):
        self.assertEqual(self.cli("integration", "apply", "bmad", str(self.base))[0], 0)
        written = sorted(p.name for p in (self.base / "_bmad" / "custom").glob("*.user.toml"))
        self.assertTrue(written)  # which names, and their content, are pinned by the template tests
        code, output = self.cli("bmad", "apply", str(self.base))
        self.assertEqual(code, 0)
        self.assertNotIn("wrote", output)

    def test_an_unknown_integration_is_an_error_rather_than_a_silent_pass(self):
        code, output = self.cli("integration", "check", "nope", str(self.base))
        self.assertEqual(code, 1)
        self.assertIn("no installable integration `nope`", output)

    def test_a_repository_the_descriptor_does_not_detect_is_an_error(self):
        empty = self.base / "elsewhere"
        empty.mkdir()
        code, output = self.cli("integration", "check", "bmad", str(empty))
        self.assertEqual(code, 1)
        self.assertIn("no _bmad/ directory under", output)

    def test_the_uninstalled_state_line_names_the_command_that_installs_it(self):
        _, output = self.cli("integration", "check", "bmad", str(self.base))
        self.assertIn("template not installed (citizen integration apply bmad)", output)

    def test_the_usage_text_documents_both_spellings(self):
        usage = harness.__doc__
        self.assertIn("citizen integration check|apply NAME", usage)
        self.assertIn("alias", usage)


class SessionNoticeTests(Surface):
    def test_the_hook_probes_the_descriptors_detect_path_and_names_the_framework(self):
        module = load_hook()
        self.assertEqual(module.integrations_present(str(REPO), str(self.base)),
                         [("bmad", "BMad Method")])
        self.assertEqual(module.integrations_present(str(REPO), str(self.base / "elsewhere")), [])

    def test_the_notice_runs_the_real_check_and_reports_only_the_notable_lines(self):
        """End to end: the hook's own subprocess, its filter, and the framework-named prefix."""
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        module = load_hook()
        with patch.object(module, "remaining", return_value=30):
            lines = module.integration_lines(str(REPO), str(self.base))
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("BMad Method integration check: "), lines[0])
        self.assertIn("template not installed (citizen integration apply bmad)", lines[0])
        self.assertNotIn("skipped", lines[0])  # a skill the repository lacks is not news

    def test_the_notice_is_silent_once_the_overrides_are_in_place(self):
        subprocess.run(["git", "init", "-q", str(self.base)], check=True)
        self.assertEqual(self.cli("integration", "apply", "bmad", str(self.base))[0], 0)
        module = load_hook()
        with patch.object(module, "remaining", return_value=30):
            self.assertEqual(module.integration_lines(str(REPO), str(self.base)), [])

    def test_a_directory_that_is_not_a_repository_says_nothing(self):
        self.assertEqual(load_hook().integration_lines(str(REPO), str(self.base)), [])

    def test_a_descriptor_with_no_install_block_is_not_probed(self):
        module = load_hook()
        repo = self.base / "fake-repo" / "policy" / "integrations"
        repo.mkdir(parents=True)
        (repo / "other.json").write_text(json.dumps({"id": "other", "name": "Other"}))
        self.assertEqual(module.integrations_present(str(self.base / "fake-repo"),
                                                     str(self.base)), [])


class NoFrameworkNameTests(unittest.TestCase):
    def test_the_cli_carries_no_framework_specific_path_or_function(self):
        source = (REPO / "bin" / "harness").read_text()
        body = source[source.index("framework integrations"):source.index("def cmd_compatibility")]
        self.assertNotIn("_bmad", body)
        self.assertNotIn("bmad_", body)
        self.assertNotIn("user.toml", body)


if __name__ == "__main__":
    unittest.main()
