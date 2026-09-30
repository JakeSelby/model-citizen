# SPDX-License-Identifier: MIT
"""Read-only inventory for every module visible to Studio."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from harness_core import catalog


SCHEMA_VERSION = 1
CHARS_PER_TOKEN = 4.0
KINDS = dict(catalog.KINDS, modes={"directory": "modes", "pattern": "*.json", "value": None})
# A personal root records each core module it forked here, with the original's text at fork time.
FORKS_FILE = "forks.json"
FORK_KINDS = ("rules", "skills")
MAX_FORK_FILES = 64
MAX_FORK_FILE_BYTES = 512 * 1024
REVISION = re.compile(r"^[0-9a-f]{40}$")


def _config_and_selection(root: Path, supplied: Optional[Mapping[str, Any]] = None):
    posture = catalog.posture_module(root)
    if posture is None:
        return {}, {"sources": {}}
    config = dict(supplied) if supplied is not None else posture._user_config(os.environ, False)
    return config, posture.selection({} if supplied is not None else os.environ,
                                     strict=False, config=config, root=root)


def _roots(root: Path, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    core = (root / "primitives").resolve()
    result = [{"id": "core", "label": "Core", "path": core, "core": True}]
    seen = {core}
    for index, value in enumerate(config.get("primitive_roots", [])):
        if isinstance(value, str) and Path(value).expanduser().is_absolute():
            path = Path(value).expanduser().resolve()
            if path in seen:
                continue
            seen.add(path)
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


def identifier(value: Any) -> bool:
    """The resolver's unit-name rule (posture._identifier): the only names joined into a path."""
    return (isinstance(value, str) and bool(value) and value[0].isalpha() and value.islower()
            and value.isascii() and all(c.isalnum() or c == "-" for c in value))


def safe_relative(value: Any) -> bool:
    """A path inside one module: relative, no empty, dot or dot-dot segment, no backslash."""
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _inside(base: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError):
        return False


def module_files(primitives: Path, kind: str, name: str) -> Optional[Dict[str, Path]]:
    """The regular files one forkable module is made of, keyed by path inside the module.

    None when the name is not an identifier, the module is absent, or it is not a plain tree of
    bounded regular files that resolves inside `primitives`.
    """
    if kind not in FORK_KINDS or not identifier(name):
        return None
    if kind == "rules":
        path = primitives / "rules" / (name + ".md")
        if not path.is_file() or path.is_symlink() or not _inside(primitives, path):
            return None
        return {name + ".md": path}
    directory = primitives / "skills" / name
    if (directory.is_symlink() or not (directory / "SKILL.md").is_file()
            or not _inside(primitives, directory)):
        return None
    found: Dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            return None
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size > MAX_FORK_FILE_BYTES:
            return None
        found[path.relative_to(directory).as_posix()] = path
        if len(found) > MAX_FORK_FILES:
            return None
    return found


def module_bytes(primitives: Path, kind: str, name: str) -> Optional[Dict[str, bytes]]:
    """Every file of a forkable module as bytes: text and binary assets alike."""
    files = module_files(primitives, kind, name)
    if files is None:
        return None
    try:
        return {relative: path.read_bytes() for relative, path in files.items()}
    except OSError:
        return None


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _forks(root_path: Path) -> Dict[str, Any]:
    try:
        data = json.loads((root_path / FORKS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("schema_version") == 1 else {}


def _text(content: Optional[bytes]) -> Optional[List[str]]:
    if content is None:
        return []
    try:
        return content.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def upstream_diff(original: Mapping[str, Optional[bytes]], current: Optional[Mapping[str, bytes]],
                  source: str) -> str:
    """A unified diff from the original as forked to the core module now; binary files by digest."""
    current = current or {}
    chunks: List[str] = []
    for relative in sorted(set(original) | set(current)):
        before, after = original.get(relative), current.get(relative)
        if before == after:
            continue
        old, new = _text(before), _text(after)
        if old is None or new is None:
            chunks.append("Binary file " + source + "/" + relative + " differs\n")
            continue
        chunks.extend(difflib.unified_diff(
            old, new, fromfile="forked/" + source + "/" + relative,
            tofile="core/" + source + "/" + relative,
        ))
    return "".join(chunks)


def _at_revision(checkout: Path, revision: str, path: str) -> Optional[bytes]:
    """One file's bytes at a recorded revision of the checkout, or None when git cannot say."""
    if not REVISION.fullmatch(revision):
        return None
    try:
        shown = subprocess.run(["git", "-C", str(checkout), "show", revision + ":" + path],
                               capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return shown.stdout if shown.returncode == 0 else None


def _fork(checkout: Path, forks: Dict[str, Any], kind: str, name: str) -> Optional[Dict[str, Any]]:
    """A forked module's provenance and whether its core original has changed since.

    `forks.json` records a digest per file, not the text: the original is read from git at the
    recorded revision and used only when its digest matches, so the diff is always of the text
    that was forked. A source that is not a forkable identifier is ignored, never joined.
    """
    entries = forks.get(kind)
    entry = entries.get(name) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
        return None
    source_kind, _, source_name = entry["source"].partition("/")
    recorded = entry.get("files")
    if (source_kind not in FORK_KINDS or not identifier(source_name) or not isinstance(recorded, dict)
            or not all(safe_relative(key) and isinstance(value, str)
                       for key, value in recorded.items())):
        return None
    revision = str(entry.get("revision", ""))
    current = module_bytes(checkout / "primitives", source_kind, source_name)
    changed = current is None or {key: digest(value) for key, value in current.items()} != recorded
    original: Dict[str, Optional[bytes]] = {}
    for relative, expected in recorded.items():
        path = ("primitives/rules/" + source_name + ".md" if source_kind == "rules"
                else "primitives/skills/" + source_name + "/" + relative)
        content = _at_revision(checkout, revision, path)
        if content is None or digest(content) != expected:
            original = {}
            break
        original[relative] = content
    available = bool(original) or not recorded
    diff = upstream_diff(original, current, entry["source"]) if changed and available else ""
    return {"source": entry["source"], "version": str(entry.get("version", "")),
            "revision": revision,
            "upstream": {"changed": changed, "missing": current is None,
                         "original_available": available, "diff": diff}}


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


def inventory(root: Path, config: Optional[Mapping[str, Any]] = None,
              metadata_only: bool = False) -> Dict[str, Any]:
    root = Path(root)
    started = time.perf_counter()
    resolved_config, selection = _config_and_selection(root, config)
    roots = _roots(root, resolved_config)
    modules: List[Dict[str, Any]] = []
    for root_entry in roots:
        forks = {} if root_entry["core"] else _forks(root_entry["path"])
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
                    "fork": _fork(root, forks, kind, name) if forks else None,
                    "source": {"path": str(path),
                               "text": "" if metadata_only else _rendered(root, "source", path, name)},
                    "rendered": {"text": "" if metadata_only else _rendered(root, kind, path, name)},
                    "projections": _projection_paths(kind, name, root_entry["core"]),
                    "context_cost": ({"tokens": 0, "estimate": "not measured",
                                      "method": "source is validated before measurement"}
                                     if metadata_only else _cost(kind, path, name)),
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
                        "manifest": hook_manifests.get(name), "fork": None,
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
