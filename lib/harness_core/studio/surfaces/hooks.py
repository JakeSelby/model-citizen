# SPDX-License-Identifier: MIT
"""Hooks: one item per hook module, its decisions read from `decisions.jsonl` (AH-S372, #1324).

Rows group by the decision's `module` (`hooks/<id>`) and list the points it wrote under. A point
no hook owns, such as `evasion-deny`, is an item of its own: no switch turns it off.

Liveness is computed at request time, against the request's `now`:

- **off**: the hook's selection switch is `off`;
- **not observable**: the hook never writes a decision (`decisions.POINT_MODULES` names no point
  for it), so its silence says nothing; never labelled silent;
- **not recorded**: `telemetry.decisions` is off, so no hook writes anything;
- **erroring**: a decision in the window carries an `error` or `timed_out` fact;
- **silent**: enabled and observable, with no decision in `SILENT_DAYS`;
- **live** otherwise.

The rotated logs are read too (`decision_rows`): a rotation an hour ago must not make every hook
look silent.

`periods.efficacy_rows` hands a provider's metrics function only the usage rows of a period, and
a hook's figures come from decisions, so `items` keeps each unit's decisions for the request on
this thread and `metrics` reads them. Hooks register no `config_key`, so each item has exactly one
period, the whole window, and those decisions are exactly the period's.

`detail` is one hook's page (`GET /api/harness/hook`, `citizen harness hook NAME`): that row,
its decisions by day and outcome, its recent decisions with session and input hash, and its
settings.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ... import catalog
from .. import periods

SILENT_DAYS = 7
MAX_ROTATED = 20
BLOCK_ANSWERS = ("deny", "blocked", "declined")
WARN_ANSWERS = ("ask", "warn", "would-deny", "note", "nudge", "cap", "budget", "cap+budget")
ERROR_FACTS = ("error", "timed_out")
POINT_PREFIX = "point:"
LABELS = {
    "allow-plan-webfetch": "Allow web research in plan mode",
    "allow-readonly-bash": "Read-only shell allowlist",
    "approvals": "Approval codes",
    "brief-guard": "Subagent brief budget",
    "filter-output": "Trim noisy command output",
    "grade-bash": "Command grader",
    "harness-session": "Session context",
    "intent-overlap": "One session per file",
    "neutralize-tool-output": "Mark untrusted tool output",
    "pre-compact": "Snapshot before compaction",
    "session-caps": "Context and cost caps",
    "stage-user-files": "Stage files you attach",
    "steer-polling": "Background long waits",
    "stop-gate": "Quality gate before a turn ends",
    "tier-agent-spawns": "Tier subagent spawns",
    "usage-feed": "Usage feed",
    "usage-log": "Usage ledger",
    "validate-plan-card": "Plan has a Review Card",
    "workspace-session": "Workspace context",
}

_REQUEST = threading.local()


def _decisions_module() -> Optional[Any]:
    path = Path(__file__).resolve().parents[4] / "policy" / "hooks" / "decisions.py"
    try:
        spec = importlib.util.spec_from_file_location("harness_studio_hook_decisions", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


_POINT_MODULES = None  # type: Optional[Dict[str, Optional[str]]]


def point_modules() -> Optional[Dict[str, Optional[str]]]:
    """`{point: "hooks/<id>" or None}` from the decision log's writer; None when unreadable."""
    global _POINT_MODULES
    if _POINT_MODULES is None:
        module = _decisions_module()
        found = getattr(module, "POINT_MODULES", None) if module is not None else None
        if isinstance(found, dict):
            _POINT_MODULES = dict(found)
    return _POINT_MODULES


def _unit_of(row: Mapping[str, Any], owners: Mapping[str, Optional[str]]) -> str:
    point = str(row.get("point") or "")
    module = row.get("module") if "module" in row else owners.get(point)
    if isinstance(module, str) and module.startswith("hooks/"):
        return module[len("hooks/"):]
    return POINT_PREFIX + (point or "unknown")


def _switch(config: Mapping[str, Any], hook: str) -> str:
    hooks = config.get("hooks")
    value = hooks.get(hook) if isinstance(hooks, dict) else None
    return "off" if value == "off" else "on"


def _recording(config: Mapping[str, Any]) -> bool:
    block = config.get("telemetry", {})
    if not isinstance(block, dict):
        return False
    return block.get("decisions", True) is not False


def _stamp(value: Optional[dt.datetime]) -> str:
    if value is None:
        return "not in window"
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def liveness(*, enabled: bool, observable: bool, recording: bool, errors: int,
             last: Optional[dt.datetime], now: dt.datetime) -> str:
    if not enabled:
        return "off"
    if not observable:
        return "not observable"
    if not recording:
        return "not recorded"
    if errors:
        return "erroring"
    if last is None or now - last > dt.timedelta(days=SILENT_DAYS):
        return "silent"
    return "live"


def figures(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Fires, blocks, warns and errors among one unit's decision rows."""
    out = {"fires": 0, "blocks": 0, "warns": 0, "errors": 0}
    for row in rows:
        answer = row.get("deterministic_answer")
        out["fires"] += 1
        if answer in BLOCK_ANSWERS:
            out["blocks"] += 1
        elif answer in WARN_ANSWERS:
            out["warns"] += 1
        if any(row.get(fact) for fact in ERROR_FACTS) or answer == "error":
            out["errors"] += 1
    return out


def decision_rows(home: Path, cutoff: dt.datetime) -> List[Dict[str, Any]]:
    """The decision log and its rotated files (`.1` newest) that hold rows since `cutoff`,
    oldest first. A rotated file last written before the cutoff holds nothing in the window."""
    target = periods.state_dir(home) / "decisions.jsonl"
    files = [target]
    for number in range(1, MAX_ROTATED + 1):
        older = target.with_name(target.name + "." + str(number))
        try:
            written = dt.datetime.fromtimestamp(older.stat().st_mtime, dt.timezone.utc)
        except OSError:
            break
        if written < cutoff:
            break
        files.append(older)
    rows: List[Dict[str, Any]] = []
    for source in reversed(files):
        rows.extend(periods._jsonl(source, "ts"))
    rows.sort(key=lambda r: r["_ts"])
    return rows


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    owners = point_modules()
    observable = (set(m[len("hooks/"):] for m in owners.values() if isinstance(m, str))
                  if owners is not None else None)
    every = [row for row in decision_rows(ctx.home, ctx.cutoff)
             if row.get("kind") == "decision" and row["_ts"] <= ctx.now]
    by_unit: Dict[str, List[Dict[str, Any]]] = {}
    last: Dict[str, dt.datetime] = {}
    for row in every:
        unit = _unit_of(row, owners or {})
        last[unit] = row["_ts"]
        if row["_ts"] >= ctx.cutoff:
            by_unit.setdefault(unit, []).append(row)
    recording = _recording(ctx.config)
    units = set(catalog.HOOK_IDS) | set(by_unit)
    stash: Dict[str, Dict[str, Any]] = {}
    out: Dict[str, Dict[str, Any]] = {}
    for unit in units:
        rows = by_unit.get(unit, [])
        built_in = unit.startswith(POINT_PREFIX)
        enabled = built_in or _switch(ctx.config, unit) == "on"
        seen = observable is None or unit in observable or built_in or bool(rows)
        found = figures(rows)
        points = sorted(set(str(r.get("point") or "") for r in rows) - {""})
        state = ("built in" if built_in and rows else
                 liveness(enabled=enabled, observable=seen, recording=recording,
                          errors=found["errors"], last=last.get(unit), now=ctx.now))
        if observable is None and not rows and not built_in:
            state = "unknown"
        stash[unit] = dict(found, points=" · ".join(points) or "none", last_fired=_stamp(last.get(unit)),
                           liveness=state)
        label = (unit[len(POINT_PREFIX):] + " (built in)") if built_in else LABELS.get(unit, unit)
        out[unit] = {"label": label, "observations": [],
                     "current": "always on" if built_in else _switch(ctx.config, unit)}
    _REQUEST.units = stash
    return out


def metrics(_rows: Sequence[Mapping[str, Any]], unit: str = "", _value: str = "") -> Dict[str, Any]:
    stash = getattr(_REQUEST, "units", None) or {}
    found = stash.get(unit)
    if found is None:
        return {"fires": 0, "blocks": 0, "warns": 0, "errors": 0, "points": "none",
                "last_fired": "not in window", "liveness": "unknown"}
    return dict(found)


periods.register_surface("hooks", items, metrics)


# --- one hook's detail --------------------------------------------------------------------

# The command that prints `GET /api/harness/hook`; `answer` serves both.
CLI_COMMAND = ("citizen", "harness", "hook", "{name}", "--days", "{days}", "--json")
RECENT_LIMIT = 20
OUTCOMES = ("block", "warn", "allow", "error")


def outcome_of(row: Mapping[str, Any]) -> str:
    """One decision's outcome for the detail chart: `error` first, then block, warn, allow."""
    answer = row.get("deterministic_answer")
    if any(row.get(fact) for fact in ERROR_FACTS) or answer == "error":
        return "error"
    if answer in BLOCK_ANSWERS:
        return "block"
    if answer in WARN_ANSWERS:
        return "warn"
    return "allow"


def known_units() -> List[str]:
    """Every hook a detail page may name: the catalog's hooks and each point no hook owns."""
    owners = point_modules() or {}
    built_in = [POINT_PREFIX + point for point, module in owners.items() if not module]
    return sorted(set(catalog.HOOK_IDS) | set(built_in))


def _modes(config: Mapping[str, Any], unit: str) -> Dict[str, str]:
    """`{point: mode}` for each decision point of this hook that takes a mode."""
    from ...decisions import controls
    owners = point_modules() or {}
    points = [p for p in controls.POINTS
              if p == unit or owners.get(p) == "hooks/" + unit or POINT_PREFIX + p == unit]
    block = config.get("governance")
    jev = block.get("jev") if isinstance(block, dict) else None
    jev = jev if isinstance(jev, dict) else {}
    named = jev.get("modes") if isinstance(jev.get("modes"), dict) else {}
    fallback = jev.get("mode") if isinstance(jev.get("mode"), str) else controls.DEFAULT_MODE
    return {p: named[p] if isinstance(named.get(p), str) else fallback for p in points}


def _recent(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One recent decision: when, where, what it decided, and the hash of what it judged. The
    judged text itself stays in the log; the Activity page is where a command is shown."""
    reason = row.get("reason")
    return {"ts": periods.iso(row["_ts"]), "point": str(row.get("point") or ""),
            "answer": str(row.get("deterministic_answer") or ""), "outcome": outcome_of(row),
            "session": str(row.get("session_id") or ""),
            "input_hash": str(row.get("input_sha256") or ""),
            "reason": reason if isinstance(reason, str) else ""}


def detail(unit: str, *, days: int = 30, home: Optional[Path] = None,
           now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """One hook's page: its row on the Hooks page, decisions by day and outcome, its recent
    decisions newest first, and its settings. Raises `KeyError` for a hook no page lists."""
    if unit not in known_units():
        raise KeyError(unit)
    home = Path(home) if home else periods.default_home()
    now = now or dt.datetime.now(dt.timezone.utc)
    days = max(1, min(int(days), periods.MAX_DAYS))
    found = periods.efficacy_rows("hooks", unit, days=days, home=home, now=now)
    row = found["rows"][0] if found["rows"] else {"item_label": unit, "setting_label": "on",
                                                  "metrics": metrics([], unit)}
    cutoff = now - dt.timedelta(days=days)
    owners = point_modules() or {}
    mine = [r for r in decision_rows(home, cutoff)
            if r.get("kind") == "decision" and cutoff <= r["_ts"] <= now
            and _unit_of(r, owners) == unit]
    first = cutoff.astimezone(dt.timezone.utc).date()
    last = now.astimezone(dt.timezone.utc).date()
    by_day = []
    for offset in range((last - first).days + 1):
        by_day.append(dict({"day": (first + dt.timedelta(days=offset)).isoformat()},
                           **{name: 0 for name in OUTCOMES}))
    for r in mine:
        by_day[(r["_ts"].astimezone(dt.timezone.utc).date() - first).days][outcome_of(r)] += 1
    config = periods._config(home)
    built_in = unit.startswith(POINT_PREFIX)
    source = Path(__file__).resolve().parents[4] / "policy" / "hooks" / (unit + ".py")
    return {
        "hook": unit, "label": row["item_label"], "built_in": built_in,
        "core": unit in catalog.CORE_HOOKS, "window_days": days,
        "summary": row["metrics"],
        "by_day": by_day,
        "recent": [_recent(r) for r in reversed(mine[-RECENT_LIMIT:])],
        "settings": {"enabled": "always on" if built_in else _switch(config, unit),
                     "modes": _modes(config, unit),
                     "recording": _recording(config),
                     "source": ("policy/hooks/%s.py" % unit) if source.is_file() else ""},
    }


def answer(name: Optional[str], days: Optional[str] = None, *,
           home: Optional[Path] = None) -> Tuple[int, Dict[str, Any]]:
    """The route's status and body: `detail`, a 400 `invalid_request` (no name, `days` not 1 to
    `MAX_DAYS`) or a 404 `unknown_hook`."""
    days = "30" if days is None else days
    if (not name or not (days.isascii() and days.isdigit())
            or not 1 <= int(days) <= periods.MAX_DAYS):
        return 400, {"error": "invalid_request"}
    try:
        return 200, detail(name, days=int(days), home=home)
    except KeyError:
        return 404, {"error": "unknown_hook"}
