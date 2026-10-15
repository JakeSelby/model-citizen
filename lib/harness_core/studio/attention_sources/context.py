# SPDX-License-Identifier: MIT
"""Home item: context overage. Sessions in the last day whose main-thread calls sent more than the
cost stance's lines, `transcripts.CONTEXT_LIMIT` (high) or only `CONTEXT_WARN` (medium), read
from what the transcript index already holds: a cold index is still warming, never waited on.
An observational reading of the user's own transcripts, not evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from .. import transcripts
from ..attention import register_attention

NAME = "context"
HREF = "/sessions"
DAYS = 1
NOTE = "An observational reading of your own transcripts, not evidence."


def _thousands(tokens: int) -> str:
    return "%dk" % (tokens // 1000)


def _item(key: str, severity: str, sessions: List[Mapping[str, Any]], calls: int, line: int,
          why: str) -> Dict[str, Any]:
    count = len(sessions)
    return {"id": "%s:%s" % (NAME, key), "source": NAME, "kind": "context-" + key,
            "severity": severity,
            "title": "%d %s in the last day sent calls past %s of context" % (
                count, "session" if count == 1 else "sessions", _thousands(line)),
            "detail": "%d main-thread %s past %s, %s %s" % (
                calls, "call" if calls == 1 else "calls", _thousands(line), why, NOTE),
            "at": max((s.get("ended") or "" for s in sessions), default="") or None,
            "action": {"label": "See sessions", "href": HREF}}


def items(snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    sessions = list(snapshot.get("sessions") or ())
    over = [s for s in sessions if s.get("over_limit")]
    warn = [s for s in sessions if s.get("over_warn") and not s.get("over_limit")]
    out = []
    if over:
        out.append(_item("over-limit", "high", over, sum(int(s["over_limit"]) for s in over),
                         transcripts.CONTEXT_LIMIT,
                         "where the cost stance hands off and a call costs about 3.3 times as much."))
    if warn:
        out.append(_item("over-warn", "medium", warn, sum(int(s["over_warn"]) for s in warn),
                         transcripts.CONTEXT_WARN, "where the cost stance says a call costs more."))
    return out


def source(home: Path) -> List[Dict[str, Any]]:
    return items(transcripts.snapshot(home, days=DAYS))


register_attention(NAME, source)
