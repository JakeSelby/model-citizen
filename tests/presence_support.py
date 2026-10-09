# SPDX-License-Identifier: MIT
"""Run the CLI, or serve a test Studio, as a person who confirmed at the Mac, for one test.

The suite switches the real presence check off in every child (`isolation.stub_presence`). A test
whose child must spend or apply calls `present_cli(self)`, or `present_cli(directory=...)` with a
directory the test already removes: it writes a launcher there, never a file in the checkout. The launcher patches
`presence.confirm` with `unittest.mock` in that child and carries the patch into a Studio it
serves and the `citizen draft` children that Studio runs.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

from harness_core.studio import state_root

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = '''import importlib.machinery, importlib.util, sys
from unittest import mock
sys.path.insert(0, %(lib)r)
from harness_core import presence, studio
from harness_core.studio import server
SELF = [sys.executable, __file__]
mock.patch.object(presence, "confirm", return_value=True).start()
mock.patch.object(server, "_cli_entry", lambda repo_root: list(SELF)).start()
_launch = studio.launch_detached
mock.patch.object(studio, "launch_detached",
                  lambda command, root, port: _launch(list(SELF), root, port)).start()
loader = importlib.machinery.SourceFileLoader("present_harness", %(cli)r)
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)
sys.exit(module.main())
'''


def present_cli(test=None, directory=None) -> List[str]:
    """argv that runs `bin/harness` as a person who confirmed, for one test: written into
    `directory` (a test's own temporary home), or a new directory `test`'s cleanup removes."""
    if directory is None:
        directory = tempfile.mkdtemp(prefix="presence-")
        test.addCleanup(shutil.rmtree, directory, True)
    path = Path(directory) / ".presence" / "present_cli.py"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LAUNCHER % {"lib": str(REPO / "lib"),
                                    "cli": str(REPO / "bin" / "harness")}, encoding="utf-8")
    return [sys.executable, str(path)]


def serve_as_present(fixture) -> None:
    """Restart a `StudioSecurityFixture` Studio through `present_cli`, so its applies pass. The
    fixture's own `studio stop` cleanup still stops it."""
    launcher = present_cli(fixture)
    subprocess.run(launcher + ["studio", "stop", "--json"], env=fixture.env,
                   capture_output=True, text=True, timeout=15)
    done = subprocess.run(launcher + ["studio", "--detach", "--no-open", "--json"],
                          env=fixture.env, capture_output=True, text=True, timeout=45)
    fixture.assertEqual(done.returncode, 0, done.stderr)
    fixture.started = json.loads(done.stdout)
    fixture.record = json.loads((state_root(fixture.home) / "instance.json").read_text())
