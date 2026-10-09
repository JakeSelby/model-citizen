# SPDX-License-Identifier: MIT
"""Run `bin/harness` as a person who confirmed at the Mac, for tests that spawn the CLI.

The suite switches the real presence check off in every child (`isolation.stub_presence`), so a
test whose child must spend or apply runs it through this file instead, which replaces
`presence.confirm` in the child itself. A Studio started through it with `studio --detach` serves
through this file too, and runs its own `citizen draft` children through it, so a test's Studio
apply is confirmed the same way. Nothing the child is passed can do the same.
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from harness_core import presence, studio  # noqa: E402
from harness_core.studio import server  # noqa: E402

STUB = [sys.executable, str(Path(__file__).resolve())]
presence.confirm = lambda reason: True
server._cli_entry = lambda repo_root: list(STUB)
_launch = studio.launch_detached
studio.launch_detached = lambda command, root, port: _launch(list(STUB), root, port)

loader = importlib.machinery.SourceFileLoader("model_citizen_harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

if __name__ == "__main__":
    sys.exit(module.main())
