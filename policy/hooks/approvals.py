#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""UserPromptSubmit hook, and the library behind it: one-use approvals the user types in chat.

Why: in `auto` mode a hook's `ask` is ignored, so `grade-bash` has to deny, and the auto-mode
classifier refuses any command the agent prefixes with the confirm marker as an attempt to bypass
a safety hook. The only channel the agent cannot produce is the user's own prompt, so the user
approves a refused command by replying `approve <code>`, and nothing else can create an approval.

- `code_for(session_id, command)` is the first six base32 characters of
  `sha256(session_id + "\\n" + command)`, over the raw command text. The same command in the same
  session always gets the same code, so there is no pending store to protect: knowing a code is
  worth nothing without an approval recorded from a prompt.
- `record(session_id, prompt)` keeps the prompt's approvals in
  `~/.local/state/agent-harness/approvals/<session_id>.json` with their time, but only when the
  whole prompt is `approve <code>` tokens (case-insensitive, several allowed, separated by
  whitespace or commas). Any other text records nothing: UserPromptSubmit also fires on turns the
  user never typed, such as task notifications, subagent hand-backs and cross-session messages,
  and those carry agent-controlled text that could otherwise smuggle in an approval.
- `consume(session_id, code)` marks one unused approval younger than `TTL` seconds used and says
  whether it found one. An approval confirms one run of one command in one session.
- The store is the user's alone: `grade-bash` grades a Bash write to it 3, and the dispatcher
  denies a file-tool write to it (`file_write_deny`).

Each `record` and `consume` holds an exclusive `flock` on `<session_id>.lock` beside the file
for its whole read-modify-write, so two consumers cannot both use one approval and a consume
cannot drop an approval a record just added. A lock that cannot be taken fails closed.

Every read and write is wrapped, so a store that cannot be read is no approval and a store that
cannot be written records nothing; neither ever raises into the hook that called it.

Test: echo '{"hook_event_name":"UserPromptSubmit","session_id":"s1","prompt":"approve ABC234"}' | python3 approvals.py
"""
import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # no advisory locks here, so the store neither records nor consumes
    fcntl = None

CODE_LENGTH = 6
TTL = 30 * 60
SESSION_ID_MAX = 128
# Bounded so a long session cannot grow its file without limit; the oldest approvals go first.
KEEP = 64
TOKEN = r"approve\s+([A-Za-z2-7]{%d})" % CODE_LENGTH
APPROVE_RE = re.compile(TOKEN, re.I)
# The whole prompt, stripped: one or more tokens and nothing else.
ONLY_TOKENS_RE = re.compile(r"(?:%s)(?:[\s,]+%s)*" % (TOKEN, TOKEN), re.I)
# A Bash command naming the store in any of these spellings is treated as a write to it once it
# is anything but read-only; see `mentions_store`.
STORE_RE = re.compile(r"agent-harness[/\\]+approvals(?=$|[/\\\s\"'`;|&)<>])|\.local[/\\]+state[/\\]+agent-harness"
                      r"(?=[\s\S]*approvals)")
FILE_DENY = ("The approvals store is written only from the user's own prompt, so no tool may write "
             "to it. Ask the user to reply with the approval the refusal named.")


def home():
    return Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())


def store_dir():
    return home() / ".local" / "state" / "agent-harness" / "approvals"


def _session_ok(value):
    """A session id safe to make a file name of: no separator, no traversal, bounded."""
    return (isinstance(value, str) and value.isascii() and 0 < len(value) <= SESSION_ID_MAX
            and value[0].isalnum() and all(c.isalnum() or c in "._-" for c in value))


def store_path(session_id):
    return store_dir() / (session_id + ".json") if _session_ok(session_id) else None


def code_for(session_id, command):
    digest = hashlib.sha256((session_id + "\n" + command).encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii")[:CODE_LENGTH]


def codes_in(prompt):
    """The codes a prompt approves, upper-cased, in order, without repeats; none unless the
    stripped prompt is nothing but `approve <code>` tokens."""
    seen = []
    text = prompt.strip() if isinstance(prompt, str) else ""
    if not ONLY_TOKENS_RE.fullmatch(text):
        return seen
    for match in APPROVE_RE.finditer(text):
        code = match.group(1).upper()
        if code not in seen:
            seen.append(code)
    return seen


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("approvals") if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _write(path, entries):
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(path.parent), 0o700)
        with os.fdopen(os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                       "w", encoding="utf-8") as handle:
            json.dump({"approvals": entries[-KEEP:]}, handle)
        os.replace(str(temp), str(path))
        return True
    except OSError:
        try:
            os.unlink(str(temp))
        except OSError:
            pass
        return False


@contextlib.contextmanager
def _locked(path):
    """Hold an exclusive lock for `path`'s session; yields False when none could be taken."""
    fd = None
    try:
        if fcntl is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(str(path.parent), 0o700)
            fd = os.open(str(path.with_suffix(".lock")), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        if fd is not None:
            os.close(fd)
        fd = None
    try:
        yield fd is not None
    finally:
        if fd is not None:
            os.close(fd)  # closing the descriptor releases the lock


def _live(entry, now):
    created = entry.get("created")
    # A created time a little past `now` is a record that took the lock after this call read
    # the clock, not a forgery: only a user prompt writes the store.
    return isinstance(created, (int, float)) and not entry.get("used") and now - created <= TTL


def record(session_id, prompt, now=None):
    """Record the prompt's `approve <code>` tokens for this session; the codes recorded."""
    path = store_path(session_id)
    codes = codes_in(prompt)
    if path is None or not codes:
        return []
    now = time.time() if now is None else now
    with _locked(path) as held:
        if not held:
            return []
        entries = [e for e in _read(path) if _live(e, now)]
        entries.extend({"code": code, "created": now, "used": False} for code in codes)
        return codes if _write(path, entries) else []


def consume(session_id, code, now=None):
    """Use one live approval of `code` in this session; True when there was one to use."""
    path = store_path(session_id)
    if path is None or not isinstance(code, str):
        return False
    if not path.exists():
        return False  # nothing was ever recorded: no lock file for a session with no approvals
    now = time.time() if now is None else now
    with _locked(path) as held:
        if not held:
            return False
        entries = _read(path)
        for entry in entries:
            if entry.get("code") == code.upper() and _live(entry, now):
                entry["used"] = True
                entry["used_at"] = now
                return _write(path, entries)
        return False


def mentions_store(text):
    return isinstance(text, str) and bool(STORE_RE.search(text))


def under_store(path):
    """Whether a file path, resolved, is the store or inside it."""
    try:
        target = os.path.realpath(os.path.expanduser(str(path)))
        root = os.path.realpath(str(store_dir()))
    except (OSError, ValueError):
        return False
    return target == root or target.startswith(root + os.sep)


def file_write_deny(paths):
    """The deny for a file-tool write whose paths reach into the store, or None."""
    if any(under_store(p) for p in paths):
        return {"hookSpecificOutput": {"permissionDecision": "deny",
                                       "permissionDecisionReason": FILE_DENY}}
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "UserPromptSubmit":
        return
    record(payload.get("session_id"), payload.get("prompt"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a recorder fault costs an approval, never the user's prompt
