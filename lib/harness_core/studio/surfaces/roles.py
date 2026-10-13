# SPDX-License-Identifier: MIT
"""Roles: one item per agent type the usage ledger saw spawned or run as a worker.

A role's setting is the class its definition names and the effort its Claude Code adapter
binding gives it. Neither lives in `config.json`, so no apply or config commit can split a role's
window and every role answers one period with the periods note: a model or effort read off the
rows is what ran, reported as a metric, never a boundary. Workflow-tool agents are left to the
Workflows surface, because no spawn routed them and no role budget prices them.

Tier rewrites are the `tier-agent-spawns` decisions naming the role. The periods frame hands a
provider only the usage rows inside a period, so `items` appends each rewrite to the request's
usage list as a `REWRITE` row, and replaces every row it reads with a copy carrying its unit and
its price (`annotate`); only this surface's metrics read either.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from harness_core import catalog

from .. import periods

ROOT = Path(__file__).resolve().parents[4]
DELEGATED = ("subagent", "worker")
REWRITE = "_tier_rewrite"
POINT = "tier-agent-spawns"
# A band answer names the worker a spawn was routed to; these name a change to a named spawn.
CHANGE_ANSWERS = ("demoted", "one-rung", "deny")
BUILT_IN = "Session model · runtime default"
# Agents with no shared definition: the runtime's own (Explore, Plan, a Codex agent's nickname)
# and spawns that named no type. One row, as the runtime sets their model and effort.
BUILT_IN_UNIT = "built-in"
BUILT_IN_LABEL = "Built-in and unnamed agents"

_LOCK = threading.Lock()
_PRICING: List[Any] = []
_CACHE: Dict[Any, Any] = {}


# --- shared with the Workflows surface ------------------------------------------------------

def pricing():
    """`policy/hooks/pricing.py`, loaded by file once; None when this checkout lacks it."""
    with _LOCK:
        if not _PRICING:
            module = None
            path = ROOT / "policy" / "hooks" / "pricing.py"
            try:
                spec = importlib.util.spec_from_file_location("harness_studio_pricing", str(path))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:
                module = None
            _PRICING.append(module)
        return _PRICING[0]


def price_table(config: Mapping[str, Any]) -> Dict[str, Any]:
    module = pricing()
    if module is None:
        return {}
    try:
        return module.load_prices(dict(config))
    except Exception:
        return {}


def row_cost(row: Mapping[str, Any], table: Mapping[str, Any]) -> Optional[float]:
    """USD for one subagent or worker row, or None when it cannot be priced."""
    module = pricing()
    if module is None or not table:
        return None
    try:
        return module.row_cost({k: v for k, v in row.items() if not k.startswith("_")}, table)
    except Exception:
        return None


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest rank over a sorted sample, the rule `citizen usage --by role` uses."""
    index = math.ceil(share * len(values)) - 1
    return values[min(max(index, 0), len(values) - 1)]


def usd(value: float) -> str:
    return "$%.2f" % value


def cost_metrics(costs: Sequence[Optional[float]], per: str) -> Dict[str, Any]:
    """Total and p50 · p75 dollars over the priced costs, and how many could not be priced."""
    priced = sorted(cost for cost in costs if cost is not None)
    out: Dict[str, Any] = {"unpriced": len(costs) - len(priced)}
    if priced:
        out["cost"] = usd(sum(priced))
        out["cost_" + per] = "%s · %s" % (usd(percentile(priced, 0.5)), usd(percentile(priced, 0.75)))
    return out


def most_common(values: Sequence[str]) -> Optional[str]:
    """The commonest non-empty value, with how many others ran beside it."""
    counts: Dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    top = max(sorted(counts), key=lambda v: counts[v])
    return top if len(counts) == 1 else "%s +%d" % (top, len(counts) - 1)


# --- roles ----------------------------------------------------------------------------------

def is_role_row(row: Mapping[str, Any]) -> bool:
    return row.get("kind") in DELEGATED and not row.get("workflow")


def role_name(row: Mapping[str, Any]) -> str:
    return str(row.get("agent_type") or "unknown")


def _bindings(root: Path) -> Dict[str, Any]:
    # Through the catalog's loader: the runtime travels as an argument, never as a path segment.
    try:
        data, _ = catalog.adapter_tiers(root, "claude-code")
    except (OSError, ValueError):
        return {}
    roles = data.get("roles") if isinstance(data, dict) else None
    return roles if isinstance(roles, dict) else {}


def definitions(root: Path = ROOT) -> Dict[str, Dict[str, str]]:
    """`{role: {"tier", "effort"}}` from each shared role definition and its adapter binding."""
    bindings = _bindings(root)
    out: Dict[str, Dict[str, str]] = {}
    directory = root / "primitives" / "roles"
    try:
        paths = sorted(directory.glob("*.md"))
    except OSError:
        return out
    for path in paths:
        try:
            fields, _body = catalog.frontmatter(path)
        except (OSError, ValueError):
            continue
        binding = bindings.get(path.stem) if isinstance(bindings.get(path.stem), dict) else {}
        out[path.stem] = {"tier": str(fields.get("tier") or ""),
                          "effort": str(binding.get("effort") or "")}
    return out


def setting(definition: Optional[Mapping[str, str]]) -> str:
    """`strong · medium effort`; a role with no shared definition runs on the runtime's own."""
    if not definition:
        return BUILT_IN
    parts = [definition.get("tier") or "class not set"]
    parts.append("%s effort" % definition["effort"] if definition.get("effort")
                 else "session effort")
    return " · ".join(parts)


def _decisions(home: Path, cutoff) -> List[Dict[str, Any]]:
    """`tier-agent-spawns` rows since `cutoff`, across the ledger and its rotated files."""
    state = periods.state_dir(home)
    out: List[Dict[str, Any]] = []
    for path in sorted(state.glob("decisions.jsonl*")):
        if path.name.endswith(".lock"):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        # A rotated file's newest row is no newer than its last write.
        if dt.datetime.fromtimestamp(info.st_mtime, dt.timezone.utc) < cutoff:
            continue
        stamp = (info.st_mtime_ns, info.st_size)
        key = ("tier", str(path), stamp)
        with _LOCK:
            rows = _CACHE.get(key)
        if rows is None:
            rows = _read_points(path)
            with _LOCK:
                if len(_CACHE) > 16:
                    _CACHE.clear()
                _CACHE[key] = rows
        out.extend(row for row in rows if row["_ts"] >= cutoff)
    return out


def _read_points(path: Path) -> List[Dict[str, Any]]:
    rows = []
    marker = ('"%s"' % POINT).encode()
    try:
        handle = path.open("rb")
    except OSError:
        return rows
    with handle:
        for line in handle:
            if marker not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("point") != POINT \
                    or row.get("kind") != "decision":
                continue
            ts = periods.parse_ts(row.get("ts"))
            if ts is not None:
                rows.append({"kind": REWRITE, "_ts": ts, "role": rewritten_role(row),
                             "answer": row.get("deterministic_answer")})
    return rows


def rewritten_role(row: Mapping[str, Any]) -> str:
    """The role a rewritten spawn ran as: the type it was given, else the band it was routed
    to, else the type it asked for."""
    answer = row.get("deterministic_answer")
    for value in (row.get("subagent_type"),
                  answer if isinstance(answer, str) and answer not in CHANGE_ANSWERS else None,
                  row.get("subagent_type_requested")):
        if isinstance(value, str) and value:
            return value
    return "(unnamed)"


def annotate(ctx: "periods.Context", keep, unit_of) -> None:
    """Replace each row `keep` accepts with a copy carrying `_unit` and `_cost`.

    `ctx.usage` is built afresh for every request, so the copies stay inside this one answer
    and the cached ledger rows are never touched.
    """
    table = price_table(ctx.config)
    for index, row in enumerate(ctx.usage):
        if keep(row):
            ctx.usage[index] = dict(row, _unit=unit_of(row), _cost=row_cost(row, table))


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    defined = definitions()

    def unit_of(row: Mapping[str, Any]) -> str:
        name = role_name(row)
        return name if name in defined else BUILT_IN_UNIT

    annotate(ctx, is_role_row, unit_of)
    out: Dict[str, Dict[str, Any]] = {}
    for row in ctx.usage:
        if not is_role_row(row):
            continue
        unit = row["_unit"]
        value = setting(defined.get(unit))
        entry = out.setdefault(unit, {"label": BUILT_IN_LABEL if unit == BUILT_IN_UNIT else unit,
                                      "observations": [], "current": value})
        entry["observations"].append((row["_ts"], value, periods.stream_of(row)))
    for row in _decisions(ctx.home, ctx.cutoff):
        unit = row["role"] if row["role"] in defined else BUILT_IN_UNIT
        if unit in out:
            ctx.usage.append(dict(row, _unit=unit))
            # A spawn is rewritten before it runs, and its row is stamped when it ends, so the
            # rewrite is an observation too: without it the period would open after it.
            entry = out[unit]
            entry["observations"].append((row["_ts"], entry["current"], "decisions"))
    return out


def over_budget(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Of the runs with a soft budget and the figure it caps, the share that ran past it."""
    measured = over = 0
    for row in rows:
        checks = []
        for figure, cap in (("output", "budget_output_tokens"), ("tool_calls", "budget_tool_calls")):
            value, limit = row.get(figure), row.get(cap)
            if isinstance(value, int) and isinstance(limit, int) and limit > 0:
                checks.append(value > limit)
        if checks:
            measured += 1
            over += any(checks)
    if not measured:
        return None
    return "%d%% · %d of %d" % (round(100.0 * over / measured), over, measured)


def metrics(rows: Sequence[Mapping[str, Any]], unit: str, _value: str) -> Dict[str, Any]:
    runs = [row for row in rows if is_role_row(row) and row.get("_unit") == unit]
    out: Dict[str, Any] = {"spawns": len(runs)}
    model = most_common([str(row.get("model") or "") for row in runs])
    effort = most_common([str(row.get("effort") or "") for row in runs])
    if model:
        out["model"] = model
    if effort:
        out["effort"] = effort
    out.update(cost_metrics([row.get("_cost") for row in runs], "per_spawn"))
    share = over_budget(runs)
    out["over_budget"] = share if share is not None else "no budget"
    out["tier_rewrites"] = sum(1 for row in rows if row.get("kind") == REWRITE
                               and row.get("_unit") == unit and row.get("answer") != "deny")
    return out


periods.register_surface("roles", items, metrics)
