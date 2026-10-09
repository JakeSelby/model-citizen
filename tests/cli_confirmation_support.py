# SPDX-License-Identifier: MIT
"""A person's yes, for tests that run a Studio CLI spend or apply.

The CLI refuses a paid start or an apply without a one-use grant (`approvals.grant`), which only
the approval path and the Studio's own dialog write. A test that stands for the person who said
yes files that grant here, under the home the CLI will read, for the exact words it will run.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "policy" / "hooks"


def load(name, alias=None):
    spec = importlib.util.spec_from_file_location(alias or "cli_confirmation_" + name.replace("-", "_"),
                                                  str(HOOKS / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


approvals = load("approvals")


@contextlib.contextmanager
def at_home(home):
    """`os.environ` pointing the approvals store at `home`, or unchanged when `home` is None."""
    if home is None:
        yield
        return
    with mock.patch.dict(os.environ, {"HOME": str(home), "HARNESS_HOME": str(home)}):
        yield


def needs_person(argv):
    """Whether the CLI asks a person for `argv`, as the command grader reads it."""
    words = [str(w) for w in argv]
    plain = [w for w in words if not w.startswith("-")]
    if plain[:1] == ["runs"] and len(plain) > 2 and plain[1] in ("replay", "eval", "native",
                                                                  "draft-test"):
        return plain[2] == "start"
    if plain[:1] == ["runs"] and plain[1:2] == ["start"]:
        return "--confirm-spend" in words
    if plain[:1] == ["draft"] and plain[1:2] in (["apply"], ["recover"]):
        return True
    return plain[:2] == ["draft", "rollback"] and "--preview" not in words


def confirm(argv, home=None, via="prompt"):
    """File a person's one-use yes for exactly `argv` under `home` (the current home if None)."""
    with at_home(home):
        if not approvals.grant([str(w) for w in argv], via):
            raise AssertionError("the grant for %r could not be written" % (argv,))


def confirm_if_needed(argv, home=None):
    if needs_person(argv):
        confirm(argv, home)
