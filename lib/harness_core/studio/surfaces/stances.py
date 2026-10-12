# SPDX-License-Identifier: MIT
"""Stances: one item per stance, its variant read from usage rows and `config.json`."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .. import periods


def _label(unit: str) -> str:
    return unit.replace("-", " ").capitalize()


def config_value(config: Mapping[str, Any], unit: str) -> Optional[str]:
    stances = config.get("stances")
    value = stances.get(unit) if isinstance(stances, dict) else None
    return value if isinstance(value, str) and value else None


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    configured = ctx.config.get("stances") if isinstance(ctx.config.get("stances"), dict) else {}
    for unit in configured:
        out[unit] = {"label": _label(unit), "observations": [],
                     "current": config_value(ctx.config, unit)}
    for row in ctx.usage:
        stances = row.get("stances")
        # A rescanned row carries the profile at the rescan, not the one the session ran under.
        if not isinstance(stances, dict) or not stances or row.get("stances_source") == "rescan":
            continue
        stream = periods.stream_of(row)
        for unit in set(stances) | set(out):
            entry = out.setdefault(unit, {"label": _label(unit), "observations": [],
                                          "current": None})
            value = stances.get(unit)
            entry["observations"].append((row["_ts"], value if isinstance(value, str) and value
                                          else "(unset)", stream))
    return out


periods.register_surface("stances", items, periods.usage_metrics,
                         config_key=lambda unit: "stances." + unit, config_value=config_value)
