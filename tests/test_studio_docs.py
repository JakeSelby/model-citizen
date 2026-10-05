# SPDX-License-Identifier: MIT
"""Every `citizen` command the Studio guide names exists, with every flag it shows."""
import argparse
import importlib.machinery
import importlib.util
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


def references(text):
    """Each command reference in document order, as `(command, flags, governors)`.

    A code-block line starting `citizen` is joined with its `\\` continuation lines; an inline span
    starting `citizen` is a command too, and both come back with `governors` None. An inline span
    starting `--` comes back with `command` None and `governors` the commands it belongs to: every
    command named earlier in its paragraph since the last flag span, so "`a` and `b`, each with
    `--x`" checks both. With none, it belongs to the command named most recently, as `[]`.
    """
    found = []
    in_block = False
    pending = None
    paragraph = []
    for line in text.splitlines():
        if line.startswith("```"):
            in_block = not in_block
            paragraph = []
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
                found.append((pending, FLAG.findall(pending), None))
                pending = None
            continue
        if not line.strip():
            paragraph = []
        for span in SPAN.findall(line):
            if span.startswith("citizen "):
                found.append((span, FLAG.findall(span), None))
                paragraph.append(span)
            elif span.startswith("--"):
                found.append((None, FLAG.findall(span), paragraph))
                paragraph = []
    return found


def documented_commands(text):
    """Each `citizen` command the text names, inline or in a code block with its continuations."""
    return [command for command, _flags, governors in references(text) if governors is None]


def expand(command):
    """The subcommand paths a command names (`a|b` expands), and the flags it shows."""
    words = command.split()[1:]
    path = []
    for word in words:
        if not WORD.match(word):
            break
        path.append(word.split("|"))
    return [list(choice) for choice in itertools.product(*path)], FLAG.findall(command)


def resolve(parser, path):
    """The option strings the parser a command path reaches defines, or None for no such command.

    Each word selects a subcommand while the parser has subcommands, and otherwise fills the next
    positional, which must be one of its choices when it has any. Options come from the parser's
    own actions, never from help text, so another option's description naming a flag proves
    nothing.
    """
    positionals = None
    for word in path:
        subcommands = [action for action in parser._actions
                       if isinstance(action, argparse._SubParsersAction)]
        if subcommands and positionals is None:
            if word not in subcommands[0].choices:
                return None
            parser = subcommands[0].choices[word]
            continue
        if positionals is None:
            positionals = [action for action in parser._actions if not action.option_strings]
        if not positionals:
            break
        action = positionals.pop(0)
        if action.choices is not None and word not in action.choices:
            return None
    return set(flag for action in parser._actions for flag in action.option_strings)


def problems(text, parser=None):
    """Every command, or flag, the text names that the CLI's parser does not define.

    A flag shown with a command, or in a prose span that names its commands, must be defined on
    every path each command expands to. A prose flag after no command in its paragraph must be on
    at least one path of the command named before it, since prose such as "every `save` needs
    `--base-revision`" follows a command naming several actions.
    """
    parser = parser if parser is not None else harness.build_parser()
    found = []

    def check(command, flags, every):
        options = [(path, resolve(parser, path)) for path in expand(command)[0]]
        for path, defined in options:
            if defined is None:
                found.append("%s: citizen %s is not a command" % (command, " ".join(path)))
        defined = [(path, opts) for path, opts in options if opts is not None]
        for flag in flags:
            if every:
                found.extend("%s: %s not on citizen %s" % (command, flag, " ".join(path))
                             for path, opts in defined if flag not in opts)
            elif defined and not any(flag in opts for _path, opts in defined):
                found.append("%s: %s not on any of its commands" % (command, flag))

    last = None
    for command, flags, governors in references(text):
        if governors is None:
            last = command
            check(command, flags, True)
        elif governors:
            for governor in governors:
                check(governor, flags, True)
        elif last is None:
            found.extend("%s: no command before it" % flag for flag in flags)
        else:
            check(last, flags, False)
    return list(dict.fromkeys(found))


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
        commands = documented_commands(GUIDE.read_text())
        self.assertTrue(any("--pack-digest" in command and command.startswith("citizen draft test")
                            for command in commands))
        prose = set(flag for command, flags, _governors in refs if command is None
                    for flag in flags)
        self.assertTrue({"--port", "--json", "--base-revision", "--changes",
                         "--request"} <= prose, prose)

    def test_the_check_catches_a_bogus_command_or_flag(self):
        fixture = "\n".join((
            "Start it with `citizen studio`; `--port PORT` picks a port and `--prot` does not.",
            "",
            "```sh",
            "citizen runs start lint --target installed   # --target is not --target-kind",
            "citizen draft test NAME --register --model M \\",
            "  --pack PACK --pack-digset DIGEST",
            "citizen draft no-such-action NAME",
            "citizen runs replay no-such-action --json",
            "```",
            "",
            "`citizen runs replay result` and `citizen draft list`, each with `--request FILE`.",
        ))
        self.assertEqual(problems(fixture), [
            "citizen studio: --prot not on any of its commands",
            "citizen runs start lint --target installed: --target not on citizen runs start lint",
            "citizen draft test NAME --register --model M --pack PACK --pack-digset DIGEST: "
            "--pack-digset not on citizen draft test",
            "citizen draft no-such-action NAME: citizen draft no-such-action is not a command",
            "citizen runs replay no-such-action --json: "
            "citizen runs replay no-such-action is not a command",
            "citizen draft list: --request not on citizen draft list",
        ])

    def test_a_flag_named_only_in_another_options_help_fails(self):
        parser = argparse.ArgumentParser(prog="citizen")
        sub = parser.add_subparsers(dest="cmd", required=True)
        test = sub.add_parser("test")
        test.add_argument("--model", help="with --register: the model the plan names")
        self.assertIn("--register", test.format_help())
        self.assertEqual(problems("`citizen test --register --model M`", parser),
                         ["citizen test --register --model M: --register not on citizen test"])

    def test_a_whole_flag_never_matches_a_longer_one(self):
        parser = argparse.ArgumentParser(prog="citizen")
        parser.add_subparsers(dest="cmd").add_parser("start").add_argument("--target-kind")
        self.assertEqual(problems("`citizen start --target K`", parser),
                         ["citizen start --target K: --target not on citizen start"])
        self.assertEqual(problems("`citizen start --target-kind K`", parser), [])
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
