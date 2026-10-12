#!/usr/bin/env python3
"""PreToolUse hook: copy each file `SendUserFile` names from outside the session's working
directory into it, so a Remote Control client can open the file.

In a Remote Control session the tool uploads nothing. The app keeps the path and asks the session
for the file when you open it, and Claude Code serves it only when the file's real path is under
the directory the session started in or a directory added to it. Agents write reports, renders
and screenshots to temp directories, scratch directories and other worktrees, so without this the
app refuses most of what they send with "Couldn't load this file".

A file whose real path is outside the hook's working directory, which Claude Code keeps inside
the session's working directories, is copied to `.agent-harness/outbox/<digest>/<name>` there,
and the path is rewritten to the copy. The digest covers the source's real path, size and
modification and change times, so sending an unchanged file again reuses its copy and any rewrite,
even one that restores the modification time, gets a new one. The outbox carries a `.gitignore` that ignores everything in it, so in a repository a
copy never shows in `git status`, a commit or the lint. Directories untouched for KEEP_DAYS are
removed when something new is staged, and `harness task` leaves the outbox out of its fingerprint.

A file already inside, a missing path and a directory are left alone; the tool reports its own
error for a path it cannot send. A file past the call's byte or time budget, or one that cannot be
copied, is left alone with a notice. This hook never denies: on any failure the call runs unchanged.

Each call that stages or keeps a file is one `stage-user-files` row in the decision log:
`staged`, `kept` or `staged+kept`, with the counts and the bytes copied, and the file names
(never their paths) as its input.

Test: echo '{"tool_name":"SendUserFile","cwd":"'"$PWD"'","tool_input":{"files":["/etc/hosts"]}}' | python3 stage-user-files.py
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

OUTBOX = Path(".agent-harness") / "outbox"
# What one call may copy, in bytes and in seconds, against the coordinator's ten-second
# PreToolUse budget. Time is checked between files, so one slow source can still outrun it; the
# hook is then stopped and the call goes through unchanged.
MAX_BYTES = 64 * 1024 * 1024
DEADLINE_SECONDS = 4
KEEP_DAYS = 14
DIGEST = re.compile(r"[0-9a-f]{16}")


def log_decision(answer, text, payload, fields=None):
    """One `stage-user-files` row in the decision log (`decisions.py`). Never raises."""
    try:
        location = Path(os.path.realpath(__file__)).parent / "decisions.py"
        spec = importlib.util.spec_from_file_location("harness_stage_user_files_decisions", str(location))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.record("stage-user-files", answer, text, payload if isinstance(payload, dict) else {},
                      fields=fields)
    except Exception:
        pass


def real(path):
    return Path(os.path.realpath(str(path)))


def outbox(root):
    """The outbox under `root`, created on first use; None when any part of it is a link."""
    box = root / OUTBOX
    ignore = box / ".gitignore"
    if any(p.is_symlink() for p in (root / OUTBOX.parent, box, ignore)):
        return None
    box.mkdir(parents=True, exist_ok=True)
    if not real(box).is_relative_to(real(root)):
        return None
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return box


def stage(source, info, box):
    """Copy `source` into `box`, or reuse its earlier copy. Returns `(target, bytes copied)`."""
    key = "%s\0%d\0%d\0%d" % (source, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    folder = box / hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    target = folder / source.name
    if folder.is_symlink() or target.is_symlink():
        raise OSError("outbox entry is a link: " + folder.name)
    if target.is_file() and target.stat().st_size == info.st_size:
        os.utime(str(folder))
        return target, 0
    folder.mkdir(exist_ok=True)
    handle, partial = tempfile.mkstemp(dir=str(folder), prefix=".", suffix=".part")
    os.close(handle)
    try:
        shutil.copyfile(str(source), partial)
        os.replace(partial, str(target))
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise
    return target, info.st_size


def prune(box, now):
    cutoff = now - KEEP_DAYS * 86400
    for entry in box.iterdir():
        try:
            if (DIGEST.fullmatch(entry.name) and not entry.is_symlink() and entry.is_dir()
                    and entry.stat().st_mtime < cutoff):
                shutil.rmtree(str(entry), ignore_errors=True)
        except OSError:
            continue


def decide(payload):
    """The hook output for one PreToolUse payload, or None to leave the call as it is."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "SendUserFile":
        return None
    inputs = payload.get("tool_input")
    cwd = payload.get("cwd")
    if not isinstance(inputs, dict) or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    files = inputs.get("files")
    if not isinstance(files, list) or not os.path.isdir(cwd):
        return None
    root, inside = Path(cwd), real(cwd)
    deadline, budget = time.monotonic() + DEADLINE_SECONDS, MAX_BYTES
    box, sent, staged, kept, copied = None, [], 0, [], 0
    for entry in files:
        sent.append(entry)
        if not isinstance(entry, str) or not entry.strip():
            continue
        try:
            path = Path(os.path.expanduser(entry))
            source = real(path if path.is_absolute() else root / path)
            if source.is_relative_to(inside) or not source.is_file():
                continue
            info = source.stat()
            if info.st_size > budget or time.monotonic() > deadline:
                kept.append(source.name)
                continue
            box = box or outbox(root)
            if box is None:
                kept.append(source.name)
                continue
            target, wrote = stage(source, info, box)
            sent[-1] = str(target)
            budget -= info.st_size
            copied += wrote
            staged += 1
        except (OSError, ValueError):
            kept.append(os.path.basename(entry.rstrip("/")) or entry)
    if staged:
        try:
            prune(box, time.time())
        except OSError:
            pass
    notes = []
    if staged:
        notes.append("copied %d file(s) from outside the session's working directory into %s/, "
                     "so Remote Control can open them" % (staged, OUTBOX.as_posix()))
    if kept:
        notes.append("could not copy %s into the working directory, so Remote Control may not open it"
                     % ", ".join(kept))
    if not notes:
        return None
    answer = "+".join(name for name, count in (("staged", staged), ("kept", len(kept))) if count)
    log_decision(answer, ", ".join(os.path.basename(str(s)) for s in sent if isinstance(s, str)),
                 payload, {"staged": staged, "kept": len(kept), "files": len(files),
                           "bytes_staged": copied})
    result = {"systemMessage": "stage-user-files: " + "; ".join(notes) + "."}
    if staged:
        result["hookSpecificOutput"] = {"hookEventName": "PreToolUse",
                                        "updatedInput": dict(inputs, files=sent)}
    return result


def main():
    try:
        result = decide(json.load(sys.stdin))
    except Exception:
        return
    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
