# SPDX-License-Identifier: MIT
"""Draft-only mode, stance and switch edits validated by the public CLI."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from harness_core import catalog

from . import drafts, selection


SCHEMA_VERSION = 1
CLI_COMMANDS = {
    "read": ("citizen", "draft", "selection", "read", "{draft}", "--json"),
    "preview": ("citizen", "draft", "selection", "preview", "{draft}",
                "--changes", "changes.json", "--json"),
    "save": ("citizen", "draft", "selection", "save", "{draft}",
             "--base-revision", "{revision}", "--idempotency-key", "KEY",
             "--changes", "changes.json", "--json"),
}
CORE_ACK = "core_switches_acknowledged"


class SelectionEditError(ValueError):
    """A draft selection request cannot be evaluated safely."""


def _posture(root: Path):
    posture = catalog.posture_module(root)
    if posture is None:
        raise SelectionEditError("the installed harness has no selection resolver")
    return posture


def _variant_paths(posture, root: Path, config: Mapping[str, Any]) -> Dict[str, Dict[str, Path]]:
    found: Dict[str, Dict[str, Path]] = {}
    for directory in posture.stance_roots(config, root):
        for path in sorted(directory.glob("*/*.md")) if directory.is_dir() else []:
            found.setdefault(path.parent.name, {}).setdefault(path.stem, path)
    return found


def _selection(root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _posture(root).selection(env={}, strict=True, config=config, root=root)
    except (OSError, ValueError) as exc:
        raise SelectionEditError(str(exc)) from exc


def _budget(root: Path, document: Dict[str, Any]) -> Dict[str, int]:
    harness = selection._harness_module(root)
    worst_lines, _ = harness.always_loaded_lines(root)
    worst_tokens, _ = harness.always_loaded_tokens(root)
    return {
        "selected_lines": harness.effective_always_loaded_lines(root, document),
        "worst_case_lines": worst_lines,
        "line_cap": harness.ALWAYS_LOADED_CAP,
        "worst_case_tokens": worst_tokens,
        "token_cap": harness.ALWAYS_LOADED_TOKEN_CAP,
    }


def _stance_text(root: Path, config: Dict[str, Any], document: Dict[str, Any]) -> Dict[str, str]:
    paths = _variant_paths(_posture(root), root, config)
    result: Dict[str, str] = {}
    for name, variant in sorted(document.get("stances", {}).items()):
        path = paths.get(name, {}).get(variant)
        if path is not None:
            try:
                result[name] = path.read_text(encoding="utf-8")
            except OSError:
                result[name] = ""
    return result


def _snapshot(root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    document = _selection(root, config)
    return {
        "selection": document,
        "budget": _budget(root, document),
        "stance_text": _stance_text(root, config, document),
    }


def _canonical_json(value: Any) -> str:
    """A type-sensitive JSON identity used for persisted no-op decisions."""
    return json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _error_code(exc: BaseException) -> str:
    return exc.code if isinstance(exc, drafts.DraftError) else "selection-invalid"


def _request_identity(changes: Any) -> str:
    try:
        encoded = _canonical_json(changes).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise SelectionEditError("changes must contain finite JSON values") from exc
    return hashlib.sha256(b"studio-selection-v1\0" + encoded).hexdigest()


def _switch_kinds(posture, root: Path) -> Tuple[str, ...]:
    return tuple(sorted(name for name, item in posture.selection_kinds(root).items()
                        if item.get("value") == "switch"))


def _change_value(path: str, value: Any, switch_kinds: Iterable[str]) -> str:
    head, separator, tail = path.partition(".")
    if path == "mode":
        if not isinstance(value, str) or not value:
            raise SelectionEditError("mode must name an installed mode")
        return value
    if path == CORE_ACK:
        if not isinstance(value, bool):
            raise SelectionEditError(CORE_ACK + " must be true or false")
        return "true" if value else "false"
    if not separator or not tail:
        raise SelectionEditError(path + " is not an editable selection field")
    if head == "stances":
        if not isinstance(value, str) or not value:
            raise SelectionEditError(path + " must name an installed variant")
        return value
    if head in switch_kinds:
        if value not in ("on", "off"):
            raise SelectionEditError(path + " must be on or off")
        return value
    raise SelectionEditError(path + " is not an editable selection field")


def _change_priority(item: Tuple[str, str], switch_kinds: Iterable[str]) -> Tuple[int, str]:
    """Order likely repairs first; refused edits are retried after each successful repair."""
    path, value = item
    kind = path.partition(".")[0]
    if path == CORE_ACK:
        return (0 if value == "true" else 5, path)
    if kind in switch_kinds and value == "off":
        return (1, path)
    if path == "mode":
        return (2, path)
    if kind == "stances":
        return (3, path)
    return (4, path)


def _candidate(root: Path, config: Dict[str, Any], changes: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Return the candidate and the successful deterministic CLI application order."""
    if not isinstance(changes, dict) or not changes:
        raise SelectionEditError("changes must be a nonempty object keyed by selection field")
    root = Path(root).resolve()
    posture = _posture(root)
    switch_kinds = _switch_kinds(posture, root)
    pending = sorted(
        ((path, _change_value(path, value, switch_kinds)) for path, value in changes.items()),
        key=lambda item: _change_priority(item, switch_kinds),
    )
    applied: List[str] = []
    with tempfile.TemporaryDirectory(prefix="studio-selection-") as temporary:
        home = Path(temporary)
        path = home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        environment = dict(os.environ)
        for name in tuple(environment):
            if name.startswith(("HARNESS_STANCE_", "HARNESS_IDENTITY_")) or name in (
                "HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG", "HARNESS_MODE",
                "HARNESS_PERMISSIONS",
            ):
                environment.pop(name, None)
        environment.update({"HARNESS_HOME": str(home), "HARNESS_QUIET": "1"})
        while pending:
            refusals: List[str] = []
            for index, (key, value) in enumerate(pending):
                run = subprocess.run(
                    [sys.executable, str(root / "bin" / "harness"), "config", "set", key, value],
                    cwd=str(root), env=environment, capture_output=True, text=True, timeout=120,
                )
                if not run.returncode:
                    applied.append(key)
                    pending.pop(index)
                    break
                refusals.append(
                    run.stderr.strip() or run.stdout.strip()
                    or "citizen config set refused the change"
                )
            else:
                raise SelectionEditError(refusals[0])
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SelectionEditError("citizen config set did not produce a readable configuration") from exc
    if not isinstance(result, dict):
        raise SelectionEditError("citizen config set did not produce a configuration object")
    return result, applied


def candidate(root: Path, config: Dict[str, Any], changes: Any) -> Dict[str, Any]:
    """Return exactly what sequential matching ``citizen config set`` calls write."""
    return _candidate(root, config, changes)[0]


def catalog_for(root: Path, config: Dict[str, Any], document: Dict[str, Any]) -> Dict[str, Any]:
    posture = _posture(root)
    kinds = posture.selection_kinds(root)
    variants = _variant_paths(posture, root, config)
    modes, mode_errors = posture.modes(config, root)
    if mode_errors:
        raise SelectionEditError("\n".join(mode_errors))
    stance_controls = []
    for name in sorted(variants):
        stance_controls.append({
            "name": name,
            "value": document.get("stances", {}).get(name),
            "options": sorted(variants[name]),
        })
    switch_groups = []
    for kind in _switch_kinds(posture, root):
        switch_groups.append({
            "kind": kind,
            "rows": [{"unit": unit, "value": document.get(kind, {}).get(unit, "on"),
                      "core": kind == "hooks" and unit in posture.CORE_HOOKS}
                     for unit in posture._units(kind, kinds[kind], config, root)],
        })
    return {
        "modes": sorted(modes),
        "stances": stance_controls,
        "switches": switch_groups,
        "core_acknowledged": config.get(CORE_ACK) is True,
    }


def read(root: Path, name: str) -> Dict[str, Any]:
    root = Path(root).resolve()
    try:
        item = drafts.read_config(root, name)
        current = _snapshot(root, item["config"])
        controls = catalog_for(root, item["config"], current["selection"])
    except (drafts.DraftError, SelectionEditError) as exc:
        return {"status": "unavailable", "message": str(exc), "draft": {},
                "controls": {}, "current": {}}
    return {"status": "ready", "message": "Draft selection is ready.",
            "draft": item["draft"], "controls": controls, "current": current}


def _preview(root: Path, name: str, changes: Any) -> Dict[str, Any]:
    item = drafts.read_config(root, name)
    before = _snapshot(root, item["config"])
    updated, applied = _candidate(root, item["config"], changes)
    after = _snapshot(root, updated)
    unchanged = _canonical_json(updated) == _canonical_json(item["config"])
    return {
        "valid": True,
        "error": "",
        "error_code": "",
        "changed": [] if unchanged else sorted(str(path) for path in changes),
        "unchanged": unchanged,
        "base_revision": item["draft"]["revision"],
        "before": before,
        "after": after,
        "controls": catalog_for(root, updated, after["selection"]),
        "applied": applied,
        "_candidate": updated,
    }


def preview(root: Path, name: str, changes: Any) -> Dict[str, Any]:
    try:
        result = _preview(Path(root), name, changes)
    except (drafts.DraftError, SelectionEditError) as exc:
        return {"valid": False, "error": str(exc), "error_code": _error_code(exc),
                "changed": [], "unchanged": False,
                "base_revision": "", "before": {}, "after": {}, "controls": {},
                "applied": []}
    result.pop("_candidate")
    return result


def save(root: Path, name: str, base_revision: str, idempotency_key: str,
         changes: Any) -> Dict[str, Any]:
    root = Path(root).resolve()
    try:
        request_identity = _request_identity(changes)
        replayed = drafts.replay_config_request(
            root, name, idempotency_key, request_identity,
        )
        if replayed is not None:
            item = drafts.read_config(root, name)
            current = _snapshot(root, item["config"])
            return {
                "valid": True, "error": "", "error_code": "",
                "changed": sorted(str(path) for path in changes), "unchanged": False,
                "base_revision": item["draft"]["revision"],
                "before": current, "after": current,
                "controls": catalog_for(root, item["config"], current["selection"]),
                "applied": [], "saved": True, "result": replayed,
            }
    except (drafts.DraftError, SelectionEditError) as exc:
        return {"valid": False, "error": str(exc), "error_code": _error_code(exc),
                "changed": [], "unchanged": False, "base_revision": "",
                "before": {}, "after": {}, "saved": False, "controls": {},
                "applied": [], "result": None}
    try:
        planned = _preview(root, name, changes)
    except (drafts.DraftError, SelectionEditError) as exc:
        return {"valid": False, "error": str(exc), "error_code": _error_code(exc),
                "changed": [], "unchanged": False,
                "base_revision": "", "before": {}, "after": {}, "saved": False,
                "controls": {}, "applied": [], "result": None}
    updated = planned.pop("_candidate")
    if planned["unchanged"]:
        try:
            replayed = drafts.replay_config_checkpoint(root, name, idempotency_key, updated)
        except drafts.DraftError as exc:
            return dict(planned, valid=False, error=str(exc), error_code=exc.code,
                        saved=False, result=None)
        if replayed is not None:
            return dict(planned, changed=sorted(str(path) for path in changes), unchanged=False,
                        saved=True, result=replayed)
        return dict(planned, saved=False, result=None)
    try:
        result = drafts.checkpoint_config(
            root, name, base_revision, idempotency_key, updated,
            check_command=[sys.executable, "bin/harness", "lint"],
            request_identity=request_identity,
        )
    except drafts.DraftError as exc:
        return dict(planned, valid=False, error=str(exc), error_code=exc.code,
                    saved=False, result=None)
    return dict(planned, saved=True, result=result)
