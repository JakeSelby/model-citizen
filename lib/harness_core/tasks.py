"""Versioned task continuation data; never imports native permissions or memory."""
import json
import subprocess
from pathlib import Path
from . import reconcile

FIELDS = {"objective", "next_steps", "decisions", "artifacts", "framework_root", "baseline", "verification"}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def repository(path):
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def snapshot_file(name):
    """A file pre-compact.py keeps in `.agent-harness/handoffs/`: a snapshot, a leftover temporary
    one, or the `.gitignore` that keeps them out of commits."""
    return name.startswith("auto-") or (name.startswith(".auto-") and name.endswith("tmp")) \
        or name == ".gitignore"


def fingerprint(root):
    # Exclude only bookkeeping; plans and progress remain task inputs even when gitignored.
    import hashlib
    import os
    bookkeeping = {".agent-harness/task.json", ".agent-harness/sync.lock"}
    paths = ["."] + [":(exclude)" + name for name in sorted(bookkeeping)]
    digest = hashlib.sha256()
    for args in (("rev-parse", "HEAD"), ("diff", "--binary", "HEAD", "--", *paths),
                 ("diff", "--cached", "--binary", "--", *paths)):
        digest.update(git(root, *args).encode())
    raw = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
                         capture_output=True, text=True, check=True).stdout
    names = set(raw.split("\0"))
    directory = root / ".agent-harness"
    # Copies of files sent to a Remote Control client are not task inputs: policy/hooks/stage-user-files.py.
    outbox = directory / "outbox"
    # Nor are the snapshots policy/hooks/pre-compact.py writes at a compaction, which would
    # otherwise mark the record saved just before it stale.
    snapshots = directory / "handoffs"
    if directory.is_symlink():
        raise ValueError("task storage cannot be a symlink")
    if directory.is_dir():
        names.update(str(p.relative_to(root)) for p in directory.rglob("*")
                     if (p.is_file() or p.is_symlink()) and outbox not in p.parents)
    names = {name for name in names if not ((root / name).parent == snapshots and snapshot_file(Path(name).name))}
    for name in sorted(names - bookkeeping - {""}):
        path = root / name
        digest.update(name.encode())
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def read(root):
    path = root / ".agent-harness" / "task.json"
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("task storage cannot be a symlink")
    if not path.exists():
        return {"schema_version": 1, "revision": 0, "status": "absent"}
    record = json.loads(path.read_text())
    if record.get("schema_version") != 1 or record.get("repository") != str(root):
        raise ValueError("task schema or repository identity mismatch")
    stale = record.get("fingerprint") != fingerprint(root)
    record["status"] = "stale" if stale else "current"
    if stale:
        record["verification"] = {"status": "unverified", "reason": "working tree changed after handoff"}
    record["authority"] = "data only; approvals and native permissions must be established in this session"
    return record


def save(root, payload, runtime, revision=0, session_id=None):
    """Write the next revision; `session_id`, the saving session's, is recorded when given."""
    if runtime not in ("claude-code", "codex") or not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("invalid task contract fields or runtime")
    if not isinstance(payload.get("objective"), str) or not payload["objective"].strip():
        raise ValueError("task objective is required")
    for name in ("next_steps", "decisions", "artifacts"):
        if not isinstance(payload.get(name, []), list) or any(not isinstance(x, str) for x in payload.get(name, [])):
            raise ValueError(name + " must be a list of strings")
    directory = root / ".agent-harness"
    if directory.is_symlink() or (directory / "task.json").is_symlink():
        raise ValueError("task storage cannot be a symlink")
    with reconcile.lock(directory):
        current = read(root)
        if current["revision"] != revision:
            raise ValueError("task revision changed since --revision "
                             + str(revision) + "; read the current task, then save against its revision")
        record = dict(payload, schema_version=1, revision=revision + 1, runtime=runtime,
                      repository=str(root), branch=git(root, "branch", "--show-current"),
                      head=git(root, "rev-parse", "HEAD"), fingerprint=fingerprint(root))
        if isinstance(session_id, str) and session_id:
            record["session_id"] = session_id
        # A caller's claim is not a gate result. Retain it as evidence, never certification.
        record["verification"] = {"status": "unverified", "reported": payload.get("verification")}
        reconcile.atomic_text(directory / "task.json", json.dumps(record, indent=2) + "\n")
    return read(root)
