# SPDX-License-Identifier: MIT
"""Home: the daily digest and the attention queue (AH-S380, #1332).

`report(home)` is what `GET /api/home` and `citizen home --json` answer: a digest of the last day
of sessions from the transcript index, the projection drift `GET /api/overview` reports, and
`attention.queue(home)`. Every cost and context figure here is an observational reading of the
user's own transcripts, never evidence.

Two attention sources read something slow: rule health runs the usage engines as subprocesses and
drift runs the doctor. They register those reads here with `register_read`; `report` starts every
registered read at once, so a cold Home waits for the slowest rather than their sum, and a read is
reused for `READ_TTL` seconds. A read that raises is not kept, and raises again to its source,
which the queue then reports as "error".
"""
from __future__ import annotations

import datetime as dt
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import attention, transcripts

SCHEMA_VERSION = 1
CLI_COMMAND = ("citizen", "home", "--json")
DIGEST_DAYS = 1
READ_TTL = 300.0
BASIS = ("An observational reading of your own session transcripts on this machine, "
         "not evidence of any effect.")

_READS: Dict[str, Callable[[Path], Any]] = {}
_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_KEY_LOCKS: Dict[Tuple[str, str], threading.Lock] = {}
_LOCK = threading.Lock()


def register_read(name: str, compute: Callable[[Path], Any]) -> None:
    """Register a slow read `compute(home)` that `read(name, home)` reuses for `READ_TTL`."""
    _READS[name] = compute


def read(name: str, home: Path) -> Any:
    """The registered read's value for `home`, computed at most once per `READ_TTL`."""
    key = (name, str(Path(home)))
    with _LOCK:
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < READ_TTL:
            return cached[1]
        value = _READS[name](Path(home))
        with _LOCK:
            _CACHE[key] = (time.monotonic(), value)
        return value


def prefetch(home: Path) -> None:
    """Run every registered read concurrently and wait for all of them; errors stay for `read`."""
    def one(name: str) -> None:
        try:
            read(name, home)
        except Exception:
            pass  # Raised again, uncached, when the source reads it.

    threads = [threading.Thread(target=one, args=(name,), name="studio-home-" + name, daemon=True)
               for name in sorted(_READS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def drift(home: Path) -> Dict[str, Any]:
    """`GET /api/overview`'s drift block: `{"status": "current" | "drift" | "failed", "items",
    "command", "message"}`. The overview reads the process's own installed sources."""
    from harness_core import overview
    return dict(overview.current()["drift"])


register_read("drift", drift)


def _median(values: List[int]) -> Optional[int]:
    return int(round(statistics.median(values))) if values else None


def digest(home: Path, *, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The last day of sessions as the index already holds it; never parses on this thread."""
    snap = transcripts.snapshot(home, days=DIGEST_DAYS, now=now)
    sessions = snap["sessions"]
    calls = [call for session in sessions for call in session.get("calls") or ()]
    contexts = [int(call.get("context_tokens") or 0) for call in calls if not call.get("sidechain")]
    priced = [session["cost"] for session in sessions if isinstance(session.get("cost"), (int, float))]
    return {
        "status": "ready", "days": DIGEST_DAYS, "basis": BASIS,
        "indexing": bool(snap["indexing"]), "pending": int(snap["pending"]),
        "sessions": len(sessions), "calls": len(calls),
        "cost": round(sum(priced), 2) if priced else None,
        "cost_complete": all(session.get("cost_complete") for session in sessions),
        "median_context": _median(contexts), "max_context": max(contexts) if contexts else None,
        "calls_over_warn": sum(int(s.get("over_warn") or 0) for s in sessions),
        "calls_over_limit": sum(int(s.get("over_limit") or 0) for s in sessions),
        "sessions_over_limit": sum(1 for s in sessions if s.get("over_limit")),
        "context_warn": transcripts.CONTEXT_WARN, "context_limit": transcripts.CONTEXT_LIMIT,
    }


def report(home: Path) -> Dict[str, Any]:
    """`{"schema_version", "generated_at", "digest", "drift", "queue"}`; one failing part never
    costs Home the others."""
    home = Path(home)
    attention.sources()  # Imports the sources, which register their reads.
    prefetch(home)
    try:
        summary = digest(home)
    except Exception as exc:
        summary = {"status": "unavailable", "message": str(exc) or exc.__class__.__name__}
    try:
        drifted = read("drift", home)
    except Exception as exc:
        drifted = {"status": "failed", "items": [], "message": str(exc) or exc.__class__.__name__}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "digest": summary,
        "drift": drifted,
        "queue": attention.queue(home),
    }
