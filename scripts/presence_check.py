#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ask for presence at this Mac once and print what the check answered. A manual check.

Run it yourself, at the Mac, from a terminal in the desktop session:

    python3 scripts/presence_check.py

It raises the same Touch ID or login-password dialog a Studio spend or apply raises
(`harness_core.presence.check`, which `confirm` answers from) and prints `present` when you
authenticated, `refused` when you cancelled, failed or let it time out, or `unavailable: <why>`
when this host cannot show it, followed by which program asked: the Model Citizen helper, whose
dialog names Model Citizen and shows its icon, or `osascript` when the helper could not be built
or verified. The first run builds the helper, which takes a few seconds. It spends nothing,
applies nothing and records nothing. Exit status: 0 present, 1 otherwise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from harness_core import presence  # noqa: E402

PATHS = {"helper": "asked by the Model Citizen helper",
         "osascript": "asked by osascript; the helper could not be built or verified",
         "none": "nothing was asked"}


def main() -> int:
    why = presence.unavailable()
    if why is not None:
        print("unavailable: " + why)
        return 1
    print("A Touch ID or login-password dialog is open; answer or cancel it.", file=sys.stderr)
    answer, path = presence.check(presence.ask(
        presence.CLI_NAME, "check that you are at this Mac, spending and changing nothing",
        "you can see the dialog a spend or an apply raises"))
    print("%s (%s)" % ("present" if answer else "refused", PATHS.get(path, path)))
    return 0 if answer else 1


if __name__ == "__main__":
    sys.exit(main())
