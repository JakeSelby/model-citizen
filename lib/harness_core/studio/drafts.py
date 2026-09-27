"""Managed draft worktrees and crash-recoverable checkpoint saves."""
from __future__ import annotations

import base64
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
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = 1
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TAG = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")
STATE_DIR = "agent-harness-draft"


class DraftError(ValueError):
    """A stable error suitable for both the CLI and the private Studio transport."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    run = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120,
    )
    if check and run.returncode:
        detail = run.stderr.strip() or run.stdout.strip() or "git command failed"
        raise DraftError("git-failed", detail)
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


def find(repo: Path, name: str) -> Tuple[Path, Dict[str, Any]]:
    for worktree in _registered_worktrees(repo):
        state_path = _paths(worktree)["state"]
        if not state_path.is_file():
            continue
        state = _read_state(worktree)
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
    for worktree in _registered_worktrees(repo):
        if _paths(worktree)["state"].is_file():
            drafts.append(describe(repo, worktree))
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


def checkpoint_config(repo: Path, name: str, base_revision: str, idempotency_key: str,
                      config: Dict[str, Any], check_command: Optional[List[str]] = None) -> Dict[str, Any]:
    """Save validated structured configuration through the normal draft transaction."""
    if not isinstance(config, dict):
        raise DraftError("invalid-config", "draft configuration must be a JSON object")
    try:
        content = (json.dumps(config, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise DraftError("invalid-config", "draft configuration must contain finite JSON values") from exc
    return checkpoint(repo, name, base_revision, idempotency_key,
                      config=content, check_command=check_command)


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


def _capture_target(path: Path, allow_symlink: bool = False) -> Dict[str, Any]:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return {"kind": "absent"}
    if stat.S_ISREG(metadata.st_mode):
        return {
            "kind": "file",
            "mode": stat.S_IMODE(metadata.st_mode),
            "content": _encoded(path.read_bytes()),
        }
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        target_path = Path(target)
        if (not allow_symlink or target_path.is_absolute() or ".." in target_path.parts
                or any(part.casefold() == ".git" for part in target_path.parts)):
            raise DraftError("symlink-path", "draft save refuses unsafe symlink paths: " + str(path))
        return {"kind": "symlink", "target": target}
    raise DraftError("unsupported-file", "draft save supports only regular files and safe symlinks: " + str(path))


def _remove_leaf(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
        raise DraftError("unsupported-file", "draft save refuses to replace this file type: " + str(path))
    path.unlink()


def _restore_target(path: Path, image: Dict[str, Any]) -> None:
    _remove_leaf(path)
    kind = image.get("kind")
    if kind == "absent":
        return
    if kind == "file":
        _atomic_bytes(path, _decoded(image["content"]) or b"", mode=int(image["mode"]))
        return
    if kind == "symlink":
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(image["target"], path)
        _fsync_directory(path.parent)
        return
    raise DraftError("recovery-conflict", "draft journal contains an unsupported file image")


def _write_target(path: Path, content: Optional[bytes], before: Dict[str, Any]) -> None:
    if content is None:
        _remove_leaf(path)
        return
    if before["kind"] == "symlink":
        raise DraftError("symlink-path", "draft save cannot replace a symlink with file content")
    mode = int(before["mode"]) if before["kind"] == "file" else 0o644
    _atomic_bytes(path, content, mode=mode)


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
        result = {"draft_id": state["draft_id"], "name": state["name"], "revision": head}
        state["revision"] = head
        state["idempotency"][journal["key"]] = {"digest": journal["digest"], "result": result}
        _atomic_json(paths["state"], state)
        _snapshot(paths, head, _decoded(journal.get("config_after")))
        paths["journal"].unlink()
        _fsync_directory(paths["root"])
        return state
    requested = [item["path"] for item in journal["files"]]
    if requested:
        _git(worktree, "reset", "--quiet", "HEAD", "--", *requested)
    for item in journal["files"]:
        target = _safe_target(worktree, item["path"], allow_leaf_symlink=True)
        _restore_target(target, item["before"])
    before_config = _decoded(journal.get("config_before"))
    if before_config is None:
        if paths["config"].exists():
            paths["config"].unlink()
    else:
        _atomic_bytes(paths["config"], before_config)
    paths["journal"].unlink()
    _fsync_directory(paths["root"])
    return state


def checkpoint(
    repo: Path,
    name: str,
    base_revision: str,
    idempotency_key: str,
    files: Optional[Dict[str, Optional[bytes]]] = None,
    config: Optional[bytes] = None,
    check_command: Optional[List[str]] = None,
    safe_symlinks: Iterable[str] = (),
) -> Dict[str, Any]:
    """Validate and commit one draft save; exact retries return the durable prior result."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DraftError("invalid-idempotency-key", "an idempotency key of 1 to 200 characters is required")
    files = {} if files is None else dict(files)
    safe_symlinks = frozenset(safe_symlinks)
    if not safe_symlinks.issubset(files):
        raise DraftError("invalid-path", "safe symlink paths must also be present in the save")
    _validate_config(config)
    digest = _request_digest(files, config, safe_symlinks)
    worktree, _ = find(repo, name)
    targets = {
        relative: _safe_target(worktree, relative, relative in safe_symlinks)
        for relative in files
    }
    with _locked(worktree):
        state = _recover(worktree, _read_state(worktree))
        prior = state["idempotency"].get(idempotency_key)
        if prior:
            if prior["digest"] != digest:
                raise DraftError("idempotency-conflict", "idempotency key was already used for a different save")
            return dict(prior["result"], replayed=True)
        actual = _revision(worktree)
        if base_revision != state["revision"] or actual != state["revision"]:
            raise DraftError("stale-revision", "draft revision changed; reload before saving")
        dirty = _git(worktree, "status", "--porcelain", "--untracked-files=all")
        if dirty.stdout.strip():
            raise DraftError("dirty-draft", "draft has changes outside the save transaction")
        paths = _paths(worktree)
        before_config = paths["config"].read_bytes() if paths["config"].is_file() else None
        after_config = before_config if config is None else config
        before_images = {
            relative: _capture_target(target, relative in safe_symlinks)
            for relative, target in targets.items()
        }
        journal = {
            "schema_version": SCHEMA_VERSION,
            "key": idempotency_key,
            "digest": digest,
            "base_revision": base_revision,
            "files": [
                {
                    "path": relative,
                    "before": before_images[relative],
                }
                for relative, target in sorted(targets.items())
            ],
            "config_before": _encoded(before_config),
            "config_after": _encoded(after_config),
        }
        _atomic_json(paths["journal"], journal)
        try:
            for relative, target in targets.items():
                _write_target(target, files[relative], before_images[relative])
            if config is not None:
                _atomic_bytes(paths["config"], config)
            command = check_command or [sys.executable, "bin/harness", "lint"]
            checked = subprocess.run(command, cwd=str(worktree), capture_output=True, text=True, timeout=600)
            if checked.returncode:
                detail = checked.stderr.strip() or checked.stdout.strip() or "draft check failed"
                raise DraftError("check-failed", detail)
            if targets:
                _git(worktree, "add", "--", *sorted(targets))
            message = (
                "chore(draft): checkpoint " + state["name"] + "\n\n"
                + "Draft-Idempotency-Key: " + idempotency_key + "\n"
                + "Draft-Request-Digest: " + digest + "\n"
            )
            committed = _git(worktree, "commit", "--allow-empty", "-m", message, check=False)
            if committed.returncode:
                raise DraftError("commit-failed", committed.stderr.strip() or committed.stdout.strip())
        except Exception:
            _recover(worktree, state)
            raise
        revision = _revision(worktree)
        result = {"draft_id": state["draft_id"], "name": state["name"], "revision": revision}
        state["revision"] = revision
        state["config_present"] = after_config is not None
        state["idempotency"][idempotency_key] = {"digest": digest, "result": result}
        _atomic_json(paths["state"], state)
        _snapshot(paths, revision, after_config)
        paths["journal"].unlink()
        _fsync_directory(paths["root"])
        return dict(result, replayed=False)


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
