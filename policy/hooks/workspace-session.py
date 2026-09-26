#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SessionStart hook: the other folders of this session's workspace, and their instructions.

`lib/harness_core/workspaces.py` decides which workspace the session's folder belongs to; this
hook supplies what the surface does not load itself. Claude Code loads an added folder's
`CLAUDE.md` (or `.claude/CLAUDE.md`) and rules only when
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` and the folder was passed as `--add-dir`, so a
member counts as loaded natively only when the variable is in this hook's environment, the member
is an `--add-dir` value in the exact argument vector of `CLAUDE_PID`, and it has one of those
files; a member with only `AGENTS.md` is always supplied. The vector is read from `/proc` on Linux
and through `sysctl` on macOS; elsewhere nothing counts as native. Codex loads nothing from an
added folder, so every member is supplied there.

It has its own SessionStart entry, and so its own 10,000-character output cap. A block of at most
`INLINE_LIMIT` characters goes inline; a longer one is written to a bundle file named for the
workspace and its content under the harness state folder, and only the member list and that path
are inlined. Silent while `workspaces_dir` is unset or the folder is in no workspace; any failure
returns nothing, so a session is never blocked. Test:

    echo '{"hook_event_name":"SessionStart","cwd":"'"$PWD"'"}' | python3 workspace-session.py
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
LIB = HOOKS.parents[1] / "lib" / "harness_core" / "workspaces.py"
NATIVE_VAR = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"
INLINE_LIMIT = 9000
# The supplied instructions stop here; a bundle is for reading, not for a whole repository.
TOTAL_LIMIT = 256 * 1024
BUNDLE_DAYS = 7
BUDGET_SECONDS = 4.0

_started = time.monotonic()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def over_budget():
    return time.monotonic() - _started > BUDGET_SECONDS


def config(posture, env):
    """The resolved configuration, from the file `posture.config_path` names."""
    try:
        with open(str(posture.config_path(env)), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _darwin_argv(pid):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not size.value:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return []
    data = buf.raw[:size.value]
    argc = int.from_bytes(data[:4], sys.byteorder)
    rest = data[4:]
    # The executable path, then NUL padding, then argc NUL-terminated arguments.
    rest = rest[rest.find(b"\0"):].lstrip(b"\0")
    return [a.decode("utf-8", "replace") for a in rest.split(b"\0")[:argc]]


def parent_command(pid):
    """The parent runtime's exact argument vector, or [] when it cannot be read."""
    if not pid or not str(pid).isdigit():
        return []
    try:
        proc = Path("/proc") / str(pid) / "cmdline"
        if proc.is_file():
            return [a for a in proc.read_bytes().decode("utf-8", "replace").split("\0") if a]
        if sys.platform == "darwin":
            return _darwin_argv(pid)
    except Exception:
        return []
    return []


def add_dirs(argv, base):
    """Every folder given as an `--add-dir` value: `--add-dir=<path>`, or the run of values after
    `--add-dir` up to the next token starting with `-`, resolved against `base`."""
    found, taking = [], False
    for arg in argv:
        if arg == "--add-dir":
            taking = True
        elif arg.startswith("--add-dir="):
            found.append(arg.split("=", 1)[1])
            taking = False
        elif arg.startswith("-"):
            taking = False
        elif taking:
            found.append(arg)
    return [os.path.realpath(os.path.join(base, os.path.expanduser(p))) for p in found if p]


def classify(ws_module, members, folder, env, runtime, given):
    """[(member, status)] for every member but the session's own: `native` or `supplied`."""
    native_on = runtime == "claude-code" and env.get(NATIVE_VAR) == "1"
    out = []
    for member in members:
        if member == folder:
            continue
        loaded = native_on and member in given and bool(ws_module.claude_files(member))
        out.append((member, "native" if loaded else "supplied"))
    return out


def instructions(ws_module, supplied):
    """(text, file paths) for the supplied members, capped at `TOTAL_LIMIT` characters."""
    parts, paths, used, left = [], [], 0, []
    for member in supplied:
        found = ws_module.member_instructions(member)
        for path, text in found["files"]:
            paths.append(path)
            if used + len(text) > TOTAL_LIMIT:
                left.append(path)
                continue
            used += len(text)
            parts.append("### " + path + "\n\n" + text.strip())
        if found["scoped"]:
            parts.append("### Path-scoped rules in " + member + "\n\nRead each before working on "
                         "files it covers:\n" + "\n".join("- " + p for p in found["scoped"]))
    if left:
        parts.append("### Not included, too long\n\nRead each before working in its folder:\n"
                     + "\n".join("- " + p for p in left))
    return "\n\n".join(parts), paths


LABELS = {"native": "loaded natively", "supplied": "supplied by this hook"}


def header(ws, folder, rule, statuses):
    lines = ["Workspace " + ws["name"] + " (decided by: " + rule + "). This session's folder "
             + folder + " works together with these folders; follow their instructions when "
             "working in them:"]
    lines.extend("- " + member + ": " + LABELS[status] for member, status in statuses)
    lines.extend("- " + missing + ": missing, skipped" for missing in ws.get("missing", []))
    return "\n".join(lines)


def desktop_line(env):
    if env.get("CLAUDE_CODE_ENTRYPOINT") != "claude-desktop":
        return None
    return ("Before first reading or editing in a member folder, ask the app for that folder with "
            "the desktop folder-grant tool (`request_directory`, a deferred tool found through tool "
            "search); do not request them all now. The app passes a granted folder as --add-dir "
            "from this chat's next launch.")


def prune(directory, now=None):
    """Remove bundle files older than `BUNDLE_DAYS`; a file that will not go is left."""
    cutoff = (time.time() if now is None else now) - BUNDLE_DAYS * 86400
    for entry in directory.glob("*.md"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def write_bundle(directory, name, text):
    """Write `text` to `<name>-<12 hex of its sha256>.md`, so sessions never overwrite each other."""
    directory.mkdir(parents=True, exist_ok=True)
    prune(directory)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    target = directory / (re.sub(r"[^A-Za-z0-9._-]", "_", name) + "-" + digest + ".md")
    fd, temp = tempfile.mkstemp(dir=str(directory), prefix=".bundle-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, str(target))
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    return target


def context(event, env=None):
    """The block for this session, or None when there is nothing to say."""
    env = os.environ if env is None else env
    posture = _load("harness_posture", HOOKS / "posture.py")
    directory = config(posture, env).get("workspaces_dir")
    if not isinstance(directory, str) or not directory.strip():
        return None
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        return None
    ws_module = _load("harness_workspaces", LIB)
    cwd = event.get("cwd") or env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    runtime = env.get("HARNESS_RUNTIME", "claude-code")
    argv = parent_command(env.get("CLAUDE_PID")) if runtime == "claude-code" else []
    given = add_dirs(argv[1:], cwd)
    result = ws_module.resolve(cwd, directory, env=env, add_dirs=given)
    if result["rule"] == "ambiguous":
        return ("This folder is in the workspaces " + ", ".join(result["candidates"])
                + " and none was attached. Pin one in "
                + os.path.join(directory, ws_module.OVERRIDES)
                + ", or launch with `citizen workspace open <name>`.")
    ws = result["workspace"]
    if ws is None:
        return None
    statuses = classify(ws_module, result["members"], result["folder"], env, runtime, set(given))
    if not statuses and not ws.get("missing"):
        return None
    head = header(ws, result["folder"], result["rule"], statuses)
    grant = desktop_line(env)
    if grant:
        head += "\n\n" + grant
    body, paths = instructions(ws_module, [m for m, s in statuses if s == "supplied"])
    whole = head + ("\n\n## Member instructions\n\n" + body if body else "")
    if len(whole) <= INLINE_LIMIT:
        return whole
    path = None
    if not over_budget():
        try:
            path = write_bundle(posture.state_dir(env) / "workspaces", ws["name"],
                                "# Workspace " + ws["name"] + " member instructions\n\n" + body + "\n")
        except Exception:
            path = None
    if path is None:
        return (head + "\n\nThe supplied members' instructions are too long to show here. Read "
                "each of these files before you work in its folder:\n"
                + "\n".join("- " + p for p in paths))
    return (head + "\n\nThe supplied members' instructions (" + str(len(body)) + " characters) "
            "are part of your instructions for this session, but too long to show here: they are "
            "in " + str(path) + ". Read that file with your file-reading tool now, before you "
            "answer or take any other action.")


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
        text = context(event if isinstance(event, dict) else {})
    except Exception:
        text = None
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": text}}))
    else:
        print("{}")


if __name__ == "__main__":
    main()
