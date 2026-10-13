# SPDX-License-Identifier: MIT
"""Setting periods: which setting each Harness item ran under, and when (AH-S369, #1321).

A Harness page's Efficacy tab shows one row per item per period, so a change can be read as a
before and an after. Nothing new is recorded for this: periods are derived at request time from
logs the harness already writes, and every one of them is only read.

A period boundary comes from one of three sources, in order of precision:

- **applies**: a completed apply in the Studio's journal (`apply.journal_path`) whose config
  commands name the item's config key. Its time is exact, its before value is read from the
  journal's `prior_config`;
- **config-git**: a commit of the user's `config.json`, when that file is tracked in git, whose
  version names a different value for the item than the version before it;
- **usage**: usage rows stamped with the item's value. Values are compared within one profile
  stream (a row kind, local or bridge), because streams interleave. A transition A to B counts only
  when A stops appearing before B starts, within `TOLERANCE`; when both stay live it is overlap
  (concurrent profiles, a worker stamped with its start profile) and starts no period.

The profile fingerprint is never a boundary: most of its changes name no setting. A surface with
no boundary in the window returns one period per item and `NOTE`.

Each surface is a provider registered with `register_surface`; a provider module under
`studio/surfaces/` registers itself on import, so a new surface adds a module and touches nothing
here.
"""
from __future__ import annotations

import base64
import datetime as dt
import importlib
import json
import os
import pkgutil
import subprocess
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

TOLERANCE = dt.timedelta(hours=2)
# A usage boundary this close to an applies or config-git boundary to the same value is the same
# change seen late: the first session under it ends hours after the change was made.
SAME_CHANGE = dt.timedelta(days=1)
WINDOW_DAYS = 7
MAX_DAYS = 365
GIT_TIMEOUT = 5.0
TERMINAL = ("completed", "recovered")
NOTE = ("No setting change was recorded for these items in this window, so each shows one "
        "period. Settings changed through the Studio's Changes flow start new periods.")

# The command that prints `GET /api/harness/periods`; `answer` serves both.
CLI_COMMAND = ("citizen", "harness", "periods", "--surface", "{surface}", "--days", "{days}",
               "--json")

Observation = Tuple[dt.datetime, str, str]  # (time, value, profile stream)


class UnknownSurface(ValueError):
    """The surface names no registered provider."""


@dataclass(frozen=True)
class Surface:
    name: str
    items_fn: Callable[["Context"], Dict[str, Dict[str, Any]]]
    metrics_fn: Callable[[Sequence[Mapping[str, Any]], str, str], Dict[str, Any]]
    config_key: Optional[Callable[[str], Optional[str]]] = None
    config_value: Optional[Callable[[Mapping[str, Any], str], Optional[str]]] = None


@dataclass
class Context:
    """What a provider reads: usage rows in the window and the current configuration."""
    home: Path
    now: dt.datetime
    cutoff: dt.datetime
    usage: List[Dict[str, Any]]
    config: Dict[str, Any] = field(default_factory=dict)


_REGISTRY: Dict[str, Surface] = {}
_LOADED = False
_LOCK = threading.Lock()
_CACHE: Dict[Any, Any] = {}


def register_surface(name: str, items_fn, metrics_fn, *, config_key=None,
                     config_value=None) -> None:
    """Register a surface. `items_fn(context)` returns `{unit: {"label", "observations",
    "current"}}`, observations being `(time, value, stream)`; `metrics_fn(rows, unit, value)`
    returns one period's metrics. `config_key(unit)` names the dotted key an apply writes, and
    `config_value(config, unit)` reads the unit's value from one version of `config.json`."""
    _REGISTRY[name] = Surface(name, items_fn, metrics_fn, config_key, config_value)


def surfaces() -> List[str]:
    _load_providers()
    return sorted(_REGISTRY)


def _load_providers() -> None:
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        from . import surfaces as package
        for info in pkgutil.iter_modules(package.__path__):
            importlib.import_module(package.__name__ + "." + info.name)
        _LOADED = True


def default_home() -> Path:
    return Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())


def state_dir(home: Path) -> Path:
    return Path(home) / ".local" / "state" / "agent-harness"


def config_path(home: Path) -> Path:
    return Path(home) / ".config" / "agent-harness" / "config.json"


def parse_ts(text: Any) -> Optional[dt.datetime]:
    if not isinstance(text, str) or not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def iso(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stream_of(row: Mapping[str, Any]) -> str:
    bridge = "bridge" if str(row.get("repo") or "").startswith("bridge-") else "local"
    return "%s/%s" % (row.get("kind") or "session", bridge)


# --- cached reads ---------------------------------------------------------------------------

def _stamp(path: Path) -> Optional[Tuple[int, int]]:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def _cached(key: Any, compute: Callable[[], Any]) -> Any:
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    value = compute()
    with _LOCK:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = value
    return value


def _jsonl(path: Path, ts_key: str) -> List[Dict[str, Any]]:
    """Every parsed row with a readable `ts_key`, oldest first, keyed on the file's mtime."""
    def read() -> List[Dict[str, Any]]:
        rows = []
        try:
            handle = path.open("rb")
        except OSError:
            return rows
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = parse_ts(row.get(ts_key))
                if ts is not None:
                    row["_ts"] = ts
                    rows.append(row)
        rows.sort(key=lambda r: r["_ts"])
        return rows
    stamp = _stamp(path)
    return [] if stamp is None else _cached(("jsonl", str(path), stamp, ts_key), read)


def _config(home: Path) -> Dict[str, Any]:
    path = config_path(home)
    stamp = _stamp(path)
    if stamp is None:
        return {}

    def read() -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    return _cached(("config", str(path), stamp), read)


# --- boundaries -----------------------------------------------------------------------------

def _boundary(at: dt.datetime, before: Optional[str], after: str, source: str) -> Dict[str, Any]:
    return {"at": at, "from": before, "to": after, "source": source}


def usage_boundaries(observations: Sequence[Observation]) -> Tuple[List[Dict[str, Any]], int]:
    """Clean value changes per profile stream, merged across streams; and the overlap count."""
    by_stream: Dict[str, Dict[str, List[Any]]] = {}
    for ts, value, stream in observations:
        spans = by_stream.setdefault(stream, {})
        span = spans.get(value)
        if span is None:
            spans[value] = [ts, ts]
        else:
            span[0] = min(span[0], ts)
            span[1] = max(span[1], ts)
    clean: List[Dict[str, Any]] = []
    overlaps = 0
    for spans in by_stream.values():
        ordered = sorted(spans.items(), key=lambda kv: kv[1][0])
        for (a, sa), (b, sb) in zip(ordered, ordered[1:]):
            if sa[1] <= sb[0] + TOLERANCE:
                clean.append(_boundary(sb[0], a, b, "usage"))
            else:
                overlaps += 1
    return clean, overlaps


def applies_boundaries(home: Path, surface: Surface, unit: str,
                       cutoff: dt.datetime) -> List[Dict[str, Any]]:
    """Completed applies whose config commands set this unit's key."""
    if surface.config_key is None:
        return []
    key = surface.config_key(unit)
    if not key:
        return []
    rows = _jsonl(state_dir(home) / "applies.jsonl", "ts")
    intents = {row.get("apply_id"): row for row in rows if row.get("phase") == "intent"}
    out = []
    for row in rows:
        if row.get("phase") not in TERMINAL or row["_ts"] < cutoff:
            continue
        intent = intents.get(row.get("apply_id"))
        if not intent:
            continue
        for command in intent.get("config") or ():
            if not isinstance(command, dict) or command.get("key") != key:
                continue
            after = ("(unset)" if command.get("action") == "unset"
                     else command.get("applied", command.get("value")))
            before = None
            if surface.config_value is not None and intent.get("prior_config"):
                try:
                    prior = json.loads(base64.b64decode(intent["prior_config"]).decode("utf-8"))
                    before = surface.config_value(prior, unit) if isinstance(prior, dict) else None
                except (ValueError, UnicodeError):
                    before = None
            out.append(_boundary(row["_ts"], before, _text(after), "applies"))
    return out


def _git_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(directory: Path, *argv: str) -> Optional[str]:
    try:
        done = subprocess.run(("git", "-C", str(directory)) + argv, capture_output=True,
                              text=True, timeout=GIT_TIMEOUT, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _git_dir_stamp(path: Path) -> Optional[Tuple[Any, ...]]:
    for parent in [path.parent] + list(path.parent.parents):
        marker = parent / ".git"
        if marker.exists():
            return (str(marker), _stamp(marker / "index") if marker.is_dir() else _stamp(marker))
    return None


def config_versions(home: Path) -> List[Tuple[dt.datetime, Dict[str, Any]]]:
    """Every committed version of `config.json`, oldest first; empty when it is not in git."""
    path = config_path(home)
    git_stamp = _git_dir_stamp(path)
    if git_stamp is None or not path.is_file():
        return []

    def read() -> List[Tuple[dt.datetime, Dict[str, Any]]]:
        log = _git(path.parent, "log", "--format=%H %cI", "--", path.name)
        prefix = _git(path.parent, "rev-parse", "--show-prefix")
        if not log or prefix is None:
            return []
        versions = []
        for line in reversed(log.splitlines()):
            sha, _sep, when = line.partition(" ")
            ts = parse_ts(when)
            text = _git(path.parent, "show", "%s:%s%s" % (sha, prefix.strip(), path.name))
            if ts is None or text is None:
                continue
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict):
                versions.append((ts, value))
        return versions
    return _cached(("config-git", str(path), git_stamp, _stamp(path)), read)


def config_git_boundaries(home: Path, surface: Surface, unit: str,
                          cutoff: dt.datetime) -> List[Dict[str, Any]]:
    if surface.config_value is None:
        return []
    out = []
    previous = None
    for index, (ts, config) in enumerate(config_versions(home)):
        value = surface.config_value(config, unit) or "(unset)"
        if index and value != previous and ts >= cutoff:
            out.append(_boundary(ts, previous, value, "config-git"))
        previous = value
    return out


def _text(value: Any) -> str:
    if value is None:
        return "(unset)"
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def merge_boundaries(named: Sequence[Dict[str, Any]],
                     observed: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Named boundaries win; a usage boundary is kept only when no named one explains it."""
    kept = list(named)
    for item in observed:
        if not any(n["to"] == item["to"] and abs(n["at"] - item["at"]) <= SAME_CHANGE
                   for n in named):
            kept.append(item)
    kept.sort(key=lambda b: b["at"])
    merged: List[Dict[str, Any]] = []
    for item in kept:
        if merged and merged[-1]["to"] == item["to"]:
            continue  # the same change seen twice, or two streams catching up
        merged.append(item)
    return merged


# --- periods --------------------------------------------------------------------------------

def _context(home: Path, days: int, now: Optional[dt.datetime]) -> Context:
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    usage = [row for row in _jsonl(state_dir(home) / "usage.jsonl", "ended")
             if row["_ts"] >= cutoff]
    return Context(home=Path(home), now=now, cutoff=cutoff, usage=usage, config=_config(home))


def _surface(name: str) -> Surface:
    _load_providers()
    found = _REGISTRY.get(name)
    if found is None:
        raise UnknownSurface(name)
    return found


def _unit_periods(surface: Surface, unit: str, item: Mapping[str, Any],
                  ctx: Context) -> List[Dict[str, Any]]:
    observations: List[Observation] = sorted(item.get("observations") or (), key=lambda o: o[0])
    observed, _overlaps = usage_boundaries(observations)
    named = (applies_boundaries(ctx.home, surface, unit, ctx.cutoff)
             + config_git_boundaries(ctx.home, surface, unit, ctx.cutoff))
    boundaries = merge_boundaries(named, observed)
    times = [o[0] for o in observations]
    current = item.get("current")
    if boundaries:
        first_value = boundaries[0]["from"] or _value_before(observations, boundaries[0]["at"])
    else:
        first_value = current or _most_common(observations) or "(unset)"
    start = times[0] if times else ctx.cutoff
    if boundaries and boundaries[0]["at"] <= start:
        start = ctx.cutoff
    spans = []
    value, source = first_value, boundaries[0]["source"] if boundaries else "none"
    for boundary in boundaries:
        spans.append((start, boundary["at"], value, source))
        start, value, source = boundary["at"], boundary["to"], boundary["source"]
    spans.append((start, None, value, source))
    out = []
    for begin, end, value, source in spans:
        inside = [o for o in observations if o[0] >= begin and (end is None or o[0] < end)]
        out.append({"surface": surface.name, "unit": unit, "label": item.get("label") or unit,
                    "value": value, "start": iso(begin), "end": iso(end), "source": source,
                    "rows": len(inside), "attributable": bool(boundaries),
                    "overlaps": sum(1 for o in inside if o[1] != value),
                    "_start": begin, "_end": end})
    return out


def _value_before(observations: Sequence[Observation], at: dt.datetime) -> str:
    before = [o[1] for o in observations if o[0] < at]
    return before[-1] if before else "(earlier setting)"


def _most_common(observations: Sequence[Observation]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for _ts, value, _stream in observations:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda v: counts[v]) if counts else None


def _compute(name: str, unit: Optional[str], days: int, home: Path,
             now: Optional[dt.datetime]) -> Tuple[List[Dict[str, Any]], Context]:
    surface = _surface(name)
    days = max(1, min(int(days), MAX_DAYS))
    ctx = _context(home, days, now)
    items = surface.items_fn(ctx)
    rows: List[Dict[str, Any]] = []
    for key in sorted(items):
        if unit is None or key == unit:
            rows.extend(_unit_periods(surface, key, items[key], ctx))
    return rows, ctx


def _public(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_") and k != "label"}


def periods(surface: str, unit: Optional[str] = None, *, days: int = 30,
            home: Optional[Path] = None, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Each item's periods: `surface, unit, value, start, end|None, source, rows, attributable,
    overlaps`, oldest first per item. Raises `UnknownSurface`."""
    rows, _ctx = _compute(surface, unit, days, Path(home) if home else default_home(), now)
    return [_public(row) for row in rows]


def efficacy_rows(surface: str, unit: Optional[str] = None, *, days: int = 30,
                  home: Optional[Path] = None,
                  now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The `/api/harness/periods` answer: `{surface, rows, note?}`, rows in the Studio frontend's
    `PeriodRow` shape. A period that started under `WINDOW_DAYS` ago is still measuring."""
    rows, ctx = _compute(surface, unit, days, Path(home) if home else default_home(), now)
    provider = _surface(surface)
    out = []
    for row in rows:
        begin, end = row["_start"], row["_end"]
        inside = [r for r in ctx.usage if r["_ts"] >= begin and (end is None or r["_ts"] < end)]
        age = ctx.now - begin
        measuring = age < dt.timedelta(days=WINDOW_DAYS)
        item = {"item_id": row["unit"], "item_label": row["label"], "setting_label": row["value"],
                "period": {"start": row["start"], "end": row["end"]},
                "status": "measuring" if measuring else "measured", "window_days": WINDOW_DAYS,
                "metrics": provider.metrics_fn(inside, row["unit"], row["value"]),
                "evals_href": "/evals?" + urllib.parse.urlencode(
                    {"surface": surface, "item": row["unit"]})}
        if measuring:
            item["measuring_day"] = max(1, min(WINDOW_DAYS, age.days + 1))
        out.append(item)
    response: Dict[str, Any] = {"surface": surface, "rows": out}
    if not any(row["attributable"] for row in rows):
        response["note"] = NOTE
    return response


def answer(surface: Optional[str], days: Optional[str] = None, *,
           home: Optional[Path] = None) -> Tuple[int, Dict[str, Any]]:
    """The route's status and body for its query text: `efficacy_rows`, or a 400 naming
    `invalid_request` (no surface, `days` not 1 to `MAX_DAYS`) or `unknown_surface`."""
    days = "30" if days is None else days
    if (not surface or not (days.isascii() and days.isdigit())
            or not 1 <= int(days) <= MAX_DAYS):
        return 400, {"error": "invalid_request"}
    try:
        return 200, efficacy_rows(surface, days=int(days), home=home)
    except UnknownSurface:
        return 400, {"error": "unknown_surface"}


def usage_metrics(rows: Sequence[Mapping[str, Any]], _unit: str = "",
                  _value: str = "") -> Dict[str, Any]:
    """The shared usage figures for one period: sessions, agents, output and cache-read share."""
    sessions = sum(1 for r in rows if (r.get("kind") or "session") == "session")
    tokens = {k: sum(int(r.get(k) or 0) for r in rows) for k in ("input", "output", "cache_read",
                                                                  "cache_write")}
    read_side = tokens["input"] + tokens["cache_read"] + tokens["cache_write"]
    return {"sessions": sessions, "agents": len(rows) - sessions, "output_tokens": tokens["output"],
            "cache_read_share": round(tokens["cache_read"] / read_side, 3) if read_side else 0}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
