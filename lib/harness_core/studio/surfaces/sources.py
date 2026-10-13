# SPDX-License-Identifier: MIT
"""Sources: where every loaded module comes from, measured per harness version (AH-S375, #1327).

One item per source root, labelled by where it comes from:

- **Core**: the harness's own `primitives/` and hooks;
- **Personal**: each directory in the user's `primitive_roots`;
- **Third-party**: each plugin enabled in Claude Code's `enabledPlugins`, other than the Model
  Citizen plugin itself, which is Core installed another way.

A Team label exists on the page but is never assigned here: nothing in the configuration marks a
primitive root as a team's, so a team-published root reads as Personal rather than as a guess.

Periods split at harness version changes in the usage rows. A row's `context_attribution` names
modules as `kind/unit` and not their root, so each is credited to the first root holding it in
load order (core first, then `primitive_roots`), the order `posture` resolves resident text in; a
stance resolves through the variant the row was stamped with. Roots are read as they are now, so a
root added mid-window is credited only for modules it still holds. Plugins are not in the
attribution, and their sessions and tokens are reported as not recorded.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .. import module_library, periods

ROOT = Path(__file__).resolve().parents[4]
# The harness's own plugin names: an enabled one is Core, not a third party.
CORE_PLUGINS = ("model-citizen", "agent-harness")
NOT_RECORDED = "not recorded"
PLUGIN_PARTS = (("skills", "*/SKILL.md"), ("agents", "*.md"), ("commands", "*.md"))

# The sources `items` read, for the `metrics` calls of the same request on the same thread.
_LOCAL = threading.local()


def claude_dir(home: Path) -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path(home) / ".claude"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _plugin_modules(path: Optional[Path]) -> Any:
    if path is None or not path.is_dir():
        return NOT_RECORDED
    return sum(len(list((path / part).glob(pattern))) for part, pattern in PLUGIN_PARTS)


def plugins(home: Path) -> List[Dict[str, Any]]:
    """Every enabled third-party plugin: its key, install path and component count."""
    base = claude_dir(home)
    enabled = _read_json(base / "settings.json").get("enabledPlugins")
    installed = _read_json(base / "plugins" / "installed_plugins.json").get("plugins")
    installed = installed if isinstance(installed, dict) else {}
    out = []
    for key, value in sorted(enabled.items() if isinstance(enabled, dict) else ()):
        if value is False or str(key).split("@")[0] in CORE_PLUGINS:
            continue
        records = installed.get(key)
        records = [records] if isinstance(records, dict) else records
        paths = [r.get("installPath") for r in records if isinstance(r, dict)] \
            if isinstance(records, list) else []
        path = next((Path(p) for p in paths if isinstance(p, str) and p), None)
        out.append({"id": "third-party:" + str(key), "source": "Third-party",
                    "label": "Third-party: " + str(key).split("@")[0],
                    "path": str(path) if path else NOT_RECORDED, "enabled": "on",
                    "modules": _plugin_modules(path), "attributed": False})
    return out


def sources(ctx: "periods.Context") -> Dict[str, Any]:
    """`{"order": [ids], "by_id": {id: source}, "first": {(kind, unit): id}}` for this request."""
    inventory = module_library.inventory(ROOT, ctx.config, metadata_only=True)["modules"]
    roots = module_library._roots(ROOT, dict(ctx.config))
    counts: Dict[str, int] = {}
    for module in inventory:
        counts[module["root"]["id"]] = counts.get(module["root"]["id"], 0) + 1
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for root in roots:
        ident = "core" if root["core"] else "personal:" + str(root["path"])
        order.append(ident)
        by_id[ident] = {"id": ident, "root_id": root["id"],
                        "source": "Core" if root["core"] else "Personal",
                        "label": "Core" if root["core"] else "Personal: " + root["label"],
                        "path": str(root["path"]), "enabled": "on",
                        "modules": counts.get(root["id"], 0), "attributed": True}
    root_ids = {by_id[i]["root_id"]: i for i in order}
    first: Dict[Any, str] = {}
    rank = {root_id: index for index, root_id in enumerate(by_id[i]["root_id"] for i in order)}
    for module in sorted(inventory, key=lambda m: rank.get(m["root"]["id"], len(rank))):
        ident = root_ids.get(module["root"]["id"])
        if ident is None:
            continue
        first.setdefault((module["kind"], module["name"]), ident)
        if module["kind"] == "stances":
            first.setdefault(("stances", module["name"].split("/", 1)[0]), ident)
    for plugin in plugins(ctx.home):
        order.append(plugin["id"])
        by_id[plugin["id"]] = plugin
    return {"order": order, "by_id": by_id, "first": first}


def credited(row: Mapping[str, Any], first: Mapping[Any, str]) -> Dict[str, int]:
    """One row's attributed context tokens by source id; empty when the row carries none."""
    attribution = row.get("context_attribution")
    modules = attribution.get("modules") if isinstance(attribution, dict) else None
    stamped = row.get("stances") if isinstance(row.get("stances"), dict) else {}
    out: Dict[str, int] = {}
    for key, tokens in (modules.items() if isinstance(modules, dict) else ()):
        kind, _sep, unit = str(key).partition("/")
        variant = stamped.get(unit) if kind == "stances" else None
        owner = (first.get((kind, unit + "/" + variant)) if isinstance(variant, str) else None) \
            or first.get((kind, unit))
        if owner is not None and isinstance(tokens, (int, float)):
            out[owner] = out.get(owner, 0) + int(tokens)
    return out


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    found = sources(ctx)
    _LOCAL.sources = found
    observations = [(row["_ts"], str(row["harness_version"]), periods.stream_of(row))
                    for row in ctx.usage if row.get("harness_version")]
    current = None if observations else "no sessions in this window"
    return {ident: {"label": found["by_id"][ident]["label"], "observations": list(observations),
                    "current": current} for ident in found["order"]}


def metrics(rows: Sequence[Mapping[str, Any]], unit: str = "", _value: str = "") -> Dict[str, Any]:
    """Sessions, attributed context tokens per session and modules for one source and period,
    with the source's label, root and enabled state for the Settings tab."""
    found = getattr(_LOCAL, "sources", None) or {"by_id": {}, "first": {}}
    source = found["by_id"].get(unit)
    sessions = [r for r in rows if (r.get("kind") or "session") == "session"]
    out: Dict[str, Any] = {"sessions": len(sessions)}
    if source is None:
        return out
    if source["attributed"]:
        carrying = [r for r in sessions if isinstance(r.get("context_attribution"), dict)]
        total = sum(credited(r, found["first"]).get(unit, 0) for r in carrying)
        out["tokens_per_session"] = round(total / len(carrying)) if carrying else NOT_RECORDED
    else:
        out["sessions"] = NOT_RECORDED
        out["tokens_per_session"] = NOT_RECORDED
    out.update({"modules": source["modules"], "source": source["source"], "root": source["path"],
                "enabled": source["enabled"]})
    return out


periods.register_surface("sources", items, metrics)
