# SPDX-License-Identifier: MIT
"""Schema-driven Studio configuration edits inside managed drafts."""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from harness_core import catalog, decision, integrations
from harness_core.decisions import controls

from . import auth, drafts


SCHEMA_VERSION = 1
REFERENCE_SOURCES = ("environment", "file")
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CLI_COMMANDS = {
    "schema": ("citizen", "draft", "settings", "schema", "--json"),
    "read": ("citizen", "draft", "settings", "read", "{draft}", "--json"),
    "preview": ("citizen", "draft", "settings", "preview", "{draft}",
                "--changes", "changes.json", "--json"),
    "save": ("citizen", "draft", "settings", "save", "{draft}",
             "--base-revision", "{revision}", "--idempotency-key", "KEY",
             "--changes", "changes.json", "--json"),
}

TELEMETRY_NOTICE = (
    "Ledger export sends local usage and decision rows to the configured OTLP/HTTP endpoint. "
    "Native export is separate: runtimes may attach account, user, session, repository, model, "
    "token, cost, tool, API, error, and duration attributes. Claude Code can resolve a header "
    "reference at runtime; Codex cannot receive authentication headers without storing a value, "
    "so Studio never writes one for it."
)


def _field(path: str, section: str, label: str, help_text: str, kind: str,
           provenance: str, **extra: Any) -> Dict[str, Any]:
    return dict({
        "path": path,
        "section": section,
        "label": label,
        "help": help_text,
        "kind": kind,
        "provenance": provenance,
        "required": False,
        "options": [],
        "reference_sources": [],
        "constraints": {},
    }, **extra)


# This is the field-description authority consumed by both the validator and the generic UI.
# Runtime validators remain authoritative for the blocks they own.
FIELDS = (
    _field("identity.name", "identity", "Name", "How the agent addresses you.", "string",
           "config.example.json identity.name", required=True),
    _field("identity.pronouns", "identity", "Pronouns", "Pronouns used when referring to you.",
           "string", "config.example.json identity.pronouns"),
    _field("identity.role", "identity", "Role", "One line used to pitch answers at the right level.",
           "string", "config.example.json identity.role", required=True),
    _field("identity.github", "identity", "GitHub handle", "Your handle, without an at-sign.",
           "string", "config.example.json identity.github"),
    _field("identity.timezone", "identity", "Timezone", "An IANA timezone such as America/New_York.",
           "string", "config.example.json identity.timezone"),
    _field("identity.expertise", "identity", "Explanation level", "How much background answers include.",
           "select", "bin/harness GUIDANCE", options=["expert", "beginner"]),
    _field("permissions", "preferences", "Permission posture",
           "Whether clients inherit, ask manually, review automatically, or bypass prompts.", "select",
           "bin/harness posture", options=["inherit", "manual", "auto", "bypass"]),
    _field("permissions_bypass_acknowledged", "preferences", "Acknowledge bypass risk",
           "Required before bypass can be saved.", "boolean", "bin/harness posture"),
    _field("primitive_roots", "preferences", "External primitive roots",
           "Absolute directories searched for additional primitives.", "string-list",
           "policy/hooks/posture.py primitive_roots", constraints={"absolute_paths": True}),
    _field("telemetry.export", "telemetry", "Ledger export", "Off, or copy ledger rows over OTLP/HTTP.",
           "select", "claude/hooks/telemetry.py KNOWN_KEYS", options=["off", "otlp"]),
    _field("telemetry.endpoint", "telemetry", "OTLP endpoint", "Base HTTP(S) URL; /v1/logs is appended.",
           "url", "claude/hooks/telemetry.py settings", default="http://localhost:4318",
           constraints={"schemes": ["http", "https"]}),
    _field("telemetry.headers_env", "telemetry", "Header environment reference",
           "Name of an environment variable containing headers. The value is never read by Studio.",
           "reference", "claude/hooks/telemetry.py settings", reference_sources=["environment"], default=None),
    _field("telemetry.headers_file", "telemetry", "Header file reference",
           "Path to a mode-600 file outside repositories. Studio never reads the file.", "reference",
           "claude/hooks/telemetry.py settings", reference_sources=["file"], default=None),
    _field("telemetry.labels", "telemetry", "Export labels",
           "A JSON object of scalar attributes added to ledger records.", "json-object",
           "claude/hooks/telemetry.py settings", default={},
           constraints={"value_types": ["string", "integer", "number", "boolean"]}),
    _field("telemetry.native", "telemetry", "Native runtime export",
           "Runtimes asked to export their own telemetry independently of ledger export.", "multi-select",
           "claude/hooks/telemetry.py NATIVE_RUNTIMES", options=["claude-code", "codex"], default=[]),
    _field("telemetry.decisions", "telemetry", "Local decision log",
           "Record local hook decisions; these rows are not exported.", "boolean",
           "claude/hooks/telemetry.py settings", default=True),
    _field("telemetry.completion_claim", "telemetry", "Completion claim",
           "Include the final assistant claim in local stop-gate evidence.", "boolean",
           "claude/hooks/telemetry.py settings", default=False),
    _field("telemetry.allow_sample_rate", "telemetry", "Allowed-command sample rate",
           "One distinct allowed command in this many is retained; zero disables sampling.", "integer",
           "claude/hooks/telemetry.py settings", constraints={"minimum": 0}, default=20),
    _field("governance.provider", "governance", "Governance provider",
           "The deterministic or transport-backed provider used for governed actions.", "select",
           "harness_core.decision providers",
           options=sorted(set(decision.PROVIDERS) | set(decision.TRANSPORT_PROVIDERS))),
    _field("governance.jev.mode", "governance", "JEV default mode",
           "Default decision mode; per-point modes can only narrow or specialize it.", "select",
           "harness_core.decisions.controls MODES", options=list(controls.MODES)),
    _field("governance.jev.modes", "governance", "JEV point modes",
           "A JSON object mapping known decision points to modes.", "json-object",
           "harness_core.decisions.controls POINTS", default={},
           constraints={"keys": list(controls.POINTS), "values": list(controls.MODES)}),
    _field("governance.jev.state_fields", "governance", "Outbound state fields",
           "The only optional context fields a JEV request may carry.", "multi-select",
           "harness_core.decisions.controls STATE_FIELDS", options=list(controls.STATE_FIELDS), default=[]),
    _field("governance.jev.sentinel", "governance", "Kill-switch path",
           "When this file exists, JEV calls stop without another configuration change.", "string",
           "harness_core.decisions.controls Controls", default=""),
    _field("governance.jev.timeout", "governance", "Request timeout", "Seconds in the interval (0, 10].",
           "number", "harness_core.decisions.controls Controls",
           constraints={"exclusive_minimum": 0, "maximum": 10}, default=2),
    _field("governance.jev.max_requests", "governance", "Session request cap",
           "Maximum provider requests in one session.", "integer",
           "harness_core.decisions.controls Controls", constraints={"minimum": 0}, default=50),
    _field("governance.jev.max_tokens", "governance", "Session token cap",
           "Maximum provider tokens in one session.", "integer",
           "harness_core.decisions.controls Controls", constraints={"minimum": 0}, default=200000),
    _field("integrations.architecture-viewer.implementation", "integrations", "Viewer implementation",
           "Use the built-in viewer or a registered custom adapter.", "select",
           "harness_core.integrations selection", options=["builtin", "custom"]),
    _field("integrations.architecture-viewer.adapter", "integrations", "Viewer adapter",
           "Registered adapter identifier; required for a custom implementation.", "string",
           "harness_core.integrations selection", constraints={
               "pattern": "^[a-z][a-z0-9-]*$",
               "depends_on": {"path": "integrations.architecture-viewer.implementation",
                              "equals": "custom"}}),
)

MODELED_CONTAINERS = tuple(sorted({
    ".".join(field["path"].split(".")[:depth])
    for field in FIELDS
    for depth in range(1, len(field["path"].split(".")))
}))

SECTIONS = (
    ("identity", "Identity", "Who the harness is helping and how answers should meet you."),
    ("preferences", "Preferences", "Machine-wide safety and primitive discovery settings."),
    ("telemetry", "Telemetry", TELEMETRY_NOTICE),
    ("governance", "Governance", "Decision-provider controls and outbound-data limits."),
    ("integrations", "Integrations", "Provider-neutral implementations selected through validated descriptors."),
)


def _get(value: Dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = value
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _set(value: Dict[str, Any], path: str, item: Any) -> None:
    parts = path.split(".")
    node = value
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = item


def _delete(value: Dict[str, Any], path: str) -> None:
    parts = path.split(".")
    node: Any = value
    parents = []
    for part in parts[:-1]:
        if not isinstance(node, dict) or not isinstance(node.get(part), dict):
            return
        parents.append((node, part))
        node = node[part]
    node.pop(parts[-1], None)
    for parent, part in reversed(parents):
        if parent.get(part) == {}:
            parent.pop(part)


def descriptor(root: Path) -> Dict[str, Any]:
    try:
        defaults = json.loads((Path(root) / "config.example.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        defaults = {}
    fields = []
    missing = object()
    for item in FIELDS:
        public = copy.deepcopy(item)
        default = _get(defaults, item["path"], missing)
        if default is missing:
            default = copy.deepcopy(item.get("default"))
        if item["kind"] == "reference":
            default = None
        public["default"] = default
        fields.append(public)
    return {
        "schema_version": SCHEMA_VERSION,
        "commands": {name: " ".join(command) for name, command in CLI_COMMANDS.items()},
        "sections": [
            {"id": name, "label": label, "description": description,
             "fields": [field for field in fields if field["section"] == name]}
            for name, label, description in SECTIONS
        ],
    }


def _reference(field: Dict[str, Any], value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, dict) or set(value) != {"source", "name"}:
        raise ValueError("must be a reference with source and name")
    source, name = value.get("source"), value.get("name")
    if source not in field["reference_sources"] or not isinstance(name, str):
        raise ValueError("must use an allowed reference source and a text name")
    if not name:
        return ""
    if source == "environment" and ENVIRONMENT_NAME.fullmatch(name) is None:
        raise ValueError("environment references use uppercase variable names")
    if source == "file" and ("\x00" in name or "\n" in name):
        raise ValueError("file references must be one path")
    return name


def _coerce(field: Dict[str, Any], value: Any) -> Any:
    kind = field["kind"]
    if kind in ("string", "url", "select"):
        if not isinstance(value, str):
            raise ValueError("must be text")
        if kind == "select" and value not in field["options"]:
            raise ValueError("must be one of " + ", ".join(field["options"]))
        if field["required"] and not value.strip():
            raise ValueError("is required")
        if kind == "url" and value and not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL")
        return value
    if kind == "reference":
        return _reference(field, value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if kind == "integer" and not isinstance(value, int):
            raise ValueError("must be a whole number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("must be a finite number")
        limits = field["constraints"]
        if "minimum" in limits and value < limits["minimum"]:
            raise ValueError("must be at least " + str(limits["minimum"]))
        if "exclusive_minimum" in limits and value <= limits["exclusive_minimum"]:
            raise ValueError("must be greater than " + str(limits["exclusive_minimum"]))
        if "maximum" in limits and value > limits["maximum"]:
            raise ValueError("must be at most " + str(limits["maximum"]))
        return value
    if kind in ("string-list", "multi-select"):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("must be a list of text values")
        if kind == "multi-select":
            unknown = sorted(set(value) - set(field["options"]))
            if unknown:
                raise ValueError("contains an unsupported choice")
        return list(value)
    if kind == "json-object":
        if not isinstance(value, dict):
            raise ValueError("must be a JSON object")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise ValueError("must contain only finite JSON values") from None
        return copy.deepcopy(value)
    raise ValueError("has an unsupported field type")


def _load_telemetry(root: Path):
    path = Path(root) / "claude" / "hooks" / "telemetry.py"
    if not path.is_file():
        raise ValueError("telemetry validator is unavailable")
    spec = importlib.util.spec_from_file_location("studio_telemetry", str(path))
    if spec is None or spec.loader is None:
        raise ValueError("telemetry validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_candidate(root: Path, candidate: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    errors: List[Dict[str, str]] = []
    warnings: List[str] = []
    missing = object()
    for path in MODELED_CONTAINERS:
        value = _get(candidate, path, missing)
        if value is not missing and not isinstance(value, dict):
            errors.append({"path": path, "message": "must be a JSON object"})
    for field in FIELDS:
        persisted = _get(candidate, field["path"], missing)
        if persisted is missing:
            if field["required"]:
                errors.append({"path": field["path"], "message": "is required"})
            continue
        try:
            if field["kind"] == "reference":
                if not isinstance(persisted, str) or not persisted:
                    raise ValueError()
                source = field["reference_sources"][0]
                _reference(field, {"source": source, "name": persisted})
            else:
                value = persisted
                if field["path"] == "telemetry.native" and isinstance(value, bool):
                    value = list(field["options"]) if value else []
                _coerce(field, value)
        except ValueError as exc:
            message = ("stored reference is malformed; clear or replace it"
                       if field["kind"] == "reference" else str(exc))
            errors.append({"path": field["path"], "message": message})
    if candidate.get("permissions") == "bypass" and candidate.get("permissions_bypass_acknowledged") is not True:
        errors.append({"path": "permissions_bypass_acknowledged",
                       "message": "must be acknowledged before bypass is saved"})
    roots = candidate.get("primitive_roots", [])
    if not isinstance(roots, list) or any(not isinstance(item, str) or not Path(item).expanduser().is_absolute()
                                          for item in roots):
        errors.append({"path": "primitive_roots", "message": "must contain only absolute directory paths"})
    else:
        try:
            defaults = json.loads((Path(root) / "config.example.json").read_text(encoding="utf-8"))
            stance_config = copy.deepcopy(defaults)
            stance_config["primitive_roots"] = list(roots)
            if isinstance(candidate.get("stances"), dict):
                stance_config["stances"].update(candidate["stances"])
            catalog.resolve_stances(Path(root), stance_config)
        except (OSError, ValueError) as exc:
            errors.append({"path": "primitive_roots", "message": str(exc)})
    try:
        _load_telemetry(root).settings(candidate)
    except (ValueError, OSError) as exc:
        errors.append({"path": "telemetry", "message": str(exc)})
    try:
        decision.provider_class(_get(candidate, "governance.provider", "none"))
        controls.Controls.from_config(candidate)
    except (decision.PolicyError, ValueError) as exc:
        errors.append({"path": "governance", "message": str(exc)})
    try:
        binding = integrations.resolve(root, candidate)
        if binding["availability"] != "available":
            warnings.append("Architecture viewer unavailable: " + str(binding.get("reason") or "not installed"))
        else:
            health = integrations.invoke(binding, "describe", timeout=30)
            if health.get("status") != "ok":
                errors.append({"path": "integrations.architecture-viewer",
                               "message": "adapter describe health check failed"})
    except integrations.IntegrationError:
        errors.append({"path": "integrations.architecture-viewer",
                       "message": "adapter describe health check failed"})
    return errors, warnings


def _public_values(config: Dict[str, Any]) -> Dict[str, Any]:
    values = {}
    missing = object()
    for field in FIELDS:
        value = _get(config, field["path"], missing)
        if field["path"] == "telemetry.native" and isinstance(value, bool):
            value = list(field["options"]) if value else []
        if field["kind"] == "reference":
            source = field["reference_sources"][0]
            if value is missing:
                value = None
            else:
                try:
                    if not isinstance(value, str) or not value:
                        raise ValueError()
                    value = {"source": source, "name": _reference(
                        field, {"source": source, "name": value},
                    )}
                except ValueError:
                    value = {"configured": True}
        elif value is missing:
            value = None
        values[field["path"]] = auth.public_data(value, field["path"].split(".")[-1])
    return values


def read(root: Path, name: str) -> Dict[str, Any]:
    try:
        item = drafts.read_config(Path(root), name)
    except drafts.DraftError as exc:
        return {"status": "unavailable", "message": str(exc), "draft": {},
                "values": {}, "warnings": []}
    config = item["config"]
    errors, warnings = _validate_candidate(Path(root), config)
    return {
        "status": "error" if errors else "ready",
        "message": errors[0]["message"] if errors else "Draft configuration is ready.",
        "draft": item["draft"],
        "values": _public_values(config),
        "warnings": warnings,
    }


def _preview(root: Path, name: str, changes: Any) -> Dict[str, Any]:
    item = drafts.read_config(Path(root), name)
    candidate = copy.deepcopy(item["config"])
    errors: List[Dict[str, str]] = []
    changed: List[str] = []
    fields = dict((field["path"], field) for field in FIELDS)
    if not isinstance(changes, dict):
        errors.append({"path": "changes", "message": "must be an object keyed by field path"})
        changes = {}
    for path, value in changes.items():
        field = fields.get(path)
        if field is None:
            errors.append({"path": str(path), "message": "is not a configurable field"})
            continue
        try:
            coerced = _coerce(field, value)
        except ValueError as exc:
            errors.append({"path": path, "message": str(exc)})
            continue
        missing = object()
        existing = _get(item["config"], path, missing)
        if field["kind"] == "reference" and coerced == "":
            _delete(candidate, path)
        else:
            _set(candidate, path, coerced)
        if field["kind"] == "reference":
            changed_value = ((coerced == "" and existing is not missing)
                             or (coerced != "" and coerced != existing))
        else:
            changed_value = existing is missing or coerced != existing
        if changed_value:
            changed.append(path)
    dependency_errors, warnings = _validate_candidate(Path(root), candidate)
    errors.extend(dependency_errors)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "changed": sorted(changed),
        "preview": _public_values(candidate),
        "base_revision": item["draft"]["revision"],
        "_candidate": candidate,
    }


def preview(root: Path, name: str, changes: Any) -> Dict[str, Any]:
    """Return a redacted validation preview; private candidate data never crosses the API."""
    try:
        result = _preview(root, name, changes)
    except drafts.DraftError as exc:
        return {"valid": False, "errors": [{"path": "draft", "message": str(exc)}],
                "warnings": [], "changed": [], "preview": {}, "base_revision": ""}
    result.pop("_candidate")
    return result


def save(root: Path, name: str, base_revision: str, idempotency_key: str,
         changes: Any) -> Dict[str, Any]:
    try:
        planned = _preview(Path(root), name, changes)
    except drafts.DraftError as exc:
        return {"valid": False, "errors": [{"path": "draft", "message": str(exc)}],
                "warnings": [], "changed": [], "preview": {}, "base_revision": "",
                "saved": False, "result": None}
    candidate = planned.pop("_candidate")
    if not planned["valid"]:
        return dict(planned, saved=False, result=None)
    try:
        result = drafts.checkpoint_config(
            Path(root), name, base_revision, idempotency_key, candidate,
            check_command=[sys.executable, "bin/harness", "lint"],
        )
    except drafts.DraftError as exc:
        return {"valid": False, "errors": [{"path": "draft", "message": str(exc)}],
                "warnings": [], "changed": [], "preview": {}, "base_revision": "",
                "saved": False, "result": None}
    return dict(planned, saved=True, result=result)
