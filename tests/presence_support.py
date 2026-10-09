# SPDX-License-Identifier: MIT
"""Run the CLI, or serve a test Studio, as a person who confirmed at the Mac.

The suite switches the real presence check off in every child (`isolation.stub_presence`). A test
whose child must spend or apply runs it through `present_cli()`: a launcher written to a private
temporary directory when the suite first asks, never a file in the checkout, which replaces
`presence.confirm` in that child and carries the replacement into a Studio it serves and the
`citizen draft` children that Studio runs. The directory is removed when the suite exits.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

from harness_core.studio import state_root

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = '''import importlib.machinery, importlib.util, sys
sys.path.insert(0, %(lib)r)
from harness_core import presence, studio
from harness_core.studio import server
SELF = [sys.executable, __file__]
presence.confirm = lambda reason: True
server._cli_entry = lambda repo_root: list(SELF)
_launch = studio.launch_detached
studio.launch_detached = lambda command, root, port: _launch(list(SELF), root, port)
loader = importlib.machinery.SourceFileLoader("model_citizen_harness", %(cli)r)
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)
sys.exit(module.main())
'''
_made: List[str] = []


def present_cli() -> List[str]:
    """argv that runs `bin/harness` as a person who confirmed; written once per suite process."""
    if not _made:
        directory = tempfile.mkdtemp(prefix="presence-")
        atexit.register(shutil.rmtree, directory, True)
        path = os.path.join(directory, "present_cli.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(LAUNCHER % {"lib": str(REPO / "lib"), "cli": str(REPO / "bin" / "harness")})
        _made.append(path)
    return [sys.executable, _made[0]]


def serve_as_present(fixture) -> None:
    """Restart a `StudioSecurityFixture` Studio through `present_cli()`, so its applies pass."""
    subprocess.run(present_cli() + ["studio", "stop", "--json"], env=fixture.env,
                   capture_output=True, text=True, timeout=15)
    done = subprocess.run(present_cli() + ["studio", "--detach", "--no-open", "--json"],
                          env=fixture.env, capture_output=True, text=True, timeout=45)
    fixture.assertEqual(done.returncode, 0, done.stderr)
    fixture.started = json.loads(done.stdout)
    fixture.record = json.loads((state_root(fixture.home) / "instance.json").read_text())
