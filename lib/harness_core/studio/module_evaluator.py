# SPDX-License-Identifier: MIT
"""Isolated worker for evaluating one disposable draft-module candidate."""
from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping


LINE_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>[1-9][0-9]*): (?P<message>.+)$")
MAX_DIAGNOSTICS = 100
MAX_MESSAGE_CHARS = 2000
TRUNCATED = " [truncated]"


def _harness(root: Path):
    sys.path.insert(0, str(root / "lib"))
    loader = importlib.machinery.SourceFileLoader("studio_candidate_harness", str(root / "bin" / "harness"))
    spec = importlib.util.spec_from_loader("studio_candidate_harness", loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate harness is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sanitize(value: str, aliases: Mapping[str, str]) -> str:
    result = value
    for source, label in sorted(aliases.items(), key=lambda item: -len(item[0])):
        if source:
            result = result.replace(source, label)
    return result


def bounded_text(value: str, aliases: Mapping[str, str], limit: int = MAX_MESSAGE_CHARS) -> str:
    """Alias internal paths, then cap the text; a cut is marked so it is never silent."""
    result = _sanitize(value, aliases)
    if len(result) <= limit:
        return result
    return result[:max(0, limit - len(TRUNCATED))] + TRUNCATED


def bounded_diagnostics(items: List[Dict[str, Any]],
                        limit: int = MAX_DIAGNOSTICS) -> List[Dict[str, Any]]:
    """Keep at most `limit` findings and say how many were left out."""
    kept = [dict(item, message=bounded_text(str(item.get("message", "")), {}))
            for item in items[:limit]]
    if len(items) > limit:
        kept.append({"message": str(len(items) - limit) + " more findings not shown; run "
                     "`citizen lint` in the draft for the full list",
                     "line": None, "severity": "error"})
    return kept


def _diagnostics(harness: Any, root: Path, relative: str,
                 config: Dict[str, Any], aliases: Mapping[str, str]) -> List[Dict[str, Any]]:
    findings = harness.lint_tree(root)
    try:
        findings += harness.check_context_cap(root, config)
    except TypeError:
        findings += harness.check_context_cap(root)
    findings += harness.check_cost_sidecars(root)
    findings += harness.check_stance_constraints(root)
    try:
        findings += harness.check_collisions(root, config)
    except TypeError:
        findings += harness.collisions.findings(root, config)
    notes: List[str] = []
    if (root / "primitives" / "roles").is_dir():
        findings += harness.changelog_fragments.findings(root, notes)
        findings += ["projection drift: " + name + "; run `citizen generate`"
                     for name in harness.primitive_catalog.projection_drift(root)]
    result = []
    for raw in findings:
        finding = bounded_text(str(raw), aliases)
        matched = LINE_FINDING.match(finding)
        line = int(matched.group("line")) if matched and matched.group("path") in (
            relative, "<draft>/" + relative,
        ) else None
        result.append({"message": matched.group("message") if line is not None else finding,
                       "line": line, "severity": "error"})
    return bounded_diagnostics(result)


def _runtime_texts(harness: Any, root: Path, config: Dict[str, Any]) -> Dict[str, str]:
    if hasattr(harness, "render_runtime_contexts"):
        return harness.render_runtime_contexts(config)
    posture = harness.load_posture()
    if posture is None:
        raise RuntimeError("candidate selection resolver is unavailable")
    selected = posture.selection({}, strict=True, config=config, root=root)
    effective = copy.deepcopy(config)
    effective["stances"] = {name: value for name, value in selected["stances"].items()
                            if value is not None}
    stances = harness.resolve_stances(effective)
    off = harness.switched_off(selected)
    externals, _warnings = harness.external_primitive_roots(effective)
    for entry in externals:
        entry["rules"] = [rule for rule in entry["rules"] if rule.stem not in off["rules"]]
        entry["skills"] = [skill for skill in entry["skills"] if skill.name not in off["skills"]]
    personal = harness.render_personal(effective, None)
    parts = []
    instructions = root / "claude" / "CLAUDE.md"
    if instructions.is_file():
        parts.append(instructions.read_text(encoding="utf-8").rstrip())
    parts.extend(rule.read_text(encoding="utf-8").rstrip()
                 for rule in sorted((root / "claude" / "rules").glob("*.md"))
                 if rule.stem not in off["rules"])
    parts.extend(rule.read_text(encoding="utf-8").rstrip()
                 for entry in externals for rule in entry["rules"])
    parts.extend(path.read_text(encoding="utf-8").rstrip()
                 for _name, path in sorted(stances.items()))
    if personal:
        parts.append(personal.rstrip())
    return {
        "claude-code": "\n".join(parts) + ("\n" if parts else ""),
        "codex": harness.render_codex_agents(stances, personal, externals, off["rules"]),
    }


def _rendering_sizes(harness: Any, root: Path,
                     config: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    rendered = _runtime_texts(harness, root, config)
    return {runtime: {"lines": len(text.splitlines()), "tokens": harness.est_tokens(len(text))}
            for runtime, text in rendered.items()}


def _authored(harness: Any, root: Path, config: Dict[str, Any]) -> Dict[str, int]:
    try:
        lines, _line_groups = harness.always_loaded_lines(root, config)
        tokens, _token_groups = harness.always_loaded_tokens(root, config)
        return {"lines": lines, "tokens": tokens}
    except TypeError:
        groups = list(harness.always_loaded_groups(root))
    seen = {(root / "primitives").resolve()}
    configured = config.get("primitive_roots", [])
    for value in configured if isinstance(configured, list) else []:
        if not isinstance(value, str):
            continue
        source = Path(value).resolve()
        if source in seen or not source.is_dir():
            continue
        seen.add(source)
        rules = sorted((source / "rules").glob("*.md")) if (source / "rules").is_dir() else []
        bodies = [path.read_text(encoding="utf-8") for path in rules if path.is_file()]
        if bodies:
            groups.append(("external rules", sum(len(body.splitlines()) for body in bodies),
                           sum(len(body) for body in bodies)))
        stances = source / "stances"
        if stances.is_dir():
            for dimension in sorted(path for path in stances.iterdir() if path.is_dir()):
                variants = [(path, path.read_text(encoding="utf-8"))
                            for path in dimension.glob("*.md") if path.is_file()]
                if variants:
                    _path, body = max(variants, key=lambda item: (len(item[1].splitlines()), item[0].name))
                    groups.append(("external stance", len(body.splitlines()), len(body)))
    return {"lines": sum(group[1] for group in groups),
            "tokens": harness.est_tokens(sum(group[2] for group in groups))}


def _active(harness: Any, root: Path, config: Dict[str, Any], item: Mapping[str, str]) -> bool:
    posture = harness.load_posture()
    if posture is None:
        raise RuntimeError("candidate selection resolver is unavailable")
    selected = posture.selection({}, strict=True, config=config, root=root)
    off = harness.switched_off(selected)
    if item["kind"] == "stances":
        dimension, variant = item["name"].split("/", 1)
        return selected.get("stances", {}).get(dimension) == variant
    if item["kind"] == "rules":
        return item["name"] not in off["rules"]
    if item["kind"] == "skills":
        return item["name"] not in off["skills"]
    return True


def evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(payload["root"])
    relative = str(payload["relative"])
    config = copy.deepcopy(payload["config"])
    aliases = dict(payload.get("aliases") or {})
    aliases[str(root)] = "<draft>"
    harness = _harness(root)
    rendered = _rendering_sizes(harness, root, config)
    authored = _authored(harness, root, config)
    item = payload["item"]
    source = (root / relative).read_text(encoding="utf-8")
    active = _active(harness, root, config, item)
    named = {runtime: source if active else "" for runtime in ("claude-code", "codex")}
    diagnostics = _diagnostics(harness, root, relative, config, aliases) \
        if payload.get("diagnostics") else []
    return {
        "ok": True,
        "line_cap": harness.ALWAYS_LOADED_CAP,
        "token_cap": harness.ALWAYS_LOADED_TOKEN_CAP,
        "authored": authored,
        "rendered": rendered,
        "named": named,
        "diagnostics": diagnostics,
    }


def main() -> int:
    try:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        result = evaluate(payload)
    except BaseException:
        result = {"ok": False, "error_code": "preview-unavailable",
                  "error": "draft module evaluation is unavailable"}
    sys.stdout.write(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
