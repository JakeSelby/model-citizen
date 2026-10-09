# SPDX-License-Identifier: MIT
"""Serve a `StudioSecurityFixture` Studio as a person who confirmed at the Mac.

The fixture's Studio runs `citizen draft apply|rollback|recover` as children, which ask the
presence check the suite switches off (`isolation.stub_presence`). A test whose Studio must apply
calls `serve_as_present` in its `setUp`: the Studio is restarted through
`tests/presence_stub_cli.py`, which carries the confirmed person into those children.
"""
import json
import subprocess
import sys
from pathlib import Path

from harness_core.studio import state_root

STUB = [sys.executable, str(Path(__file__).resolve().with_name("presence_stub_cli.py"))]


def serve_as_present(fixture) -> None:
    subprocess.run(STUB + ["studio", "stop", "--json"], env=fixture.env, capture_output=True,
                   text=True, timeout=15)
    done = subprocess.run(STUB + ["studio", "--detach", "--no-open", "--json"], env=fixture.env,
                          capture_output=True, text=True, timeout=45)
    fixture.assertEqual(done.returncode, 0, done.stderr)
    fixture.started = json.loads(done.stdout)
    fixture.record = json.loads((state_root(fixture.home) / "instance.json").read_text())
