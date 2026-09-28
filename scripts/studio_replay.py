#!/usr/bin/env python3
"""Compatibility entry point for the installed Studio live replay runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from harness_core.studio.replay_runner import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
