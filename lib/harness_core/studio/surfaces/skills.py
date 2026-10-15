# SPDX-License-Identifier: MIT
"""Skills: one item per skill a session loaded or invoked, read from the transcript index
(AH-S378, #1330).

Loaded is a session's skill listing; invoked is a `Skill` tool call or a slash command naming a
loaded skill, as `transcripts` defines them. Every figure is an observational reading of the
user's own transcripts: it counts what ran and is never evidence of what a skill is worth.

The index is read through `transcripts.snapshot`, which answers from its cache and warms the rest
on a background thread, so a request never parses a transcript. While it is warming, each row's
metrics carry `indexing` and the summary says so, because the counts are short until it finishes.

`periods.efficacy_rows` hands a provider's metrics only the usage rows inside a period, so `items`
appends one `SESSION` row per indexed session to the request's usage list, stamped when the
session ended, and keeps the index state on this thread; only this surface's metrics read either.
A skill's switch lives at `skills.<unit>` in `config.json`, so an apply or a committed config
change that switches it starts a period.

`summary` is the Skills page's lead (`GET /api/harness/skills`, `citizen harness skills`): the
same counts over the whole window, with the index state, even before any session is cached.
"""
from __future__ import annotations

import datetime as dt
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .. import periods, transcripts

SESSION = "_skill_session"
STREAM = "transcripts"
NEVER_INVOKED = "loaded, never invoked"
INVOKED = "invoked"
NOT_LOADED = "not loaded"
BASIS = ("Observational: read from your own Claude Code transcripts on this machine. These "
         "counts say what ran, not what a skill is worth.")

_REQUEST = threading.local()


def config_value(config: Mapping[str, Any], unit: str) -> str:
    """The switch one version of `config.json` sets; "on", the selection's default for a skill,
    when it sets none, so making the default explicit starts no period."""
    switches = config.get("skills")
    value = switches.get(unit) if isinstance(switches, dict) else None
    return value if isinstance(value, str) and value else "on"


def use(loaded: int, invoked: int) -> str:
    """The skill's use in a span: invoked, loaded but never invoked, or not loaded at all."""
    if invoked:
        return INVOKED
    return NEVER_INVOKED if loaded else NOT_LOADED


def _session_time(found: Mapping[str, Any]) -> Optional[dt.datetime]:
    return periods.parse_ts(found.get("ended")) or periods.parse_ts(found.get("started"))


def _skills(found: Mapping[str, Any]) -> Tuple[List[str], Dict[str, int]]:
    loaded = [name for name in found.get("skills_loaded") or () if isinstance(name, str) and name]
    raw = found.get("skills_invoked")
    invoked = {name: count for name, count in (raw.items() if isinstance(raw, dict) else ())
               if isinstance(name, str) and name and isinstance(count, int) and count > 0}
    return loaded, invoked


def indexed(home: Path, days: int,
            now: dt.datetime) -> Tuple[List[Tuple[dt.datetime, Dict[str, Any]]], Dict[str, Any]]:
    """The cached sessions that ended inside the window, oldest first, and the index state
    `{"indexing", "pending"}`. Never parses a transcript on the caller's thread."""
    snap = transcripts.snapshot(home, days=days, now=now)
    cutoff = now - dt.timedelta(days=days)
    found = []
    for session in snap.get("sessions") or ():
        ts = _session_time(session)
        if ts is not None and cutoff <= ts <= now:
            found.append((ts, session))
    found.sort(key=lambda pair: pair[0])
    return found, {"indexing": bool(snap.get("indexing")), "pending": int(snap.get("pending") or 0)}


def _window_days(ctx: "periods.Context") -> int:
    return max(1, int(math.ceil((ctx.now - ctx.cutoff).total_seconds() / 86400.0)))


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    found, state = indexed(ctx.home, _window_days(ctx), ctx.now)
    _REQUEST.state = state
    out: Dict[str, Dict[str, Any]] = {}
    for ts, session in found:
        loaded, invoked = _skills(session)
        ctx.usage.append({"kind": SESSION, "_ts": ts, "session_id": session.get("session_id"),
                          "loaded": loaded, "invoked": invoked})
        for name in sorted(set(loaded) | set(invoked)):
            entry = out.setdefault(name, {"label": name, "observations": [],
                                          "current": config_value(ctx.config, name)})
            entry["observations"].append((ts, entry["current"], STREAM))
    return out


def indexing_text(state: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not state or not state.get("indexing"):
        return None
    pending = int(state.get("pending") or 0)
    if not pending:
        return "still indexing"
    return "still indexing · %d %s pending" % (pending,
                                               "transcript" if pending == 1 else "transcripts")


def metrics(rows: Sequence[Mapping[str, Any]], unit: str, _value: str = "") -> Dict[str, Any]:
    sessions = [row for row in rows if row.get("kind") == SESSION]
    loaded = sum(1 for row in sessions if unit in row["loaded"])
    invoked = [row["invoked"][unit] for row in sessions if row["invoked"].get(unit)]
    out: Dict[str, Any] = {"sessions": len(sessions), "loaded_in": loaded,
                           "invoked_in": len(invoked), "invocations": sum(invoked),
                           "use": use(loaded, len(invoked))}
    # A share of the sessions that loaded it; a skill invoked without a listing has none.
    out["invoked_rate"] = round(len(invoked) / loaded, 3) if loaded else NOT_LOADED
    text = indexing_text(getattr(_REQUEST, "state", None))
    if text:
        out["indexing"] = text
    return out


def summary(*, days: int = 30, home: Optional[Path] = None,
            now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Per skill over the whole window: sessions that loaded it, sessions that invoked it, and
    its invocations; with the index state and the reading's basis."""
    now = now or dt.datetime.now(dt.timezone.utc)
    found, state = indexed(Path(home) if home else periods.default_home(), days, now)
    table: Dict[str, Dict[str, Any]] = {}
    for _ts, session in found:
        loaded, invoked = _skills(session)
        for name in set(loaded) | set(invoked):
            entry = table.setdefault(name, {"skill": name, "loaded_sessions": 0,
                                            "invoked_sessions": 0, "invocations": 0})
            entry["loaded_sessions"] += int(name in loaded)
            if invoked.get(name):
                entry["invoked_sessions"] += 1
                entry["invocations"] += invoked[name]
    skills = []
    for name in sorted(table):
        entry = table[name]
        entry["use"] = use(entry["loaded_sessions"], entry["invoked_sessions"])
        skills.append(entry)
    return {"window_days": days, "sessions": len(found), "indexing": state["indexing"],
            "pending": state["pending"], "basis": BASIS, "skills": skills}


def answer(days: Optional[str] = None, *,
           home: Optional[Path] = None) -> Tuple[int, Dict[str, Any]]:
    """The route's status and body: `summary`, or a 400 `invalid_request` when `days` is not 1
    to `MAX_DAYS`."""
    days = "30" if days is None else days
    if not (days.isascii() and days.isdigit()) or not 1 <= int(days) <= periods.MAX_DAYS:
        return 400, {"error": "invalid_request"}
    return 200, summary(days=int(days), home=home)


periods.register_surface("skills", items, metrics,
                         config_key=lambda unit: "skills." + unit, config_value=config_value)
