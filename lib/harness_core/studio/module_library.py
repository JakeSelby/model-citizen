# SPDX-License-Identifier: MIT
"""Read-only inventory for every module visible to Studio."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from harness_core import catalog


SCHEMA_VERSION = 1
CHARS_PER_TOKEN = 4.0
KINDS = dict(catalog.KINDS, modes={"directory": "modes", "pattern": "*.json", "value": None})


def _config_and_selection(root: Path):
    posture = catalog.posture_module(root)
    if posture is None:
        return {}, {"sources": {}}
    config = posture._user_config(os.environ, False)
    return config, posture.selection(os.environ, strict=False, config=config, root=root)


def _roots(root: Path, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = [{"id": "core", "label": "Core", "path": root / "primitives", "core": True}]
    for index, value in enumerate(config.get("primitive_roots", [])):
        if isinstance(value, str) and Path(value).expanduser().is_absolute():
            path = Path(value).expanduser()
            result.append({"id": "root-%d" % (index + 1), "label": path.name or str(path),
                           "path": path, "core": False})
    return result


def _frontmatter_description(path: Path) -> str:
    try:
        fields, _ = catalog.frontmatter(path)
    except (OSError, ValueError):
        return ""
    return fields.get("description", "")


def _cost(kind: str, path: Path, name: str) -> Dict[str, Any]:
    if kind in ("skills", "roles", "workflows"):
        text = name + ": " + _frontmatter_description(path)
        method = "chars/4 of resident listing name and description"
    elif kind in ("rules", "stances"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            text = ""
        method = "chars/4 of resident source text"
    else:
        text, method = "", "not resident before the first prompt"
    return {"tokens": int(round(len(text) / CHARS_PER_TOKEN)), "estimate": "soft estimate",
            "method": method}


def _projection_paths(kind: str, name: str, core: bool) -> List[Dict[str, str]]:
    rule_path = ("~/.claude/rules/harness/%s.md" % name if core else
                 "~/.claude/rules/harness-roots/<root-slug>/%s.md" % name)
    mappings = {
        "rules": (("claude-code", rule_path),
                  ("codex", "~/.codex/AGENTS.md (aggregated)")),
        "stances": (("claude-code", "~/.claude/rules/harness-stances/%s.md" % name.split("/", 1)[0]),
                    ("codex", "~/.codex/AGENTS.md (aggregated)")),
        "skills": (("claude-code", "~/.claude/skills/%s/SKILL.md" % name),
                   ("codex", "~/.agents/skills/%s/SKILL.md" % name)),
        "roles": (("claude-code", "~/.claude/agents/%s.md" % name),
                  ("codex", "~/.codex/agents/%s.toml" % name)),
        "workflows": (("claude-code", "~/.claude/commands/%s.md" % name),
                      ("codex", "~/.agents/skills/harness-%s/SKILL.md" % name)),
        "hooks": (("claude-code", "~/.claude/settings.json hooks"),
                  ("codex", "~/.codex/hooks.json")),
        "presentation": (("claude-code", "~/.claude/output-styles/%s.md" % name),
                         ("codex", "~/.codex/AGENTS.md (aggregated)")),
        "modes": (),
    }
    return [{"runtime": runtime, "path": path} for runtime, path in mappings.get(kind, ())]


def _rendered(root: Path, kind: str, path: Path, name: str) -> str:
    try:
        if kind == "roles" and path.is_relative_to(root):
            return catalog.role_projection(root, "claude-code", path)
        if kind == "workflows":
            return path.read_text(encoding="utf-8").replace("{{arguments}}", "$ARGUMENTS")
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _manifest(root_path: Path, kind: str, name: str) -> Optional[Dict[str, Any]]:
    path = root_path / "manifests.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = (data.get(kind) or {}).get(name)
    return value if isinstance(value, dict) else None


def _source_entries(root: Path, root_entry: Dict[str, Any], kind: str,
                    definition: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if not definition.get("directory") or not definition.get("pattern"):
        return
    base = root_entry["path"] / definition["directory"]
    pattern = definition["pattern"]
    paths = sorted(base.glob(pattern)) if base.is_dir() else []
    for path in paths:
        if kind == "skills":
            name = path.parent.name
        elif kind == "stances":
            name = path.parent.name + "/" + path.stem
        else:
            name = path.stem
        yield {"name": name, "path": path}


def _state(kind: str, name: str, selection: Dict[str, Any]) -> Dict[str, Any]:
    unit, variant = (name.split("/", 1) + [""])[:2] if kind == "stances" else (name, "")
    values = selection.get(kind) if isinstance(selection.get(kind), dict) else {}
    sources = ((selection.get("sources") or {}).get(kind) or {})
    if kind == "modes":
        active = selection.get("mode") == name
        layer = (selection.get("sources") or {}).get("mode", "default") if active else "not selected"
        return {"value": "on" if active else "off", "layer": str(layer), "switchable": True}
    if kind == "stances":
        value = values.get(unit)
        return {"value": "on" if value == variant else "off",
                "layer": str(sources.get(unit, "default")), "switchable": True}
    if catalog.KINDS.get(kind, {}).get("value") == "switch":
        return {"value": str(values.get(unit, "on")),
                "layer": str(sources.get(unit, "default")), "switchable": True}
    return {"value": "on", "layer": "not switchable", "switchable": False}


def inventory(root: Path) -> Dict[str, Any]:
    root = Path(root)
    started = time.perf_counter()
    config, selection = _config_and_selection(root)
    roots = _roots(root, config)
    modules: List[Dict[str, Any]] = []
    for root_entry in roots:
        for kind, definition in KINDS.items():
            for source in _source_entries(root, root_entry, kind, definition):
                path, name = source["path"], source["name"]
                manifest_name = name.split("/", 1)[0] if kind == "stances" else name
                modules.append({
                    "key": "%s:%s:%s" % (root_entry["id"], kind, name),
                    "name": name, "kind": kind,
                    "root": {"id": root_entry["id"], "label": root_entry["label"],
                             "path": str(root_entry["path"]), "core": root_entry["core"]},
                    "state": _state(kind, name, selection),
                    "collision": False,
                    "manifest": _manifest(root_entry["path"], kind, manifest_name),
                    "source": {"path": str(path), "text": _rendered(root, "source", path, name)},
                    "rendered": {"text": _rendered(root, kind, path, name)},
                    "projections": _projection_paths(kind, name, root_entry["core"]),
                    "context_cost": _cost(kind, path, name),
                })
    # Hooks live in the kernel rather than under primitives.
    manifests = root / "policy" / "hooks" / "manifests.json"
    try:
        hook_manifests = json.loads(manifests.read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError):
        hook_manifests = {}
    for name in catalog.HOOK_IDS:
        path = root / catalog.HOOKS_DIRECTORY / (name + ".py")
        if not path.is_file():
            continue
        modules.append({"key": "core:hooks:" + name, "name": name, "kind": "hooks",
                        "root": {"id": "core", "label": "Core", "path": str(root / "policy"), "core": True},
                        "state": _state("hooks", name, selection), "collision": False,
                        "manifest": hook_manifests.get(name),
                        "source": {"path": str(path), "text": path.read_text(encoding="utf-8")},
                        "rendered": {"text": "Registered through the shared lifecycle adapter."},
                        "projections": _projection_paths("hooks", name, True),
                        "context_cost": _cost("hooks", path, name)})
    counts: Dict[str, int] = {}
    for item in modules:
        identity = item["kind"] + "/" + item["name"]
        counts[identity] = counts.get(identity, 0) + 1
    for item in modules:
        item["collision"] = counts[item["kind"] + "/" + item["name"]] > 1
    modules.sort(key=lambda item: (item["kind"], item["name"], item["root"]["id"]))
    return {"schema_version": SCHEMA_VERSION, "modules": modules,
            "summary": {"modules": len(modules),
                        "collisions": sum(1 for item in modules if item["collision"]),
                        "roots": len(roots),
                        "generated_ms": round((time.perf_counter() - started) * 1000, 3)}}


def filter_modules(modules: Iterable[Dict[str, Any]], query: str = "", kind: str = "",
                   root: str = "", state: str = "", cost: str = "") -> List[Dict[str, Any]]:
    """Filter an already-built inventory without filesystem work."""
    needle = query.casefold().strip()
    result = []
    for item in modules:
        tokens = item["context_cost"]["tokens"]
        if needle and needle not in (item["name"] + " " + item["kind"] + " " +
                                     item["root"]["label"]).casefold():
            continue
        if kind and item["kind"] != kind:
            continue
        if root and item["root"]["id"] != root:
            continue
        if state and item["state"]["value"] != state:
            continue
        if cost == "none" and tokens != 0:
            continue
        if cost == "low" and not 0 < tokens < 100:
            continue
        if cost == "medium" and not 100 <= tokens < 1000:
            continue
        if cost == "high" and tokens < 1000:
            continue
        result.append(item)
    return result
