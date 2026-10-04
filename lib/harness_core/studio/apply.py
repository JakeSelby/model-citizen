# SPDX-License-Identifier: MIT
"""Review a draft against its base, then apply it through the governed path.

Review is read-only: every file and configuration key the draft changes, the checks (`citizen lint`
and strict resolution, run in the draft), the context-budget delta and the exact commands an apply
runs. Apply runs those same steps under the sync lock and the configuration lock, with the draft's
writer lock held so it cannot change underneath: a fast-forward of the developer's own root, one
`citizen config set` (or `unset`) per key in the order review proved, then `citizen sync` and the
doctor checks.

The draft's personal root lives in its checkout (`module_authoring.OWN_ROOT`), registered as the
installed checkout's path. Apply moves it out: the files go to `personal-primitives` beside the
user's configuration, and the registration is rewritten to that directory. A file is written only
when the live copy still equals the draft's base, or already equals the draft, so a later hand edit
is refused rather than overwritten. Any other change to the checkout edits a core module in place
and is refused, with a fork and a contribution branch offered instead.

The commands review shows are what apply runs: the configuration writes are `config set`'s own
locked body, and the order is found by running the commands against a copy of the live
configuration before anything is written.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import datetime
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from harness_core import reconcile

from . import drafts, module_authoring, module_editing


SCHEMA_VERSION = 1
OWN_ROOT = module_authoring.OWN_ROOT
CLI_COMMANDS = {
    "review": ("citizen", "draft", "review", "{draft}", "--json"),
    "apply": ("citizen", "draft", "apply", "{draft}", "--revision", "{revision}", "--json"),
    "recover": ("citizen", "draft", "recover", "--draft", "{draft}", "[--abandon]", "--json"),
}
# Written by `config set` itself as a consequence of a stance key, never set on its own.
DERIVED_KEYS = ("init_defaults",)
CORE_ACK = "core_switches_acknowledged"
MAX_FINDINGS = 200
MAX_LOG_LINES = 400
CHECK_TIMEOUT = 600


class ApplyError(ValueError):
    """A review or apply that cannot proceed; nothing has been written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def home_dir() -> Path:
    return Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())


def config_file(home: Path) -> Path:
    return Path(home) / ".config" / "agent-harness" / "config.json"


def destination(home: Path) -> Path:
    """Where the draft's personal root lands: beside the configuration, outside every checkout."""
    return config_file(home).parent / OWN_ROOT


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _read_json_bytes(content: Optional[bytes]) -> Dict[str, Any]:
    if content is None:
        return {}
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ApplyError("invalid-config", "the live configuration is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ApplyError("invalid-config", "the live configuration is not a JSON object")
    return value


def _flatten(config: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Every settable leaf as a dotted key; an empty object contributes nothing."""
    flat: Dict[str, Any] = {}
    for key, value in config.items():
        if not prefix and key in DERIVED_KEYS:
            continue
        path = prefix + str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, path + "."))
        else:
            flat[path] = value
    return flat


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, allow_nan=False)


def _priority(change: Dict[str, Any]) -> Tuple[int, str]:
    """Likely repairs first; a refused write is retried after each one that succeeds."""
    key, value = change["key"], change.get("value")
    kind = key.partition(".")[0]
    if key == CORE_ACK:
        return (0 if value == "true" else 7, key)
    if key == "primitive_roots":
        return (1, key)
    if value == "off":
        return (2, key)
    if key == "mode":
        return (3, key)
    if kind == "stances":
        return (4, key)
    if value == "on":
        return (6, key)
    return (5, key)


def _citizen(command: Dict[str, Any]) -> str:
    if command["action"] == "unset":
        return "citizen config unset " + shlex.quote(command["key"])
    return "citizen config set %s %s" % (shlex.quote(command["key"]), shlex.quote(command["value"]))


# --------------------------------------------------------------------------- the personal root


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, check=False)


def _changed_paths(worktree: Path, base: str, revision: str) -> List[Dict[str, str]]:
    shown = _git(worktree, "diff", "--no-renames", "--name-status", "-z", base + ".." + revision)
    if shown.returncode:
        raise ApplyError("diff-unavailable", "the draft's changes could not be listed")
    fields = shown.stdout.decode("utf-8", "surrogateescape").split("\0")
    changed: List[Dict[str, str]] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index], fields[index + 1]
        if status and path:
            changed.append({"path": path, "status": {"A": "added", "D": "deleted"}.get(status[0], "modified")})
    return sorted(changed, key=lambda item: item["path"])


def _blob(worktree: Path, revision: str, path: str) -> Tuple[Optional[str], Optional[bytes]]:
    entry = drafts._tree_entry(worktree, revision, path)
    if entry is None:
        return None, None
    mode, _sha = entry
    shown = _git(worktree, "cat-file", "blob", revision + ":" + path)
    if shown.returncode:
        raise ApplyError("diff-unavailable", "draft file " + path + " could not be read")
    return mode, shown.stdout


def _contained(dest: Path, relative: str) -> Path:
    """`dest/relative`, refused when the root or any directory on the way is a link or a file.

    A link that already exists in the personal root would otherwise carry a read or a write to
    wherever it points.
    """
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or Path(relative).is_absolute():
        raise ApplyError("root-symlink", relative + " is not a path inside the personal root")
    current = dest
    for part in ("",) + tuple(parts[:-1]):
        current = current / part if part else current
        if os.path.islink(str(current)) or (os.path.lexists(str(current)) and not current.is_dir()):
            raise ApplyError("root-symlink", "%s in your personal root is a link or a file, not a "
                             "directory; apply never writes through it" % (
                                 current.relative_to(dest).as_posix() if current != dest else str(dest)))
    return current / parts[-1]


def _live_file(path: Path, display: str) -> Tuple[Optional[bytes], int]:
    """A live file's bytes and permission bits, or (None, 0) when it does not exist."""
    try:
        metadata = os.lstat(str(path))
    except FileNotFoundError:
        return None, 0
    if not stat.S_ISREG(metadata.st_mode):
        raise ApplyError("root-diverged", display + " in your personal root is not a regular file")
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def _registered_root(repo: Path, worktree: Path, raw_config: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """Whether the draft registers its own root, and every other in-checkout root it registers."""
    own = False
    others: List[str] = []
    for value in raw_config.get("primitive_roots", []) if isinstance(raw_config.get("primitive_roots"), list) else []:
        if not isinstance(value, str):
            continue
        path = Path(os.path.abspath(str(Path(value).expanduser())))
        for checkout in (repo, worktree):
            if module_editing._lexically_inside(checkout, path):
                if path == Path(os.path.abspath(str(checkout / OWN_ROOT))):
                    own = True
                else:
                    others.append(value)
                break
    return own, others


def _root_target(repo: Path, worktree: Path, roots: Any, dest: Path) -> Any:
    """`primitive_roots` with the draft's own root rewritten to `dest`, duplicates dropped."""
    if not isinstance(roots, list):
        return roots
    own = {os.path.abspath(str(repo / OWN_ROOT)), os.path.abspath(str(worktree / OWN_ROOT))}
    rewritten: List[Any] = []
    for value in roots:
        if isinstance(value, str) and os.path.abspath(str(Path(value).expanduser())) in own:
            value = str(dest)
        if value not in rewritten:
            rewritten.append(value)
    return rewritten


def _root_plan(worktree: Path, state: Mapping[str, Any], changed: List[Dict[str, str]],
               dest: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """The fast-forward of the personal root: each file to write or delete, or a refusal."""
    operations: List[Dict[str, Any]] = []
    refusals: List[Dict[str, str]] = []
    if os.path.islink(str(dest)) or (os.path.lexists(str(dest)) and not dest.is_dir()):
        return [], [{"code": "root-symlink",
                     "message": str(dest) + " is a link or a file, not a directory; nothing was written"}]
    prefix = OWN_ROOT + "/"
    for item in changed:
        if not item["path"].startswith(prefix):
            continue
        relative = item["path"][len(prefix):]
        base_mode, base_bytes = _blob(worktree, state["base_revision"], item["path"])
        mode, content = _blob(worktree, state["revision"], item["path"])
        if any(value is not None and value not in ("100644", "100755") for value in (base_mode, mode)):
            refusals.append({"code": "root-unsupported-file",
                             "message": item["path"] + " is not a regular file; apply copies files only"})
            continue
        try:
            live, live_mode = _live_file(_contained(dest, relative), relative)
        except ApplyError as exc:
            refusals.append({"code": exc.code, "message": str(exc)})
            continue
        if live == content:
            continue
        if live != base_bytes:
            refusals.append({"code": "root-diverged", "message": (
                relative + " in " + str(dest) + " changed since this draft was made; apply never "
                "overwrites it. Bring the draft up to date with it, or move your copy aside")})
            continue
        operations.append({"path": relative, "draft_path": item["path"],
                           "action": "delete" if content is None else "write",
                           "content": content, "prior": live, "prior_mode": live_mode,
                           "executable": mode == "100755"})
    return operations, refusals


def _root_commands(worktree: Path, revision: str, operations: List[Dict[str, Any]],
                   dest: Path) -> List[str]:
    writes = [item["draft_path"] for item in operations if item["action"] == "write"]
    commands: List[str] = []
    if writes:
        commands.append("mkdir -p %s && git -C %s archive --format=tar %s -- %s | tar -x -C %s" % (
            shlex.quote(str(dest.parent)), shlex.quote(str(worktree)), shlex.quote(revision),
            " ".join(shlex.quote(path) for path in writes), shlex.quote(str(dest.parent))))
    deletes = [item["path"] for item in operations if item["action"] == "delete"]
    if deletes:
        commands.append("rm -f -- " + " ".join(shlex.quote(str(dest / path)) for path in deletes))
    return commands


def _make_directories(directory: Path, created: List[str]) -> None:
    """`mkdir -p`, recording each directory it made, outermost first, in `created`."""
    missing = []
    current = directory
    while not os.path.lexists(str(current)):
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        created.append(str(path))


def missing_directories(dest: Path, paths: Iterable[str]) -> List[str]:
    """The directories writing `paths` under `dest` would make, `dest` included, outermost first.

    Journalled before the first write, so a restore removes only directories that did not exist.
    """
    missing: List[str] = []
    for relative in paths:
        current = (dest / relative).parent
        chain = []
        while not os.path.lexists(str(current)) and str(current) not in missing:
            chain.append(str(current))
            if current == dest or current.parent == current:
                break
            current = current.parent
        missing.extend(reversed(chain))
    return missing


def _write_root(dest: Path, operations: List[Dict[str, Any]], created: List[str]) -> None:
    """Write the planned files; `created` collects every directory made, even when a write fails."""
    for item in operations:
        target = _contained(dest, item["path"])
        if item["action"] == "delete":
            if os.path.lexists(str(target)):
                target.unlink()
            continue
        _make_directories(target.parent, created)
        drafts._atomic_bytes(target, item["content"], 0o755 if item["executable"] else 0o644)


def _restore_root(dest: Path, operations: List[Dict[str, Any]], created: Iterable[str]) -> None:
    """Put each file back as it was, permission bits included, and drop the directories apply made."""
    for item in operations:
        target = _contained(dest, item["path"])
        if item["prior"] is None:
            if os.path.lexists(str(target)):
                target.unlink()
        else:
            _make_directories(target.parent, [])
            drafts._atomic_bytes(target, item["prior"], item.get("prior_mode") or 0o644)
    # Deepest first, and only when empty: anything another writer put there stays.
    for directory in sorted(created, key=lambda value: value.count(os.sep), reverse=True):
        with contextlib.suppress(OSError):
            os.rmdir(directory)


# --------------------------------------------------------------------------- configuration


def _config_changes(repo: Path, worktree: Path, base: Mapping[str, Any], draft: Mapping[str, Any],
                    live: Mapping[str, Any], dest: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Every key the draft changes from its base: what it writes now, or why it cannot."""
    target = dict(draft)
    if "primitive_roots" in target:
        target["primitive_roots"] = _root_target(repo, worktree, target["primitive_roots"], dest)
    before, after, now = _flatten(base), _flatten(draft), _flatten(live)
    wanted = _flatten(target)
    changes: List[Dict[str, Any]] = []
    refusals: List[Dict[str, str]] = []
    for key in sorted(set(before) | set(after)):
        if key in before and key in after and _canonical(before[key]) == _canonical(after[key]):
            continue
        removed = key not in after
        desired = None if removed else wanted[key]
        current_present = key in now
        current = now.get(key)
        row = {"key": key, "before": before.get(key), "after": desired, "live": current,
               "action": "unset" if removed else "set"}
        if (removed and not current_present) or (
                not removed and current_present and _canonical(current) == _canonical(desired)):
            row["action"] = "none"
        elif (key in before) != current_present or (
                current_present and _canonical(current) != _canonical(before[key])):
            refusals.append({"code": "config-diverged", "message": (
                key + " changed in your configuration since this draft was made; apply never "
                "overwrites it. Rebase the draft's configuration on the live value first")})
            row["action"] = "conflict"
        if row["action"] in ("set", "unset"):
            row["value"] = None if removed else _value_text(desired)
        changes.append(row)
    return changes, refusals


def _check_environment(home: Path) -> Dict[str, str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("HARNESS_STANCE_", "HARNESS_IDENTITY_", "HARNESS_MANAGE_"))
                   and key not in ("HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG", "HARNESS_MODE",
                                   "HARNESS_PERMISSIONS", "HARNESS_QUIET")}
    environment["HARNESS_HOME"] = str(home)
    return environment


def _simulate(repo: Path, worktree: Path, base: Mapping[str, Any], draft: Mapping[str, Any],
              live_bytes: Optional[bytes], live_dest: Path,
              root_operations: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Run the configuration commands against a copy of the live state; return their order.

    The installed CLI writes each key, retrying a refused one after every success, and the result
    must hold the draft's value for every key it changes. The order found is the one apply runs.
    """
    with tempfile.TemporaryDirectory(prefix="studio-apply-") as temporary:
        home = Path(temporary)
        path = config_file(home)
        path.parent.mkdir(parents=True)
        if live_bytes is not None:
            path.write_bytes(live_bytes)
        dest = destination(home)
        if live_dest.is_dir():
            shutil.copytree(str(live_dest), str(dest), symlinks=True)
        _write_root(dest, root_operations, [])
        changes, _refusals = _config_changes(repo, worktree, base, draft,
                                             _read_json_bytes(live_bytes), dest)
        pending = sorted((item for item in changes if item["action"] in ("set", "unset")),
                         key=_priority)
        environment = _check_environment(home)
        order: List[str] = []
        while pending:
            refusals: List[str] = []
            for index, change in enumerate(pending):
                argv = ([sys.executable, str(repo / "bin" / "harness"), "config", change["action"],
                         change["key"]] + ([change["value"]] if change["action"] == "set" else []))
                run = subprocess.run(argv, cwd=str(repo), env=environment, capture_output=True,
                                     text=True, timeout=120)
                if not run.returncode:
                    order.append(change["key"])
                    pending.pop(index)
                    break
                refusals.append(change["key"] + ": " + ((run.stderr.strip() or run.stdout.strip()
                                                         or "citizen config refused the change")
                                                        .splitlines() or [""])[-1])
            else:
                return order, [{"code": "config-refused", "message": line} for line in refusals]
        result = _flatten(_read_json_bytes(path.read_bytes() if path.is_file() else None))
        mismatched = [item["key"] for item in changes if item["action"] in ("set", "unset") and (
            (item["action"] == "unset" and item["key"] in result)
            or (item["action"] == "set" and _canonical(result.get(item["key"])) != _canonical(item["after"])))]
        if mismatched:
            return order, [{"code": "config-unreproducible", "message": (
                key + " does not reach the draft's value through `citizen config`")} for key in mismatched]
        return order, []


# --------------------------------------------------------------------------- checks and budget


def _checks(worktree: Path, mapped: Dict[str, Any]) -> Dict[str, Any]:
    """`citizen lint` and strict resolution, run in the draft with its configuration."""
    findings: List[str] = []
    with module_editing._draft_check_environment(mapped) as environment:
        environment = dict(environment)
        environment.pop("HARNESS_QUIET", None)
        try:
            linted = subprocess.run([sys.executable, "bin/harness", "lint"], cwd=str(worktree),
                                    env=environment, capture_output=True, text=True,
                                    timeout=CHECK_TIMEOUT)
            lines = [line for line in (linted.stdout + linted.stderr).splitlines()
                     if line.strip() and not line.startswith(("context:", "lint: "))]
            if linted.returncode:
                findings.extend(lines or ["citizen lint exited %d" % linted.returncode])
        except (OSError, subprocess.SubprocessError) as exc:
            findings.append("citizen lint did not complete: " + str(exc))
    try:
        posture = module_authoring.load_posture(worktree)
        findings.extend("resolution: " + line
                        for line in module_authoring.resolution_findings(posture, mapped, worktree))
    except (OSError, ValueError, module_authoring.AuthoringError) as exc:
        findings.append("resolution: " + str(exc))
    findings = list(dict.fromkeys(findings))
    return {"status": "failed" if findings else "passed", "findings": findings[:MAX_FINDINGS],
            "truncated": len(findings) > MAX_FINDINGS, "command": "citizen lint"}


def _selected_lines(harness: Any, root: Path, config: Dict[str, Any]) -> int:
    posture = module_authoring.load_posture(root)
    document = posture.selection(env={}, strict=False, config=dict(config), root=root)
    total = harness.effective_always_loaded_lines(root, document)
    off = harness.switched_off(document)["rules"]
    core = Path(os.path.abspath(str(root / "primitives")))
    for value in config.get("primitive_roots", []):
        directory = Path(os.path.abspath(str(Path(str(value)).expanduser())))
        if directory == core:
            continue
        for path in sorted((directory / "rules").glob("*.md")) if (directory / "rules").is_dir() else []:
            if path.stem not in off:
                total += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def _budget(repo: Path, worktree: Path, live: Dict[str, Any], mapped: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from . import selection
        harness = selection._harness_module(repo)
        before = _selected_lines(harness, repo, live)
        after = _selected_lines(harness, worktree, mapped)
        return {"before_lines": before, "after_lines": after, "delta_lines": after - before,
                "line_cap": harness.ALWAYS_LOADED_CAP}
    except Exception:  # the budget is information; a resolver refusal is reported by the checks
        return None


# --------------------------------------------------------------------------- review


def _core_offer(repo: Path, state: Mapping[str, Any], core: List[Dict[str, str]]) -> Dict[str, Any]:
    modules = []
    for item in core:
        parts = item["path"].split("/")
        if len(parts) >= 3 and parts[0] == "primitives" and parts[1] in module_authoring.FORK_KINDS:
            name = parts[2][:-3] if parts[1] == "rules" and parts[2].endswith(".md") else parts[2]
            key = "core:%s:%s" % (parts[1], name)
            if key not in [entry["source"] for entry in modules]:
                modules.append({"source": key, "kind": parts[1], "name": name})
    branch = state["branch"]
    contribution = "contrib/" + state["name"]
    return {
        "files": [item["path"] for item in core],
        "fork": {"modules": modules,
                 "command": "citizen draft module plan %s --request request.json --json" % shlex.quote(state["name"]),
                 "note": "Fork the module into your personal root under a new name, then revert the "
                         "in-place edit in the draft."},
        "branch": {"name": branch, "revision": state["revision"], "commands": [
            "git -C %s push origin %s:refs/heads/%s" % (shlex.quote(str(repo)), shlex.quote(branch),
                                                         shlex.quote(contribution)),
            "cd %s && gh pr create --head %s --base main --fill" % (shlex.quote(str(repo)),
                                                                    shlex.quote(contribution)),
        ]},
    }


def _dirty(worktree: Path) -> List[str]:
    """Paths whose working copy differs from the draft's revision, untracked files included.

    Apply writes the revision's blobs and lint reads the working copy, so the two are the same
    content only when this is empty.
    """
    shown = _git(worktree, "status", "--porcelain", "-z", "--untracked-files=all")
    if shown.returncode:
        raise ApplyError("diff-unavailable", "the draft's working copy could not be read")
    entries = shown.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted(entry[3:] for entry in entries if len(entry) > 3)


@contextlib.contextmanager
def _revision_checkout(worktree: Path, revision: str):
    """A detached, throwaway checkout of exactly `revision`: what apply writes, not the working copy."""
    with tempfile.TemporaryDirectory(prefix="studio-apply-check-") as temporary:
        checkout = Path(os.path.realpath(temporary)) / "checkout"
        added = _git(worktree, "worktree", "add", "--detach", "--quiet", str(checkout), revision)
        if added.returncode:
            raise ApplyError("checks-unavailable", "the draft revision could not be checked out to check it")
        try:
            yield checkout
        finally:
            removed = _git(worktree, "worktree", "remove", "--force", str(checkout))
            if removed.returncode:
                _git(worktree, "worktree", "prune")


def checks_for(repo: Path, worktree: Path, raw_config: Dict[str, Any], revision: str) -> Dict[str, Any]:
    """The checks, run on a checkout of `revision` itself.

    Lint and resolution read files, and apply writes the revision's blobs, so the checks read the
    same blobs: a working-copy edit made before or during the run can change neither.
    """
    repo = Path(repo).resolve()
    with _revision_checkout(Path(worktree).resolve(), revision) as checkout:
        result = _checks(checkout, module_editing._mapped_config(repo, checkout, raw_config))
    result["findings"] = [line.replace(str(checkout), "<draft>") for line in result["findings"]]
    return result


def _plan(repo: Path, worktree: Path, state: Dict[str, Any], raw_config: Dict[str, Any],
          home: Path, checks: Optional[Dict[str, Any]] = None, applying: bool = False) -> Dict[str, Any]:
    """Everything an apply would do, checked and simulated, without writing anything live."""
    repo, worktree = Path(repo).resolve(), Path(worktree).resolve()
    dest = destination(home)
    live_path = config_file(home)
    if live_path.is_symlink():
        raise ApplyError("invalid-config", "the live configuration is a symlink; apply refuses it")
    live_bytes = live_path.read_bytes() if live_path.is_file() else None
    live = _read_json_bytes(live_bytes)
    base_config = drafts._config_value(drafts._paths(worktree)["base_config"])
    refusals: List[Dict[str, str]] = []
    if state["revision"] != drafts._revision(worktree):
        refusals.append({"code": "draft-inconsistent",
                         "message": "the draft checkout does not match its recorded revision"})
    dirty = _dirty(worktree)
    if dirty:
        refusals.append({"code": "draft-dirty", "message": (
            "the draft's working copy has %d change(s) no checkpoint holds (%s); checks read the "
            "working copy and apply writes the checkpoint, so save or discard them first"
            % (len(dirty), ", ".join(dirty[:5])))})
    # Inside apply the sync lock's holder is this apply itself.
    running = "" if applying else running_apply(home)
    interrupted = unfinished_applies(home)
    if running:
        refusals.append({"code": "apply-running", "message": running + " is applying now; review again "
                                                                      "when it finishes"})
    elif interrupted:
        refusals.append({"code": "interrupted-apply", "message": (
            "an earlier %s was interrupted; restore it (`citizen draft recover`) or "
            "abandon it (`citizen draft recover --abandon`) first" % interrupted_what(interrupted[-1]))})
    described = drafts.describe(repo, worktree, state)
    if described["rebase_conflicted"]:
        refusals.append({"code": "draft-rebasing", "message": "the draft is in the middle of a rebase"})
    elif described["behind_installed"]:
        refusals.append({"code": "draft-behind", "message": (
            "the installed harness moved since this draft was made; rebase the draft "
            "(`citizen draft rebase %s`) and review it again" % state["name"])})
    own, others = _registered_root(repo, worktree, raw_config)
    for value in others:
        refusals.append({"code": "unsupported-root", "message": (
            value + " is a root inside the checkout other than " + OWN_ROOT + "; apply moves only "
            + OWN_ROOT + " out of the checkout")})
    changed = _changed_paths(worktree, state["base_revision"], state["revision"])
    for item in changed:
        item["scope"] = "personal" if own and item["path"].startswith(OWN_ROOT + "/") else "core"
    core = [item for item in changed if item["scope"] == "core"]
    if core:
        refusals.append({"code": "core-change", "message": (
            "this draft changes %d core file(s) in place; apply never edits core. Fork the module "
            "into your personal root, or open a pull request from the draft's branch" % len(core))})
    personal = [item for item in changed if item["scope"] == "personal"]
    operations, root_refusals = _root_plan(worktree, state, personal, dest)
    refusals.extend(root_refusals)
    changes, config_refusals = _config_changes(repo, worktree, base_config, raw_config, live, dest)
    refusals.extend(config_refusals)
    order: List[str] = []
    if not any(item["code"] in ("config-diverged",) for item in refusals):
        order, simulated = _simulate(repo, worktree, base_config, raw_config, live_bytes, dest,
                                     operations)
        refusals.extend(simulated)
    by_key = {item["key"]: item for item in changes}
    config_commands = [{"action": by_key[key]["action"], "key": key, "value": by_key[key].get("value")}
                       for key in order]
    mapped = module_editing._mapped_config(repo, worktree, raw_config)
    if checks is None:
        checks = checks_for(repo, worktree, raw_config, state["revision"])
    if checks["status"] == "failed":
        refusals.append({"code": "checks-failed",
                         "message": "the draft has %d check finding(s); fix them in the draft first"
                                    % len(checks["findings"])})
    pending = operations or config_commands
    if not pending and not refusals:
        refusals.append({"code": "nothing-to-apply",
                         "message": "the live harness already matches this draft"})
    commands: List[Dict[str, str]] = []
    for line in _root_commands(worktree, state["revision"], operations, dest):
        commands.append({"step": "root", "command": line})
    for command in config_commands:
        commands.append({"step": "config", "command": _citizen(command)})
    commands.append({"step": "sync", "command": "citizen sync"})
    commands.append({"step": "check", "command": "citizen doctor"})
    return {
        "schema_version": SCHEMA_VERSION,
        "draft": {"name": state["name"], "draft_id": state["draft_id"], "revision": state["revision"],
                  "base_revision": state["base_revision"], "branch": state["branch"],
                  "path": str(worktree)},
        "destination": str(dest),
        "files": [{"path": item["path"], "status": item["status"], "scope": item["scope"]}
                  for item in changed],
        "root": [{"path": item["path"], "action": item["action"]} for item in operations],
        "config": [{key: row.get(key) for key in ("key", "action", "before", "after", "live")}
                   for row in changes],
        "checks": checks,
        "budget": _budget(repo, worktree, live, mapped),
        "commands": commands,
        "core": _core_offer(repo, state, core) if core else None,
        "interrupted": None if running or not interrupted else _interrupted_offer(interrupted[-1]),
        "refusals": refusals,
        "can_apply": not refusals,
        "apply_command": " ".join(CLI_COMMANDS["apply"]).format(
            draft=shlex.quote(state["name"]), revision=state["revision"]),
        "nothing_applied": True,
        "_operations": operations,
        "_config_commands": config_commands,
        "_dest": dest,
    }


def _public(planned: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in planned.items() if not key.startswith("_")}


def unavailable(code: str, message: str) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "draft": {}, "destination": "", "files": [],
            "root": [], "config": [], "checks": {"status": "unavailable", "findings": [],
                                                 "truncated": False, "command": "citizen lint"},
            "budget": None, "commands": [], "core": None, "interrupted": None,
            "refusals": [{"code": code, "message": message}], "can_apply": False,
            "apply_command": "", "nothing_applied": True}


def review(repo: Path, name: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """What applying the draft would change, check and run. Writes nothing."""
    repo = Path(repo).resolve()
    home = home_dir() if home is None else Path(home)
    try:
        # Lock-free and recovering: a save beside the review is never refused busy.
        return drafts.read_snapshot(repo, name, lambda worktree, state, raw_config: _public(
            _plan(repo, worktree, state, raw_config, home)))
    except (ApplyError, drafts.DraftError, module_editing.ModuleEditError) as exc:
        return unavailable(getattr(exc, "code", "review-unavailable"), str(exc))


# --------------------------------------------------------------------------- apply


class Operations:
    """The CLI's own governed operations, handed in by `citizen draft apply`.

    `lock(holder)` is the sync lock, `config_lock(holder)` the configuration lock; both refuse at
    once with ValueError naming the current holder. `config_set` and `config_unset` are the locked
    bodies of `citizen config set|unset`. `sync(dry)` is `citizen sync` without its lock and
    returns `{"code", "attention", "refused"}`: its exit code, its "needs your attention" items and
    whether it refused before writing. `doctor` returns the doctor's lines; `record(row)` appends
    one row to the decision log when the user's configuration allows it.
    """

    def __init__(self, lock: Callable[[str], Any], config_lock: Callable[[str], Any],
                 config_set: Callable[[str, str], Any], config_unset: Callable[[str], Any],
                 sync: Callable[[bool], Dict[str, Any]], doctor: Callable[[], List[str]],
                 record: Callable[[Dict[str, Any]], None]) -> None:
        self.lock = lock
        self.config_lock = config_lock
        self.config_set = config_set
        self.config_unset = config_unset
        self.sync = sync
        self.doctor = doctor
        self.record = record


# A `failed` row closes its intent only when it says the restore finished.
TERMINAL_PHASES = ("completed", "recovered", "abandoned")


def journal_path(home: Path) -> Path:
    return Path(home) / ".local" / "state" / "agent-harness" / "applies.jsonl"


def _encoded(content: Optional[bytes]) -> Optional[str]:
    return None if content is None else base64.b64encode(content).decode("ascii")


def _decoded(content: Optional[str]) -> Optional[bytes]:
    return None if content is None else base64.b64decode(content.encode("ascii"))


def _digest(content: Optional[bytes]) -> Optional[str]:
    return None if content is None else hashlib.sha256(content).hexdigest()


def _journal(home: Path, row: Dict[str, Any]) -> None:
    """One appended line per phase: intent before the first write, then the outcome."""
    path = journal_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(row, schema_version=SCHEMA_VERSION), sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        size = os.fstat(descriptor).st_size
        if size and os.pread(descriptor, 1, size - 1) != b"\n":
            # A row an earlier full disk cut short stays its own unreadable line.
            data = b"\n" + data
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("the apply journal accepted no bytes")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _journal_outcome(home: Path, row: Dict[str, Any]) -> str:
    """Append an outcome row; a failure to record it is reported, never raised past the result."""
    try:
        _journal(home, row)
        return ""
    except OSError as exc:
        return "; the apply journal could not record this outcome (%s)" % exc


def unfinished_applies(home: Path) -> List[Dict[str, Any]]:
    """Intent rows with no terminal row: applies a crash or a kill interrupted."""
    path = journal_path(home)
    if not path.is_file():
        return []
    intents: Dict[str, Dict[str, Any]] = {}
    finished = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("apply_id"), str):
            continue
        if row.get("phase") == "intent":
            intents[row["apply_id"]] = row
        elif row.get("phase") in TERMINAL_PHASES or (
                row.get("phase") == "failed" and row.get("restored") is True):
            finished.add(row["apply_id"])
    return [row for key, row in intents.items() if key not in finished]


def interrupted_what(intent: Mapping[str, Any]) -> str:
    """How an open intent is named to the user: an apply, or a rollback of one."""
    if intent.get("kind") == "rollback":
        return "rollback of draft %s's apply %s" % (intent.get("draft", "?"),
                                                    str(intent.get("reverses") or "")[:12])
    return "apply of draft %s" % intent.get("draft", "?")


def running_apply(home: Path) -> str:
    """The apply or rollback holding the sync lock right now, by its live holder record, or ""."""
    record = reconcile.lock_holder_record(journal_path(home).parent)
    if not record or not str(record.get("holder", "")).startswith(("citizen draft apply",
                                                                    "citizen draft rollback")):
        return ""
    try:
        os.kill(int(record.get("pid")), 0)
    except (OSError, TypeError, ValueError):
        return ""  # a holder record a killed apply left behind
    return str(record["holder"])


def _interrupted_offer(intent: Mapping[str, Any]) -> Dict[str, Any]:
    return {"apply_id": str(intent.get("apply_id", "")), "draft": str(intent.get("draft", "")),
            "kind": "rollback" if intent.get("kind") == "rollback" else "apply",
            "started": str(intent.get("ts", "")),
            "recover_command": "citizen draft recover --json",
            "abandon_command": "citizen draft recover --abandon --json"}


def _result(status: str, code: str, message: str, planned: Optional[Mapping[str, Any]] = None,
            **extra: Any) -> Dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION, "status": status, "applied": status == "applied",
        "error_code": code, "message": message, "holder": "", "apply_id": "",
        "review": dict(_public(planned), nothing_applied=status != "applied")
        if planned is not None else {},
        "doctor": {"status": "skipped", "checks": []}, "restored": False, "log": [],
    }
    payload.update(extra)
    return payload


def _doctor(lines: List[str]) -> Dict[str, Any]:
    from harness_core import overview
    checks = overview.doctor_checks(lines)
    attention = any(item["status"] == "attention" for item in checks)
    return {"status": "attention" if attention else "passed", "checks": checks}


def _sync_settled(outcome: Mapping[str, Any], baseline: Iterable[str]) -> Tuple[bool, List[str]]:
    """Whether a sync left the projection complete, and the attention items it newly raised.

    A sync that exits non-zero only to list "needs your attention" items the user already had is
    complete; one that refused, or raised an item that was not there before, is not.
    """
    if outcome.get("code") == 0:
        return True, []
    if outcome.get("refused"):
        return False, []
    new = sorted(set(outcome.get("attention") or []) - set(baseline))
    return outcome.get("code") == 2 and not new, new


def apply(repo: Path, name: str, revision: str, operations: Operations, actor: str = "citizen",
          home: Optional[Path] = None, log: Optional[List[str]] = None) -> Dict[str, Any]:
    """Apply the reviewed revision of a draft, or refuse with nothing written.

    The draft's writer lock is taken first and its checks run before the sync and configuration
    locks, so a long lint never holds up hooks, sync or `config set`. Under all three locks an
    interrupted earlier apply is restored before anything else.
    """
    repo = Path(repo).resolve()
    home = home_dir() if home is None else Path(home)
    log = [] if log is None else log
    if not isinstance(revision, str) or not revision:
        return _result("refused", "invalid-request", "the reviewed draft revision is required")
    holder = "citizen draft apply %s%s" % (name, " from Studio" if actor == "studio" else "")
    try:
        with contextlib.ExitStack() as stack:
            worktree, state, raw_config = stack.enter_context(drafts.locked_context(repo, name))
            if state["revision"] != revision:
                return _result("refused", "stale-revision",
                               "the draft changed since it was reviewed; review it again")
            checks = checks_for(repo, worktree, raw_config, state["revision"])
            try:
                stack.enter_context(operations.lock(holder))
                stack.enter_context(operations.config_lock(holder))
            except ValueError as exc:
                text = str(exc)
                _sep, _colon, named = text.partition(": ")
                return _result("refused", "busy", text + "; nothing was applied", holder=named)
            interrupted = unfinished_applies(home)
            if interrupted and interrupted[-1].get("kind") == "rollback" \
                    and interrupted[-1].get("draft") != name:
                # Restoring another draft's rollback needs that draft's own confirmation.
                return _result("refused", "interrupted-rollback", (
                    "an earlier %s was interrupted; restore it (`citizen draft recover`) or abandon "
                    "it (`citizen draft recover --abandon`) first. Nothing was applied"
                    % interrupted_what(interrupted[-1])),
                    apply_id=str(interrupted[-1].get("apply_id", "")))
            if interrupted:
                return _recover(home, interrupted[-1], operations, log, actor)
            planned = _plan(repo, worktree, state, raw_config, home, checks=checks, applying=True)
            if planned["refusals"]:
                first = planned["refusals"][0]
                return _result("refused", first["code"], first["message"] + "; nothing was applied",
                               planned)
            return _execute(home, state, planned, operations, actor, log)
    except (ApplyError, drafts.DraftError, module_editing.ModuleEditError) as exc:
        return _result("refused", getattr(exc, "code", "apply-refused"),
                       str(exc) + "; nothing was applied")


def _execute(home: Path, state: Mapping[str, Any], planned: Dict[str, Any],
             operations: Operations, actor: str, log: List[str]) -> Dict[str, Any]:
    apply_id = uuid.uuid4().hex
    dest: Path = planned["_dest"]
    root_operations: List[Dict[str, Any]] = planned["_operations"]
    commands: List[Dict[str, Any]] = planned["_config_commands"]
    live_path = config_file(home)
    prior_config = live_path.read_bytes() if live_path.is_file() else None
    prior_mode = stat.S_IMODE(os.stat(str(live_path)).st_mode) if live_path.is_file() else 0o600
    files = [item["path"] for item in planned["files"]]
    after = {row["key"]: row["after"] for row in planned["config"]}
    identity = {"apply_id": apply_id, "actor": actor, "draft": state["name"],
                "draft_id": state["draft_id"], "revision": state["revision"],
                "base_revision": state["base_revision"], "destination": str(dest)}
    try:
        # The attention items the user already has, so only an item this apply raises fails it.
        baseline = operations.sync(True)
    except (Exception, SystemExit):
        baseline = {"code": 1, "attention": [], "refused": True}
    try:
        # Everything recovery needs to put the prior state back after a crash or a kill.
        _journal(home, dict(
            identity, ts=_now(), phase="intent", prior_config=_encoded(prior_config),
            prior_mode=prior_mode, attention=sorted(baseline.get("attention") or []),
            config=[{"key": item["key"], "action": item["action"], "value": item["value"],
                     "applied": after.get(item["key"])} for item in commands],
            files=[{"path": item["path"], "action": item["action"],
                    "prior": _encoded(item["prior"]), "prior_mode": item["prior_mode"],
                    "applied_sha256": _digest(item["content"])} for item in root_operations],
            created=missing_directories(dest, [item["path"] for item in root_operations
                                               if item["action"] == "write"])))
    except OSError as exc:
        return _result("refused", "journal-unavailable",
                       "the apply journal could not be written (%s); nothing was applied" % exc, planned)
    created: List[str] = []
    synced = False
    try:
        _write_root(dest, root_operations, created)
        for command in commands:
            if command["action"] == "unset":
                operations.config_unset(command["key"])
            else:
                operations.config_set(command["key"], command["value"])
        synced = True
        settled, new = _sync_settled(operations.sync(False), baseline.get("attention") or [])
        if not settled:
            raise ApplyError("sync-refused", "citizen sync did not complete the applied configuration"
                             + (": " + "; ".join(new) if new else ""))
    except BaseException as exc:  # noqa: B036 - an interrupt must restore too, then propagate
        restored = _restore(live_path, prior_config, prior_mode, dest, root_operations, created,
                            operations, synced, baseline.get("attention") or [])
        message = str(exc) or exc.__class__.__name__
        note = _journal_outcome(home, dict(identity, ts=_now(), phase="failed", reason=message,
                                           restored=restored))
        _decision(operations, identity, files, "failed", "Apply failed and was rolled back: " + message)
        if not isinstance(exc, (Exception, SystemExit)):
            raise
        return _result("failed", getattr(exc, "code", "apply-failed"),
                       message + ("; the previous configuration and files were restored" if restored
                                  else "; restoring the previous state did not complete and stays "
                                       "open, so run `citizen draft recover`") + note,
                       planned, apply_id=apply_id, restored=restored, log=log[-MAX_LOG_LINES:])
    try:
        doctor = _doctor(operations.doctor())
    except (Exception, SystemExit):
        doctor = {"status": "unavailable", "checks": []}
    note = _journal_outcome(home, dict(identity, ts=_now(), phase="completed", doctor=doctor["status"]))
    _decision(operations, identity, files, "completed",
              "Applied draft %s through the governed path." % state["name"])
    return _result("applied", "", "Applied draft %s; sync and the doctor checks ran." % state["name"]
                   + note, planned, apply_id=apply_id, doctor=doctor, log=log[-MAX_LOG_LINES:])


def _restore(live_path: Path, prior_config: Optional[bytes], prior_mode: int, dest: Path,
             root_operations: List[Dict[str, Any]], created: List[str], operations: Operations,
             synced: bool, baseline: Iterable[str]) -> bool:
    """Put the configuration and files back; True only when the re-projection settled too."""
    try:
        if prior_config is None:
            if live_path.exists():
                live_path.unlink()
        else:
            drafts._atomic_bytes(live_path, prior_config, prior_mode)
        _restore_root(dest, root_operations, created)
        if synced:
            # Re-project the restored state: a sync that failed may have written part of the new one.
            return _sync_settled(operations.sync(False), baseline)[0]
        return True
    except BaseException:  # noqa: B036 - a restore that cannot finish is reported, not raised
        return False


def _set_path(document: Dict[str, Any], key: str, present: bool, value: Any) -> None:
    parts = key.split(".")
    node = document
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            if not present:
                return
            child = node[part] = {}
        node = child
    if present:
        node[parts[-1]] = copy.deepcopy(value)
    else:
        node.pop(parts[-1], None)


def _recover(home: Path, intent: Dict[str, Any], operations: Operations, log: List[str],
             actor: str = "citizen") -> Dict[str, Any]:
    """Restore an apply that was interrupted: only the keys and files it wrote.

    Each of those must still hold its prior or its applied value; anything else is a later edit,
    so recovery refuses and writes nothing. Keys and files the apply never touched are left as
    they are now, whatever happened to them since.
    """
    identity = {key: intent.get(key) for key in ("apply_id", "draft", "draft_id",
                                                  "revision", "base_revision", "destination")}
    identity["actor"] = actor
    live_path = config_file(home)
    dest = Path(str(intent.get("destination") or destination(home)))
    conflicts: List[str] = []
    try:
        prior_config = _decoded(intent.get("prior_config"))
        current_bytes = live_path.read_bytes() if live_path.is_file() else None
        current_document = _read_json_bytes(current_bytes)
        current = _flatten(current_document)
        prior = _flatten(_read_json_bytes(prior_config))
        written = {item["key"]: item for item in intent.get("config", [])}
        for key, item in sorted(written.items()):
            if key in current and key in prior and _canonical(current[key]) == _canonical(prior[key]):
                continue
            if key not in current and key not in prior:
                continue
            if item.get("action") == "unset" and key not in current:
                continue
            if item.get("action") == "set" and key in current and \
                    _canonical(current[key]) == _canonical(item.get("applied")):
                continue
            conflicts.append("configuration key " + key)
        operations_list = []
        for item in intent.get("files", []):
            target = _contained(dest, item["path"])
            live, _mode = _live_file(target, item["path"])
            if _digest(live) not in (_digest(_decoded(item.get("prior"))), item.get("applied_sha256")):
                conflicts.append(str(target))
            operations_list.append({"path": item["path"], "prior": _decoded(item.get("prior")),
                                    "prior_mode": item.get("prior_mode")})
    except (ApplyError, OSError, ValueError, KeyError, TypeError) as exc:
        conflicts.append("the journal row could not be read back (%s)" % exc)
    if conflicts:
        return _result("refused", "interrupted-apply-conflict", (
            "an earlier %s was interrupted, and %s changed since. Put those back "
            "yourself and run `citizen draft recover`, or keep them with `citizen draft recover "
            "--abandon`. Nothing was applied" % (interrupted_what(intent), ", ".join(conflicts))),
            apply_id=str(identity["apply_id"] or ""))
    interrupt: Optional[BaseException] = None
    try:
        untouched = {key: value for key, value in current.items() if key not in written}
        if prior_config is not None and untouched == {
                key: value for key, value in prior.items() if key not in written}:
            restored_bytes: Optional[bytes] = prior_config  # nothing else moved: byte for byte
        else:
            document = copy.deepcopy(current_document)
            for key in written:
                _set_path(document, key, key in prior, prior.get(key))
            if any(key.startswith("stances.") for key in written):
                prior_document = _read_json_bytes(prior_config)
                _set_path(document, "init_defaults", "init_defaults" in prior_document,
                          prior_document.get("init_defaults"))
            restored_bytes = (json.dumps(document, indent=2) + "\n").encode("utf-8")
        if prior_config is None and not untouched:
            if live_path.exists():
                live_path.unlink()
        elif restored_bytes != current_bytes:
            drafts._atomic_bytes(live_path, restored_bytes, int(intent.get("prior_mode") or 0o600))
        # Only the directories the interrupted write journalled as new: an existing one stays.
        _restore_root(dest, operations_list, list(intent.get("created") or []))
        restored = _sync_settled(operations.sync(False), intent.get("attention") or [])[0]
    except BaseException as exc:  # noqa: B036 - the intent stays open; an interrupt propagates
        restored = False
        if not isinstance(exc, Exception):
            interrupt = exc
    # An interrupted rollback is undone as a rollback: the apply it reversed is in effect again.
    rollback = intent.get("kind") == "rollback"
    what = "a " + interrupted_what(intent) if rollback else "an " + interrupted_what(intent)
    note = ""
    if restored:
        note = _journal_outcome(home, dict(identity, ts=_now(), phase="recovered"))
        if rollback:
            _decision(operations, identity, [], "recovered",
                      "An interrupted rollback was undone; draft %s's apply is in effect again."
                      % identity["draft"], event="studio.rollback",
                      extra={"reverses": str(intent.get("reverses") or "")})
        else:
            _decision(operations, identity, [], "recovered",
                      "An interrupted apply of draft %s was rolled back." % identity["draft"])
    if interrupt is not None:
        raise interrupt
    if restored:
        return _result("recovered", "interrupted-rollback" if rollback else "", (
            "an earlier %s was interrupted; it was undone and synced, so that apply is in effect "
            "again and nothing else changed. Run `citizen draft rollback %s` again if you still "
            "want it rolled back" % (what, str(intent.get("reverses") or ""))
            if rollback else
            "an earlier %s was interrupted; its configuration keys and files were restored and "
            "synced. Review the draft and apply again" % what) + note,
            restored=True, apply_id=str(identity["apply_id"] or ""), log=log[-MAX_LOG_LINES:])
    return _result("failed", "interrupted-apply-failed", (
        "an earlier %s was interrupted, and restoring it did not complete; it stays open, so run "
        "`citizen draft recover` again" % what),
        apply_id=str(identity["apply_id"] or ""), log=log[-MAX_LOG_LINES:])


def recover(operations: Operations, abandon: bool = False, draft: str = "", actor: str = "citizen",
            home: Optional[Path] = None, log: Optional[List[str]] = None) -> Dict[str, Any]:
    """Restore, or explicitly abandon, the open interrupted apply, under the sync and config locks.

    Abandoning keeps the current configuration and files as they are and closes the intent; it is
    the way out when the user has deliberately kept what the interrupted apply wrote. `draft`, when
    given, must name the interrupted apply's draft, as the Studio's confirmation does.
    """
    home = home_dir() if home is None else Path(home)
    log = [] if log is None else log
    holder = "citizen draft recover%s" % (" from Studio" if actor == "studio" else "")
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(operations.lock(holder))
            stack.enter_context(operations.config_lock(holder))
        except ValueError as exc:
            text = str(exc)
            return _result("refused", "busy", text + "; nothing was changed",
                           holder=text.partition(": ")[2])
        open_intents = unfinished_applies(home)
        if not open_intents:
            return _result("refused", "nothing-to-recover", "no interrupted apply is open")
        intent = open_intents[-1]
        if draft and draft != intent.get("draft"):
            return _result("refused", "confirmation-mismatch", (
                "the open interrupted apply is of draft %s, not %s; nothing was changed"
                % (intent.get("draft"), draft)), apply_id=str(intent.get("apply_id", "")))
        if abandon:
            identity = {key: intent.get(key) for key in ("apply_id", "draft", "revision")}
            identity["actor"] = actor
            try:
                _journal(home, dict(identity, ts=_now(), phase="abandoned"))
            except OSError as exc:
                return _result("failed", "journal-unavailable",
                               "the apply journal could not record the abandon (%s)" % exc)
            if intent.get("kind") == "rollback":
                _decision(operations, identity, [], "abandoned",
                          "An interrupted %s was abandoned; what it wrote was kept."
                          % interrupted_what(intent), event="studio.rollback",
                          extra={"reverses": str(intent.get("reverses") or "")})
            else:
                _decision(operations, identity, [], "abandoned",
                          "An interrupted apply of draft %s was abandoned; its writes were kept."
                          % identity["draft"])
            return _result("abandoned", "interrupted-rollback" if intent.get("kind") == "rollback"
                           else "", (
                "the interrupted %s was abandoned: its configuration and files are kept as they "
                "are now. Run `citizen sync` if the projection needs it"
                % interrupted_what(intent)), apply_id=str(identity["apply_id"] or ""))
        return _recover(home, intent, operations, log, actor)


def _decision(operations: Operations, identity: Mapping[str, Any], files: List[str], outcome: str,
              reason: str, event: str = "studio.apply", extra: Optional[Mapping[str, Any]] = None) -> None:
    command = " ".join(CLI_COMMANDS["apply"]).format(draft=shlex.quote(str(identity["draft"])),
                                                     revision=identity["revision"])
    if event == "studio.rollback":
        command = "citizen draft recover --json"
    try:
        operations.record({
            "kind": "event", "event": event, "id": identity["apply_id"], "ts": _now(),
            "detail": dict({"draft": identity["draft"], "files": files, "outcome": outcome,
                            "reason": reason, "command": command,
                            "actor": "Studio" if identity["actor"] == "studio" else "citizen",
                            "revision": identity["revision"]}, **(extra or {})),
        })
    except (OSError, ValueError):
        pass
