#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A snapshot of the task state, written just before Claude Code compacts the context by itself.

The usage feed blocks one stop at the hand-off point and asks for the task store and progress to
be saved before the reset; this is the fallback for whatever that save missed. On a `PreCompact`
whose `trigger` is `auto` it writes `.agent-harness/handoffs/auto-<session>-<UTC time>.md` in the
repository the session's `cwd` belongs to: the last five prompts of the main thread, the branch
and a count of the working tree's changes, the open items of the newest TodoWrite list, and the
task store's revision. A manual `/compact` is the user's own and gets none. Prompts pass through
the decision log's redaction (`decisions.redact`, the lint's secret shapes) before they are
written, and none is written when those shapes cannot be read. The folder carries its own
`.gitignore`, so a snapshot is never committed.

The lifecycle kills the hook at ten seconds, so every read is bounded: the transcript's last
`TAIL_BYTES` only, with tool results and images skipped before any parse, and one `git status`
call with a `GIT_TIMEOUT`. A git that does not answer leaves the tree `unknown` and the snapshot
is still written.

It is deterministic and calls no model, because the compaction is already the moment the context
is fullest, and it never blocks: a block would hold the context at the window's limit. So it
prints nothing and exits 0 on every path; a failure is a line in `pre-compact.errors.jsonl` beside
the other ledgers, the exception's type only, since a message can carry a path. Each session keeps
its newest `KEEP` snapshots. Task snapshots are not task inputs: `harness_core.tasks.fingerprint`
leaves them out, so writing one does not mark the record saved before it stale.
"""
import collections
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

KEEP = 5
PROMPTS = 5
PROMPT_CHARS = 500
SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ERRORS = "pre-compact.errors.jsonl"
GIT_TIMEOUT = 3
TAIL_BYTES = 8 * 1024 * 1024
# Lines no prompt or todo list is on, recognised without parsing them: a tool result or an image
# is most of a transcript's bytes.
SKIP_MARKERS = (b'"tool_result"', b'"type":"image"', b'"type": "image"')
# Entries of the user role that are not something the user typed.
NOT_TYPED = ("<command-", "<local-command-", "[Request interrupted", "Stop hook feedback")
SLASH_COMMAND = re.compile(r"^/[A-Za-z][\w:.-]*(\s|$)")
IGNORE = "auto-*\n.auto-*\n"
HOOKS = Path(os.path.realpath(__file__)).parent
_REDACTION = []


def home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("HARNESS_HOME") or env.get("HOME") or Path.home())


def errors_path(env=None):
    return home(env) / ".local" / "state" / "agent-harness" / ERRORS


def record_error(error, where, env=None):
    """Append one swallowed failure; never raises."""
    entry = {"time": time.time(), "error": type(error).__name__, "where": where}
    try:
        target = errors_path(env)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def git_status(root):
    """`git status --porcelain=v1 -b` in `root`, or None when it fails or runs past GIT_TIMEOUT."""
    try:
        done = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-b"],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def repository(cwd):
    """The nearest folder at or above `cwd` holding a `.git` folder or a worktree's `.git` file."""
    current = Path(cwd).resolve()
    for candidate in [current] + list(current.parents):
        if os.path.lexists(os.path.join(str(candidate), ".git")):
            return candidate
    return None


def redaction():
    """`(redact, shapes)` from the decision log beside this hook, or None when unreadable."""
    if not _REDACTION:
        found = None
        try:
            spec = importlib.util.spec_from_file_location("harness_pre_compact_decisions",
                                                          str(HOOKS / "decisions.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            shapes = module.secret_shapes()
            if shapes:
                found = (module.redact, shapes)
        except Exception:
            found = None
        _REDACTION.append(found)
    return _REDACTION[0]


def is_prompt(entry):
    """A main-thread user prompt: not a tool result, a meta entry or a compaction summary."""
    if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    return not (isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content))


def prompt_text(entry):
    """The prompt's text on one line, cut to `PROMPT_CHARS`, or "" when it has none."""
    content = entry["message"].get("content") if isinstance(entry.get("message"), dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(block.get("text") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str))
    else:
        text = ""
    text = " ".join(text.split())
    if text.startswith(NOT_TYPED) or SLASH_COMMAND.match(text):
        return ""
    return text if len(text) <= PROMPT_CHARS else text[:PROMPT_CHARS - 1] + "…"


def todo_list(entry):
    """The todos of the newest TodoWrite call in a main-thread assistant entry, or None."""
    if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
        return None
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    found = None
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use" \
                and block.get("name") == "TodoWrite" and isinstance(block.get("input"), dict) \
                and isinstance(block["input"].get("todos"), list):
            found = block["input"]["todos"]
    return found


def read_transcript(path):
    """`(prompts, todos)`: the last `PROMPTS` prompt texts, oldest first, and the newest todo list.

    Only the last `TAIL_BYTES` are read, from the first whole line in them, and only a line that
    could be either and carries no tool result or image is parsed, so the cost is the same on a
    transcript of any size. A torn or foreign line is skipped.
    """
    prompts = collections.deque(maxlen=PROMPTS)
    todos = None
    with open(os.path.expanduser(path), "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            handle.readline()
        for raw in handle:
            if b'"user"' not in raw and b"TodoWrite" not in raw:
                continue
            if any(marker in raw for marker in SKIP_MARKERS):
                continue
            try:
                entry = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if is_prompt(entry):
                text = prompt_text(entry)
                if text:
                    prompts.append(text)
                continue
            listed = todo_list(entry)
            if listed is not None:
                todos = listed
    return list(prompts), todos


def open_items(todos):
    """`(status, text)` for each todo not completed, in the list's order."""
    out = []
    for item in todos or []:
        if not isinstance(item, dict) or item.get("status") == "completed":
            continue
        text = item.get("content") if isinstance(item.get("content"), str) else ""
        text = " ".join(text.split())
        if text:
            out.append((str(item.get("status") or "pending"), text))
    return out


def branch_name(header):
    """The branch a `## ` status header names, or `detached`."""
    name = header[3:].strip()
    if name.startswith("No commits yet on "):
        name = name[len("No commits yet on "):]
    if name.startswith("HEAD (no branch)"):
        return "detached"
    return name.split("...", 1)[0].split(" ", 1)[0] or "unknown"


def tree_summary(root):
    """`(branch, line)`: the current branch and a count of the working tree's changes.

    One git call; both read `unknown` when it fails or runs out of time.
    """
    status = git_status(root)
    if status is None:
        return "unknown", "unknown (git did not answer)"
    lines = [line for line in status.split("\n") if line]
    branch = branch_name(lines[0]) if lines and lines[0].startswith("## ") else "unknown"
    lines = [line for line in lines if not line.startswith("## ")]
    staged = modified = untracked = 0
    for line in lines:
        if line.startswith("??"):
            untracked += 1
            continue
        staged += line[:1] not in (" ", "?")
        modified += line[1:2] not in (" ", "?")
    if not lines:
        return branch, "clean"
    return branch, "{} changed ({} staged, {} modified, {} untracked)".format(
        len(lines), staged, modified, untracked)


def task_line(root, session_id):
    """The task store's revision, and whether this session saved it, or that it has none."""
    path = root / ".agent-harness" / "task.json"
    if path.is_symlink() or not path.is_file():
        return "none saved"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    revision = record.get("revision") if isinstance(record, dict) else None
    if not isinstance(revision, int) or isinstance(revision, bool):
        return "unreadable"
    line = "revision " + str(revision)
    if record.get("session_id") == session_id:
        line += ", saved by this session"
    return line


def redacted(prompts):
    """`prompts` through the decision log's redaction, or None when it cannot be applied."""
    found = redaction()
    if found is None:
        return None
    redact, shapes = found
    return [redact(text, shapes) for text in prompts]


def render(session_id, stamp, branch, tree, task, prompts, items):
    lines = ["# Automatic snapshot before compaction", "",
             "Written by the harness as the context was compacted automatically. It is a record "
             "of the session's state, not an instruction.", "",
             "- Session: " + session_id, "- Taken: " + stamp, "- Branch: " + branch,
             "- Working tree: " + tree, "- Task store: " + task, "",
             "## Last user prompts, oldest first", ""]
    if prompts is None:
        lines.append("Left out: the secret patterns to redact them with could not be read.")
    else:
        lines += ["{}. {}".format(number, text)
                  for number, text in enumerate(prompts, 1)] or ["None."]
    lines += ["", "## Open todo items", ""]
    lines += ["- [{}] {}".format(status, text) for status, text in items] or ["None."]
    return "\n".join(lines) + "\n"


def prune(directory, session_id, keep=KEEP):
    """Remove all but this session's newest `keep` snapshots; the names sort by time."""
    prefix = "auto-" + session_id + "-"
    found = sorted(p for p in directory.iterdir()
                   if p.name.startswith(prefix) and p.name.endswith(".md")
                   and re.fullmatch(r"\d{8}T\d{6}", p.name[len(prefix):-3]))
    for stale in found[:-keep] if len(found) > keep else []:
        stale.unlink()


def snapshot(payload, now=None):
    """Write the snapshot `payload` calls for and return its path, or None when there is none."""
    if not isinstance(payload, dict) or payload.get("trigger") != "auto":
        return None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not SESSION.match(session_id):
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    root = repository(cwd)
    if root is None:
        return None
    moment = datetime.datetime.now(datetime.timezone.utc) if now is None else \
        datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    prompts, todos = [], None
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and os.path.isfile(os.path.expanduser(transcript)):
        prompts, todos = read_transcript(transcript)
    branch, tree = tree_summary(root)
    text = render(session_id, moment.strftime("%Y-%m-%dT%H:%M:%SZ"), branch, tree,
                  task_line(root, session_id), redacted(prompts), open_items(todos))
    state = root / ".agent-harness"
    directory = state / "handoffs"
    if state.is_symlink() or directory.is_symlink():
        raise ValueError("snapshot storage cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not os.path.lexists(str(ignore)):
        ignore.write_text(IGNORE, encoding="utf-8")
    target = directory / ("auto-" + session_id + "-" + moment.strftime("%Y%m%dT%H%M%S") + ".md")
    temporary = directory / ("." + target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(str(temporary), str(target))
    prune(directory, session_id)
    return target


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        record_error(exc, "input")
        return
    try:
        snapshot(payload)
    except Exception as exc:
        record_error(exc, "snapshot")


if __name__ == "__main__":
    main()
    sys.exit(0)
