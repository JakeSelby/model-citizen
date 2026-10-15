# SPDX-License-Identifier: MIT
"""Home: the daily digest and the attention queue (AH-S380, #1332).

`report(home)` is what `GET /api/home` and `citizen home --json` answer: a digest of the last day
of sessions from the transcript index, the projection drift `GET /api/overview` reports, and
`attention.queue(home)`. Every cost and context figure here is an observational reading of the
user's own transcripts, never evidence.

Two attention sources read something slow: rule health runs the usage engines as subprocesses and
drift runs the doctor. They register those reads here with `register_read`, and a read is reused
for `READ_TTL` seconds. `report(home)`, which `citizen home --json` prints, starts every registered
read at once and waits for the slowest. `report(home, wait=False)`, which `GET /api/home` answers,
never waits: like `transcripts.snapshot`, it answers from what is cached, an expired value
included, and warms the rest on background threads; a source with nothing cached yet is
"pending" in the queue, and drift's block says so. A read that raises is not kept: a waiting
caller sees it raised again, and a non-waiting one sees that error for `ERROR_TTL` seconds before
the next warm, so the queue reports "error" instead of checking forever.
"""
from __future__ import annotations

import contextvars
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
ERROR_TTL = 30.0
BASIS = ("An observational reading of your own session transcripts on this machine, "
         "not evidence of any effect.")

_READS: Dict[str, Callable[[Path], Any]] = {}
_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_KEY_LOCKS: Dict[Tuple[str, str], threading.Lock] = {}
_ERRORS: Dict[Tuple[str, str], Tuple[float, BaseException]] = {}
_WARMING: Dict[Tuple[str, str], threading.Thread] = {}
_LOCK = threading.Lock()
# False while `report(home, wait=False)` runs: a read then answers from the cache or raises Pending.
_WAIT: contextvars.ContextVar[bool] = contextvars.ContextVar("studio_home_wait", default=True)


def register_read(name: str, compute: Callable[[Path], Any]) -> None:
    """Register a slow read `compute(home)` that `read(name, home)` reuses for `READ_TTL`."""
    _READS[name] = compute


def read(name: str, home: Path) -> Any:
    """The registered read's value for `home`, computed at most once per `READ_TTL`.

    Inside `report(home, wait=False)` it never computes on the caller's thread: it answers the
    cached value, even an expired one, warming a fresh one in the background, and raises
    `attention.Pending` when nothing is cached yet.
    """
    key = (name, str(Path(home)))
    if not _WAIT.get():
        return _cached(name, Path(home), key)
    with _LOCK:
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        with _LOCK:
            cached = _CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < READ_TTL:
            return cached[1]
        try:
            value = _READS[name](Path(home))
        except BaseException as exc:
            with _LOCK:
                _ERRORS[key] = (time.monotonic(), exc)
            raise
        with _LOCK:
            _CACHE[key] = (time.monotonic(), value)
            _ERRORS.pop(key, None)
        return value


def _cached(name: str, home: Path, key: Tuple[str, str]) -> Any:
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        failed = _ERRORS.get(key)
    if cached is not None:
        if now - cached[0] >= READ_TTL:
            warm(name, home)
        return cached[1]
    if warming(name, home):
        raise attention.Pending(name)
    if failed is not None and now - failed[0] < ERROR_TTL:
        raise failed[1]
    warm(name, home)
    raise attention.Pending(name)


def warm(name: str, home: Path) -> bool:
    """Compute the read on a daemon thread, one per read and home at a time; True when one started."""
    key = (name, str(Path(home)))
    with _LOCK:
        running = _WARMING.get(key)
        if running is not None and running.is_alive():
            return False

        def one() -> None:
            try:
                read(name, Path(home))
            except Exception:
                pass  # Kept in _ERRORS for the next non-waiting read.

        # A fresh thread starts with an empty context, so its read waits for the value.
        thread = threading.Thread(target=one, name="studio-home-warm-" + name, daemon=True)
        _WARMING[key] = thread
        thread.start()
        return True


def warming(name: str, home: Path) -> bool:
    thread = _WARMING.get((name, str(Path(home))))
    return thread is not None and thread.is_alive()


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
        _ERRORS.clear()


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


def report(home: Path, *, wait: bool = True) -> Dict[str, Any]:
    """`{"schema_version", "generated_at", "digest", "drift", "queue"}`; one failing part never
    costs Home the others. With `wait=False` no slow read runs on this thread: see the module."""
    home = Path(home)
    attention.sources()  # Imports the sources, which register their reads.
    if wait:
        prefetch(home)
    token = _WAIT.set(wait)
    try:
        if not wait:
            for name in sorted(_READS):
                _cached_quietly(name, home)
        try:
            summary = digest(home)
        except Exception as exc:
            summary = {"status": "unavailable", "message": str(exc) or exc.__class__.__name__}
        try:
            drifted = read("drift", home)
        except attention.Pending:
            drifted = {"status": "pending", "items": [], "message": None}
        except Exception as exc:
            drifted = {"status": "failed", "items": [], "message": str(exc) or exc.__class__.__name__}
        queue = attention.queue(home)
    finally:
        _WAIT.reset(token)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "digest": summary,
        "drift": drifted,
        "queue": queue,
    }


def _cached_quietly(name: str, home: Path) -> None:
    """Start every cold read at once, before any source asks for its own."""
    try:
        _cached(name, home, (name, str(home)))
    except Exception:
        pass  # Each source meets its own pending or failed read in the queue.
