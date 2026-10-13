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
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

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
