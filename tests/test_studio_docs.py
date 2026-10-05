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
import test_doc_links

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "studio.md"

loader = importlib.machinery.SourceFileLoader("harness_cli_for_studio_docs",
                                              str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader(loader.name, loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

WORD = re.compile(r"^[a-z][a-z-]*(\|[a-z][a-z-]*)*$")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")
SPAN = re.compile(r"`([^`]+)`")


def has_flag(flag, text):
    """Whether help text lists `flag` as a whole flag, so `--target` never matches `--target-kind`."""
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(flag), text) is not None


def references(text):
    """Each command reference in document order, as `(command, flags, attributed)`.

    A code-block line starting `citizen` is joined with its `\\` continuation lines. An inline span
    starting `citizen` is a command too. An inline span starting `--` names flags of the command
    referenced most recently before it, and is returned with `attributed` set.
    """
    found = []
    in_block = False
    pending = None
    for line in text.splitlines():
        if line.startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            body = line.split("  #", 1)[0].rstrip()
            if pending is not None:
                pending += " " + body.rstrip(" \\").strip()
            elif body.startswith("citizen "):
                pending = body.rstrip(" \\")
            else:
                continue
            if not body.endswith("\\"):
                found.append((pending, FLAG.findall(pending), False))
                pending = None
            continue
        for span in SPAN.findall(line):
            if span.startswith("citizen "):
                found.append((span, FLAG.findall(span), False))
            elif span.startswith("--"):
                found.append((None, FLAG.findall(span), True))
    return found


def documented_commands(text):
    """Each `citizen` command the text names, inline or in a code block with its continuations."""
    return [command for command, _flags, attributed in references(text) if not attributed]


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


def problems(text):
    """Every command, or flag, the text names that the CLI's own `--help` does not list.

    A flag shown with a command must be in the help of every path it expands to. A flag in a prose
    span must be in the help of at least one path of the command referenced before it, since prose
    such as "every `save` needs `--base-revision`" follows a command naming several actions.
    """
    found = []
    helps = {}

    def help_for(path):
        key = tuple(path)
        if key not in helps:
            helps[key] = help_text(path)
        return helps[key]

    last = None
    for command, flags, attributed in references(text):
        if attributed:
            if last is None:
                found.extend("%s: no command before it" % flag for flag in flags)
                continue
            texts = [help_for(path)[1] for path in expand(last)[0]]
            found.extend("%s: %s not in its help" % (last, flag) for flag in flags
                         if not any(has_flag(flag, text) for text in texts))
            continue
        last = command
        for path in expand(command)[0]:
            code, text = help_for(path)
            if code != 0:
                found.append("%s: citizen %s fails --help" % (command, " ".join(path)))
                continue
            found.extend("%s: %s not in citizen %s --help" % (command, flag, " ".join(path))
                         for flag in flags if not has_flag(flag, text))
    return found


class StudioGuideCommandTests(unittest.TestCase):
    def test_the_guide_names_commands(self):
        commands = documented_commands(GUIDE.read_text())
        self.assertGreater(len(commands), 30)
        for name in ("citizen studio", "citizen draft apply", "citizen draft rollback",
                     "citizen runs compare", "citizen draft test", "citizen runs replay",
                     "citizen runs eval", "citizen runs native", "citizen runs draft-test",
                     "citizen reports trends"):
            self.assertTrue(any(command.startswith(name) for command in commands), name)

    def test_every_named_command_and_flag_exists(self):
        self.assertEqual(problems(GUIDE.read_text()), [])

    def test_continuation_lines_and_prose_flags_are_read(self):
        refs = references(GUIDE.read_text())
        commands = [command for command, _flags, attributed in refs if not attributed]
        self.assertTrue(any("--pack-digest" in command and command.startswith("citizen draft test")
                            for command in commands))
        prose = set(flag for _command, flags, attributed in refs if attributed for flag in flags)
        self.assertTrue({"--port", "--json", "--base-revision", "--changes",
                         "--confirm-spend"} <= prose, prose)

    def test_the_check_catches_a_bogus_command_or_flag(self):
        fixture = "\n".join((
            "Start it with `citizen studio`; `--port PORT` picks a port and `--prot` does not.",
            "",
            "```sh",
            "citizen runs start lint --target installed   # --target is not --target-kind",
            "citizen draft test NAME --register --model M \\",
            "  --pack PACK --pack-digset DIGEST",
            "citizen draft no-such-action NAME",
            "```",
            "",
            "Then `citizen draft apply NAME --revision REV` applies it.",
        ))
        self.assertEqual(problems(fixture), [
            "citizen studio: --prot not in its help",
            "citizen runs start lint --target installed: --target not in citizen runs start lint "
            "--help",
            "citizen draft test NAME --register --model M --pack PACK --pack-digset DIGEST: "
            "--pack-digset not in citizen draft test --help",
            "citizen draft no-such-action NAME: citizen draft no-such-action fails --help",
        ])

    def test_a_whole_flag_never_matches_a_longer_one(self):
        self.assertFalse(has_flag("--target", "  --target-kind KIND\n"))
        self.assertFalse(has_flag("--base", "  --base-revision REV\n"))
        self.assertTrue(has_flag("--target-kind", "  --target-kind KIND\n"))
        self.assertEqual(expand("citizen draft module read|save NAME --json"),
                         ([["draft", "module", "read"], ["draft", "module", "save"]], ["--json"]))

    def test_the_guide_is_linked_from_the_entry_pages(self):
        for rel in ("README.md", "docs/getting-started.md", "docs/usage.md"):
            self.assertTrue("](studio.md" in (REPO / rel).read_text()
                            or "](docs/studio.md" in (REPO / rel).read_text(), rel)

    def test_every_cross_doc_anchor_in_the_guide_resolves(self):
        targets = [target for target in test_doc_links.LINK.findall(GUIDE.read_text())
                   if "#" in target and "://" not in target]
        self.assertGreaterEqual(len(targets), 4)
        for target in targets:
            path, _, anchor = target.partition("#")
            with self.subTest(target=target):
                dest = (GUIDE.parent / path) if path else GUIDE
                self.assertIn(anchor, test_doc_links.anchors(dest.read_text()))


if __name__ == "__main__":
    unittest.main()
