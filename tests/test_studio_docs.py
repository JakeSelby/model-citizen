# SPDX-License-Identifier: MIT
"""Every `citizen` command the Studio guide names exists, with every flag it shows."""
import contextlib
import importlib.machinery
import importlib.util
import io
import itertools
import re
import unittest
from pathlib import Path

import isolation  # noqa: F401 -- moves the process onto a disposable home

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "studio.md"

loader = importlib.machinery.SourceFileLoader("harness_cli_for_studio_docs",
                                              str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader(loader.name, loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

WORD = re.compile(r"^[a-z][a-z-]*(\|[a-z][a-z-]*)*$")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def documented_commands(text):
    """Each command in an inline code span or a code-block line that starts with `citizen`."""
    commands = re.findall(r"`(citizen [^`]+)`", text)
    in_block = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_block = not in_block
            continue
        if in_block and line.startswith("citizen "):
            commands.append(line.split("  #", 1)[0].rstrip(" \\"))
    return commands


def expand(command):
    """The subcommand paths a command names (`a|b` expands), and the flags it shows."""
    words = command.split()[1:]
    path = []
    for word in words:
        if not WORD.match(word):
            break
        path.append(word.split("|"))
    return [list(choice) for choice in itertools.product(*path)], FLAG.findall(command)


def help_text(path):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            harness.main(path + ["--help"])
        except SystemExit as exc:
            return exc.code, out.getvalue()
    return 0, out.getvalue()


class StudioGuideCommandTests(unittest.TestCase):
    def test_the_guide_names_commands(self):
        commands = documented_commands(GUIDE.read_text())
        self.assertGreater(len(commands), 30)
        for name in ("citizen studio", "citizen draft apply", "citizen draft rollback",
                     "citizen runs compare", "citizen draft test"):
            self.assertTrue(any(command.startswith(name) for command in commands), name)

    def test_every_named_command_and_flag_exists(self):
        for command in documented_commands(GUIDE.read_text()):
            paths, flags = expand(command)
            for path in paths:
                with self.subTest(command=command, path=" ".join(path)):
                    code, text = help_text(path)
                    self.assertEqual(code, 0, text)
                    for flag in flags:
                        self.assertIn(flag, text)

    def test_an_unknown_command_or_flag_fails_the_check(self):
        code, _ = help_text(["draft", "no-such-action"])
        self.assertNotEqual(code, 0)
        _, text = help_text(["draft", "apply"])
        self.assertNotIn("--no-such-flag", text)
        self.assertEqual(expand("citizen draft module read|save NAME --json"),
                         ([["draft", "module", "read"], ["draft", "module", "save"]], ["--json"]))

    def test_the_guide_is_linked_from_the_entry_pages(self):
        for rel in ("README.md", "docs/getting-started.md"):
            self.assertTrue("](studio.md" in (REPO / rel).read_text()
                            or "](docs/studio.md" in (REPO / rel).read_text(), rel)


if __name__ == "__main__":
    unittest.main()
