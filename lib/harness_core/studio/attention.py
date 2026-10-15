# SPDX-License-Identifier: MIT
"""Home's attention queue: at most five things that need the user, one action each (AH-S380, #1332).

A source is a function `fn(home) -> [item]` registered under a name with `register_attention`.
Sources live in `attention_sources/` and register on import, so Phase A signals, the transcript
index and the Routines and Evals pages each add their items without editing this module.

An item is a dict:

    {"id": "<source>:<key>", "source": <registered name>, "kind": str,
     "severity": "high" | "medium" | "low", "title": str, "detail": str,
     "at": iso8601 | None, "action": {"label": str, "href": "/studio-route"}}

The queue orders by severity, then newest first, then id, and keeps `limit`. A source that raises
or returns something malformed is dropped and reported as "error" in `sources`; one bad source
never costs Home the rest.
"""
from __future__ import annotations

import importlib
import pkgutil
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

LIMIT = 5
SEVERITIES = ("high", "medium", "low")
FIELDS = ("id", "source", "kind", "severity", "title", "detail", "at", "action")

_REGISTRY: Dict[str, Callable[[Path], List[Dict[str, Any]]]] = {}
_LOADED = False
_LOCK = threading.Lock()


def register_attention(name: str, fn: Callable[[Path], List[Dict[str, Any]]]) -> None:
    """Register (or replace) the source `name`."""
    if not isinstance(name, str) or not name:
        raise ValueError("an attention source needs a name")
    _REGISTRY[name] = fn


def sources() -> List[str]:
    _load_sources()
    return sorted(_REGISTRY)


def _load_sources() -> None:
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        from . import attention_sources as package
        for info in pkgutil.iter_modules(package.__path__):
            try:
                importlib.import_module(package.__name__ + "." + info.name)
            except Exception:
                continue  # A source that cannot import is absent, never fatal to Home.
        _LOADED = True


def valid(item: Any, source: str) -> Optional[Dict[str, Any]]:
    """The item restricted to its contract fields, or None when it breaks the contract."""
    if not isinstance(item, Mapping):
        return None
    action = item.get("action")
    if not (isinstance(item.get("id"), str) and item["id"].startswith(source + ":")
            and item.get("source") == source
            and isinstance(item.get("kind"), str) and item["kind"]
            and item.get("severity") in SEVERITIES
            and isinstance(item.get("title"), str) and item["title"]
            and isinstance(item.get("detail"), str)
            and (item.get("at") is None or isinstance(item.get("at"), str))
            and isinstance(action, Mapping) and set(action) == {"label", "href"}
            and isinstance(action.get("label"), str) and action["label"]
            and isinstance(action.get("href"), str) and action["href"].startswith("/")
            and not action["href"].startswith("//")):
        return None
    clean = dict((name, item.get(name)) for name in FIELDS)
    clean["action"] = {"label": action["label"], "href": action["href"]}
    return clean


def queue(home: Path, *, limit: int = LIMIT) -> Dict[str, Any]:
    """`{"items": [...] (at most limit), "total": int, "sources": {name: "ok" | "error"}}`."""
    _load_sources()
    items: List[Dict[str, Any]] = []
    status: Dict[str, str] = {}
    for name in sorted(_REGISTRY):
        try:
            found = _REGISTRY[name](Path(home))
            if not isinstance(found, list):
                raise TypeError("a source returns a list")
            checked = [valid(item, name) for item in found]
            if any(item is None for item in checked):
                raise ValueError("a source returned a malformed item")
        except Exception:
            status[name] = "error"
            continue
        status[name] = "ok"
        items.extend(checked)  # type: ignore[arg-type]
    seen = set()
    unique = []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    # Newest first within a severity: sort on time descending, then stably on severity.
    unique.sort(key=lambda i: i["id"])
    unique.sort(key=lambda i: i["at"] or "", reverse=True)
    unique.sort(key=lambda i: SEVERITIES.index(i["severity"]))
    return {"items": unique[:max(int(limit), 0)], "total": len(unique), "sources": status}
