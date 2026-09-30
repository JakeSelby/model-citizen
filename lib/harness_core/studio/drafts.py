"""Managed draft worktrees and crash-recoverable checkpoint saves."""
from __future__ import annotations

import base64
import copy
import contextlib
import datetime
import difflib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .module_evaluator import bounded_text


SCHEMA_VERSION = 1
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TAG = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")
STATE_DIR = "agent-harness-draft"


class DraftError(ValueError):
    """A stable error suitable for both the CLI and the private Studio transport."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _path_aliases(labelled: Mapping[str, Any]) -> Dict[str, str]:
    """Map each spelling of an internal path to its public label."""
    aliases: Dict[str, str] = {}
    for label, path in labelled.items():
        if not path:
            continue
        for form in (str(path), os.path.abspath(str(path)), os.path.realpath(str(path))):
            aliases[form] = label
    return aliases


def _public_detail(text: str, labelled: Mapping[str, Any]) -> str:
    """Bound tool output for a refusal and alias its internal paths like the evaluator does."""
    return bounded_text(text, _path_aliases(dict(labelled, **{"<temporary>": tempfile.gettempdir()})))


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    run = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120,
    )
    if check and run.returncode:
        detail = run.stderr.strip() or run.stdout.strip() or "git command failed"
        raise DraftError("git-failed", _public_detail(detail, {"<draft>": repo}))
    return run


def _revision(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", "--verify", ref + "^{commit}").stdout.strip()


def resolve_base(repo: Path, base: str) -> Tuple[str, str]:
    """Resolve the installed checkout or one exact release tag without revision expressions."""
    if base == "installed":
        return "installed", _revision(repo)
    if not TAG.fullmatch(base):
        raise DraftError("invalid-base", "--base must be installed or an exact v* release tag")
    ref = "refs/tags/" + base
    exists = _git(repo, "show-ref", "--verify", "--quiet", ref, check=False)
    if exists.returncode:
        raise DraftError("unknown-base", "release tag does not exist: " + base)
    return base, _revision(repo, ref)


def _admin_dir(worktree: Path) -> Path:
    raw = _git(worktree, "rev-parse", "--git-dir").stdout.strip()
    path = Path(raw)
    return (worktree / path).resolve() if not path.is_absolute() else path.resolve()


def _paths(worktree: Path) -> Dict[str, Path]:
    root = _admin_dir(worktree) / STATE_DIR
    return {
        "root": root,
        "state": root / "state.json",
        "journal": root / "journal.json",
        "lock": root / "writer.lock",
        "config": root / "config.json",
        "base_config": root / "base-config.json",
        "snapshots": root / "snapshots",
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".draft-", dir=str(path.parent))
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _read_state(worktree: Path) -> Dict[str, Any]:
    path = _paths(worktree)["state"]
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DraftError("not-a-draft", f"draft state is unavailable in {worktree}: {exc}")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise DraftError("unsupported-draft", "draft state has an unsupported schema version")
    return state


@contextlib.contextmanager
def _locked(worktree: Path):
    lock = _paths(worktree)["lock"]
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DraftError("busy", "another writer is changing this draft") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextlib.contextmanager
def request_lock(repo: Path, name: str, idempotency_key: str,
                 request_identity: str, timeout: float = 620.0):
    """Serialize an exact save across processes, then let the caller reread durable state."""
    worktree, _ = find(repo, name)
    digest = hashlib.sha256(
        (name + "\0" + idempotency_key + "\0" + request_identity).encode("utf-8"),
    ).hexdigest()
    path = _paths(worktree)["root"] / ("request-" + digest + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(path, "a", encoding="utf-8") as stream:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise DraftError(
                        "busy", "another matching save is still changing this draft",
                    ) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _snapshot(paths: Dict[str, Path], revision: str, config: Optional[bytes]) -> None:
    if config is None:
        return
    _atomic_bytes(paths["snapshots"] / (revision + ".json"), config)


def create(
    repo: Path,
    name: str,
    base: str,
    config_path: Path,
    create_worktree: Callable[[Path, str, str, str], Path],
    cleanup_worktree: Callable[[Path, Path, str, str], None],
) -> Dict[str, Any]:
    """Create a draft through the caller's managed-worktree primitive."""
    if not NAME.fullmatch(name):
        raise DraftError("invalid-name", "draft name must use only letters, numbers, dots, underscores, and hyphens")
    config = config_path.read_bytes() if config_path.is_file() else None
    base_ref, base_revision = resolve_base(repo, base)
    worktree_name = "draft-" + name
    branch = "draft/" + name
    try:
        worktree = create_worktree(repo, worktree_name, branch, base_revision)
    except SystemExit as exc:
        raise DraftError("create-failed", str(exc)) from exc
    try:
        paths = _paths(worktree)
        paths["root"].mkdir(mode=0o700)
        if config is not None:
            _atomic_bytes(paths["config"], config)
            _atomic_bytes(paths["base_config"], config)
        state: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "draft_id": str(uuid.uuid4()),
            "name": name,
            "worktree_name": worktree_name,
            "branch": branch,
            "base_ref": base_ref,
            "base_revision": base_revision,
            "revision": base_revision,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "config_present": config is not None,
            "idempotency": {},
        }
        _atomic_json(paths["state"], state)
        _snapshot(paths, base_revision, config)
        return describe(repo, worktree, state)
    except BaseException as primary:
        try:
            cleanup_worktree(repo, worktree, branch, base_revision)
        except BaseException as cleanup:
            raise DraftError(
                "create-cleanup-failed", f"{primary}; cleanup failed: {cleanup}",
            ) from primary
        if isinstance(primary, DraftError):
            raise
        raise DraftError("create-failed", str(primary)) from primary


def _registered_worktrees(repo: Path) -> Iterable[Path]:
    out = _git(repo, "worktree", "list", "--porcelain")
    for block in out.stdout.strip().split("\n\n"):
        record = block.splitlines()
        line = next((item for item in record if item.startswith("worktree ")), None)
        if line is None or any(item == "prunable" or item.startswith("prunable ") for item in record):
            continue
        path = Path(line[9:])
        if path.is_dir():
            yield path.resolve()


def _vanished(worktree: Path) -> bool:
    return not (worktree / ".git").exists()


def _state_path(worktree: Path) -> Optional[Path]:
    """Return a registered worktree's draft state path, or None once it has no checkout."""
    try:
        return _paths(worktree)["state"]
    except DraftError:
        if _vanished(worktree):
            return None
        raise


def _draft_worktrees(repo: Path) -> Iterable[Path]:
    """Yield registered worktrees holding draft state, skipping any removed mid-scan.

    Other sessions add and remove worktrees of a shared repository at any time, so one listed a
    moment ago can be gone before its git directory resolves; only that failure is skipped. A
    checkout that still exists and cannot be read is an error.
    """
    for worktree in _registered_worktrees(repo):
        state_path = _state_path(worktree)
        if state_path is not None and state_path.is_file():
            yield worktree


def find(repo: Path, name: str) -> Tuple[Path, Dict[str, Any]]:
    for worktree in _draft_worktrees(repo):
        try:
            state = _read_state(worktree)
        except DraftError:
            if _vanished(worktree):
                continue
            raise
        if state.get("name") == name or state.get("draft_id") == name:
            return worktree, state
    raise DraftError("not-found", "draft does not exist: " + name)


def _rebase_in_progress(worktree: Path) -> bool:
    admin = _admin_dir(worktree)
    return (admin / "rebase-merge").exists() or (admin / "rebase-apply").exists()


def describe(repo: Path, worktree: Path, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = _read_state(worktree) if state is None else state
    installed = _revision(repo)
    behind = _git(repo, "rev-list", "--count", state["base_revision"] + ".." + installed, check=False)
    behind_by = int(behind.stdout.strip()) if behind.returncode == 0 and behind.stdout.strip().isdigit() else None
    actual = _revision(worktree)
    return {
        "draft_id": state["draft_id"],
        "name": state["name"],
        "path": str(worktree),
        "branch": state["branch"],
        "base": state["base_ref"],
        "base_revision": state["base_revision"],
        "revision": state["revision"],
        "actual_revision": actual,
        "installed_revision": installed,
        "behind_by": behind_by,
        "behind_installed": bool(behind_by),
        "can_rebase": bool(behind_by) and not _rebase_in_progress(worktree),
        "rebase_conflicted": _rebase_in_progress(worktree),
        "state_matches_checkout": actual == state["revision"],
        "created_at": state["created_at"],
    }


def list_drafts(repo: Path) -> List[Dict[str, Any]]:
    drafts: List[Dict[str, Any]] = []
    for worktree in _draft_worktrees(repo):
        try:
            drafts.append(describe(repo, worktree))
        except DraftError:
            if not _vanished(worktree):
                raise
    return sorted(drafts, key=lambda item: (item["name"], item["draft_id"]))


def read_config(repo: Path, name: str) -> Dict[str, Any]:
    """Read one draft's private configuration under the draft writer lock."""
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        path = _paths(worktree)["config"]
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            ) if path.is_file() else {}
        except (OSError, UnicodeError, ValueError) as exc:
            raise DraftError("invalid-config", "draft configuration is unavailable") from exc
        if not isinstance(value, dict):
            raise DraftError("invalid-config", "draft configuration must be a JSON object")
        return {"draft": describe(repo, worktree, state), "config": value}


@contextlib.contextmanager
def locked_context(repo: Path, name: str):
    """Yield one revision/config snapshot while holding the draft writer lock."""
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        path = _paths(worktree)["config"]
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            ) if path.is_file() else {}
        except (OSError, UnicodeError, ValueError) as exc:
            raise DraftError("invalid-config", "draft configuration is unavailable") from exc
        if not isinstance(value, dict):
            raise DraftError("invalid-config", "draft configuration must be a JSON object")
        yield worktree.resolve(), state, value


def checkpoint_config(repo: Path, name: str, base_revision: str, idempotency_key: str,
                      config: Dict[str, Any], check_command: Optional[List[str]] = None,
                      request_identity: Optional[str] = None) -> Dict[str, Any]:
    """Save validated structured configuration through the normal draft transaction."""
    if not isinstance(config, dict):
        raise DraftError("invalid-config", "draft configuration must be a JSON object")
    try:
        content = (json.dumps(config, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise DraftError("invalid-config", "draft configuration must contain finite JSON values") from exc
    return checkpoint(repo, name, base_revision, idempotency_key,
                      config=content, check_command=check_command,
                      request_identity=request_identity)


def replay_config_request(
    repo: Path, name: str, idempotency_key: str, request_identity: str,
) -> Optional[Dict[str, Any]]:
    """Return a durable result identified by the original config request, if present."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DraftError("invalid-idempotency-key", "an idempotency key of 1 to 200 characters is required")
    if not request_identity:
        raise DraftError("invalid-idempotency-key", "a config request identity is required")
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        prior = state["idempotency"].get(idempotency_key)
        if prior is None or "request_identity" not in prior:
            return None
        if prior["request_identity"] != request_identity:
            raise DraftError(
                "idempotency-conflict", "idempotency key was already used for a different save",
            )
        return dict(prior["result"], replayed=True)


def replay_config_checkpoint(
    repo: Path, name: str, idempotency_key: str, config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return a durable matching config checkpoint, or ``None`` for a new request."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DraftError("invalid-idempotency-key", "an idempotency key of 1 to 200 characters is required")
    try:
        content = (json.dumps(config, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise DraftError("invalid-config", "draft configuration must contain finite JSON values") from exc
    digest = _request_digest({}, content)
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        prior = state["idempotency"].get(idempotency_key)
        if prior is None:
            return None
        if prior["digest"] != digest:
            raise DraftError(
                "idempotency-conflict", "idempotency key was already used for a different save",
            )
        return dict(prior["result"], replayed=True)


def _text_diff(before: Optional[bytes], after: Optional[bytes], before_name: str, after_name: str) -> str:
    old = [] if before is None else before.decode("utf-8", "replace").splitlines(keepends=True)
    new = [] if after is None else after.decode("utf-8", "replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile=before_name, tofile=after_name))


def diff(repo: Path, name: str) -> Dict[str, Any]:
    worktree, state = find(repo, name)
    paths = _paths(worktree)
    source = _git(worktree, "diff", "--binary", state["base_revision"] + ".." + state["revision"])
    base_config = paths["base_config"].read_bytes() if paths["base_config"].is_file() else None
    config = paths["config"].read_bytes() if paths["config"].is_file() else None
    return {
        "draft": describe(repo, worktree, state),
        "source_patch": source.stdout,
        "config_patch": _text_diff(base_config, config, "config@base", "config@draft"),
        "nothing_applied": True,
    }


def _request_digest(
    files: Dict[str, Optional[bytes]], config: Optional[bytes], safe_symlinks: Iterable[str] = (),
) -> str:
    payload = {
        "files": [
            {"path": path, "sha256": None if content is None else hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ],
        "config_sha256": None if config is None else hashlib.sha256(config).hexdigest(),
        "safe_symlinks": sorted(safe_symlinks),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_config(config: Optional[bytes]) -> None:
    if config is None:
        return
    try:
        value = json.loads(config.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DraftError("invalid-config", "draft configuration must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DraftError("invalid-config", "draft configuration must be a JSON object")


def _safe_target(worktree: Path, relative: str, allow_leaf_symlink: bool = False) -> Path:
    candidate = Path(relative)
    if (candidate.is_absolute() or not candidate.parts or ".." in candidate.parts
            or any(part.casefold() == ".git" for part in candidate.parts)):
        raise DraftError("invalid-path", "draft save path must stay inside the checkout: " + relative)
    target = worktree.joinpath(*candidate.parts)
    cursor = worktree
    for index, part in enumerate(candidate.parts):
        cursor = cursor / part
        if cursor.is_symlink() and not (allow_leaf_symlink and index == len(candidate.parts) - 1):
            raise DraftError("symlink-path", "draft save refuses symlink paths: " + relative)
    return target


def _encoded(content: Optional[bytes]) -> Optional[str]:
    return None if content is None else base64.b64encode(content).decode("ascii")


def _decoded(content: Optional[str]) -> Optional[bytes]:
    return None if content is None else base64.b64decode(content.encode("ascii"))


@contextlib.contextmanager
def _anchored_target(worktree: Path, relative: str, allow_leaf_symlink: bool = False,
                     create_parents: bool = True):
    """Hold the target's parent directory open without following any component."""
    candidate = Path(relative)
    _safe_target(worktree, relative, allow_leaf_symlink=allow_leaf_symlink)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(worktree), flags)
    except OSError as exc:
        raise DraftError("stale-source", "draft checkout changed; reload before saving") from exc
    try:
        for part in candidate.parts[:-1]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_parents:
                    raise DraftError(
                        "stale-source", "draft module changed; reload before saving",
                    )
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    child = os.open(part, flags, dir_fd=descriptor)
                except OSError as exc:
                    raise DraftError(
                        "symlink-path", "draft save refuses changed parent paths: " + relative,
                    ) from exc
            except OSError as exc:
                raise DraftError("symlink-path", "draft save refuses changed parent paths: " + relative) from exc
            os.close(descriptor)
            descriptor = child
        yield descriptor, candidate.name
    finally:
        os.close(descriptor)


def _capture_target_at(parent: int, leaf: str, display: str,
                       allow_symlink: bool = False) -> Dict[str, Any]:
    try:
        metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISREG(metadata.st_mode):
        try:
            descriptor = os.open(
                leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent,
            )
        except OSError as exc:
            raise DraftError("stale-source", "draft module changed; reload before saving") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise DraftError("stale-source", "draft module changed; reload before saving")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                content = stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return {"kind": "file", "mode": stat.S_IMODE(metadata.st_mode),
                "identity": [metadata.st_dev, metadata.st_ino],
                "content": _encoded(content)}
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(leaf, dir_fd=parent)
        target_path = Path(target)
        if (not allow_symlink or target_path.is_absolute() or ".." in target_path.parts
                or any(part.casefold() == ".git" for part in target_path.parts)):
            raise DraftError("symlink-path", "draft save refuses unsafe symlink paths: " + display)
        return {"kind": "symlink", "target": target}
    raise DraftError("unsupported-file", "draft save supports only regular files and safe symlinks: " + display)


def _remove_leaf_at(parent: int, leaf: str, display: str) -> None:
    try:
        metadata = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
        raise DraftError("unsupported-file", "draft save refuses to replace this file type: " + display)
    os.unlink(leaf, dir_fd=parent)
    os.fsync(parent)


def _atomic_bytes_at(parent: int, leaf: str, content: bytes, mode: int,
                     expected: Optional[Dict[str, Any]] = None) -> None:
    desired = {"kind": "file", "mode": mode, "content": _encoded(content)}
    before = _capture_target_at(parent, leaf, leaf, allow_symlink=True)
    if expected is not None and not _same_captured_image(before, expected):
        raise DraftError("stale-source", "draft module changed; reload before saving")
    _replace_image_at(parent, leaf, leaf, desired, before)


def _same_captured_image(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Compare a live capture to the exact image previously held by the transaction."""
    fields = ("kind", "mode", "identity", "content", "target")
    return all(left.get(field) == right.get(field) for field in fields)


def _temporary_prefix(role: str, leaf: str) -> str:
    """Name a transaction temporary after its target, so settling one path never takes a sibling's."""
    return ".draft-" + role + "-" + hashlib.sha256(os.fsencode(leaf)).hexdigest()[:16] + "-"


def _prepare_image_at(parent: int, leaf: str, image: Dict[str, Any]) -> Optional[str]:
    """Create a durable unpublished leaf for one desired image."""
    kind = image.get("kind")
    if kind == "absent":
        return None
    temporary = _temporary_prefix("candidate", leaf) + uuid.uuid4().hex
    if kind == "symlink":
        os.symlink(image["target"], temporary, dir_fd=parent)
        return temporary
    if kind != "file":
        raise DraftError("recovery-conflict", "draft journal contains an unsupported file image")
    mode = int(image["mode"])
    content = _decoded(image.get("content")) or b""
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode, dir_fd=parent,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_prepared_at(parent: int, temporary: str, leaf: str,
                         image: Dict[str, Any]) -> None:
    """Publish only into an absent destination; never replace a concurrent writer."""
    try:
        if image.get("kind") == "file":
            os.link(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
        else:
            os.symlink(image["target"], leaf, dir_fd=parent)
    except FileExistsError as exc:
        raise DraftError("stale-source", "draft module changed; reload before saving") from exc


def _restore_backup_at(parent: int, backup: str, leaf: str,
                       image: Dict[str, Any]) -> bool:
    """Restore a displaced image without clobbering a newer destination."""
    try:
        if image.get("kind") == "file":
            os.link(backup, leaf, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
        elif image.get("kind") == "symlink":
            os.symlink(image["target"], leaf, dir_fd=parent)
        else:
            return False
    except FileExistsError:
        return False
    os.fsync(parent)
    return True


def _replace_image_at(parent: int, leaf: str, display: str,
                      desired: Dict[str, Any], expected: Dict[str, Any]) -> None:
    """Compare and replace one leaf with a journaled no-clobber protocol.

    POSIX replace is not compare-and-swap. Moving the held leaf aside makes the destination
    absent, verifying that backup binds the comparison to the rename, and publishing with link
    or symlink gives the kernel an atomic no-clobber condition.
    """
    prepared = _prepare_image_at(parent, leaf, desired)
    backup: Optional[str] = None
    preserve_backup = False
    published = False
    try:
        if expected.get("kind") == "absent":
            current = _capture_target_at(parent, leaf, display, allow_symlink=True)
            if current.get("kind") != "absent":
                raise DraftError("stale-source", "draft module changed; reload before saving")
        else:
            backup = _temporary_prefix("backup", leaf) + uuid.uuid4().hex
            try:
                os.rename(leaf, backup, src_dir_fd=parent, dst_dir_fd=parent)
            except FileNotFoundError as exc:
                raise DraftError("stale-source", "draft module changed; reload before saving") from exc
            held = _capture_target_at(parent, backup, display, allow_symlink=True)
            if not _same_captured_image(held, expected):
                if not _restore_backup_at(parent, backup, leaf, held):
                    preserve_backup = True
                    raise DraftError(
                        "recovery-conflict",
                        "draft module changed during replacement; preserving both versions",
                    )
                raise DraftError("stale-source", "draft module changed; reload before saving")
        if desired.get("kind") != "absent":
            assert prepared is not None
            _publish_prepared_at(parent, prepared, leaf, desired)
            published = True
        os.fsync(parent)
    except BaseException as exc:
        if not isinstance(exc, (DraftError, OSError)):
            # An unexpected failure may leave the backup as the only copy; recovery restores it.
            preserve_backup = backup is not None
            raise
        if published:
            current = _capture_target_at(parent, leaf, display, allow_symlink=True)
            if _same_image(current, desired):
                try:
                    _remove_leaf_at(parent, leaf, display)
                except OSError:
                    preserve_backup = backup is not None
        if backup is not None and not preserve_backup:
            held = _capture_target_at(parent, backup, display, allow_symlink=True)
            if held.get("kind") != "absent":
                preserve_backup = not _restore_backup_at(parent, backup, leaf, held)
        if isinstance(exc, DraftError):
            raise
        raise DraftError("recovery-conflict", "draft replacement could not be made durable") from exc
    finally:
        for temporary in (prepared, None if preserve_backup else backup):
            if temporary is None:
                continue
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.fsync(parent)


def _restore_target_at(parent: int, leaf: str, display: str, image: Dict[str, Any]) -> None:
    current = _capture_target_at(parent, leaf, display, allow_symlink=True)
    _replace_image_at(parent, leaf, display, image, current)


def _leftovers_at(parent: int, leaf: str) -> List[Tuple[str, Dict[str, Any]]]:
    """List one target's transaction temporaries in its anchored directory with their images."""
    prefixes = (_temporary_prefix("backup", leaf), _temporary_prefix("candidate", leaf))
    found = []
    for entry in sorted(os.listdir(parent)):
        if not entry.startswith(prefixes):
            continue
        try:
            found.append((entry, _capture_target_at(parent, entry, entry, allow_symlink=True)))
        except DraftError:
            continue
    return found


def _settle_target_at(parent: int, leaf: str, display: str, before: Dict[str, Any],
                      after: Optional[Dict[str, Any]]) -> None:
    """Return one journaled path to its before-image unless a third party replaced it.

    A leaf that is absent while a backup holding the before-image survives was interrupted
    between moving the original aside and publishing its replacement, so it is restored even
    though a deletion was never requested. This path's temporaries matching either image are then
    removed, since leaving them would make the draft dirty; any other temporary, a sibling's
    included, is preserved.
    """
    current = _capture_target_at(parent, leaf, display, allow_symlink=True)
    leftovers = _leftovers_at(parent, leaf)
    backup_prefix = _temporary_prefix("backup", leaf)
    interrupted = (
        current.get("kind") == "absent" and before.get("kind") in ("file", "symlink")
        and any(entry.startswith(backup_prefix) and _same_image(held, before)
                for entry, held in leftovers)
    )
    if after is None or _same_image(current, after) or interrupted:
        _restore_target_at(parent, leaf, display, before)
        leftovers = _leftovers_at(parent, leaf)
    images = [image for image in (before, after) if image and image.get("kind") != "absent"]
    removed = False
    for entry, held in leftovers:
        if any(_same_image(held, image) for image in images):
            os.unlink(entry, dir_fd=parent)
            removed = True
    if removed:
        os.fsync(parent)


def _write_target_at(parent: int, leaf: str, display: str, content: Optional[bytes],
                     before: Dict[str, Any]) -> None:
    current = _capture_target_at(parent, leaf, display, allow_symlink=True)
    if not _same_captured_image(current, before):
        raise DraftError("stale-source", "draft module changed; reload before saving")
    if before["kind"] == "symlink":
        if content is not None:
            raise DraftError("symlink-path", "draft save cannot replace a symlink with file content")
    desired = _written_image(before, content)
    _replace_image_at(parent, leaf, display, desired, before)


def _written_image(before: Dict[str, Any], content: Optional[bytes]) -> Dict[str, Any]:
    if content is None:
        return {"kind": "absent"}
    mode = int(before["mode"]) if before["kind"] == "file" else 0o644
    return {"kind": "file", "mode": mode, "content": _encoded(content)}


def _same_image(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return all(left.get(field) == right.get(field)
               for field in ("kind", "mode", "content", "target"))


def _commit_matches(worktree: Path, base_revision: str, key: str, digest: str) -> bool:
    parent = _git(worktree, "rev-parse", "HEAD^", check=False)
    if parent.returncode or parent.stdout.strip() != base_revision:
        return False
    message = _git(worktree, "show", "-s", "--format=%B", "HEAD").stdout
    return ("Draft-Idempotency-Key: " + key) in message and ("Draft-Request-Digest: " + digest) in message


def _recover(worktree: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    paths = _paths(worktree)
    if not paths["journal"].is_file():
        return state
    try:
        journal = json.loads(paths["journal"].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DraftError("recovery-conflict", "draft journal is unreadable; preserving it for inspection") from exc
    head = _revision(worktree)
    if head != journal["base_revision"]:
        if not _commit_matches(worktree, journal["base_revision"], journal["key"], journal["digest"]):
            raise DraftError("recovery-conflict", "draft moved during an incomplete save; preserving the journal")
        committed_paths = [item["path"] for item in journal["files"]]
        if committed_paths:
            # The ref advanced from a private index; bring the draft index up to the commit.
            _git(worktree, "reset", "--quiet", head, "--", *committed_paths)
        result = {"draft_id": state["draft_id"], "name": state["name"], "revision": head}
        state["revision"] = head
        state["config_present"] = journal.get("config_after") is not None
        saved_request = {"digest": journal["digest"], "result": result}
        if journal.get("request_identity") is not None:
            saved_request["request_identity"] = journal["request_identity"]
        if isinstance(journal.get("canonical_response"), dict):
            response = copy.deepcopy(journal["canonical_response"])
            response["result"] = dict(result, replayed=False)
            saved_request["response"] = response
        state["idempotency"][journal["key"]] = saved_request
        _atomic_json(paths["state"], state)
        _snapshot(paths, head, _decoded(journal.get("config_after")))
        paths["journal"].unlink()
        _fsync_directory(paths["root"])
        return state
    requested = [item["path"] for item in journal["files"]]
    if requested:
        _git(worktree, "reset", "--quiet", "HEAD", "--", *requested)
    for item in journal["files"]:
        with _anchored_target(worktree, item["path"], allow_leaf_symlink=True) as (parent, leaf):
            _settle_target_at(parent, leaf, item["path"], item["before"], item.get("after"))
    before_config = _decoded(journal.get("config_before"))
    if before_config is None:
        if paths["config"].exists():
            paths["config"].unlink()
    else:
        _atomic_bytes(paths["config"], before_config)
    paths["journal"].unlink()
    _fsync_directory(paths["root"])
    return state


def _git_index(worktree: Path, index: Path, *args: str,
               input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(index)
    run = subprocess.run(
        ["git", "-C", str(worktree), *args], input=input_bytes,
        capture_output=True, timeout=120, env=environment,
    )
    if run.returncode:
        detail = (run.stderr or run.stdout or b"git command failed").decode("utf-8", "replace").strip()
        raise DraftError("git-failed", _public_detail(detail, {"<draft>": worktree, "<index>": index}))
    return run


def _tree_entry(worktree: Path, tree: str, relative: str) -> Optional[Tuple[str, str]]:
    shown = _git(worktree, "ls-tree", tree, "--", relative).stdout.strip()
    if not shown:
        return None
    header, _tab, name = shown.partition("\t")
    fields = header.split()
    if len(fields) != 3 or name != relative:
        raise DraftError("commit-failed", "validated tree contains an unexpected path")
    return fields[0], fields[2]


def _validated_commit(worktree: Path, state: Dict[str, Any], base_revision: str,
                      files: Dict[str, Optional[bytes]],
                      before_images: Dict[str, Dict[str, Any]], message: str) -> str:
    """Build a private tree from validated bytes and advance the draft ref with CAS."""
    descriptor, raw_index = tempfile.mkstemp(prefix="draft-index-")
    os.close(descriptor)
    os.unlink(raw_index)
    index = Path(raw_index)
    try:
        _git_index(worktree, index, "read-tree", base_revision)
        expected_changed = set()
        expected_entries: Dict[str, Optional[Tuple[str, str]]] = {}
        for relative, content in sorted(files.items()):
            before = before_images[relative]
            if content is None:
                _git_index(worktree, index, "update-index", "--force-remove", "--", relative)
                expected_entries[relative] = None
                if before.get("kind") != "absent":
                    expected_changed.add(relative)
                continue
            blob = _git_index(
                worktree, index, "hash-object", "-w", "--stdin", input_bytes=content,
            ).stdout.decode("ascii").strip()
            mode = "100755" if int(before.get("mode", 0o644)) & 0o111 else "100644"
            _git_index(worktree, index, "update-index", "--add", "--cacheinfo",
                       mode, blob, relative)
            expected_entries[relative] = (mode, blob)
            prior = _tree_entry(worktree, base_revision, relative)
            if prior != (mode, blob):
                expected_changed.add(relative)
        tree = _git_index(worktree, index, "write-tree").stdout.decode("ascii").strip()
        changed = set(_git(
            worktree, "diff-tree", "--no-commit-id", "--name-only", "-r",
            base_revision, tree,
        ).stdout.splitlines())
        if changed != expected_changed:
            raise DraftError("commit-failed", "validated tree changed an unexpected path")
        for relative, expected in expected_entries.items():
            if _tree_entry(worktree, tree, relative) != expected:
                raise DraftError("commit-failed", "validated tree does not match the checked bytes")
        committed = subprocess.run(
            ["git", "-C", str(worktree), "commit-tree", tree, "-p", base_revision],
            input=message.encode("utf-8"), capture_output=True, timeout=120,
        )
        if committed.returncode:
            detail = (committed.stderr or committed.stdout or b"git commit-tree failed") \
                .decode("utf-8", "replace").strip()
            raise DraftError("commit-failed", _public_detail(detail, {"<draft>": worktree}))
        revision = committed.stdout.decode("ascii").strip()
        if _revision(worktree, revision + "^") != base_revision:
            raise DraftError("commit-failed", "checkpoint parent does not match the validated revision")
        if _git(worktree, "show", "-s", "--format=%T", revision).stdout.strip() != tree:
            raise DraftError("commit-failed", "checkpoint tree does not match the validated tree")
        branch_ref = "refs/heads/" + str(state["branch"])
        symbolic = _git(worktree, "symbolic-ref", "--quiet", "HEAD", check=False)
        if symbolic.returncode or symbolic.stdout.strip() != branch_ref:
            raise DraftError("stale-revision", "draft checkout moved outside the draft service")
        advanced = _git(
            worktree, "update-ref", branch_ref, revision, base_revision, check=False,
        )
        if advanced.returncode:
            raise DraftError("stale-revision", "draft revision changed; reload before saving")
        if files:
            _git(worktree, "reset", "--quiet", revision, "--", *sorted(files))
        return revision
    finally:
        try:
            index.unlink()
        except FileNotFoundError:
            pass


def checkpoint(
    repo: Path,
    name: str,
    base_revision: str,
    idempotency_key: str,
    files: Optional[Dict[str, Optional[bytes]]] = None,
    config: Optional[bytes] = None,
    check_command: Optional[List[str]] = None,
    safe_symlinks: Iterable[str] = (),
    request_identity: Optional[str] = None,
    expected_digests: Optional[Dict[str, str]] = None,
    canonical_response: Optional[Dict[str, Any]] = None,
    check_environment: Optional[Dict[str, str]] = None,
    _locked_worktree: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate and commit one draft save; exact retries return the durable prior result."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DraftError("invalid-idempotency-key", "an idempotency key of 1 to 200 characters is required")
    files = {} if files is None else dict(files)
    expected_digests = {} if expected_digests is None else dict(expected_digests)
    if not set(expected_digests).issubset(files):
        raise DraftError("invalid-path", "digest-protected paths must also be present in the save")
    safe_symlinks = frozenset(safe_symlinks)
    if not safe_symlinks.issubset(files):
        raise DraftError("invalid-path", "safe symlink paths must also be present in the save")
    _validate_config(config)
    digest = _request_digest(files, config, safe_symlinks)
    worktree = Path(_locked_worktree).resolve() if _locked_worktree is not None else find(repo, name)[0]
    targets = {
        relative: _safe_target(worktree, relative, relative in safe_symlinks)
        for relative in files
    }
    lock = contextlib.nullcontext() if _locked_worktree is not None else _locked(worktree)
    with lock:
        state = _recover(worktree, _read_state(worktree))
        prior = state["idempotency"].get(idempotency_key)
        if prior:
            if prior["digest"] != digest:
                raise DraftError("idempotency-conflict", "idempotency key was already used for a different save")
            return dict(prior["result"], replayed=True)
        actual = _revision(worktree)
        if base_revision != state["revision"] or actual != state["revision"]:
            raise DraftError("stale-revision", "draft revision changed; reload before saving")
        paths = _paths(worktree)
        before_config = paths["config"].read_bytes() if paths["config"].is_file() else None
        after_config = before_config if config is None else config
        with contextlib.ExitStack() as stack:
            anchors = {
                relative: stack.enter_context(_anchored_target(
                    worktree, relative, allow_leaf_symlink=relative in safe_symlinks,
                ))
                for relative in files
            }
            before_images = {
                relative: _capture_target_at(parent, leaf, relative, relative in safe_symlinks)
                for relative, (parent, leaf) in anchors.items()
            }
            for relative, expected in expected_digests.items():
                image = before_images[relative]
                actual = (hashlib.sha256(_decoded(image.get("content")) or b"").hexdigest()
                          if image["kind"] == "file" else "")
                if not expected or actual != expected:
                    raise DraftError("stale-source", "draft module changed; reload before saving")
            dirty = _git(worktree, "status", "--porcelain", "--untracked-files=all")
            if dirty.stdout.strip():
                raise DraftError("dirty-draft", "draft has changes outside the save transaction")
            journal = {
                "schema_version": SCHEMA_VERSION,
                "key": idempotency_key,
                "digest": digest,
                "base_revision": base_revision,
                "files": [{"path": relative, "before": before_images[relative],
                           "after": _written_image(before_images[relative], files[relative])}
                          for relative in sorted(targets)],
                "config_before": _encoded(before_config),
                "config_after": _encoded(after_config),
            }
            if request_identity is not None:
                journal["request_identity"] = request_identity
            if canonical_response is not None:
                journal["canonical_response"] = canonical_response
            _atomic_json(paths["journal"], journal)
            written = []
            config_written = False
            try:
                for relative, (parent, leaf) in anchors.items():
                    written.append(relative)
                    _write_target_at(parent, leaf, relative, files[relative], before_images[relative])
                if config is not None:
                    _atomic_bytes(paths["config"], config)
                    config_written = True
                command = check_command or [sys.executable, "bin/harness", "lint"]
                try:
                    checked = subprocess.run(
                        command, cwd=str(worktree), capture_output=True, text=True, timeout=600,
                        env=check_environment,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise DraftError("check-timeout", "draft validation timed out") from exc
                if checked.returncode:
                    detail = checked.stderr.strip() or checked.stdout.strip() or "draft check failed"
                    raise DraftError("check-failed", _public_detail(detail, {
                        "<draft>": worktree,
                        "<check-home>": (check_environment or {}).get("HARNESS_HOME"),
                    }))
                for relative, (parent, leaf) in anchors.items():
                    current = _capture_target_at(parent, leaf, relative, allow_symlink=True)
                    intended = _written_image(before_images[relative], files[relative])
                    if not _same_image(current, intended):
                        raise DraftError(
                            "stale-source", "draft module changed during validation; reload before saving",
                        )
                    with _anchored_target(
                        worktree, relative,
                        allow_leaf_symlink=relative in safe_symlinks,
                        create_parents=False,
                    ) as (current_parent, current_leaf):
                        held_metadata = os.fstat(parent)
                        current_metadata = os.fstat(current_parent)
                        if (current_leaf != leaf or
                                (current_metadata.st_dev, current_metadata.st_ino) !=
                                (held_metadata.st_dev, held_metadata.st_ino)):
                            raise DraftError(
                                "stale-source",
                                "draft module parent changed during validation; reload before saving",
                            )
                message = (
                    "chore(draft): checkpoint " + state["name"] + "\n\n"
                    + "Draft-Idempotency-Key: " + idempotency_key + "\n"
                    + "Draft-Request-Digest: " + digest + "\n"
                )
                revision = _validated_commit(
                    worktree, state, base_revision, files, before_images, message,
                )
            except Exception:
                try:
                    commit_completed = _commit_matches(
                        worktree, base_revision, idempotency_key, digest,
                    )
                except Exception:
                    commit_completed = False
                if commit_completed:
                    recovered = _recover(worktree, state)
                    return dict(recovered["idempotency"][idempotency_key]["result"], replayed=False)
                if targets:
                    _git(worktree, "reset", "--quiet", "HEAD", "--", *sorted(targets))
                for relative in written:
                    parent, leaf = anchors[relative]
                    _settle_target_at(
                        parent, leaf, relative, before_images[relative],
                        _written_image(before_images[relative], files[relative]),
                    )
                if config_written:
                    if before_config is None:
                        if paths["config"].exists():
                            paths["config"].unlink()
                    else:
                        _atomic_bytes(paths["config"], before_config)
                if paths["journal"].exists():
                    paths["journal"].unlink()
                    _fsync_directory(paths["root"])
                raise
            if _revision(worktree) != revision:
                raise DraftError("stale-revision", "draft revision changed after checkpoint")
            result = {"draft_id": state["draft_id"], "name": state["name"], "revision": revision}
            state["revision"] = revision
            state["config_present"] = after_config is not None
            saved_request = {"digest": digest, "result": result}
            if request_identity is not None:
                saved_request["request_identity"] = request_identity
            if canonical_response is not None:
                response = dict(canonical_response)
                response["result"] = dict(result, replayed=False)
                saved_request["response"] = response
            state["idempotency"][idempotency_key] = saved_request
            _atomic_json(paths["state"], state)
            _snapshot(paths, revision, after_config)
            paths["journal"].unlink()
            _fsync_directory(paths["root"])
            return dict(result, replayed=False)


def replay_request_response(
    repo: Path, name: str, idempotency_key: str, request_identity: str,
) -> Optional[Dict[str, Any]]:
    """Return the original durable response without resolving current module state."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DraftError("invalid-idempotency-key", "an idempotency key of 1 to 200 characters is required")
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        prior = state["idempotency"].get(idempotency_key)
        if prior is None:
            return None
        if prior.get("request_identity") != request_identity:
            raise DraftError("idempotency-conflict", "idempotency key was already used for a different save")
        response = prior.get("response")
        if not isinstance(response, dict):
            raise DraftError("recovery-conflict", "saved module response is unavailable")
        replayed = copy.deepcopy(response)
        if isinstance(replayed.get("result"), dict):
            replayed["result"]["replayed"] = True
        return replayed


def rebase(repo: Path, name: str) -> Dict[str, Any]:
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        if _rebase_in_progress(worktree):
            raise DraftError("rebase-conflict", "draft already has a rebase conflict to resolve or abort")
        if _revision(worktree) != state["revision"]:
            raise DraftError("stale-revision", "draft checkout moved outside the draft service")
        if _git(worktree, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise DraftError("dirty-draft", "draft must be clean before rebasing")
        installed = _revision(repo)
        run = _git(worktree, "rebase", installed, check=False)
        if run.returncode:
            detail = run.stderr.strip() or run.stdout.strip() or "rebase stopped on a conflict"
            raise DraftError("rebase-conflict", detail)
        revision = _revision(worktree)
        state["base_ref"] = "installed"
        state["base_revision"] = installed
        state["revision"] = revision
        _atomic_json(_paths(worktree)["state"], state)
        config = _paths(worktree)["config"]
        _snapshot(_paths(worktree), revision, config.read_bytes() if config.is_file() else None)
        return describe(repo, worktree, state)


def discard(
    repo: Path,
    name: str,
    remove_worktree: Callable[[Path, Path, str, str], None],
) -> Dict[str, Any]:
    worktree, _ = find(repo, name)
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        if _rebase_in_progress(worktree):
            raise DraftError("rebase-conflict", "abort or resolve the draft rebase before discarding")
        if _revision(worktree) != state["revision"]:
            raise DraftError("stale-revision", "draft checkout moved outside the draft service")
        symbolic = _git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if symbolic.returncode or symbolic.stdout.strip() != state["branch"]:
            raise DraftError("branch-mismatch", "draft branch no longer matches its recorded branch")
        try:
            remove_worktree(repo, worktree, state["branch"], state["revision"])
        except SystemExit as exc:
            raise DraftError("discard-failed", str(exc)) from exc
    return {"discarded": state["name"], "draft_id": state["draft_id"], "branch": state["branch"]}
