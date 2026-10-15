# SPDX-License-Identifier: MIT
"""The Sessions page: cost per turn, context per call against the cost stance's two lines, reset
events and the costliest sessions (AH-S377, #1329).

Everything here is read from the transcript index (`transcripts.py`) and nothing else, so each
figure is an observation of the user's own transcripts, never a measured effect. The list answers
from `transcripts.snapshot`, so it never blocks on a cold index: while transcripts are still being
indexed it says so and shows what is ready.

The window holds the sessions whose transcript changed in the last `days` days; the call figures
(context median, counts over each line, the histogram, cost per turn) count only the main-thread
calls made inside it. A session row's own figures cover the whole session.
"""
from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import periods, transcripts

DEFAULT_DAYS = 30
BUCKET = 50000
BUCKETS = 12  # 0 to 600k in 50k steps, then one bucket for 600k and over
BASIS = ("Observed in your transcripts: context is input plus cache read plus cache write per "
         "main-thread call, and cost is each call priced at the current price table. A reading of "
         "your own logs, not a measured effect.")


def lines() -> Dict[str, int]:
    return {"warn": transcripts.CONTEXT_WARN, "limit": transcripts.CONTEXT_LIMIT}


def _days(text: Optional[str]) -> Optional[int]:
    text = str(DEFAULT_DAYS) if text is None else text
    if not (text.isascii() and text.isdigit()) or not 1 <= int(text) <= periods.MAX_DAYS:
        return None
    return int(text)


def answer_list(days: Optional[str] = None, *, home: Optional[Path] = None,
                now: Optional[dt.datetime] = None) -> Tuple[int, Dict[str, Any]]:
    """`GET /api/sessions`: 200 with `overview`, or 400 `invalid_request` for a bad `days`."""
    window = _days(days)
    if window is None:
        return 400, {"error": "invalid_request"}
    return 200, overview(Path(home) if home else periods.default_home(), days=window, now=now)


def answer_show(session_id: Optional[str], *,
                home: Optional[Path] = None) -> Tuple[int, Dict[str, Any]]:
    """`GET /api/sessions/session`: 200 with `detail`, 400 `invalid_request` for a missing or
    malformed id, 404 `unknown_session` for a well-formed id no transcript has."""
    if not isinstance(session_id, str) or not transcripts.SESSION_ID.match(session_id):
        return 400, {"error": "invalid_request"}
    found = detail(Path(home) if home else periods.default_home(), session_id)
    if found is None:
        return 404, {"error": "unknown_session"}
    return 200, found


# --- the list ----------------------------------------------------------------------------------

def overview(home: Path, *, days: int = DEFAULT_DAYS,
             now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    snap = transcripts.snapshot(home, days=days, now=now)
    end = now or dt.datetime.now(dt.timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt.timezone.utc)
    low, high = periods.iso(end - dt.timedelta(days=days)), periods.iso(end)

    def inside(ts: Optional[str]) -> bool:
        return bool(ts) and low <= ts <= high  # the index's ISO stamps sort as text

    calls: List[Mapping[str, Any]] = []
    turn_costs: List[float] = []
    events: List[Dict[str, Any]] = []
    rows = []
    for one in snap["sessions"]:
        calls.extend(c for c in one["calls"] if not c["sidechain"] and inside(c["ts"]))
        turn_costs.extend(t["cost"] for t in one["turns"]
                          if t["cost"] is not None and t["calls"] and inside(t["ts"]))
        events.extend(dict(e) for e in one["events"] if inside(e["ts"]))
        rows.append(session_row(one))
    rows.sort(key=lambda r: (r["cost"] is not None, r["cost"] or 0, r["max_context"]), reverse=True)
    events.sort(key=lambda e: e["ts"] or "", reverse=True)
    contexts = [c["context_tokens"] for c in calls]
    priced = [c["cost"] for c in calls if c["cost"] is not None]
    return {
        "window_days": days, "indexing": bool(snap["indexing"]), "pending": int(snap["pending"]),
        "lines": lines(), "basis": BASIS,
        "summary": {
            "sessions": len(rows), "calls": len(calls),
            "median_context": int(round(statistics.median(contexts))) if contexts else 0,
            "over_warn": sum(1 for n in contexts if n > transcripts.CONTEXT_WARN),
            "over_limit": sum(1 for n in contexts if n > transcripts.CONTEXT_LIMIT),
            "cost": round(sum(priced), 6) if priced else None,
            "unpriced_calls": len(calls) - len(priced),
            "turns": len(turn_costs),
            "median_turn_cost": round(statistics.median(turn_costs), 6) if turn_costs else None,
            "mean_turn_cost": round(sum(turn_costs) / len(turn_costs), 6) if turn_costs else None,
            "compacts": sum(1 for e in events if e["kind"] == "compact"),
            "auto_compacts": sum(1 for e in events if e["kind"] == "compact" and e["trigger"] == "auto"),
            "reseeds": sum(1 for e in events if e["kind"] == "reseed"),
        },
        "histogram": histogram(contexts),
        "sessions": rows,
        "events": events,
    }


def histogram(contexts: List[int]) -> List[Dict[str, Any]]:
    """Calls per 50k of context, the last bucket open-ended."""
    counts = [0] * (BUCKETS + 1)
    for n in contexts:
        counts[min(n // BUCKET, BUCKETS)] += 1
    return [{"from": i * BUCKET, "to": (i + 1) * BUCKET if i < BUCKETS else None, "calls": count}
            for i, count in enumerate(counts)]


def session_row(one: Mapping[str, Any]) -> Dict[str, Any]:
    main = sum(1 for c in one["calls"] if not c["sidechain"])
    turns = [t for t in one["turns"] if t["calls"]]
    cwd = one.get("cwd")
    return {
        "session_id": one["session_id"], "title": one.get("title"), "project": one["project"],
        "repository": Path(cwd).name if cwd else one["project"],
        "started": one.get("started"), "ended": one.get("ended"), "models": list(one["models"]),
        "calls": main, "subagent_calls": len(one["calls"]) - main, "turns": len(turns),
        "cost": one["cost"], "cost_complete": one["cost_complete"],
        "cost_per_turn": round(one["cost"] / len(turns), 6) if one["cost"] is not None and turns else None,
        "max_context": one["max_context"], "median_context": one["median_context"],
        "over_warn": one["over_warn"], "over_limit": one["over_limit"],
        "compacts": sum(1 for e in one["events"] if e["kind"] == "compact"),
        "reseeds": sum(1 for e in one["events"] if e["kind"] == "reseed"),
    }


# --- one session -------------------------------------------------------------------------------

def detail(home: Path, session_id: str) -> Optional[Dict[str, Any]]:
    one = transcripts.session(home, session_id)
    if one is None:
        return None
    main = [c for c in one["calls"] if not c["sidechain"]]
    return {
        "session": session_row(one), "lines": lines(), "basis": BASIS,
        "calls": [{"index": i, "ts": c["ts"], "model": c["model"], "turn": c["turn"],
                   "context_tokens": c["context_tokens"], "output": c["output"],
                   "cost": c["cost"], "event": c["event"]} for i, c in enumerate(main)],
        "turns": [dict(t) for t in one["turns"]],
        "events": [dict(e) for e in one["events"]],
    }
