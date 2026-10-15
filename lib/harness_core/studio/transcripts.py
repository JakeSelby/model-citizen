# SPDX-License-Identifier: MIT
"""Transcript index: per-call context and cost, reset events and skill use (AH-S376, #1328).

Sessions, Skills, Home and Evals read Claude Code's own transcripts, at request time, rather than
anything a hook records: `<home>/.claude/projects/<project>/<session>.jsonl` and the session's
`<session>/subagents/agent-*.jsonl`. The index only ever reads them.

One API response is written as several assistant records, the early ones carrying a partial
streaming `output_tokens`, so calls are deduplicated by message id (or `requestId`) keeping each
field's largest figure, as `policy/hooks/usage-log.py` does. A call's context is the prompt it
sent: input plus cache read plus cache write. Its cost comes from `policy/hooks/pricing.py`
`row_cost`, through the loader the Roles surface shares; an unpriceable call has cost None and
leaves its session's `cost_complete` false rather than reading low.

A reset is a `compact_boundary` system record; a reseed is the harness's SessionStart context
carrying `RESET_MARK`. Each is stamped on the first main-thread call after it, so a context series
shows where it fell. Loaded skills are the `skill_listing` attachments' names; invoked skills are
`Skill` tool calls and slash commands naming a loaded skill.

Each transcript's index is cached as JSON under `cache_dir(home)`, keyed on the stamps (mtime and
size) of its file and its subagent files and on the price table, so an unchanged transcript is
answered without being opened. A cold read of a month of real transcripts takes tens of seconds,
so a page reads `snapshot`, which answers from the cache at once and warms the rest on a daemon
thread.
"""
from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from . import periods

CONTEXT_WARN = 160000
CONTEXT_LIMIT = 400000
CACHE_VERSION = 1
# The opening of `RESET_LINE` in `policy/hooks/harness-session.py`; a test keeps the two in step.
RESET_MARK = "[harness: Your context was reset automatically"
SYNTHETIC = "<synthetic>"
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMMAND = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
FIELDS = (("input", "input_tokens"), ("cache_read", "cache_read_input_tokens"),
          ("cache_write", "cache_creation_input_tokens"), ("output", "output_tokens"))
TIERS = (("cache_write_5m", "ephemeral_5m_input_tokens"),
         ("cache_write_1h", "ephemeral_1h_input_tokens"))
TOKEN_NAMES = tuple(name for name, _ in FIELDS + TIERS)


def projects_dir(home: Path) -> Path:
    return Path(home) / ".claude" / "projects"


def cache_dir(home: Path) -> Path:
    return periods.state_dir(home) / "studio" / "transcripts"


# --- public reads ------------------------------------------------------------------------------

def sessions(home: Path, *, days: int = 30, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Every main transcript modified inside the window, indexed, newest first."""
    table = _prices(home)
    found = [index_file(home, path, table) for path in _window(home, days, now)]
    found.sort(key=lambda s: (s.get("ended") or "", s["session_id"]), reverse=True)
    return found


def _window(home: Path, days: int, now: Optional[dt.datetime]) -> List[Path]:
    cutoff = _cutoff(days, now)
    chosen = []
    for path in _main_files(home):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if dt.datetime.fromtimestamp(mtime, dt.timezone.utc) >= cutoff:
            chosen.append(path)
    return chosen


def session(home: Path, session_id: str) -> Optional[Dict[str, Any]]:
    """One session by id, from any project, or None when the id is malformed or unknown."""
    if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
        return None
    root = projects_dir(home)
    try:
        projects = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return None
    for project in projects:
        path = project / (session_id + ".jsonl")
        if path.is_file():
            return index_file(home, path)
    return None


def calls(home: Path, *, days: int = 30, now: Optional[dt.datetime] = None,
          session_id: Optional[str] = None, sidechain: bool = False) -> List[Dict[str, Any]]:
    """Calls in time order: main-thread only unless `sidechain`, one session when named."""
    if session_id is not None:
        one = session(home, session_id)
        chosen = [one] if one else []
    else:
        chosen = sessions(home, days=days, now=now)
    rows = [call for s in chosen for call in s["calls"] if sidechain or not call["sidechain"]]
    rows.sort(key=lambda c: (c["ts"] or "", c["session_id"]))
    return rows


def events(home: Path, *, days: int = 30, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    rows = [event for s in sessions(home, days=days, now=now) for event in s["events"]]
    rows.sort(key=lambda e: (e["ts"] or "", e["session_id"]))
    return rows


def skill_usage(home: Path, *, days: int = 30,
                now: Optional[dt.datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Per skill: the sessions that loaded it, the sessions that invoked it, and its call count."""
    usage: Dict[str, Dict[str, Any]] = {}
    for s in sessions(home, days=days, now=now):
        for name in s["skills_loaded"]:
            usage.setdefault(name, {"loaded": [], "invoked": [], "calls": 0})["loaded"].append(s["session_id"])
        for name, count in s["skills_invoked"].items():
            entry = usage.setdefault(name, {"loaded": [], "invoked": [], "calls": 0})
            entry["invoked"].append(s["session_id"])
            entry["calls"] += count
    return dict(sorted(usage.items()))


# --- one transcript ----------------------------------------------------------------------------

def index_file(home: Path, path: Path, table: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The index of one main transcript, from the cache when nothing it was built from changed."""
    path = Path(path)
    table = _prices(home) if table is None else table
    key, cache, subagents = _cache_key(home, path, table)
    hit = _cached(cache, key)
    if hit is not None:
        return hit
    built = build(path, subagents, table)
    _write_cache(cache, {"key": key, "session": built})
    return built


def cached_file(home: Path, path: Path, table: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The cached index of one transcript when it is current, else None. Never parses."""
    key, cache, _ = _cache_key(home, Path(path), table)
    return _cached(cache, key)


def _cache_key(home: Path, path: Path, table: Mapping[str, Any]):
    subagents = _subagent_files(path)
    key = {"version": CACHE_VERSION, "path": str(path),
           "stamps": [_stamp(p) for p in [path] + subagents],
           "prices": hashlib.sha256(json.dumps(table, sort_keys=True, default=str)
                                    .encode("utf-8")).hexdigest()}
    cache = cache_dir(home) / (hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32] + ".json")
    return key, cache, subagents


def _cached(cache: Path, key: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    data = _read_cache(cache)
    return data["session"] if data is not None and data.get("key") == key else None


# --- non-blocking reads ------------------------------------------------------------------------

_WARMING: Dict[str, threading.Thread] = {}
_WARM_LOCK = threading.Lock()


def snapshot(home: Path, *, days: int = 30, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """What the cache already holds for the window, never parsing on the caller's thread.

    `{"sessions": [...], "indexing": bool, "pending": int}`: when any transcript in the window is
    not cached yet, a background warm starts (or continues) and `indexing` is true, so a page can
    answer at once and say it is still indexing instead of blocking on a cold read.
    """
    table = _prices(home)
    found, pending = [], 0
    for path in _window(home, days, now):
        hit = cached_file(home, path, table)
        if hit is None:
            pending += 1
        else:
            found.append(hit)
    found.sort(key=lambda s: (s.get("ended") or "", s["session_id"]), reverse=True)
    if pending:
        warm(home, days=days, now=now)
    return {"sessions": found, "indexing": bool(pending) or warming(home), "pending": pending}


def warm(home: Path, *, days: int = 30, now: Optional[dt.datetime] = None) -> bool:
    """Index the window on a daemon thread, one per home at a time; True when one started."""
    name = str(Path(home))
    with _WARM_LOCK:
        running = _WARMING.get(name)
        if running is not None and running.is_alive():
            return False
        thread = threading.Thread(target=_warm, args=(Path(home), days, now),
                                  name="studio-transcripts-warm", daemon=True)
        _WARMING[name] = thread
        thread.start()
        return True


def warming(home: Path) -> bool:
    thread = _WARMING.get(str(Path(home)))
    return thread is not None and thread.is_alive()


def _warm(home: Path, days: int, now: Optional[dt.datetime]) -> None:
    try:
        sessions(home, days=days, now=now)
    except Exception:
        pass  # A failed warm leaves the cache as it was; the next request tries again.


def build(path: Path, subagents: Iterable[Path] = (),
          table: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Index one transcript and its subagent files. Never raises on a malformed line."""
    path = Path(path)
    state = _State(path.stem)
    _read(path, state, agent_id=None)
    for sub in subagents:
        stem = Path(sub).stem
        _read(Path(sub), state, agent_id=stem[len("agent-"):] if stem.startswith("agent-") else stem)
    return _finish(state, path, table or {})


class _State:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.slots: Dict[Any, Dict[str, Any]] = {}
        self.links: Dict[str, Any] = {}
        self.order: List[Any] = []
        self.events: List[Dict[str, Any]] = []
        self.pending: Optional[str] = None
        self.turn_starts: List[str] = []
        self.loaded: set = set()
        self.invoked: Dict[str, int] = {}
        self.tool_ids: set = set()
        self.commands: List[str] = []
        self.title: Optional[str] = None
        self.cwd: Optional[str] = None
        self.stamps: List[str] = []
        self.malformed = 0


def _read(path: Path, state: _State, agent_id: Optional[str]) -> None:
    try:
        stream = open(str(path), encoding="utf-8", errors="replace")
    except OSError:
        return
    with stream:
        for number, line in enumerate(stream):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                state.malformed += 1
                continue
            if not isinstance(entry, dict):
                state.malformed += 1
                continue
            _entry(entry, state, agent_id, (str(path), number))


def _entry(entry: Dict[str, Any], state: _State, agent_id: Optional[str], where: Tuple[str, int]) -> None:
    kind = entry.get("type")
    ts = _ts(entry.get("timestamp"))
    side = agent_id is not None or bool(entry.get("isSidechain"))
    if ts:
        state.stamps.append(ts)
    if state.cwd is None and isinstance(entry.get("cwd"), str) and not side:
        state.cwd = entry["cwd"]
    if kind == "custom-title" and isinstance(entry.get("customTitle"), str):
        state.title = entry["customTitle"]
    elif kind == "system" and entry.get("subtype") == "compact_boundary" and not side:
        meta = entry.get("compactMetadata") if isinstance(entry.get("compactMetadata"), dict) else {}
        trigger = meta.get("trigger") if meta.get("trigger") in ("auto", "manual") else None
        state.events.append({"session_id": state.session_id, "ts": ts, "kind": "compact",
                             "trigger": trigger, "pre_tokens": _int(meta.get("preTokens")),
                             "post_tokens": _int(meta.get("postTokens"))})
        state.pending = state.pending or "compact"
    elif kind == "attachment" and isinstance(entry.get("attachment"), dict):
        _attachment(entry["attachment"], state, ts, side)
    elif kind == "user" and not side:
        _user(entry, state, ts)
    elif kind == "assistant":
        _assistant(entry, state, ts, agent_id, side, where)


def _attachment(item: Dict[str, Any], state: _State, ts: Optional[str], side: bool) -> None:
    if item.get("type") == "skill_listing" and not side:
        names = item.get("names")
        if isinstance(names, list):
            state.loaded.update(n for n in names if isinstance(n, str) and n)
    elif item.get("type") == "hook_additional_context" and item.get("hookEvent") == "SessionStart" \
            and not side:
        content = item.get("content")
        texts = content if isinstance(content, list) else [content]
        if any(isinstance(t, str) and RESET_MARK in t for t in texts):
            state.events.append({"session_id": state.session_id, "ts": ts, "kind": "reseed",
                                 "trigger": None, "pre_tokens": None, "post_tokens": None})
            state.pending = "reseed"


def _user(entry: Dict[str, Any], state: _State, ts: Optional[str]) -> None:
    """A prompt the user sent opens a turn; tool results and meta records do not."""
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    elif isinstance(content, str):
        text = content
    else:
        return
    state.commands.extend(COMMAND.findall(text))
    state.turn_starts.append(ts or (state.turn_starts[-1] if state.turn_starts else ""))


def _assistant(entry: Dict[str, Any], state: _State, ts: Optional[str], agent_id: Optional[str],
               side: bool, where: Tuple[str, int]) -> None:
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    model = message.get("model") if isinstance(message.get("model"), str) else ""
    if model == SYNTHETIC:
        return
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
            ident = block.get("id") or (where, len(state.tool_ids))
            skill = (block.get("input") or {}).get("skill") if isinstance(block.get("input"), dict) else None
            if isinstance(skill, str) and skill and ident not in state.tool_ids:
                state.tool_ids.add(ident)
                state.invoked[skill] = state.invoked.get(skill, 0) + 1
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    key = _key(state, message.get("id"), entry.get("requestId"), where)
    slot = state.slots.get(key)
    if slot is None:
        slot = dict((name, 0) for name in TOKEN_NAMES)
        slot.update({"session_id": state.session_id, "ts": ts, "model": model,
                     "message_id": message.get("id") if isinstance(message.get("id"), str) else None,
                     "event": None, "sidechain": side, "agent_id": agent_id})
        if not side and state.pending:
            slot["event"], state.pending = state.pending, None
        state.slots[key] = slot
        state.order.append(key)
    if model and not slot["model"]:
        slot["model"] = model
    if slot["message_id"] is None and isinstance(message.get("id"), str) and message["id"]:
        slot["message_id"] = message["id"]
    for name, field in FIELDS:
        slot[name] = max(slot[name], _int(usage.get(field)) or 0)
    tiers = usage.get("cache_creation")
    if isinstance(tiers, dict):
        for name, field in TIERS:
            slot[name] = max(slot[name], _int(tiers.get(field)) or 0)


def _key(state: _State, mid: Any, request_id: Any, where: Tuple[str, int]) -> Any:
    """Message id, else request id; a record with neither is its own call, never merged."""
    mid = mid if isinstance(mid, str) and mid else None
    rid = request_id.strip() if isinstance(request_id, str) else ""
    if mid:
        if rid:
            linked = state.links.get(rid)
            if isinstance(linked, tuple) and linked in state.slots and mid not in state.slots:
                state.slots[mid] = state.slots.pop(linked)
                state.order[state.order.index(linked)] = mid
            state.links[rid] = mid
        return mid
    if rid:
        return state.links.setdefault(rid, ("request", rid))
    return ("line",) + where


def _finish(state: _State, path: Path, table: Mapping[str, Any]) -> Dict[str, Any]:
    starts = state.turn_starts
    rows = []
    for key in state.order:
        slot = state.slots[key]
        call = dict(slot)
        call["context_tokens"] = slot["input"] + slot["cache_read"] + slot["cache_write"]
        call["cost"] = _cost(call, table)
        call["turn"] = max(bisect.bisect_right(starts, call["ts"] or "") - 1, 0) if starts else 0
        rows.append(call)
    rows.sort(key=lambda c: (c["ts"] or "", c["sidechain"]))
    main = [c for c in rows if not c["sidechain"]]
    contexts = [c["context_tokens"] for c in main]
    turns = []
    for number in range(max(len(starts), 1 if rows else 0)):
        mine = [c for c in rows if c["turn"] == number]
        priced = [c["cost"] for c in mine if c["cost"] is not None]
        turns.append({"turn": number, "ts": starts[number] if number < len(starts) else (mine[0]["ts"] if mine else None),
                      "calls": len(mine), "cost": round(sum(priced), 6) if priced else None,
                      "cost_complete": len(priced) == len(mine),
                      "max_context": max([c["context_tokens"] for c in mine if not c["sidechain"]] or [0])})
    priced = [c["cost"] for c in rows if c["cost"] is not None]
    models = []
    for c in rows:
        if c["model"] and c["model"] not in models:
            models.append(c["model"])
    loaded = sorted(state.loaded)
    invoked = dict(state.invoked)
    for name in state.commands:
        if name in state.loaded:
            invoked[name] = invoked.get(name, 0) + 1
    stamps = sorted(state.stamps)
    return {
        "session_id": state.session_id, "path": str(path), "project": path.parent.name,
        "cwd": state.cwd, "title": state.title,
        "started": stamps[0] if stamps else None, "ended": stamps[-1] if stamps else None,
        "models": models, "calls": rows, "events": sorted(state.events, key=lambda e: e["ts"] or ""),
        "turns": turns,
        "cost": round(sum(priced), 6) if priced else None, "cost_complete": len(priced) == len(rows),
        "max_context": max(contexts) if contexts else 0,
        "median_context": int(round(statistics.median(contexts))) if contexts else 0,
        "over_warn": sum(1 for c in contexts if c > CONTEXT_WARN),
        "over_limit": sum(1 for c in contexts if c > CONTEXT_LIMIT),
        "skills_loaded": loaded, "skills_invoked": dict(sorted(invoked.items())),
        "malformed_lines": state.malformed,
    }



# --- helpers -----------------------------------------------------------------------------------

def _cost(call: Mapping[str, Any], table: Mapping[str, Any]) -> Optional[float]:
    from .surfaces import roles
    if not table or not call.get("model"):
        return None
    row = {"model": call["model"], "max_prompt": call["context_tokens"]}
    row.update((name, call[name]) for name in TOKEN_NAMES)
    cost = roles.row_cost(row, table)
    if cost is not None:
        return cost
    # `row_cost` reads `max_prompt` as the largest of many requests, so a prompt over a model's
    # long-context threshold is unpriced there. One call is one request: its tier is known.
    module = roles.pricing()
    try:
        rate = module.prompt_rate(module.price_for(table, call["model"]), call["context_tokens"])
        tokens = module.row_tokens(row, rate) if rate else None
        return module.tokens_cost(tokens, rate) if tokens else None
    except Exception:
        return None


def _prices(home: Path) -> Dict[str, Any]:
    from .surfaces import roles
    return roles.price_table(periods._config(home))


def _main_files(home: Path) -> List[Path]:
    root = projects_dir(home)
    try:
        projects = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    files = []
    for project in projects:
        try:
            files.extend(p for p in project.iterdir() if p.suffix == ".jsonl" and p.is_file())
        except OSError:
            continue
    return sorted(files)


def _subagent_files(path: Path) -> List[Path]:
    folder = path.parent / path.stem / "subagents"
    try:
        return sorted(p for p in folder.iterdir() if p.name.startswith("agent-") and p.suffix == ".jsonl")
    except OSError:
        return []


def _stamp(path: Path) -> Optional[List[Any]]:
    try:
        info = path.stat()
    except OSError:
        return None
    return [path.name, info.st_mtime_ns, info.st_size]


def _read_cache(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(str(path), encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("session"), dict) else None


def _write_cache(path: Path, data: Dict[str, Any]) -> None:
    """Best effort: an unwritable cache costs a re-read next time and nothing else."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, separators=(",", ":"))
            os.replace(temporary, str(path))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except (OSError, TypeError, ValueError):
        pass


def _cutoff(days: int, now: Optional[dt.datetime]) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now - dt.timedelta(days=days)


def _ts(value: Any) -> Optional[str]:
    parsed = periods.parse_ts(value)
    return periods.iso(parsed) if parsed else None


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
