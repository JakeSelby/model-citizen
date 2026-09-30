# SPDX-License-Identifier: MIT
"""Add a module to a draft's personal root from a template, or fork a core module into it.

A draft can write only inside its own checkout, so the personal root a draft adds modules to is a
directory in that checkout, registered in the draft's `primitive_roots` as the installed checkout's
path; `module_editing._mapped_config` maps it into the draft, as it does for every draft-owned root.
Every new or forked module is checked twice before a checkpoint exists: its manifest by the
resolver's own `validate_manifest`, then the whole selection strictly (AD-22's dependency, conflict
and slot refusals, and every mode) on a candidate copy of the root, and again by the save's check
command, after `citizen lint`, on the written draft.

Switches are keyed by unit name across roots, so a fork takes a new name: switching the core
module off under the same name would switch the fork off with it. The root's `forks.json` records
the source, the harness version, the draft's base revision and a sha256 per file; `module_library`
reads the original back from git at that revision to diff it against the core module after an update.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import drafts, module_editing, module_library


SCHEMA_VERSION = 1
OWN_ROOT = "personal-primitives"
TEMPLATE_KINDS = ("rules", "skills", "stances", "modes")
FORK_KINDS = module_library.FORK_KINDS
MANIFEST_KINDS = ("rules", "skills")
MAX_DESCRIPTION = 400
LIB = Path(__file__).resolve().parents[2]
CLI_COMMANDS = {
    "read": ("citizen", "draft", "module", "templates", "{draft}", "--json"),
    "preview": ("citizen", "draft", "module", "plan", "{draft}", "--request", "request.json",
                "--json"),
    "save": ("citizen", "draft", "module", "add", "{draft}", "--request", "request.json",
             "--base-revision", "{revision}", "--idempotency-key", "KEY", "--json"),
    "library": ("citizen", "draft", "module", "library", "{draft}", "--json"),
}
TEMPLATES = (
    {"kind": "rules", "label": "Rule", "name_hint": "lowercase-name",
     "detail": "A resident rule, with a manifest."},
    {"kind": "skills", "label": "Skill", "name_hint": "lowercase-name",
     "detail": "An on-demand skill whose listing is resident, with a manifest."},
    {"kind": "stances", "label": "Stance variant", "name_hint": "dimension/variant",
     "detail": "A variant of a new or existing stance dimension; variants are chosen, not switched."},
    {"kind": "modes", "label": "Mode", "name_hint": "lowercase-name",
     "detail": "A named selection preset, empty until you add keys."},
)
_FRONTMATTER_NAME = re.compile(r"^name:.*$", re.MULTILINE)
_CHECK = ("import sys; sys.path.insert(0, sys.argv[1]); "
          "from harness_core.studio import module_authoring; "
          "raise SystemExit(module_authoring.check_main())")


class AuthoringError(ValueError):
    """A new or forked module request that cannot be planned or saved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any) -> bool:
    # The resolver's identifier rule (posture._identifier), for names the draft has not loaded yet.
    return (isinstance(value, str) and bool(value) and value[0].isalpha() and value.islower()
            and value.isascii() and all(c.isalnum() or c == "-" for c in value))


def _title(name: str) -> str:
    words = name.replace("-", " ").strip()
    return words[:1].upper() + words[1:]


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def load_posture(root: Path):
    """The checkout's own resolver, loaded fresh so no draft module stays cached in-process."""
    path = Path(root) / "policy" / "hooks" / "posture.py"
    spec = importlib.util.spec_from_file_location("studio_authoring_posture_" + uuid.uuid4().hex,
                                                  str(path))
    if spec is None or spec.loader is None or not path.is_file():
        raise AuthoringError("resolver-unavailable", "the draft has no selection resolver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolution_findings(posture: Any, config: Mapping[str, Any], root: Path) -> List[str]:
    """Every refusal strict resolution gives this configuration: manifests, switches and modes."""
    findings: List[str] = []
    try:
        posture.selection(env={}, strict=True, config=dict(config), root=root)
    except ValueError as exc:
        findings.extend(line for line in str(exc).splitlines() if line.strip())
    found, errors = posture.modes(dict(config), root)
    findings.extend(errors)
    for name, path in sorted(found.items()):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            findings.append("mode file " + name + " is not readable JSON: " + str(exc))
            continue
        findings.extend(posture.validate_mode(name, data, dict(config), root))
    return sorted(dict.fromkeys(findings))


def check_main() -> int:
    """The save's check command, run in the written draft: `citizen lint`, then strict resolution."""
    linted = subprocess.run([sys.executable, "bin/harness", "lint"], capture_output=True, text=True)
    if linted.returncode:
        sys.stderr.write(linted.stdout + linted.stderr)
        return linted.returncode
    home = Path(os.environ.get("HARNESS_HOME") or Path.home())
    path = home / ".config" / "agent-harness" / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        findings = resolution_findings(load_posture(Path.cwd()), config, Path.cwd())
    except (OSError, ValueError) as exc:
        findings = [str(exc)]
    for line in findings:
        sys.stderr.write("manifest check: " + line + "\n")
    return 1 if findings else 0


def _own_root(worktree: Path, mapped: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The first registered root inside the draft checkout, other than core: the draft's own."""
    core = Path(os.path.abspath(str(worktree / "primitives")))
    for index, value in enumerate(mapped.get("primitive_roots", [])):
        path = Path(os.path.abspath(str(value)))
        if path != core and module_editing._lexically_inside(worktree, path):
            return {"id": "root-%d" % (index + 1), "label": path.name or "draft root",
                    "relative": path.relative_to(worktree).as_posix(), "path": path}
    return None


def _existing(worktree: Path, mapped: Mapping[str, Any]) -> Dict[str, set]:
    found: Dict[str, set] = {kind: set() for kind in TEMPLATE_KINDS}
    for entry in module_library._roots(worktree, dict(mapped)):
        for kind in TEMPLATE_KINDS:
            definition = module_library.KINDS[kind]
            for source in module_library._source_entries(worktree, entry, kind, definition):
                found[kind].add(source["name"])
    return found


def _manifest_template(kind: str, claim: str) -> Dict[str, Any]:
    surface = ["resident-context"] if kind == "rules" else ["resident-context", "on-demand-context"]
    return {"claims": [claim], "surface": surface, "instruments": [], "slot": None,
            "dependencies": [], "conflicts": []}


def _template_files(kind: str, name: str, description: str) -> Dict[str, bytes]:
    if kind == "rules":
        return {"rules/" + name + ".md":
                ("# " + _title(name) + "\n\n- " + description + "\n").encode("utf-8")}
    if kind == "skills":
        return {"skills/" + name + "/SKILL.md": (
            "---\nname: " + name + "\ndescription: " + description + "\n---\n\n# "
            + _title(name) + "\n\n" + description + "\n").encode("utf-8")}
    if kind == "stances":
        dimension, variant = name.split("/", 1)
        return {"stances/" + dimension + "/" + variant + ".md": (
            "# " + _title(dimension) + " stance: " + variant + "\n\n" + description + "\n"
        ).encode("utf-8")}
    return {"modes/" + name + ".json": _canonical({"schema_version": 1,
                                                   "description": description})}


def _renamed_skill(content: bytes, name: str) -> bytes:
    """A forked SKILL.md whose frontmatter `name:` is the fork's, so two skills never share one."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    if not text.startswith("---\n"):
        return content
    closing = text.find("\n---", 4)
    head = text[:closing] if closing > 0 else text
    return (_FRONTMATTER_NAME.sub("name: " + name, head, count=1) + text[len(head):]).encode("utf-8")


def _shown(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return "(binary file, %d bytes, sha256 %s)" % (len(content), module_library.digest(content))


def _json_file(worktree: Path, relative: str) -> Dict[str, Any]:
    path = worktree / relative
    if not path.exists():
        return {"schema_version": 1}
    try:
        text, _digest = module_editing._regular_text_at(worktree, relative)
        data = json.loads(text)
    except (module_editing.ModuleEditError, ValueError) as exc:
        raise AuthoringError("root-file-unreadable", relative.rsplit("/", 1)[-1]
                             + " in the personal root is not readable JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise AuthoringError("root-file-unreadable", relative.rsplit("/", 1)[-1]
                             + " in the personal root is not a schema_version 1 file")
    return data


def _normalized(request: Any) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise AuthoringError("invalid-request", "the request must be a JSON object")
    action = request.get("action")
    if action not in ("add", "fork"):
        raise AuthoringError("invalid-request", "action is add or fork")
    description = request.get("description", "")
    if (not isinstance(description, str) or len(description) > MAX_DESCRIPTION
            or any(not character.isprintable() for character in description)):
        raise AuthoringError("invalid-description",
                             "description is one line of at most %d characters" % MAX_DESCRIPTION)
    if "---" in description:
        # `catalog.frontmatter` splits a skill on the first `---`, so the listing would be cut short.
        raise AuthoringError("invalid-description", "description cannot contain ---")
    name = request.get("name", "")
    create_root = request.get("create_root", False)
    if not isinstance(name, str) or not isinstance(create_root, bool):
        raise AuthoringError("invalid-request", "name is text and create_root is true or false")
    result = {"action": action, "name": name.strip(), "description": description.strip(),
              "create_root": create_root}
    if action == "add":
        kind = request.get("kind")
        if kind not in TEMPLATE_KINDS:
            raise AuthoringError("invalid-kind", "a template kind is one of " + ", ".join(TEMPLATE_KINDS))
        result["kind"] = kind
    else:
        source = request.get("source")
        parts = source.split(":") if isinstance(source, str) else []
        if (len(parts) != 3 or parts[0] != "core" or parts[1] not in FORK_KINDS
                or not module_library.identifier(parts[2])):
            raise AuthoringError("fork-unavailable",
                                 "only a core " + " or ".join(FORK_KINDS) + " module can be forked")
        result.update(kind=parts[1], source=source, source_name=parts[2])
        if not result["name"]:
            result["name"] = parts[2] + "-fork"
    kind, name = result["kind"], result["name"]
    valid = (all(_identifier(part) for part in name.split("/"))
             and name.count("/") == 1) if kind == "stances" else _identifier(name)
    if not valid:
        raise AuthoringError("invalid-name", "a " + kind[:-1] + " name is "
                             + ("dimension/variant, each " if kind == "stances" else "")
                             + "lowercase letters, digits and hyphens, starting with a letter")
    if action == "add" and not result["description"]:
        raise AuthoringError("invalid-description",
                             "describe what the module is for; it becomes the manifest claim")
    return result


def _plan(repo: Path, worktree: Path, state: Dict[str, Any], raw_config: Dict[str, Any],
          request: Dict[str, Any]) -> Dict[str, Any]:
    """Everything a save would write, checked, without writing anything."""
    raw = copy.deepcopy(raw_config)
    mapped = module_editing._mapped_config(repo, worktree, raw)
    own = _own_root(worktree, mapped)
    created = False
    config_changes: List[Dict[str, Any]] = []
    if own is None:
        if not request["create_root"]:
            raise AuthoringError(
                "root-required",
                "this draft has no personal root; create " + OWN_ROOT + " in the draft and "
                "register it in primitive_roots to add or fork a module",
            )
        if os.path.lexists(str(worktree / OWN_ROOT)):
            raise AuthoringError("root-path-occupied",
                                 OWN_ROOT + " already exists in the draft but is not registered")
        roots = raw.get("primitive_roots", [])
        raw["primitive_roots"] = list(roots) + [str(repo / OWN_ROOT)]
        config_changes.append({"path": "primitive_roots", "value": "+ <checkout>/" + OWN_ROOT})
        mapped = module_editing._mapped_config(repo, worktree, raw)
        own = _own_root(worktree, mapped)
        created = True
    if own is None:
        raise AuthoringError("root-required", "the personal root could not be registered")
    kind, name = request["kind"], request["name"]
    existing = _existing(worktree, mapped)
    if name in existing[kind]:
        raise AuthoringError("module-exists", kind + "/" + name + " already exists in a primitive "
                             "root; a second definition would collide or switch with it")
    posture = load_posture(worktree)
    switch_kinds = [key for key, entry in posture.selection_kinds(worktree).items()
                    if entry.get("value") == "switch"]
    fork = None
    manifest: Optional[Dict[str, Any]] = None
    if request["action"] == "add":
        files = _template_files(kind, name, request["description"])
        if kind in MANIFEST_KINDS:
            manifest = _manifest_template(kind, request["description"])
    else:
        source = request["source_name"]
        originals = module_library.module_bytes(worktree / "primitives", kind, source)
        if originals is None:
            raise AuthoringError("fork-unavailable", "core " + kind + "/" + source
                                 + " is not a forkable module in this draft")
        files = {}
        for relative, content in originals.items():
            if kind == "skills" and relative == "SKILL.md":
                content = _renamed_skill(content, name)
            prefix = "rules/" if kind == "rules" else "skills/" + name + "/"
            files[prefix + (name + ".md" if kind == "rules" else relative)] = content
        shipped = module_library._manifest(worktree / "primitives", kind, source)
        manifest = copy.deepcopy(shipped) if shipped else _manifest_template(
            kind, "Fork of core " + kind + "/" + source + ".")
        if request["description"]:
            manifest["claims"] = [request["description"]]
        switches = raw.get(kind, {})
        if not isinstance(switches, dict):
            raise AuthoringError("invalid-config", kind + " in the draft configuration is not an object")
        raw[kind] = dict(switches, **{source: "off"})
        config_changes.append({"path": kind + "." + source, "value": "off"})
        version_path = worktree / "VERSION"
        fork = {"source": kind + "/" + source,
                "version": version_path.read_text(encoding="utf-8").strip()
                if version_path.is_file() else "",
                # The base revision is in the installed checkout's history, so the original
                # can be read back from git; the draft's own commits may be discarded.
                "revision": state["base_revision"],
                "files": {relative: module_library.digest(content)
                          for relative, content in originals.items()}}
        forks_relative = own["relative"] + "/" + module_library.FORKS_FILE
        forks = _json_file(worktree, forks_relative)
        forks.setdefault(kind, {})
        if not isinstance(forks[kind], dict):
            raise AuthoringError("root-file-unreadable", "forks.json is not keyed by kind")
        forks[kind][name] = fork
        files[module_library.FORKS_FILE] = _canonical(forks)
    if manifest is not None:
        problem = posture.validate_manifest(kind, name, manifest, switch_kinds)
        if problem:
            raise AuthoringError("manifest-refused", problem)
        manifests_relative = own["relative"] + "/manifests.json"
        manifests = _json_file(worktree, manifests_relative)
        manifests.setdefault(kind, {})
        if not isinstance(manifests[kind], dict):
            raise AuthoringError("root-file-unreadable", "manifests.json is not keyed by kind")
        manifests[kind][name] = manifest
        files["manifests.json"] = _canonical(manifests)
    written = {own["relative"] + "/" + relative: content for relative, content in files.items()}
    for relative in written:
        if (worktree / relative).exists() and not relative.endswith(("/manifests.json",
                                                                     "/" + module_library.FORKS_FILE)):
            raise AuthoringError("module-exists", relative.rsplit("/", 1)[-1] + " already exists")
    # The candidate carries every configuration change too: a fork's switch-off is resolved with it.
    mapped = module_editing._mapped_config(repo, worktree, raw)
    findings = _candidate_findings(posture, worktree, mapped, own, files)
    key = own["id"] + ":" + kind + ":" + name
    return {
        "valid": not findings, "error": "" if not findings else "Fix the manifest findings before saving.",
        "error_code": "" if not findings else "manifest-refused",
        "base_revision": state["revision"], "action": request["action"],
        "module": {"key": key, "kind": kind, "name": name},
        "root": {"id": own["id"], "label": own["label"], "created": created},
        "files": [{"path": relative, "text": _shown(content)}
                  for relative, content in sorted(files.items())],
        "manifest": manifest, "fork": None if fork is None else {
            "source": fork["source"], "version": fork["version"], "revision": fork["revision"]},
        "config_changes": config_changes, "findings": findings, "nothing_applied": True,
        "_files": written, "_config": raw, "_mapped": mapped,
    }


def _candidate_findings(posture: Any, worktree: Path, mapped: Dict[str, Any],
                        own: Dict[str, Any], files: Dict[str, bytes]) -> List[str]:
    """Strict resolution with the planned files in a copy of the personal root."""
    with tempfile.TemporaryDirectory(prefix="studio-authoring-") as temporary:
        candidate = Path(temporary) / own["label"]
        if own["path"].is_dir():
            shutil.copytree(str(own["path"]), str(candidate), symlinks=True)
        for relative, content in files.items():
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        config = dict(mapped)
        config["primitive_roots"] = [
            str(candidate) if Path(os.path.abspath(str(value))) == own["path"] else value
            for value in mapped.get("primitive_roots", [])]
        findings = resolution_findings(posture, config, worktree)
        return [line.replace(str(candidate), own["label"]) for line in findings]


def _public(planned: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in planned.items() if not key.startswith("_")}


def _refusal(exc: BaseException, base_revision: str = "") -> Dict[str, Any]:
    code = getattr(exc, "code", "authoring-invalid")
    message = str(exc) if isinstance(exc, (AuthoringError, drafts.DraftError,
                                           module_editing.ModuleEditError)) \
        else "the module could not be planned"
    return {"valid": False, "error": message, "error_code": code, "base_revision": base_revision,
            "action": "", "module": None, "root": None, "files": [], "manifest": None,
            "fork": None, "config_changes": [], "findings": [], "nothing_applied": True}


def read(repo: Path, name: str) -> Dict[str, Any]:
    """The draft's personal root, the templates, and the core modules a fork can start from."""
    repo = Path(repo).resolve()
    try:
        with drafts.locked_context(repo, name) as (worktree, state, raw_config):
            mapped = module_editing._mapped_config(repo, worktree, raw_config)
            own = _own_root(worktree, mapped)
            forkable = [{"key": "core:" + kind + ":" + unit, "kind": kind, "name": unit}
                        for kind in FORK_KINDS
                        for unit in sorted(_existing(worktree, {"primitive_roots": []})[kind])
                        if module_library.module_files(worktree / "primitives", kind, unit) is not None]
            return {"status": "ready", "message": "Module templates are ready.",
                    "draft": {"name": state["name"], "revision": state["revision"]},
                    "root": None if own is None else {"id": own["id"], "label": own["label"]},
                    "offer": {"label": OWN_ROOT, "registers": "<checkout>/" + OWN_ROOT},
                    "templates": list(TEMPLATES), "forkable": forkable,
                    "nothing_applied": True, "error_code": ""}
    except (drafts.DraftError, module_editing.ModuleEditError) as exc:
        return {"status": "unavailable", "message": str(exc), "draft": {}, "root": None,
                "offer": {}, "templates": [], "forkable": [], "nothing_applied": True,
                "error_code": exc.code}


def preview(repo: Path, name: str, request: Any) -> Dict[str, Any]:
    repo = Path(repo).resolve()
    try:
        normalized = _normalized(request)
        with drafts.locked_context(repo, name) as (worktree, state, raw_config):
            return _public(_plan(repo, worktree, state, raw_config, normalized))
    except (AuthoringError, drafts.DraftError, module_editing.ModuleEditError) as exc:
        return _refusal(exc)


def _request_identity(request: Dict[str, Any], base_revision: str) -> str:
    payload = json.dumps({"request": request, "base_revision": base_revision}, sort_keys=True)
    return "authoring:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save(repo: Path, name: str, base_revision: str, idempotency_key: str,
         request: Any) -> Dict[str, Any]:
    """Checkpoint a planned module once its manifest and the draft pass every check."""
    repo = Path(repo).resolve()
    identity = ""
    try:
        normalized = _normalized(request)
        if not isinstance(base_revision, str) or not base_revision:
            raise AuthoringError("invalid-request", "a base revision is required")
        identity = _request_identity(normalized, base_revision)
        with drafts.request_lock(repo, name, idempotency_key, identity):
            replayed = drafts.replay_request_response(repo, name, idempotency_key, identity)
            if replayed is not None:
                return replayed
            with drafts.locked_context(repo, name) as (worktree, state, raw_config):
                if state["revision"] != base_revision:
                    raise drafts.DraftError("stale-revision",
                                            "draft revision changed; reload before saving")
                planned = _plan(repo, worktree, state, raw_config, normalized)
                public = _public(planned)
                if not planned["valid"]:
                    return dict(public, saved=False, result=None)
                canonical = dict(public, saved=True, result=None)
                with module_editing._draft_check_environment(planned["_mapped"]) as environment:
                    result = drafts.checkpoint(
                        repo, name, base_revision, idempotency_key,
                        files=planned["_files"], config=_canonical(planned["_config"]),
                        check_command=[sys.executable, "-c", _CHECK, str(LIB)],
                        request_identity=identity, canonical_response=canonical,
                        check_environment=environment, _locked_worktree=worktree,
                    )
            if result.get("replayed"):
                replayed = drafts.replay_request_response(repo, name, idempotency_key, identity)
                if replayed is not None:
                    return replayed
            return dict(public, saved=True, result=result)
    except (AuthoringError, drafts.DraftError, module_editing.ModuleEditError) as exc:
        return dict(_refusal(exc, base_revision if isinstance(base_revision, str) else ""),
                    saved=False, result=None)


def library(repo: Path, name: str) -> Dict[str, Any]:
    """The module library as the draft would resolve it, with draft paths made relative."""
    repo = Path(repo).resolve()
    with drafts.locked_context(repo, name) as (worktree, _state, raw_config):
        mapped = module_editing._mapped_config(repo, worktree, raw_config)
        payload = module_library.inventory(worktree, mapped)
    prefix = str(worktree) + os.sep

    def relative(value: str) -> str:
        return "<draft>/" + value[len(prefix):] if value.startswith(prefix) else value

    for item in payload["modules"]:
        item["source"]["path"] = relative(item["source"]["path"])
        item["root"]["path"] = relative(item["root"]["path"])
    payload["repository"] = "<draft>"
    return payload
