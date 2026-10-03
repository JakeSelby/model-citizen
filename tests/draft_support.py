"""Discard a test's draft and fail the test if its branch or worktree survives.

Draft branches live in the shared repository, whose refs every worktree sees, so a draft a
test leaves behind makes a later ``draft create`` with the same name fail.

The same sharing means a suite running in another worktree sees this suite's in-flight drafts.
Each test process therefore carries a run token, and a draft named through :func:`draft_name`
embeds it, so a leak check counts only the drafts of its own run (:func:`leaked_drafts`). A child
test process inherits the token through ``RUN_TOKEN_ENV``.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "harness"
# Not a HARNESS_ variable: the tests strip those from every environment they build.
RUN_TOKEN_ENV = "DRAFT_SUPPORT_RUN_TOKEN"
_TOKEN = re.compile(r"^[a-z0-9]+$")


def new_run_token() -> str:
    return "run" + secrets.token_hex(4)


def _inherited_token() -> str:
    token = os.environ.get(RUN_TOKEN_ENV, "")
    return token if _TOKEN.match(token) else new_run_token()


RUN_TOKEN = _inherited_token()


def draft_name(prefix: str, token: Optional[str] = None) -> str:
    """A unique draft name ``<prefix><token>-<random>`` stamped with this run's token."""
    return f"{prefix}{token or RUN_TOKEN}-{secrets.token_hex(4)}"


def leaked_drafts(prefixes: Iterable[str], token: Optional[str] = None,
                  repo: Path = ROOT) -> List[str]:
    """The ``draft/*`` branches in ``repo`` that one of ``prefixes`` names for ``token``'s run.

    A sibling suite's drafts carry another token and are not counted.
    """
    owned = tuple(prefix + (token or RUN_TOKEN) + "-" for prefix in prefixes)
    return sorted(name for name in draft_branches(repo) if name.startswith(owned))


def draft_branch_exists(name: str, repo: Path = ROOT) -> bool:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/draft/" + name],
        capture_output=True, text=True,
    )
    return shown.returncode == 0


def draft_branches(repo: Path = ROOT) -> Set[str]:
    """Every ``draft/*`` branch name in ``repo``'s shared refs."""
    listed = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:strip=3)", "refs/heads/draft/"],
        capture_output=True, text=True, check=True,
    )
    return set(listed.stdout.split())


def draft_worktree_registered(name: str, repo: Path = ROOT) -> bool:
    listed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    return ("branch refs/heads/draft/" + name) in listed.stdout.splitlines()


def discard_draft(
    test: unittest.TestCase,
    name: str,
    env: Dict[str, str],
    stop: Optional[Callable[[], None]] = None,
    attempts: int = 200,
    repo: Path = ROOT,
    missing_ok: bool = False,
    cli: Path = CLI,
) -> None:
    """Stop any writer, discard ``name`` and assert nothing of it is left.

    ``stop`` runs first: a Studio server still finishing a save holds the draft's writer lock,
    and discard correctly refuses a draft that is mid-write. ``missing_ok`` is for a test that
    may already have discarded the draft itself. ``cli`` is the checkout's own entry point, for a
    draft created in a linked worktree.
    """
    if missing_ok and not draft_branch_exists(name, repo) and not draft_worktree_registered(name, repo):
        if stop is not None:
            stop()
        return
    # A quiet CLI prints nothing, which would hide a `busy` refusal from the retry below and
    # the reason for any other refusal from the assertion.
    env = {key: value for key, value in env.items() if key != "HARNESS_QUIET"}
    discarded = None
    try:
        if stop is not None:
            stop()
    finally:
        # A stop that raises (a Studio that will not exit) must not leave the branch behind.
        for _ in range(attempts):
            discarded = subprocess.run(
                [sys.executable, str(cli), "draft", "discard", name, "--json"],
                cwd=repo, env=env, capture_output=True, text=True, timeout=30,
            )
            if discarded.returncode == 0 or '"code": "busy"' not in discarded.stdout:
                break
            time.sleep(0.05)
    assert discarded is not None
    test.assertEqual(discarded.returncode, 0, discarded.stderr or discarded.stdout)
    test.assertFalse(draft_branch_exists(name, repo), f"draft/{name} survived discard")
    test.assertFalse(draft_worktree_registered(name, repo), f"draft-{name} worktree survived discard")


def register_draft_cleanup(
    test: unittest.TestCase,
    name: str,
    env: Dict[str, str],
    stop: Optional[Callable[[], None]] = None,
    missing_ok: bool = False,
) -> None:
    """Register :func:`discard_draft` as a cleanup for ``test``."""
    test.addCleanup(discard_draft, test, name, env, stop, missing_ok=missing_ok)
