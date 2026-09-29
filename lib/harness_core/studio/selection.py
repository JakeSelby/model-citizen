# SPDX-License-Identifier: MIT
"""Effective Studio selection with the provenance needed to explain it."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shlex
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from harness_core import catalog


SCHEMA_VERSION = 1
CLI_COMMANDS = {
    "selection": ("citizen", "selection", "--json"),
    "budget": ("citizen", "lint"),
}
RUNTIMES = (("claude-code", "Claude Code"), ("codex", "Codex"))
_HARNESS_MODULES: Dict[str, Any] = {}
_EPHEMERAL_IMPORT_LOCK = threading.RLock()


class SelectionError(ValueError):
    """A requested selection cannot be resolved safely."""


def _harness_module(root: Path):
    key = str(root)
    if key not in _HARNESS_MODULES:
        path = root / "bin" / "harness"
        loader = importlib.machinery.SourceFileLoader("studio_selection_harness", str(path))
        spec = importlib.util.spec_from_loader("studio_selection_harness", loader)
        if spec is None or spec.loader is None:
            raise SelectionError("the installed harness cannot report its context budget")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _HARNESS_MODULES[key] = module
    return _HARNESS_MODULES[key]


def discard_ephemeral_modules(root: Path) -> None:
    """Forget modules loaded for a disposable preview checkout."""
    resolved = Path(root).resolve()
    for cache in (_HARNESS_MODULES, catalog._POSTURE_MODULES):
        for key in list(cache):
            try:
                matches = Path(key).resolve() == resolved
            except OSError:
                matches = key == str(root)
            if matches:
                cache.pop(key, None)


@contextmanager
def ephemeral_harness_module(root: Path):
    """Load one disposable checkout without retaining its imports or search paths."""
    root = Path(root).resolve()
    with _EPHEMERAL_IMPORT_LOCK:
        prior_path = list(sys.path)
        resident = {name: module for name, module in sys.modules.items()
                    if name == "harness_core" or name.startswith("harness_core.")}
        for name in resident:
            sys.modules.pop(name, None)
        prior_modules = set(sys.modules)
        sys.path.insert(0, str(root / "lib"))
        try:
            yield _harness_module(root)
        finally:
            discard_ephemeral_modules(root)
            for name, module in list(sys.modules.items()):
                source = getattr(module, "__file__", None)
                if name in prior_modules or name in resident or not source:
                    continue
                try:
                    Path(source).resolve().relative_to(root)
                except (OSError, ValueError):
                    continue
                sys.modules.pop(name, None)
            sys.modules.update(resident)
            sys.path[:] = prior_path
            for path in list(sys.path_importer_cache):
                try:
                    Path(path).resolve().relative_to(root)
                except (OSError, ValueError):
                    continue
                sys.path_importer_cache.pop(path, None)


def _path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return str(Path(value).expanduser().resolve())


def _entry(source: str, value: object, source_file: str, saved: bool) -> Dict[str, object]:
    return {"source": source, "value": value, "source_file": source_file, "saved": saved}


def _default_values(posture, config: Mapping[str, object], root: Path) -> Dict[str, Dict[str, object]]:
    defaults: Dict[str, Dict[str, object]] = {}
    for kind, descriptor in posture.selection_kinds(root).items():
        switch = descriptor.get("value") == "switch"
        values = {unit: ("on" if switch else posture.DEFAULT_STANCES.get(unit))
                  for unit in posture._units(kind, descriptor, config, root)}
        if not switch:
            values.update(posture.DEFAULT_STANCES)
        defaults[kind] = dict(sorted(values.items()))
    return defaults


def _layer_origins(posture, config: Mapping[str, object], env: Mapping[str, str],
                   root: Path) -> Iterable[Tuple[str, Mapping[str, object], str, bool, bool]]:
    """Yield source, document, source path, saved state and environment-sugar marker."""
    _, ladder = posture.layers(config, env, True, root)
    session_index = 0
    modes = posture.modes(config, root)[0]
    for source, document in ladder:
        source_file, saved, environment = "", True, False
        if source in ("init", "user"):
            source_file = str(posture.config_path(env).expanduser().resolve())
        elif source == "project":
            source_file = _path(env.get("HARNESS_PROJECT_CONFIG"))
        elif source == "session":
            if session_index == 0:
                source_file = _path(env.get("HARNESS_SESSION_CONFIG"))
            else:
                source_file, saved, environment = "environment", False, True
            session_index += 1
        elif source.startswith("mode:"):
            mode_path = modes.get(source.partition(":")[2])
            source_file = str(mode_path.resolve()) if mode_path is not None else ""
        yield source, document, source_file, saved, environment


def _environment_name(kind: str, unit: str) -> str:
    if kind == "mode":
        return "HARNESS_MODE"
    if kind == "stances":
        return "HARNESS_STANCE_" + unit.upper().replace("-", "_")
    return "environment"


def _history(posture, config: Mapping[str, object], env: Mapping[str, str], root: Path,
             document: Mapping[str, object]) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    default_file = str((root / "policy" / "hooks" / "posture.py").resolve())
    defaults = _default_values(posture, config, root)
    rows: Dict[str, object] = {}
    for kind in sorted(defaults):
        rows[kind] = {unit: [_entry("default", value, default_file, True)]
                      for unit, value in defaults[kind].items()}
        for unit in document.get(kind, {}):
            rows[kind].setdefault(unit, [])
    mode_history: List[Dict[str, object]] = [_entry("default", None, default_file, True)]

    for source, layer, source_file, saved, environment in _layer_origins(
            posture, config, env, root):
        if not isinstance(layer, Mapping):
            continue
        if isinstance(layer.get("mode"), str) and str(layer["mode"]).strip():
            origin = _environment_name("mode", "") if environment else source_file
            mode_history.append(_entry(source, str(layer["mode"]).strip(), origin, saved))
        for kind, units in rows.items():
            selected = layer.get(kind)
            if not isinstance(selected, Mapping):
                continue
            for unit, value in selected.items():
                if unit not in units or not isinstance(value, str) or not value.strip():
                    continue
                origin = _environment_name(kind, unit) if environment else source_file
                units[unit].append(_entry(source, value.strip(), origin, saved))

    groups = []
    for kind in sorted(rows):
        group_rows = []
        selected = document.get(kind, {})
        selected_sources = document.get("sources", {}).get(kind, {})
        for unit, entries in sorted(rows[kind].items()):
            effective = entries[-1]
            # The resolver may refuse a non-strict core-hook override. Keep its authoritative
            # source/value even when the attempted layer history ends later.
            if effective["value"] != selected.get(unit) or effective["source"] != selected_sources.get(unit):
                effective = _entry(selected_sources.get(unit, "default"), selected.get(unit),
                                   default_file, True)
            group_rows.append({"unit": unit, "value": selected.get(unit),
                               "source": effective["source"],
                               "source_file": effective["source_file"],
                               "saved": effective["saved"], "overridden": entries[:-1]})
        groups.append({"kind": kind, "rows": group_rows})
    mode = mode_history[-1]
    return ({"value": document.get("mode"), "source": document["sources"]["mode"],
             "source_file": mode["source_file"], "saved": mode["saved"],
             "overridden": mode_history[:-1]}, groups)


def _budgets(root: Path, config: Mapping[str, object], document: Mapping[str, object]) -> List[Dict[str, object]]:
    harness = _harness_module(root)
    lines, _ = harness.always_loaded_lines(root)
    tokens, _ = harness.always_loaded_tokens(root)
    selected_lines = harness.effective_always_loaded_lines(root, document)
    budgets = []
    for runtime, label in RUNTIMES:
        section = "claude" if runtime == "claude-code" else runtime
        runtime_config = config.get(section)
        managed = not isinstance(runtime_config, Mapping) or runtime_config.get("manage") is not False
        budgets.append({"runtime": runtime, "label": label, "managed": managed,
                        "used_tokens": tokens, "token_cap": harness.ALWAYS_LOADED_TOKEN_CAP,
                        "used_lines": lines, "line_cap": harness.ALWAYS_LOADED_CAP,
                        "selected_lines": selected_lines})
    return budgets


def report(root: Path, repository: str = "", project_file: str = "",
           environ: Optional[Mapping[str, str]] = None) -> Dict[str, object]:
    """Resolve one Studio selection report through the policy kernel and lint authority."""
    root = Path(root).resolve()
    env = dict(os.environ if environ is None else environ)
    repository_path = Path(repository).expanduser().resolve() if repository else Path.cwd().resolve()
    if not repository_path.is_dir():
        raise SelectionError("repository is not a directory")
    if project_file:
        project_path = Path(project_file).expanduser().resolve()
        if not project_path.is_file():
            raise SelectionError("project selection file is not readable")
        env["HARNESS_PROJECT_CONFIG"] = str(project_path)

    posture = catalog.posture_module(root)
    if posture is None:
        raise SelectionError("the installed harness has no selection resolver")
    try:
        config = posture._user_config(env, True)
        document = posture.selection(env, strict=True, config=config, root=root)
        mode, groups = _history(posture, config, env, root, document)
        budgets = _budgets(root, config, document)
    except (OSError, ValueError) as exc:
        raise SelectionError(str(exc)) from exc

    selected_project = _path(env.get("HARNESS_PROJECT_CONFIG"))
    selection_command = "citizen selection --json"
    if selected_project:
        selection_command = "HARNESS_PROJECT_CONFIG=%s %s" % (
            shlex.quote(selected_project), selection_command)
    return {"schema_version": SCHEMA_VERSION, "repository": str(repository_path),
            "project_file": selected_project, "selection": document, "mode": mode,
            "groups": groups, "budgets": budgets,
            "commands": {"selection": selection_command, "budget": "citizen lint"}}
