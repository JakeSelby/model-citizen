"""Discard a test's draft and fail the test if its branch or worktree survives.

Draft branches live in the shared repository, whose refs every worktree sees, so a draft a
test leaves behind makes a later ``draft create`` with the same name fail.
"""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Callable, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "harness"


def draft_branch_exists(name: str, repo: Path = ROOT) -> bool:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/draft/" + name],
        capture_output=True, text=True,
    )
    return shown.returncode == 0


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
) -> None:
    """Stop any writer, discard ``name`` and assert nothing of it is left.

    ``stop`` runs first: a Studio server still finishing a save holds the draft's writer lock,
    and discard correctly refuses a draft that is mid-write.
    """
    if stop is not None:
        stop()
    discarded = None
    for _ in range(attempts):
        discarded = subprocess.run(
            [sys.executable, str(CLI), "draft", "discard", name, "--json"],
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
) -> None:
    """Register :func:`discard_draft` as a cleanup for ``test``."""
    test.addCleanup(discard_draft, test, name, env, stop)
