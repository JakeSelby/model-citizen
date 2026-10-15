# SPDX-License-Identifier: MIT
"""What the Evals page reads (AH-S381, #1333): pre-registrations, the compatibility catalog's
native qualification, before and after each setting-period boundary, and the auto-trim reading.

Two kinds of answer live here and never mix:

- **pre-registered**: the files under `benchmarks/preregistrations/`, which
  `docs/evidence-standard.md` item 1 requires before a run may be cited, and the compatibility
  catalog, read only through `harness_core.compatibility.catalog`, which validates every linked
  record's digest, version and cases before it answers;
- **observational**: before and after a period boundary from `periods.py`, and context per call
  before and after a session's first reset from the transcript index. Both read this machine's
  own sessions, never a controlled run, so each answer carries `label: "observational"`,
  the standard's `standard_label: "exploratory"` and `OBSERVATIONAL_NOTE`, and supports no claim.

Every function only reads. `answer(kind, query)` gives a route's status and body; the server
and `citizen evals` both call it, so the two cannot drift.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import periods

SCHEMA_VERSION = 1
PREREGISTRATION_DIR = "benchmarks/preregistrations"
PREREGISTERED = "pre-registered"
OBSERVATIONAL = "observational"
# The evidence standard's own word for a check that cannot meet the experiment protocol.
STANDARD_LABEL = "exploratory"
OBSERVATIONAL_NOTE = ("Observational: read from this machine's own sessions, not from a controlled "
                      "experiment. A check that cannot meet the experiment protocol is labelled "
                      "exploratory and supports no claim.")
CONTEXT_WARN = 160_000  # the cost stance's line: above it a call costs more
DEFAULT_DAYS = 30
SESSION_LIMIT = 20
# An after period that finished its measuring window this recently is a result worth raising.
READY_DAYS = 7
RESET_EVENTS = ("compact", "reseed")
KINDS = ("preregistrations", "evidence", "before-after", "auto-trim")
QUERY = {"preregistrations": (), "evidence": (), "before-after": ("days", "surface", "item"),
         "auto-trim": ("days",)}
CLI_COMMANDS = {
    "preregistrations": ("citizen", "evals", "preregistrations", "--json"),
    "evidence": ("citizen", "evals", "evidence", "--json"),
    "before-after": ("citizen", "evals", "before-after", "--days", "{days}", "--json"),
    "auto-trim": ("citizen", "evals", "auto-trim", "--days", "{days}", "--json"),
}

ContextCall = Dict[str, Any]  # {"session_id", "ts", "context_tokens", "after_reset"}
ContextReading = Dict[str, Any]  # {"calls": [ContextCall], "indexing": bool, "pending": int}
ContextReader = Callable[[Path, int, Optional[dt.datetime]], Optional[ContextReading]]


def observational() -> Dict[str, str]:
    """The label every observational answer carries, in one place."""
    return {"label": OBSERVATIONAL, "standard_label": STANDARD_LABEL, "note": OBSERVATIONAL_NOTE}


# --- pre-registrations ----------------------------------------------------------------------

_FIELD = re.compile(r"^- \*\*(?P<name>[^*]+?):\*\*\s*(?P<value>.+?)\s*$")
_DATED = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")


def _preregistration(path: Path, relative: str) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title, fields, deviations, in_log = None, {}, 0, False
    for line in text.splitlines():
        if title is None and line.startswith("# "):
            title = line[2:].strip()
        if line.startswith("## "):
            in_log = line[3:].strip().lower() == "deviation log"
            continue
        match = _FIELD.match(line)
        if match:
            fields.setdefault(match.group("name").strip().lower(), match.group("value"))
        if in_log and _DATED.match(line):
            deviations += 1
    named = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
    date = fields.get("date registered") or (named.group(1) if named else None)
    return {"path": relative, "file": path.name, "title": title or path.stem,
            "date": date, "status": fields.get("status"), "question": fields.get("question"),
            "deviations": deviations, "label": PREREGISTERED}


def preregistrations(repo: Path) -> Dict[str, Any]:
    """Each committed pre-registration: title, date registered, status if it states one, its
    question and how many dated deviations its log carries. Newest first."""
    directory = Path(repo) / PREREGISTRATION_DIR
    items = []
    for path in sorted(directory.glob("*.md")) if directory.is_dir() else ():
        try:
            items.append(_preregistration(path, PREREGISTRATION_DIR + "/" + path.name))
        except OSError:
            continue
    items.sort(key=lambda item: (item["date"] or "", item["file"]), reverse=True)
    return {"schema_version": SCHEMA_VERSION, "directory": PREREGISTRATION_DIR, "items": items}


# --- compatibility catalog ------------------------------------------------------------------

def compatibility_evidence(repo: Path, reader: Optional[Callable[[Path], Dict[str, Any]]] = None
                           ) -> Dict[str, Any]:
    """The catalog's clients and the records each links, as `compatibility.catalog` validated
    them. A catalog the reader refuses is `unavailable` with its reason, never a partial read.
    `qualification_source` is `drifted` when the runtime source has changed since the released
    qualification, as `citizen compatibility` warns."""
    from harness_core import compatibility
    read = reader or compatibility.catalog
    root = Path(repo)
    try:
        data = read(root)
        drift = bool(compatibility.source_drift(root, data)) if reader is None else False
        source = "drifted" if drift else "current"
    except (OSError, ValueError, KeyError) as exc:
        return {"schema_version": SCHEMA_VERSION, "state": "unavailable", "reason": str(exc),
                "harness_version": None, "qualification_source": None, "required_cases": [],
                "clients": [], "limitations": []}
    clients = []
    for row in data.get("clients", []):
        clients.append({
            "id": row.get("id"), "runtime": row.get("runtime"), "client": row.get("client"),
            "platform": row.get("platform"), "status": row.get("status"),
            "required_for_release": bool(row.get("required_for_release")),
            "runtime_version": row.get("runtime_version"),
            "client_version": row.get("client_version"),
            "records": [{"path": item.get("path"), "sha256": item.get("sha256")}
                        for item in row.get("evidence", []) if isinstance(item, dict)]})
    return {"schema_version": SCHEMA_VERSION, "state": "ready", "reason": None,
            "harness_version": data.get("harness_version"), "qualification_source": source,
            "required_cases": list(data.get("required_cases", [])), "clients": clients,
            "limitations": list(data.get("limitations", []))}


# --- before and after -----------------------------------------------------------------------

def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def metric_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One entry per metric either side reports; `delta` only when both are numbers."""
    out = []
    for key in sorted(set(before) | set(after)):
        a, b = _number(before.get(key)), _number(after.get(key))
        out.append({"key": key, "before": before.get(key), "after": after.get(key),
                    "delta": round(b - a, 3) if a is not None and b is not None else None})
    return out


def _side(row: Mapping[str, Any]) -> Dict[str, Any]:
    side = {"setting": row["setting_label"], "period": row["period"], "status": row["status"],
            "metrics": row["metrics"]}
    if "measuring_day" in row:
        side["measuring_day"] = row["measuring_day"]
    return side


def surface_boundaries(surface: str, *, days: int, home: Path,
                       now: Optional[dt.datetime] = None,
                       item: Optional[str] = None) -> List[Dict[str, Any]]:
    """Each boundary on one surface: the period before it and the period after it, with the
    boundary's source as `periods.py` names it (any string, read generically)."""
    named = periods.periods(surface, item, days=days, home=home, now=now)
    measured = periods.efficacy_rows(surface, item, days=days, home=home, now=now)["rows"]
    out = []
    for index in range(1, min(len(named), len(measured))):
        prev, cur = named[index - 1], named[index]
        if prev["unit"] != cur["unit"]:
            continue
        before, after = measured[index - 1], measured[index]
        out.append({
            "id": "%s:%s:%s" % (surface, cur["unit"], cur["start"]),
            "surface": surface, "item_id": cur["unit"], "item_label": after["item_label"],
            "at": cur["start"], "source": cur["source"], "from": prev["value"], "to": cur["value"],
            "before": _side(before), "after": _side(after),
            "changes": metric_changes(before["metrics"], after["metrics"]),
            "href": "/evals?" + urllib.parse.urlencode(
                {"tab": "before-after", "surface": surface, "item": cur["unit"]})})
    return out


def before_after(home: Path, *, days: int = DEFAULT_DAYS, now: Optional[dt.datetime] = None,
                 surface: Optional[str] = None, item: Optional[str] = None) -> Dict[str, Any]:
    """Every period boundary in the window across surfaces, newest first. A surface whose
    provider fails is named in `surfaces` as `error` and leaves the others standing."""
    names = [surface] if surface else periods.surfaces()
    boundaries: List[Dict[str, Any]] = []
    states: Dict[str, str] = {}
    for name in names:
        try:
            boundaries.extend(surface_boundaries(name, days=days, home=Path(home), now=now,
                                                 item=item))
            states[name] = "ok"
        except periods.UnknownSurface:
            raise
        except Exception:  # noqa: BLE001 -- one provider's failure never blanks the page
            states[name] = "error"
    boundaries.sort(key=lambda b: (b["at"] or "", b["surface"], b["item_id"]), reverse=True)
    return dict(observational(), schema_version=SCHEMA_VERSION, days=days,
                window_days=periods.WINDOW_DAYS, surfaces=states, boundaries=boundaries)


# --- auto-trim ------------------------------------------------------------------------------

def _context_calls(home: Path, days: int,
                   now: Optional[dt.datetime] = None) -> Optional[ContextReading]:
    """Main-thread calls as `{session_id, ts, context_tokens, after_reset}` with the index's
    `indexing` and `pending`, from the transcript index's non-blocking `snapshot(home, *, days,
    now)`; None when that module is not installed. The snapshot answers from its cache at once
    and warms the rest in the background, so a request never waits on a cold index.

    A call is after the reset once its session has seen its first `compact` or `reseed` event:
    the index marks the first call after each event, and everything later in that session
    stays after it.
    """
    try:
        from . import transcripts  # type: ignore[attr-defined]
    except ImportError:
        return None
    snap = transcripts.snapshot(home, days=days, now=now)
    rows = sorted((call for session in snap.get("sessions") or []
                   for call in session.get("calls") or [] if not call.get("sidechain")),
                  key=lambda r: (str(r.get("session_id")), str(r.get("ts"))))
    out: List[ContextCall] = []
    reset: Dict[str, bool] = {}
    for row in rows:
        session_id = str(row.get("session_id"))
        if row.get("event") in RESET_EVENTS:
            reset[session_id] = True
        out.append({"session_id": session_id, "ts": row.get("ts"),
                    "context_tokens": int(row.get("context_tokens") or 0),
                    "after_reset": reset.get(session_id, False)})
    return {"calls": out, "indexing": bool(snap.get("indexing")),
            "pending": int(snap.get("pending") or 0)}


def _summary(values: Sequence[int]) -> Dict[str, Any]:
    if not values:
        return {"calls": 0, "median_context": None, "mean_context": None, "over_warn_share": None}
    return {"calls": len(values), "median_context": int(statistics.median(values)),
            "mean_context": int(round(statistics.mean(values))),
            "over_warn_share": round(sum(1 for v in values if v > CONTEXT_WARN) / len(values), 3)}


def auto_trim(home: Path, *, days: int = DEFAULT_DAYS, now: Optional[dt.datetime] = None,
              reader: Optional[ContextReader] = None) -> Dict[str, Any]:
    """Context per call before and after each session's first reset, over sessions that had
    one. `state` is `ready`, `indexing` (the index is still reading transcripts and no session
    read so far reset), `empty` (fully indexed, no session reset in the window) or
    `unavailable` (no transcript index installed). While the index is still reading, `ready`
    covers the transcripts read so far: `indexing` is true and `reason` says so."""
    base = dict(observational(), schema_version=SCHEMA_VERSION, days=days,
                context_warn=CONTEXT_WARN)
    reading = (reader or _context_calls)(Path(home), days, now)
    if reading is None:
        return dict(base, state="unavailable",
                    reason="The transcript index is not installed in this Studio yet.",
                    indexing=False, pending=0,
                    sessions_with_reset=0, sessions_without_reset=0,
                    before=_summary([]), after=_summary([]), median_change=None, sessions=[])
    indexing = bool(reading.get("indexing"))
    pending = int(reading.get("pending") or 0)
    grouped: Dict[str, List[ContextCall]] = {}
    for call in reading.get("calls") or []:
        grouped.setdefault(str(call["session_id"]), []).append(call)
    before: List[int] = []
    after: List[int] = []
    sessions = []
    without = 0
    for session_id, rows in grouped.items():
        rows = sorted(rows, key=lambda r: str(r["ts"]))
        pre = [int(r["context_tokens"]) for r in rows if not r["after_reset"]]
        post = [int(r["context_tokens"]) for r in rows if r["after_reset"]]
        if not pre or not post:
            without += 1
            continue
        before.extend(pre)
        after.extend(post)
        reset_at = next(r["ts"] for r in rows if r["after_reset"])
        sessions.append({"session_id": session_id, "reset_at": reset_at,
                         "before": _summary(pre), "after": _summary(post)})
    sessions.sort(key=lambda s: str(s["reset_at"]), reverse=True)
    summary_before, summary_after = _summary(before), _summary(after)
    change = (summary_after["median_context"] - summary_before["median_context"]
              if sessions else None)
    if indexing:
        state = "ready" if sessions else "indexing"
        reason: Optional[str] = (
            "Still indexing %d transcript%s; this reading covers the ones read so far."
            % (pending, "" if pending == 1 else "s"))
    else:
        state = "ready" if sessions else "empty"
        reason = None if sessions else "No session in this window reset its context."
    return dict(base, state=state, reason=reason, indexing=indexing, pending=pending,
                sessions_with_reset=len(sessions), sessions_without_reset=without,
                before=summary_before, after=summary_after, median_change=change,
                sessions=sessions[:SESSION_LIMIT])


# --- Home attention -------------------------------------------------------------------------

def ready_items(home: Path, *, now: Optional[dt.datetime] = None,
                days: int = DEFAULT_DAYS) -> List[Dict[str, Any]]:
    """Home's "eval result ready" items: a before and after whose after period finished its
    measuring window in the last `READY_DAYS` days. The item says it is observational."""
    now = now or dt.datetime.now(dt.timezone.utc)
    items = []
    for boundary in before_after(home, days=days, now=now)["boundaries"]:
        at = periods.parse_ts(boundary["at"])
        if at is None or boundary["after"]["status"] != "measured":
            continue
        ready = at + dt.timedelta(days=periods.WINDOW_DAYS)
        if not now - dt.timedelta(days=READY_DAYS) <= ready <= now:
            continue
        items.append({
            "id": "evals:" + boundary["id"], "source": "evals", "kind": "eval-ready",
            "severity": "low",
            "title": "Before and after ready: %s" % boundary["item_label"],
            "detail": "%s to %s, measured for %d days. Observational." % (
                boundary["from"] or "(earlier setting)", boundary["to"], periods.WINDOW_DAYS),
            "at": periods.iso(ready),
            "action": {"label": "Open in Evals", "href": boundary["href"]}})
    return items


# --- routes and the CLI ---------------------------------------------------------------------

def _days(text: Optional[str]) -> Optional[int]:
    text = str(DEFAULT_DAYS) if text is None else text
    if not (text.isascii() and text.isdigit()) or not 1 <= int(text) <= periods.MAX_DAYS:
        return None
    return int(text)


def answer(kind: str, query: Optional[Mapping[str, str]] = None, *, repo: Path,
           home: Optional[Path] = None) -> Tuple[int, Dict[str, Any]]:
    """A route's status and body: 200 with the reader's answer, or 400 naming
    `invalid_request` (an unknown parameter, `days` not 1 to `periods.MAX_DAYS`, an empty
    surface or item) or `unknown_surface`."""
    query = dict(query or {})
    if kind not in KINDS or set(query) - set(QUERY[kind]):
        return 400, {"error": "invalid_request"}
    home = Path(home) if home else periods.default_home()
    if kind == "preregistrations":
        return 200, preregistrations(repo)
    if kind == "evidence":
        return 200, compatibility_evidence(repo)
    days = _days(query.get("days"))
    if days is None:
        return 400, {"error": "invalid_request"}
    if kind == "auto-trim":
        return 200, auto_trim(home, days=days)
    surface, item = query.get("surface"), query.get("item")
    if surface == "" or item == "":
        return 400, {"error": "invalid_request"}
    try:
        return 200, before_after(home, days=days, surface=surface, item=item)
    except periods.UnknownSurface:
        return 400, {"error": "unknown_surface"}
