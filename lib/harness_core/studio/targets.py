"""Resolve Studio run targets and build their isolated runtime profiles."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import drafts


COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class TargetError(ValueError):
    """A requested target cannot be resolved or built without touching live state."""


@dataclass(frozen=True)
class ResolvedTarget:
    kind: str
    ref: str
    revision: str
    version: str
    draft: Optional[str]
    config: Dict[str, Any]
    source: Path
    snapshot: bool = False


@dataclass(frozen=True)
class WorktreeImage:
    revision: str
    patch: str
    untracked: Tuple[Tuple[str, str, int, bytes], ...]


class TargetService:
    """One explicit repository authority for target resolution and profile construction."""

    def __init__(self, repository: Path):
        self.repository = Path(repository).resolve()

    def build(self, kind: str, ref: str, run_directory: Path) -> Dict[str, Any]:
        return build(self.repository, kind, ref, run_directory)


def _run(argv, *, cwd: Optional[Path] = None, env=None, input_text: Optional[str] = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env, input=input_text,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TargetError("target command could not run") from exc
    if done.returncode:
        detail = done.stderr.strip() or done.stdout.strip() or "target command failed"
        raise TargetError(detail)
    return done


def _git(repo: Path, *args: str, input_text: Optional[str] = None) -> str:
    return _run(["git", "-C", str(repo), *args], input_text=input_text).stdout.strip()


def _revision(repo: Path, ref: str = "HEAD") -> str:
    value = _git(repo, "rev-parse", "--verify", ref + "^{commit}")
    if not COMMIT.fullmatch(value):
        raise TargetError("target did not resolve to a full commit")
    return value.lower()


def _version(source: Path, fallback: str) -> str:
    path = source / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        value = ""
    return value or fallback


def _config_digest(config: Dict[str, Any]) -> str:
    try:
        encoded = json.dumps(config, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise TargetError("target configuration must contain finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _clone(repo: Path, revision: str, destination: Path) -> None:
    _run(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repo),
          str(destination)], timeout=300)
    _git(destination, "checkout", "--quiet", "--detach", revision)
    # One query and one batched delete: a shared repository carries hundreds of refs.
    kept = set(_git(destination, "for-each-ref", "--merged=HEAD", "--format=%(refname)",
                    "refs/tags").splitlines())
    doomed = [ref for ref in _git(destination, "for-each-ref", "--format=%(refname)").splitlines()
              if ref not in kept]
    if doomed:
        _git(destination, "update-ref", "--no-deref", "--stdin",
             input_text="".join("delete " + ref + "\n" for ref in doomed))
    remotes = _git(destination, "remote").splitlines()
    for remote in remotes:
        _git(destination, "remote", "remove", remote)
    _git(destination, "reflog", "expire", "--expire=now", "--all")
    _git(destination, "gc", "--quiet", "--prune=now")
    if _revision(destination) != revision.lower():
        raise TargetError("target snapshot did not land on its resolved commit")


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _validate_symlinks(root: Path, allowed: Tuple[Path, ...]) -> None:
    roots = tuple(path.resolve() for path in allowed)
    for current, directories, files in os.walk(str(root), followlinks=False):
        base = Path(current)
        names = list(directories) + list(files)
        directories[:] = [name for name in directories if not (base / name).is_symlink()
                          and name != ".git"]
        for name in names:
            path = base / name
            if not path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise TargetError("target contains an unsafe symlink: "
                                  + path.relative_to(root).as_posix()) from exc
            if not any(_inside(resolved, allowed_root) for allowed_root in roots):
                raise TargetError("target symlink escapes its immutable checkout: "
                                  + path.relative_to(root).as_posix())


def _registered_worktree(repo: Path, requested: str) -> Path:
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute() or not candidate.is_dir():
        raise TargetError("worktree target must name an existing absolute path")
    candidate = candidate.resolve()
    records = _git(repo, "worktree", "list", "--porcelain").splitlines()
    registered = {Path(line[9:]).resolve() for line in records if line.startswith("worktree ")}
    if candidate not in registered:
        raise TargetError("worktree target is not registered with this repository")
    return candidate


def _worktree_image(worktree: Path) -> WorktreeImage:
    revision = _revision(worktree)
    patch = _run(["git", "-C", str(worktree), "diff", "--binary", "HEAD", "--"]).stdout
    raw = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, timeout=120, check=False,
    )
    if raw.returncode:
        raise TargetError("could not list uncommitted worktree files")
    entries: List[Tuple[str, str, int, bytes]] = []
    for item in raw.stdout.split(b"\0"):
        if not item:
            continue
        try:
            relative = Path(item.decode("utf-8"))
        except UnicodeError as exc:
            raise TargetError("worktree contains a non-UTF-8 path") from exc
        origin = worktree / relative
        metadata = os.lstat(origin)
        if origin.is_symlink():
            entries.append((relative.as_posix(), "link", stat.S_IMODE(metadata.st_mode),
                            os.fsencode(os.readlink(origin))))
        elif origin.is_file():
            entries.append((relative.as_posix(), "file", stat.S_IMODE(metadata.st_mode),
                            origin.read_bytes()))
        else:
            raise TargetError("worktree contains an unsupported untracked entry: " + relative.as_posix())
    return WorktreeImage(revision, patch, tuple(entries))


def _copy_untracked(image: WorktreeImage, source: Path) -> None:
    for name, kind, mode, content in image.untracked:
        target = source / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "link":
            target.symlink_to(os.fsdecode(content))
        else:
            target.write_bytes(content)
            os.chmod(target, mode)


def _snapshot_worktree(worktree: Path, source: Path) -> Tuple[str, bool]:
    for attempt in range(2):
        before = _worktree_image(worktree)
        if source.exists():
            shutil.rmtree(str(source))
        _clone(worktree, before.revision, source)
        dirty = bool(before.patch or before.untracked)
        if before.patch:
            _run(["git", "apply", "--index", "--binary", "-"], cwd=source,
                 input_text=before.patch, timeout=300)
        _copy_untracked(before, source)
        _validate_symlinks(source, (source,))
        after = _worktree_image(worktree)
        if after != before:
            if attempt == 0:
                continue
            shutil.rmtree(str(source), ignore_errors=True)
            raise TargetError("worktree changed while its target snapshot was being built")
        if not dirty:
            return before.revision, False
        _git(source, "add", "-A")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_AUTHOR_NAME": "Model Citizen Studio",
            "GIT_AUTHOR_EMAIL": "studio.invalid",
            "GIT_COMMITTER_NAME": "Model Citizen Studio",
            "GIT_COMMITTER_EMAIL": "studio.invalid",
        }
        _run(["git", "-C", str(source), "commit", "--quiet", "--allow-empty", "-m",
              "chore(studio): snapshot run target"], env=environment)
        return _revision(source), True
    raise AssertionError("bounded worktree snapshot loop did not return")


def _branch_revision(repo: Path, ref: str) -> str:
    if COMMIT.fullmatch(ref):
        return _revision(repo, ref.lower())
    if (not BRANCH.fullmatch(ref) or ".." in ref or "//" in ref or ref.endswith(("/", "."))
            or "@{" in ref):
        raise TargetError("branch target must name a branch or full commit")
    for candidate in ("refs/heads/" + ref, "refs/remotes/origin/" + ref):
        exists = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
                                 candidate], timeout=120, check=False)
        if exists.returncode == 0:
            return _revision(repo, candidate)
    raise TargetError("branch target does not exist: " + ref)


def resolve(repository: Path, kind: str, ref: str, source: Path) -> ResolvedTarget:
    """Resolve one target to an immutable checkout; worktree dirt becomes a snapshot commit."""
    repo = Path(repository).resolve()
    if not (repo / ".git").exists() or not (repo / "bin" / "harness").is_file():
        raise TargetError("target repository is unavailable")
    if source.exists():
        raise TargetError("target source already exists")
    config: Dict[str, Any] = {}
    draft_name: Optional[str] = None
    snapshot = False
    if kind == "installed":
        allowed = {"current", "installed", str(repo)}
        if ref not in allowed:
            raise TargetError("installed target must name the current checkout")
        revision = _revision(repo)
        _clone(repo, revision, source)
    elif kind == "release":
        if not drafts.TAG.fullmatch(ref):
            raise TargetError("release target must name an exact v* tag")
        revision = _revision(repo, "refs/tags/" + ref)
        _clone(repo, revision, source)
    elif kind == "branch":
        revision = _branch_revision(repo, ref)
        _clone(repo, revision, source)
    elif kind == "worktree":
        worktree = _registered_worktree(repo, ref)
        revision, snapshot = _snapshot_worktree(worktree, source)
    elif kind == "draft":
        try:
            item = drafts.read_config(repo, ref)
            worktree, _state = drafts.find(repo, ref)
        except drafts.DraftError as exc:
            raise TargetError(str(exc)) from exc
        revision = str(item["draft"]["revision"])
        if item["draft"].get("actual_revision") != revision:
            raise TargetError("draft checkout does not match its checkpoint")
        config = dict(item["config"])
        draft_name = ref
        _clone(worktree, revision, source)
    else:
        raise TargetError("unsupported target kind: " + str(kind))
    _validate_symlinks(source, (source,))
    return ResolvedTarget(kind, ref, revision, _version(source, ref), draft_name,
                          config, source, snapshot)


def _sync_environment(profile: Path) -> Dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TERM": "dumb",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": str(profile),
        "HARNESS_HOME": str(profile),
        "CLAUDE_CONFIG_DIR": str(profile / ".claude"),
        "CODEX_HOME": str(profile / ".codex"),
        "XDG_CONFIG_HOME": str(profile / ".config"),
        "XDG_DATA_HOME": str(profile / ".local" / "share"),
        "XDG_STATE_HOME": str(profile / ".local" / "state"),
        "XDG_CACHE_HOME": str(profile / ".cache"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    }
    if os.environ.get("TMPDIR"):
        environment["TMPDIR"] = os.environ["TMPDIR"]
    return environment


def _sync_command() -> List[str]:
    command = [sys.executable, "bin/harness", "sync"]
    sandbox = Path("/usr/bin/sandbox-exec")
    if platform.system() == "Darwin" and sandbox.is_file():
        return [str(sandbox), "-p", "(version 1) (allow default) (deny network*)"] + command
    if platform.system() == "Linux":
        unshare = shutil.which("unshare", path="/usr/sbin:/usr/bin:/sbin:/bin")
        if unshare is None:
            raise TargetError("target sync requires network namespace isolation on Linux")
        return [unshare, "--net", "--"] + command
    return command


def build(repository: Path, kind: str, ref: str, run_directory: Path) -> Dict[str, Any]:
    """Build the target checkout into a disposable home and return its recorded identity."""
    root = Path(run_directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = root / "source"
    profile = root / "profile"
    target = resolve(repository, kind, ref, source)
    profile.mkdir(mode=0o700)
    config_dir = profile / ".config" / "agent-harness"
    config_dir.mkdir(mode=0o700, parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps(target.config, allow_nan=False, indent=2,
                                      sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(config_path, 0o600)
    done = _run(_sync_command(), cwd=source,
                env=_sync_environment(profile), timeout=600)
    _validate_symlinks(profile, (profile, source))
    return {
        "kind": target.kind,
        "ref": target.ref,
        "version": target.version,
        "revision": target.revision,
        "draft": target.draft,
        "config_digest": _config_digest(target.config),
        "source_path": str(source),
        "profile_path": str(profile),
        "snapshot": target.snapshot,
        "sync_output": done.stdout,
    }
