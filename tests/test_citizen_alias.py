# SPDX-License-Identifier: MIT
"""`citizen` is the command, and `harness` stays a supported alias for it.

`bin/citizen` is a regular wrapper around `bin/harness`, which stays the real file because hooks
and the installer find the checkout through it. Usage names whichever command was typed.
Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO, harness

VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()
NAMES = ("citizen", "harness")


def run(command, *args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "HARNESS_QUIET"}
    return subprocess.run([sys.executable, str(command), *args], capture_output=True, text=True,
                          cwd=cwd, env=env, timeout=60)


class EntrypointTests(unittest.TestCase):
    def test_citizen_is_a_regular_executable_wrapper(self):
        entrypoint = REPO / "bin" / "citizen"
        self.assertTrue(entrypoint.is_file())
        self.assertFalse(entrypoint.is_symlink())
        self.assertFalse((REPO / "bin" / "harness").is_symlink())
        self.assertTrue(os.access(str(entrypoint), os.X_OK))


class ProgramNameTests(unittest.TestCase):
    def test_the_invoked_name_is_kept_and_anything_else_reads_citizen(self):
        self.assertEqual(harness.program_name("/x/bin/citizen"), "citizen")
        self.assertEqual(harness.program_name("harness"), "harness")
        for other in ("/x/bin/harness.py", "__main__.py", "", "agent-harness"):
            with self.subTest(argv0=other):
                self.assertEqual(harness.program_name(other), "citizen")

    def test_the_usage_docstring_leads_with_citizen_and_names_the_alias(self):
        usage = harness.__doc__
        self.assertIn("    citizen sync", usage)
        self.assertNotIn("    harness sync", usage)
        self.assertIn("`harness` is a supported alias for `citizen`", usage)


class CommandTests(unittest.TestCase):
    def test_help_names_the_command_that_was_typed(self):
        for name in NAMES:
            with self.subTest(name=name):
                out = run(REPO / "bin" / name, "--help")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertTrue(out.stdout.startswith("usage: " + name + " "), out.stdout[:80])

    def test_version_prints_the_product_name_through_either_command(self):
        for name in NAMES:
            with self.subTest(name=name):
                out = run(REPO / "bin" / name, "--version")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.strip(), "model-citizen " + VERSION)

    def test_a_link_to_citizen_elsewhere_still_finds_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "citizen"
            link.symlink_to(REPO / "bin" / "citizen")
            out = run(link, "integration", "check", "bmad", tmp, cwd=tmp)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertIn("no _bmad/ directory under", out.stdout)


class CheckoutDetectionTests(unittest.TestCase):
    def load(self, name):
        path = str(REPO / "bin" / name)
        loader = importlib.machinery.SourceFileLoader("cli_through_" + name, path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_either_name_resolves_the_same_checkout(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.load(name).REPO, REPO.resolve())

    def test_the_hooks_still_find_the_checkout_through_bin_harness(self):
        spec = importlib.util.spec_from_file_location(
            "usage_log_alias", REPO / "policy" / "hooks" / "usage-log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.harness_version(), VERSION)


if __name__ == "__main__":
    unittest.main()
