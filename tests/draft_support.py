"""Discard a test's draft and fail the test if its branch or worktree survives.

Draft branches live in the shared repository, whose refs every worktree sees, so a draft a
test leaves behind makes a later ``draft create`` with the same name fail.

The same sharing means a suite running in another worktree sees this suite's in-flight drafts.
Each test process therefore carries a run token, every test names its drafts through
:func:`draft_name`, which embeds the token, and a leak check counts only the drafts of its own run
(:func:`leaked_drafts`); the scan in ``test_draft_cleanup`` holds every test to it. A child test
process inherits the token through ``RUN_TOKEN_ENV``, and when ``NAME_LOG_ENV`` names a file it
records each name it makes there, so the parent can prove its filter sees the child's drafts.
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
# Not HARNESS_ variables: the tests strip those from every environment they build.
RUN_TOKEN_ENV = "DRAFT_SUPPORT_RUN_TOKEN"
NAME_LOG_ENV = "DRAFT_SUPPORT_NAME_LOG"
# p<pid of the process that minted it>r<random>: unique per process, so no fixed value is usable.
_TOKEN = re.compile(r"^p([0-9]+)r[0-9a-f]{8}$")


def new_run_token() -> str:
    """A token unique to this process and call."""
    return "p%dr%s" % (os.getpid(), secrets.token_hex(4))


def check_token(token: str) -> str:
    """``token`` if it has the minted shape; a ``ValueError`` otherwise."""
    if not isinstance(token, str) or not _TOKEN.match(token):
        raise ValueError("not a draft run token: %r (mint one with new_run_token)" % (token,))
    return token


def _inherited_token() -> str:
    """This process's token: a fresh one, or the one its parent test process handed down.

    An inherited token must have been minted by this process or its direct parent; anything else,
    such as a value exported once for every run, would let two runs count each other's drafts.
    """
    token = os.environ.get(RUN_TOKEN_ENV)
    if token is None:
        return new_run_token()
    minted_by = int(check_token(token)[1:token.index("r")])
    if minted_by not in (os.getpid(), os.getppid()):
        raise RuntimeError("%s=%s was minted by process %d, not this one or its parent; "
                           "unset it" % (RUN_TOKEN_ENV, token, minted_by))
    return token


RUN_TOKEN = _inherited_token()


def draft_name(prefix: str, token: Optional[str] = None) -> str:
    """A unique draft name ``<prefix><token>-<random>`` stamped with this run's token."""
    name = "%s%s-%s" % (prefix, RUN_TOKEN if token is None else check_token(token),
                        secrets.token_hex(4))
    log = os.environ.get(NAME_LOG_ENV)
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(name + "\n")
    return name


def owned_by(name: str, prefixes: Iterable[str], token: Optional[str] = None) -> bool:
    """Whether ``name`` is a draft one of ``prefixes`` names for ``token``'s run."""
    token = RUN_TOKEN if token is None else check_token(token)
    return name.startswith(tuple(prefix + token + "-" for prefix in prefixes))


def leaked_drafts(prefixes: Iterable[str], token: Optional[str] = None,
                  repo: Path = ROOT) -> List[str]:
    """The ``draft/*`` branches in ``repo`` that one of ``prefixes`` names for ``token``'s run.

    A sibling suite's drafts carry another token and are not counted.
    """
    prefixes, token = tuple(prefixes), RUN_TOKEN if token is None else check_token(token)
    return sorted(name for name in draft_branches(repo) if owned_by(name, prefixes, token))


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
