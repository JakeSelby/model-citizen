# SPDX-License-Identifier: MIT
"""Draft-only module text editing through the harness lint authority."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from . import drafts, module_library, selection
from .module_evaluator import bounded_diagnostics


SCHEMA_VERSION = 1
EDITABLE_KINDS = frozenset(("rules", "skills", "stances"))
MAX_SOURCE_BYTES = 512 * 1024
MAX_PROJECTION_CHARS = 12000
MAX_INVENTORY_MODULES = 512
MAX_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_EXTERNAL_FILES = 2048
MAX_EXTERNAL_BYTES = 16 * 1024 * 1024
MAX_LINT_TERMS_BYTES = 64 * 1024
_LINT_TERMS_PARTS = (".config", "agent-harness", "lint-terms.txt")
CLI_COMMANDS = {
    "read": ("citizen", "draft", "module", "read", "{draft}", "{module}", "--json"),
    "preview": ("citizen", "draft", "module", "preview", "{draft}", "{module}",
                "--content", "source.md", "--json"),
    "save": ("citizen", "draft", "module", "save", "{draft}", "{module}",
             "--base-revision", "{revision}", "--source-digest", "{digest}",
             "--idempotency-key", "KEY", "--content", "source.md", "--json"),
}
_LINE_FINDING = re.compile(r"^(?P<path>.+?):(?P<line>[1-9][0-9]*): (?P<message>.+)$")
_PREVIEW_GUARD = threading.Lock()
_GLOBAL_PREVIEWS = threading.BoundedSemaphore(4)
_SAVE_LOCKS = tuple(threading.Lock() for _ in range(32))


@dataclass
class _PreviewState:
    condition: threading.Condition
    running: bool = False
    pending: Optional[object] = None
    users: int = 0


_PREVIEW_STATES: Dict[str, _PreviewState] = {}


class ModuleEditError(ValueError):
    """A module editor request cannot be resolved or evaluated safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _latest_preview(name: str, operation: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run one preview plus only the newest waiter for a draft."""
    with _PREVIEW_GUARD:
        state = _PREVIEW_STATES.get(name)
        if state is None:
            state = _PreviewState(threading.Condition(_PREVIEW_GUARD))
            _PREVIEW_STATES[name] = state
        state.users += 1
        token = object()
        if state.running:
            state.pending = token
            state.condition.notify_all()
            while state.running and state.pending is token:
                state.condition.wait()
            if state.pending is not token:
                state.users -= 1
                if state.users == 0 and not state.running:
                    _PREVIEW_STATES.pop(name, None)
                raise ModuleEditError("preview-superseded", "a newer preview replaced this request")
            state.pending = None
        elif state.pending is not None:
            # A newly arrived request wins even when the notified waiter has not resumed yet.
            state.pending = None
            state.condition.notify_all()
        state.running = True
    try:
        with _GLOBAL_PREVIEWS:
            return operation()
    finally:
        with _PREVIEW_GUARD:
            state.running = False
            state.users -= 1
            state.condition.notify_all()
            if state.users == 0 and state.pending is None:
                _PREVIEW_STATES.pop(name, None)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _lexically_inside(root: Path, candidate: Path) -> bool:
    try:
        Path(os.path.abspath(str(candidate))).relative_to(Path(os.path.abspath(str(root))))
        return True
    except ValueError:
        return False


def _mapped_config(repo: Path, worktree: Path, config: Mapping[str, Any]) -> Dict[str, Any]:
    repo, worktree = repo.resolve(), worktree.resolve()
    mapped = copy.deepcopy(dict(config))
    configured_roots = mapped.get("primitive_roots", [])
    if not isinstance(configured_roots, list):
        raise ModuleEditError("invalid-config", "primitive_roots must be a JSON array")
    roots = []
    seen = {Path(os.path.abspath(str(worktree / "primitives")))}
    for value in configured_roots:
        if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
            continue
        source = Path(os.path.realpath(str(Path(value).expanduser())))
        if _lexically_inside(worktree, source):
            mapped_source = source
        elif _lexically_inside(repo, source):
            mapped_source = worktree / source.relative_to(repo)
        else:
            mapped_source = source
        canonical = Path(os.path.abspath(str(mapped_source)))
        if canonical in seen:
            continue
        roots.append(str(canonical))
        seen.add(canonical)
    mapped["primitive_roots"] = roots
    return mapped


def _regular_text(path: Path) -> Tuple[str, str]:
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ModuleEditError("module-unavailable", "draft module is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ModuleEditError("module-refused", "draft module must be an existing regular file")
        if metadata.st_size > MAX_SOURCE_BYTES:
            raise ModuleEditError("module-too-large", "draft module exceeds the editor size limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise ModuleEditError("module-too-large", "draft module exceeds the editor size limit")
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModuleEditError("module-encoding", "draft module must be readable UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return text, hashlib.sha256(content).hexdigest()


def _regular_text_at(worktree: Path, relative: str) -> Tuple[str, str]:
    """Read one bounded source through a no-follow chain rooted at the checkout."""
    try:
        with drafts._anchored_target(
            worktree, relative, create_parents=False,
        ) as (parent, leaf):
            descriptor = os.open(
                leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent,
            )
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ModuleEditError(
                        "module-refused", "draft module must be an existing regular file",
                    )
                if metadata.st_size > MAX_SOURCE_BYTES:
                    raise ModuleEditError(
                        "module-too-large", "draft module exceeds the editor size limit",
                    )
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    content = stream.read(MAX_SOURCE_BYTES + 1)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
    except drafts.DraftError as exc:
        raise ModuleEditError("module-unavailable", "draft module is unavailable") from exc
    except OSError as exc:
        raise ModuleEditError("module-unavailable", "draft module is unavailable") from exc
    if len(content) > MAX_SOURCE_BYTES:
        raise ModuleEditError("module-too-large", "draft module exceeds the editor size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModuleEditError("module-encoding", "draft module must be readable UTF-8") from exc
    return text, hashlib.sha256(content).hexdigest()


def _editable_from_context(
    worktree: Path, _state: Dict[str, Any], config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    # Configured roots are canonical (see _mapped_config); compare against the same form.
    worktree = Path(os.path.realpath(str(worktree)))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    items: List[Dict[str, Any]] = []
    total_bytes = 0
    seen_roots = {Path(os.path.abspath(str(worktree / "primitives")))}

    def entries(descriptor: int) -> List[os.DirEntry]:
        found = []
        try:
            iterator = os.scandir(descriptor)
            for entry in iterator:
                found.append(entry)
                if len(found) > MAX_INVENTORY_MODULES * 4:
                    raise ModuleEditError(
                        "module-inventory-too-large", "draft module inventory exceeds the item limit",
                    )
        except OSError as exc:
            raise ModuleEditError(
                "module-inventory-refused", "draft module inventory changed while loading",
            ) from exc
        return sorted(found, key=lambda entry: entry.name)

    def child_dir(parent: int, name: str) -> Optional[int]:
        try:
            return os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ModuleEditError(
                "module-inventory-refused", "draft module inventory contains an unsafe directory",
            ) from exc

    def add(root_id: str, label: str, relative: str, kind: str, name: str) -> None:
        nonlocal total_bytes
        if len(items) >= MAX_INVENTORY_MODULES:
            raise ModuleEditError(
                "module-inventory-too-large", "draft module inventory exceeds the item limit",
            )
        text, _digest = _regular_text_at(worktree, relative)
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_INVENTORY_BYTES:
            raise ModuleEditError(
                "module-inventory-too-large", "draft module inventory exceeds the byte limit",
            )
        if kind in ("rules", "stances"):
            measured = text
            method = "chars/4 of resident source text"
        else:
            description = ""
            if text.startswith("---\n"):
                closing = text.find("\n---\n", 4)
                if closing >= 0:
                    for line in text[4:closing].splitlines():
                        if line.startswith("description:"):
                            description = line.partition(":")[2].strip().strip("\"'")
                            break
            measured = name + ": " + description
            method = "chars/4 of resident listing name and description"
        items.append({
            "key": "%s:%s:%s" % (root_id, kind, name), "name": name, "kind": kind,
            "root": {"id": root_id, "label": label, "core": False},
            "projections": module_library._projection_paths(kind, name, False),
            "context_cost": {
                "tokens": int(round(len(measured) / module_library.CHARS_PER_TOKEN)),
                "estimate": "soft estimate", "method": method,
            },
            "_relative": relative,
        })

    roots = config.get("primitive_roots", [])
    if not isinstance(roots, list):
        raise ModuleEditError("invalid-config", "primitive_roots must be a JSON array")
    for index, value in enumerate(roots):
        if not isinstance(value, str):
            continue
        root = Path(os.path.abspath(value))
        if root in seen_roots or not _lexically_inside(worktree, root):
            continue
        seen_roots.add(root)
        relative_root = root.relative_to(worktree).as_posix()
        try:
            with drafts._anchored_target(
                worktree, relative_root + "/.module-inventory", create_parents=False,
            ) as (root_parent, _leaf):
                root_fd = os.dup(root_parent)
        except (OSError, drafts.DraftError) as exc:
            raise ModuleEditError(
                "module-inventory-refused", "configured draft module root is unavailable",
            ) from exc
        root_id, label = "root-%d" % (index + 1), root.name or "draft root"
        try:
            rules_fd = child_dir(root_fd, "rules")
            if rules_fd is not None:
                try:
                    for entry in entries(rules_fd):
                        if entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                            name = entry.name[:-3]
                            add(root_id, label, relative_root + "/rules/" + entry.name, "rules", name)
                finally:
                    os.close(rules_fd)
            skills_fd = child_dir(root_fd, "skills")
            if skills_fd is not None:
                try:
                    for entry in entries(skills_fd):
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        unit_fd = child_dir(skills_fd, entry.name)
                        if unit_fd is None:
                            continue
                        try:
                            skill_entries = {item.name: item for item in entries(unit_fd)}
                            skill = skill_entries.get("SKILL.md")
                            if skill is not None and skill.is_file(follow_symlinks=False):
                                add(root_id, label,
                                    relative_root + "/skills/" + entry.name + "/SKILL.md",
                                    "skills", entry.name)
                        finally:
                            os.close(unit_fd)
                finally:
                    os.close(skills_fd)
            stances_fd = child_dir(root_fd, "stances")
            if stances_fd is not None:
                try:
                    for dimension in entries(stances_fd):
                        if not dimension.is_dir(follow_symlinks=False):
                            continue
                        dimension_fd = child_dir(stances_fd, dimension.name)
                        if dimension_fd is None:
                            continue
                        try:
                            for variant in entries(dimension_fd):
                                if variant.name.endswith(".md") and variant.is_file(follow_symlinks=False):
                                    name = dimension.name + "/" + variant.name[:-3]
                                    add(root_id, label, relative_root + "/stances/" +
                                        dimension.name + "/" + variant.name, "stances", name)
                        finally:
                            os.close(dimension_fd)
                finally:
                    os.close(stances_fd)
        finally:
            os.close(root_fd)
    return sorted(items, key=lambda item: (item["kind"], item["name"], item["root"]["id"]))


def _public_module(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": item["key"], "name": item["name"], "kind": item["kind"],
        "root": {"id": item["root"]["id"], "label": item["root"]["label"]},
        "projections": item["projections"], "context_cost": item["context_cost"],
    }


def list_modules(repo: Path, name: str) -> List[Dict[str, Any]]:
    repo = Path(repo).resolve()
    with drafts.locked_context(repo, name) as (worktree, state, raw_config):
        config = _mapped_config(repo, worktree, raw_config)
        items = _editable_from_context(worktree, state, config)
        return [_public_module(item) for item in items]


def read(repo: Path, name: str, key: str = "") -> Dict[str, Any]:
    repo = Path(repo).resolve()
    try:
        with drafts.locked_context(repo, name) as (worktree, state, raw_config):
            config = _mapped_config(repo, worktree, raw_config)
            items = _editable_from_context(worktree, state, config)
            modules = [_public_module(item) for item in items]
            if not key:
                return {"status": "ready", "message": "Editable draft modules are ready.",
                        "draft": {"name": state["name"], "revision": state["revision"]},
                        "modules": modules, "module": None, "content": "", "source_digest": "",
                        "nothing_applied": True, "error_code": ""}
            item = next((candidate for candidate in items if candidate["key"] == key), None)
            if item is None:
                raise ModuleEditError(
                    "module-not-editable",
                    "module is not an editable draft-owned rule, skill, or stance",
                )
            content, digest = _regular_text_at(worktree, item["_relative"])
            return {"status": "ready", "message": "Draft module is ready.",
                    "draft": {"name": state["name"], "revision": state["revision"]},
                    "modules": modules, "module": _public_module(item), "content": content,
                    "source_digest": digest, "nothing_applied": True, "error_code": ""}
    except (drafts.DraftError, ModuleEditError) as exc:
        return {"status": "unavailable", "message": str(exc), "draft": {}, "modules": [],
                "module": None, "content": "", "source_digest": "", "nothing_applied": True,
                "error_code": exc.code}
    except BaseException:
        return {"status": "unavailable", "message": "draft module inventory is unavailable",
                "draft": {}, "modules": [], "module": None, "content": "",
                "source_digest": "", "nothing_applied": True,
                "error_code": "module-invalid"}


def _content_bytes(content: Any) -> bytes:
    if not isinstance(content, str):
        raise ModuleEditError("invalid-content", "module content must be text")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ModuleEditError("module-encoding", "module content must be valid UTF-8") from exc
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ModuleEditError("module-too-large", "module content exceeds the editor size limit")
    return encoded


def _copy_candidate(worktree: Path, revision: str, relative: str,
                    content: bytes) -> Tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory(prefix="studio-module-preview-")
    target = Path(temporary.name) / "candidate"
    try:
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(worktree), str(target)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        temporary.cleanup()
        raise ModuleEditError("preview-timeout", "draft preview clone timed out") from exc
    except OSError as exc:
        temporary.cleanup()
        raise ModuleEditError("preview-unavailable", "draft preview clone is unavailable") from exc
    if not cloned.returncode:
        try:
            checked = subprocess.run(
                ["git", "-C", str(target), "checkout", "--quiet", "--detach", revision],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            temporary.cleanup()
            raise ModuleEditError("preview-timeout", "draft preview checkout timed out") from exc
        except OSError as exc:
            temporary.cleanup()
            raise ModuleEditError("preview-unavailable", "draft preview checkout is unavailable") from exc
        if checked.returncode:
            temporary.cleanup()
            raise ModuleEditError("preview-unavailable", "could not select the draft preview revision")
    else:
        temporary.cleanup()
        raise ModuleEditError("preview-unavailable", "could not clone the draft preview revision")
    try:
        _candidate_write(target, relative, content)
    except (OSError, drafts.DraftError) as exc:
        temporary.cleanup()
        raise ModuleEditError("preview-unavailable", "draft preview source is unavailable") from exc
    return temporary, target.resolve()


def _candidate_write(candidate: Path, relative: str, content: bytes) -> None:
    with drafts._anchored_target(candidate, relative, create_parents=False) as (parent, leaf):
        before = drafts._capture_target_at(parent, leaf, relative)
        if before["kind"] != "file":
            raise drafts.DraftError("stale-source", "draft module changed; reload before saving")
        drafts._write_target_at(parent, leaf, relative, content, before)


def _copy_external_root(source: Path, destination: Path,
                        counters: Dict[str, int]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(str(source), flags)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ModuleEditError("external-root-refused", "configured primitive root is unavailable") from exc

    def copy_directory(source_fd: int, target: Path) -> None:
        target.mkdir(mode=0o700)
        try:
            entries = sorted(os.scandir(source_fd), key=lambda entry: entry.name)
        except OSError as exc:
            raise ModuleEditError(
                "external-root-refused", "configured primitive root cannot be enumerated",
            ) from exc
        for entry in entries:
            if entry.name in (".", ".."):
                continue
            counters["files"] += 1
            if counters["files"] > MAX_EXTERNAL_FILES:
                raise ModuleEditError(
                    "external-root-too-large", "configured primitive roots exceed the preview file limit",
                )
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ModuleEditError(
                    "external-root-refused", "configured primitive root changed during preview",
                ) from exc
            output = target / entry.name
            if stat.S_ISLNK(metadata.st_mode):
                raise ModuleEditError(
                    "external-root-refused", "configured primitive roots may not contain symlinks",
                )
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child = os.open(entry.name, flags, dir_fd=source_fd)
                except OSError as exc:
                    raise ModuleEditError(
                        "external-root-refused", "configured primitive root changed during preview",
                    ) from exc
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ModuleEditError(
                            "external-root-refused", "configured primitive root changed during preview",
                        )
                    copy_directory(child, output)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ModuleEditError(
                    "external-root-refused", "configured primitive roots contain an unsupported entry",
                )
            counters["bytes"] += metadata.st_size
            if counters["bytes"] > MAX_EXTERNAL_BYTES:
                raise ModuleEditError(
                    "external-root-too-large", "configured primitive roots exceed the preview byte limit",
                )
            try:
                descriptor = os.open(
                    entry.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=source_fd,
                )
            except OSError as exc:
                raise ModuleEditError(
                    "external-root-refused", "configured primitive root changed during preview",
                ) from exc
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)):
                    raise ModuleEditError(
                        "external-root-refused", "configured primitive root changed during preview",
                    )
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = -1
                    data = stream.read(metadata.st_size + 1)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if len(data) != metadata.st_size:
                raise ModuleEditError(
                    "external-root-refused", "configured primitive root changed during preview",
                )
            output.write_bytes(data)
            os.chmod(output, stat.S_IMODE(metadata.st_mode))

    try:
        copy_directory(root_fd, destination)
    finally:
        os.close(root_fd)


def _snapshot_external_roots(candidate: Path, config: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Freeze configured roots once and return stable labels for rendered evidence."""
    candidate = candidate.resolve()
    snap = copy.deepcopy(dict(config))
    aliases = {str(candidate): "<draft>"}
    root_dir = candidate.parent / "external-roots"
    roots = []
    counters = {"files": 0, "bytes": 0}
    for index, value in enumerate(snap.get("primitive_roots", [])):
        source = Path(os.path.abspath(str(value)))
        if _lexically_inside(candidate, source):
            roots.append(str(source))
            continue
        destination = root_dir / ("root-" + str(index + 1))
        root_dir.mkdir(parents=True, exist_ok=True)
        _copy_external_root(source, destination, counters)
        roots.append(str(destination))
        aliases[str(destination)] = "<primitive-root:" + source.name + ">"
    snap["primitive_roots"] = roots
    return snap, aliases


def _diagnostics(harness: Any, candidate: Path, relative: str,
                 config: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = harness.lint_tree(candidate)
    try:
        # The draft's configuration, so a switched-off rule is discounted as `citizen lint` does.
        findings += harness.check_context_cap(candidate, config)
    except TypeError:
        findings += harness.check_context_cap(candidate)
    findings += harness.check_cost_sidecars(candidate)
    findings += harness.check_stance_constraints(candidate)
    try:
        findings += harness.check_collisions(candidate, config)
    except TypeError:
        findings += harness.collisions.findings(candidate, config)
    notes: List[str] = []
    if (candidate / "primitives" / "roles").is_dir():
        findings += harness.changelog_fragments.findings(candidate, notes)
        findings += ["projection drift: " + name + "; run `citizen generate`"
                     for name in harness.primitive_catalog.projection_drift(candidate)]
    result = []
    for finding in findings:
        matched = _LINE_FINDING.match(finding)
        line = int(matched.group("line")) if matched and matched.group("path") == relative else None
        result.append({"message": matched.group("message") if line is not None else finding,
                       "line": line, "severity": "error"})
    return result


def _runtime_renderings(harness: Any, root: Path, config: Mapping[str, Any],
                        aliases: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    try:
        if hasattr(harness, "render_runtime_contexts"):
            rendered = harness.render_runtime_contexts(dict(config))
            for source, label in (aliases or {}).items():
                rendered = {runtime: text.replace(source, label)
                            for runtime, text in rendered.items()}
            return rendered
        posture = harness.load_posture()
        if posture is None:
            raise ValueError("selection resolver unavailable")
        selected = posture.selection({}, strict=True, config=config, root=root)
        effective = copy.deepcopy(dict(config))
        effective["stances"] = {name: value for name, value in selected["stances"].items()
                                if value is not None}
        stances = harness.resolve_stances(effective)
        off = harness.switched_off(selected)
        externals, _warnings = harness.external_primitive_roots(effective)
        for entry in externals:
            entry["rules"] = [rule for rule in entry["rules"] if rule.stem not in off["rules"]]
            entry["skills"] = [skill for skill in entry["skills"] if skill.name not in off["skills"]]
        personal = harness.render_personal(effective, None)
        claude_parts = []
        instructions = root / "claude" / "CLAUDE.md"
        if instructions.is_file():
            claude_parts.append(instructions.read_text(encoding="utf-8").rstrip())
        claude_parts.extend(rule.read_text(encoding="utf-8").rstrip()
                            for rule in sorted((root / "claude" / "rules").glob("*.md"))
                            if rule.stem not in off["rules"])
        claude_parts.extend(rule.read_text(encoding="utf-8").rstrip()
                            for entry in externals for rule in entry["rules"])
        claude_parts.extend(path.read_text(encoding="utf-8").rstrip()
                            for _name, path in sorted(stances.items()))
        if personal:
            claude_parts.append(personal.rstrip())
        claude_text = "\n".join(claude_parts) + ("\n" if claude_parts else "")
        rendered = {
            "claude-code": claude_text,
            "codex": harness.render_codex_agents(
                stances, personal, externals, off["rules"],
            ),
        }
        for source, label in (aliases or {}).items():
            rendered = {runtime: text.replace(source, label)
                        for runtime, text in rendered.items()}
        return rendered
    except (OSError, ValueError, KeyError, SystemExit) as exc:
        raise ModuleEditError(
            "projection-unavailable", "the draft cannot render runtime projections",
        ) from exc


def _runtime_sizes(harness: Any, root: Path,
                   config: Mapping[str, Any]) -> Dict[str, Tuple[int, int]]:
    rendered = _runtime_renderings(harness, root, config)
    return {runtime: (len(text.splitlines()), harness.est_tokens(len(text)))
            for runtime, text in rendered.items()}


def _budgets(harness: Any, before: Dict[str, Tuple[int, int]],
             after: Dict[str, Tuple[int, int]],
             authored_after: Optional[Tuple[int, int]] = None) -> List[Dict[str, Any]]:
    result = []
    for runtime, label in selection.RUNTIMES:
        old_lines, old_tokens = before[runtime]
        lines, tokens = after[runtime]
        cap_lines, cap_tokens = authored_after or (lines, tokens)
        result.append({
        "runtime": runtime, "label": label,
        "lines": lines, "line_delta": lines - old_lines,
        "line_cap": harness.ALWAYS_LOADED_CAP,
        "tokens": tokens, "token_delta": tokens - old_tokens,
        "token_cap": harness.ALWAYS_LOADED_TOKEN_CAP,
        "over_cap": (cap_lines > harness.ALWAYS_LOADED_CAP
                     or cap_tokens > harness.ALWAYS_LOADED_TOKEN_CAP),
        })
    return result


def _evaluated_budgets(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = []
    old_authored, authored = before["authored"], after["authored"]
    for runtime, label in selection.RUNTIMES:
        old_runtime = before["rendered"][runtime]
        runtime_total = after["rendered"][runtime]
        result.append({
            "runtime": runtime, "label": label,
            "lines": authored["lines"],
            "line_delta": authored["lines"] - old_authored["lines"],
            "line_cap": after["caps"]["lines"],
            "tokens": authored["tokens"],
            "token_delta": authored["tokens"] - old_authored["tokens"],
            "token_cap": after["caps"]["tokens"],
            "over_cap": (authored["lines"] > after["caps"]["lines"]
                         or authored["tokens"] > after["caps"]["tokens"]),
            "rendered_lines": runtime_total["lines"],
            "rendered_line_delta": runtime_total["lines"] - old_runtime["lines"],
            "rendered_tokens": runtime_total["tokens"],
            "rendered_token_delta": runtime_total["tokens"] - old_runtime["tokens"],
        })
    return result


def _evaluated_projections(item: Dict[str, Any], evaluated: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for projection in item["projections"]:
        full = str(evaluated["named"].get(projection["runtime"], ""))
        result.append({
            "runtime": projection["runtime"], "path": projection["path"],
            "text": full[:MAX_PROJECTION_CHARS],
            "truncated": len(full) > MAX_PROJECTION_CHARS,
        })
    return result


def _evaluate_candidate(candidate: Path, config: Mapping[str, Any], item: Dict[str, Any],
                        aliases: Mapping[str, str], diagnostics: bool,
                        environment: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    worker = Path(__file__).with_name("module_evaluator.py")
    payload = {
        "root": str(candidate), "relative": item["_relative"],
        "config": dict(config), "aliases": dict(aliases),
        "item": {"kind": item["kind"], "name": item["name"]},
        "diagnostics": diagnostics,
    }
    descriptor, payload_name = tempfile.mkstemp(prefix="studio-module-eval-", suffix=".json")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, sort_keys=True)
        try:
            run = subprocess.run(
                [sys.executable, str(worker), payload_name], capture_output=True, text=True,
                timeout=180, env=None if environment is None else dict(environment),
            )
        except subprocess.TimeoutExpired as exc:
            raise ModuleEditError("preview-timeout", "draft module evaluation timed out") from exc
        except OSError as exc:
            raise ModuleEditError(
                "preview-unavailable", "draft module evaluation is unavailable",
            ) from exc
        try:
            result = json.loads(run.stdout)
        except (TypeError, ValueError) as exc:
            raise ModuleEditError(
                "preview-unavailable", "draft module evaluation is unavailable",
            ) from exc
        if run.returncode or not isinstance(result, dict) or not result.get("ok"):
            code = result.get("error_code") if isinstance(result, dict) else None
            raise ModuleEditError(
                code if isinstance(code, str) else "preview-unavailable",
                "draft module evaluation is unavailable",
            )
        # Caps are worker-authoritative constants but kept out of exception text and paths.
        result["caps"] = {
            "lines": int(result.pop("line_cap", 0) or 0),
            "tokens": int(result.pop("token_cap", 0) or 0),
        }
        if not result["caps"]["lines"] or not result["caps"]["tokens"]:
            # Older draft candidates do not expose cap fields in the worker response.
            result["caps"] = {"lines": 225, "tokens": 3500}
        raw = result.get("diagnostics")
        result["diagnostics"] = bounded_diagnostics(raw if isinstance(raw, list) else [])
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(payload_name)
        except FileNotFoundError:
            pass


def _projections(harness: Any, candidate: Path, config: Mapping[str, Any],
                 item: Dict[str, Any], rendered: Dict[str, str]) -> List[Dict[str, Any]]:
    posture = harness.load_posture()
    if posture is None:
        raise ModuleEditError("projection-unavailable", "the draft cannot render runtime projections")
    selected = posture.selection({}, strict=True, config=config, root=candidate)
    off = harness.switched_off(selected)
    source = (candidate / item["_relative"]).read_text(encoding="utf-8")
    if item["kind"] == "stances":
        dimension, variant = item["name"].split("/", 1)
        active = selected.get("stances", {}).get(dimension) == variant
    elif item["kind"] == "rules" and item["name"] in off["rules"]:
        active = False
    elif item["kind"] == "skills" and item["name"] in off["skills"]:
        active = False
    else:
        active = True
    if item["kind"] in ("rules", "stances"):
        texts = {runtime: (rendered[runtime] if active else "")
                 for runtime, _label in selection.RUNTIMES}
    else:
        texts = {runtime: (source if active else "") for runtime, _label in selection.RUNTIMES}
    result = []
    for projection in item["projections"]:
        full = texts[projection["runtime"]]
        start = 0
        if len(full) > MAX_PROJECTION_CHARS and source.strip():
            marker = ""
            if projection["runtime"] == "codex":
                if item["kind"] == "stances":
                    dimension, variant = item["name"].split("/", 1)
                    marker = "<!-- stance " + dimension + ": " + variant + " -->"
                elif item["kind"] == "rules":
                    marker = "/" + item["name"] + ".md -->"
            location = full.find(marker) if marker else -1
            if location < 0:
                location = full.find(source.strip())
            if location >= 0:
                start = min(location, len(full) - MAX_PROJECTION_CHARS)
        result.append({
            "runtime": projection["runtime"], "path": projection["path"],
            "text": full[start:start + MAX_PROJECTION_CHARS],
            "truncated": len(full) > MAX_PROJECTION_CHARS,
        })
    return result


def _locked_module_snapshot(repo: Path, worktree: Path, state: Dict[str, Any],
                            raw_config: Dict[str, Any], key: str
                            ) -> Tuple[Dict[str, Any], Dict[str, Any], str, str]:
    config = _mapped_config(repo.resolve(), worktree, raw_config)
    try:
        items = _editable_from_context(worktree, state, config)
    except ModuleEditError as error:
        if error.code in {"module-inventory-refused", "module-inventory-unavailable"}:
            raise drafts.DraftError(
                "stale-source", "draft module changed; reload before saving",
            ) from error
        raise
    matches = [item for item in items if item["key"] == key]
    if len(matches) != 1:
        raise drafts.DraftError(
            "stale-source", "draft module changed; reload before saving",
        )
    item = matches[0]
    source, source_digest = _regular_text_at(worktree, item["_relative"])
    return config, item, source, source_digest


def _preview_snapshot(worktree: Path, state: Dict[str, Any], config: Dict[str, Any],
                      item: Dict[str, Any], source: str, source_digest: str,
                      encoded: bytes) -> Dict[str, Any]:
    temporary, candidate = _copy_candidate(
        worktree, state["revision"], item["_relative"], encoded,
    )
    try:
        candidate_config = _mapped_config(worktree, candidate, config)
        candidate_config, aliases = _snapshot_external_roots(candidate, candidate_config)
        _candidate_write(candidate, item["_relative"], source.encode("utf-8"))
        # Preview sees the same scrubbed environment and home as save-time lint.
        with _draft_check_environment(config) as environment:
            before = _evaluate_candidate(
                candidate, candidate_config, item, aliases, False, environment,
            )
            _candidate_write(candidate, item["_relative"], encoded)
            after = _evaluate_candidate(
                candidate, candidate_config, item, aliases, True, environment,
            )
        diagnostics = after["diagnostics"]
        budgets = _evaluated_budgets(before, after)
        projections = _evaluated_projections(item, after)
    finally:
        temporary.cleanup()
    valid = not diagnostics and not any(budget["over_cap"] for budget in budgets)
    return {
        "valid": valid, "error": "" if valid else "Fix the module findings before saving.",
        "error_code": "" if valid else "lint-refused",
        "base_revision": state["revision"], "source_digest": source_digest,
        "content_digest": hashlib.sha256(encoded).hexdigest(),
        "unchanged": source.encode("utf-8") == encoded, "module": _public_module(item),
        "diagnostics": diagnostics, "budgets": budgets,
        "projections": projections, "nothing_applied": True,
        "_relative": item["_relative"], "_content": encoded, "_config": config,
    }


def _preview(repo: Path, name: str, key: str, content: Any) -> Dict[str, Any]:
    encoded = _content_bytes(content)
    with drafts.locked_context(repo, name) as (worktree, state, raw_config):
        config, item, source, source_digest = _locked_module_snapshot(
            repo, worktree, state, raw_config, key,
        )
    return _preview_snapshot(
        worktree, state, config, item, source, source_digest, encoded,
    )


def _failure(exc: BaseException, repo: Optional[Path] = None) -> Dict[str, Any]:
    known = isinstance(exc, (drafts.DraftError, ModuleEditError))
    code = exc.code if known else "preview-unavailable"
    message = (drafts._public_detail(str(exc), {"<installed>": repo}) if known
               else "draft module evaluation is unavailable")
    return {"valid": False, "error": message, "error_code": code,
            "base_revision": "", "source_digest": "", "content_digest": "",
            "unchanged": False, "module": None, "diagnostics": [], "budgets": [],
            "projections": [], "nothing_applied": True}


def preview(repo: Path, name: str, key: str, content: Any) -> Dict[str, Any]:
    try:
        result = _latest_preview(
            name, lambda: _preview(Path(repo).resolve(), name, key, content),
        )
    except BaseException as exc:
        return _failure(exc, Path(repo))
    result.pop("_relative")
    result.pop("_content")
    result.pop("_config")
    return result


def _request_identity(key: str, base_revision: str, source_digest: str, content: bytes) -> str:
    payload = json.dumps({"module": key, "base_revision": base_revision,
                          "source_digest": source_digest,
                          "content_sha256": hashlib.sha256(content).hexdigest()},
                         sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"studio-module-v1\0" + payload).hexdigest()


def _user_lint_terms() -> Optional[bytes]:
    """Read the personal lint-terms file `citizen lint` reads, following no link below home."""
    root = os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or str(Path.home())
    refused = ModuleEditError(
        "lint-terms-refused",
        "the personal lint-terms file must be a regular file of at most "
        + str(MAX_LINT_TERMS_BYTES) + " bytes reached through no symlink",
    )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return None
    try:
        for part in _LINT_TERMS_PARTS[:-1]:
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise refused from exc
            os.close(directory)
            directory = child
        leaf = _LINT_TERMS_PARTS[-1]
        try:
            metadata = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LINT_TERMS_BYTES:
            raise refused
        try:
            descriptor = os.open(
                leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
        except OSError as exc:
            raise refused from exc
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise refused
            content = stream.read(MAX_LINT_TERMS_BYTES + 1)
    finally:
        os.close(directory)
    if len(content) > MAX_LINT_TERMS_BYTES:
        raise refused
    return content


@contextlib.contextmanager
def _draft_check_environment(config: Mapping[str, Any]):
    """Give the public lint command the locked draft configuration and the user's lint terms.

    `lint-terms.txt` is a `citizen lint` input, not draft configuration, so preview and save
    enforce the same forbidden terms the command does. `HARNESS_LINT_TERMS` passes through
    unchanged for the same reason: the command reads the file and the variable together.
    """
    terms = _user_lint_terms()
    with tempfile.TemporaryDirectory(prefix="studio-module-check-") as temporary:
        home = Path(temporary)
        path = home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(config), sort_keys=True) + "\n", encoding="utf-8")
        if terms is not None:
            descriptor = os.open(str(path.parent / _LINT_TERMS_PARTS[-1]),
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(terms)
        # `load_config` folds identity, permission and manage variables over the config file.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("HARNESS_STANCE_", "HARNESS_IDENTITY_",
                                              "HARNESS_MANAGE_"))
                       and key not in ("HARNESS_HOME", "HARNESS_PROJECT_CONFIG",
                                       "HARNESS_SESSION_CONFIG", "HARNESS_MODE",
                                       "HARNESS_PERMISSIONS")}
        environment["HARNESS_HOME"] = str(home)
        yield environment


def save(repo: Path, name: str, key: str, base_revision: str, source_digest: str,
         idempotency_key: str, content: Any) -> Dict[str, Any]:
    repo = Path(repo).resolve()
    identity = ""
    try:
        encoded = _content_bytes(content)
        identity = _request_identity(key, base_revision, source_digest, encoded)
        lock_key = hashlib.sha256(
            (str(repo) + "\0" + name + "\0" + idempotency_key).encode(),
        ).digest()
        save_lock = _SAVE_LOCKS[int.from_bytes(lock_key[:4], "big") % len(_SAVE_LOCKS)]
        with save_lock, drafts.request_lock(repo, name, idempotency_key, identity):
            replayed = drafts.replay_request_response(repo, name, idempotency_key, identity)
            if replayed is not None:
                return replayed
            with drafts.locked_context(repo, name) as (worktree, state, raw_config):
                if state["revision"] != base_revision:
                    raise drafts.DraftError(
                        "stale-revision", "draft revision changed; reload before saving",
                    )
                try:
                    config, item, source, actual_digest = _locked_module_snapshot(
                        repo, worktree, state, raw_config, key,
                    )
                except ModuleEditError as exc:
                    if exc.code in ("module-not-editable", "module-unavailable", "module-refused"):
                        raise drafts.DraftError(
                            "stale-source", "draft module changed; reload before saving",
                        ) from exc
                    raise
                if not source_digest or actual_digest != source_digest:
                    raise drafts.DraftError(
                        "stale-source", "draft module changed; reload before saving",
                    )
                with _GLOBAL_PREVIEWS:
                    planned = _preview_snapshot(
                        worktree, state, config, item, source, actual_digest, encoded,
                    )
                relative = planned.pop("_relative")
                encoded = planned.pop("_content")
                check_config = planned.pop("_config")
                if not planned["valid"] or planned["unchanged"]:
                    return dict(planned, saved=False, result=None, saved_lint=[])
                canonical = dict(planned, saved=True, result=None, saved_lint=[])
                with _draft_check_environment(check_config) as check_environment:
                    result = drafts.checkpoint(
                        repo, name, base_revision, idempotency_key,
                        files={relative: encoded},
                        check_command=[sys.executable, "bin/harness", "lint"],
                        request_identity=identity, expected_digests={relative: source_digest},
                        canonical_response=canonical, check_environment=check_environment,
                        _locked_worktree=worktree,
                    )
            if result.get("replayed"):
                replayed = drafts.replay_request_response(repo, name, idempotency_key, identity)
                if replayed is None:
                    raise drafts.DraftError(
                        "recovery-conflict", "saved module response is unavailable",
                    )
                return replayed
            return dict(planned, saved=True, result=result, saved_lint=[])
    except BaseException as exc:
        if identity:
            try:
                replayed = drafts.replay_request_response(repo, name, idempotency_key, identity)
                if replayed is not None:
                    return replayed
            except (drafts.DraftError, OSError, ValueError):
                pass
        return dict(_failure(exc, repo), saved=False, result=None, saved_lint=[])
