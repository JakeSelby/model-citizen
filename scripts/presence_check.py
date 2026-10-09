#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ask for presence at this Mac once and print what the check answered. A manual check.

Run it yourself, at the Mac, from a terminal in the desktop session:

    python3 scripts/presence_check.py

It raises the same Touch ID or login-password dialog a Studio spend or apply raises
(`harness_core.presence.confirm`) and prints `present` when you authenticated, `refused` when you
cancelled, failed or let it time out, or `unavailable: <why>` when this host cannot show it. It
spends nothing, applies nothing and records nothing. Exit status: 0 present, 1 otherwise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from harness_core import presence  # noqa: E402


def main() -> int:
    why = presence.unavailable()
    if why is not None:
        print("unavailable: " + why)
        return 1
    print("A Touch ID or login-password dialog is open; answer or cancel it.", file=sys.stderr)
    if presence.confirm("run the Model Citizen presence check (nothing is spent or changed)"):
        print("present")
        return 0
    print("refused")
    return 1


if __name__ == "__main__":
    sys.exit(main())
