#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stop hook: run the repository's own gate and refuse to finish while it is red.

Opt-in per repository — the gate is the fenced block under the `## Gate` heading of the
repo's `AGENTS.md`, executed together in one shell. A repo without that block is untouched.
Trusted folders only: the block is a repository's own text, so it runs only where Claude
Code's folder-trust dialog has been accepted (the `hasTrustDialogAccepted` flag it records
per project), the same consent that gates a repository's `.claude/settings.json` hooks, or
where the root is listed in ~/.config/agent-harness/trusted.txt by `harness trust`.
Bounded: after MAX_BLOCKS consecutive blocks the turn is released, so a gate that can never
pass cannot trap a session. The count is kept per session, so two sessions stopping in the same
checkout never reset each other's; a session silent for STALE_SECONDS is forgotten. A timeout releases the turn as unverified; unexpected errors block. Neither records success.
Declinable: once this session has been blocked, a final message carrying a line that opens with
`Gate cannot pass:` and a reason (see `stated_reason`) releases the turn as unverified, logged as
`declined`. Any other finish is blocked again while the gate stays red.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_BLOCKS = 8
STALE_SECONDS = 24 * 3600
BUDGET_SECONDS = 240
TAIL_LINES = 30
GATE_FILES = ("AGENTS.md", "CLAUDE.md")
STATE = Path.home() / ".local" / "state" / "agent-harness" / "stop-gate"
TRUSTED = Path.home() / ".config" / "agent-harness" / "trusted.txt"
DECLINE_MARKER = "Gate cannot pass:"
MIN_REASON_CHARS = 12
# The marker opens its own line, after at most a list bullet, a quote mark or bold emphasis, so a
# message that merely mentions the phrase mid-sentence, or quotes this hook's instruction, does not
# match. The reason is everything after the colon on that line.
DECLINE_LINE = re.compile(r"^\s*(?:[-*>]\s+)?(?:\*\*|__)?gate cannot pass(?:\*\*|__)?\s*:"
                          r"(?:\*\*|__)?\s*(?P<why>.*?)\s*$", re.IGNORECASE)


_LOG = []


def decisions():
    """The sibling decision log, or None. A log that will not load costs nothing but its rows."""
    if not _LOG:
        module = None
        try:
            path = Path(__file__).resolve().parent / "decisions.py"
            spec = importlib.util.spec_from_file_location("harness_decisions", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            module = None
        _LOG.append(module)
    return _LOG[0]


def log_gate(payload, root, commands, answer, outcome):
    """Record what this Stop event was answered with, and how the gate turned out.

    Both records are written here because both facts are known here: the hook runs the gate
    itself, so the outcome does not wait for a later event. The judged input is the repository's
    own gate block — the text this hook decided to run — and never the turn's final message.

    The claim the turn ended on is the transcript's, not the payload's: a Stop event carries no
    assistant text, so the log reads a capped tail of the file the event names. That read only
    happens under `telemetry.completion_claim`, which is off, because it is the one field in the
    log that holds model prose. Both runtimes' names for the file are accepted.
    """
    module = decisions()
    if module is None:
        return
    payload = payload if isinstance(payload, dict) else {}
    text = str(root) + "\n" + "\n".join(commands)
    transcript = (payload.get("transcript_path") or payload.get("rollout_path")
                  or payload.get("session_path") or "")
    identity = module.record("stop-gate", answer, text, payload, transcript=transcript)
    if identity and outcome is not None:
        module.observe(identity, outcome, "stop-gate", payload.get("session_id") or "")


def final_message(payload):
    """The text of the turn's last assistant message, or "" when it cannot be read.

    The event's own `last_assistant_message` is taken when the runtime sends one; otherwise the
    transcript the event names is read through the decision log's bounded reader. Nothing read
    means nothing to honour, so every failure here keeps the block.
    """
    text = payload.get("last_assistant_message")
    if isinstance(text, str) and text.strip():
        return text
    transcript = (payload.get("transcript_path") or payload.get("rollout_path")
                  or payload.get("session_path") or "")
    module = decisions()
    if not transcript or module is None:
        return ""
    try:
        claim, _ = module.read_claim(transcript)
    except Exception:
        return ""
    return claim or ""


def stated_reason(text):
    """The reason a final message gives for the gate not passing, or None when it gives none.

    Only an explicit `Gate cannot pass: <why>` line counts, with at least MIN_REASON_CHARS of
    reason, so an ordinary "done" never releases a red gate. The block message names the line.
    """
    for line in (text or "").split("\n"):
        match = DECLINE_LINE.match(line)
        if match:
            why = match.group("why").strip(" *_`")
            if len(why) >= MIN_REASON_CHARS:
                return why
    return None


def git(root, *args):
    try:
        out = subprocess.run(["git", "-C", root, *args],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return out.stdout if out.returncode == 0 else ""


def git_root(cwd):
    root = git(cwd, "rev-parse", "--show-toplevel").strip()
    return root or None


def claude_config():
    """Claude Code's per-user state file, honouring CLAUDE_CONFIG_DIR."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config_dir) if config_dir else Path.home()) / ".claude.json"


def listed_roots():
    """Roots recorded by `harness trust`, as written and resolved."""
    try:
        lines = TRUSTED.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    roots = set()
    for ln in lines:
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            roots.update((ln, str(Path(ln).resolve())))
    return roots


def trusted(root, cwd):
    """True when the folder-trust dialog has been accepted for the working directory, the
    repository root, or a directory between them, or when `harness trust` listed the root."""
    if os.environ.get("HARNESS_RUNTIME") == "codex":
        return bool(listed_roots() & {str(Path(root)), str(Path(root).resolve())})
    try:
        projects = json.loads(claude_config().read_text(encoding="utf-8")).get("projects") or {}
    except Exception:
        projects = {}
    if not isinstance(projects, dict):
        projects = {}
    top = Path(root).resolve()
    if listed_roots() & {str(Path(root)), str(top)}:
        return True
    keys = {str(Path(root)), str(top)}
    path = Path(cwd)
    while path.resolve() == top or top in path.resolve().parents:
        keys.update((str(path), str(path.resolve())))  # symlinked temp dirs record either form
        if path.resolve() == top:
            break
        path = path.parent
    return any(isinstance(projects.get(k), dict) and projects[k].get("hasTrustDialogAccepted") is True
               for k in keys)


def gate_file(root):
    for name in GATE_FILES:
        path = Path(root) / name
        if path.is_file():
            return path
    return None


def gate_commands(root):
    path = gate_file(root)
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    start = None
    for i, raw in enumerate(lines):
        if raw.strip().lower() == "## gate":
            start = i + 1
            break
    if start is None:
        return []
    commands = []
    fenced = False
    for raw in lines[start:]:
        text = raw.strip()
        if text.startswith("```"):
            if fenced:
                break
            fenced = True
        elif not fenced:
            if text.startswith("#"):  # the next heading, with no block between
                break
        elif text and not text.startswith("#"):
            commands.append(text)
    return commands


def tree_hash(root):
    digest = hashlib.sha256()
    digest.update(("gate-v2:" + str(Path(root).resolve())).encode())
    def checked(*args):
        return subprocess.run(["git", "-C", root, *args], capture_output=True,
                              check=True, timeout=10).stdout
    for args in (("rev-parse", "HEAD"), ("status", "--porcelain", "-z"),
                 ("diff", "--binary"), ("diff", "--cached", "--binary")):
        digest.update(hashlib.sha256(checked(*args)).digest())
    for name in checked("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not name:
            continue
        path = Path(root) / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    digest.update("\n".join(gate_commands(root)).encode())
    digest.update(str(BUDGET_SECONDS).encode())
    return digest.hexdigest()


def state_path(root):
    return STATE / (hashlib.sha256(root.encode("utf-8")).hexdigest() + ".json")


def read_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gate-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_gate(root, commands):
    """The first red command as (command, exit code, output), or None when every one passes."""
    cmd = "\n".join(commands)
    out = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", cmd], cwd=root,
                         capture_output=True, text=True, timeout=BUDGET_SECONDS)
    if out.returncode != 0:
        return cmd, out.returncode, (out.stdout or "") + (out.stderr or "")
    return None


def reason(path, cmd, code, output):
    tail = "\n".join(output.splitlines()[-TAIL_LINES:]).strip()
    return (
        f"The gate in {path.name} is red: `{cmd}` exited {code}.\n\n"
        f"{tail}\n\n"
        "That command is the check block this repository defines under `## Gate`, run at the end "
        "of a turn once files have changed. Fix it and finish. If it cannot pass for a reason "
        "outside this task, end your reply with a line that starts `" + DECLINE_MARKER + "` "
        "followed by why; that ends the turn as unverified. Any other finish is blocked again."
    )


def live_sessions(state, now):
    """The per-session block counts in `state`, without entries silent for STALE_SECONDS."""
    sessions = state.get("sessions")
    if not isinstance(sessions, dict):
        return {}
    kept = {}
    for session, entry in sessions.items():
        try:
            blocks, seen = int(entry["blocks"]), float(entry["seen"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - seen < STALE_SECONDS:
            kept[session] = {"blocks": blocks, "seen": seen}
    return kept


def release(path, session, note):
    # Re-read: the gate can run for minutes, and another session may have recorded blocks meanwhile.
    sessions = live_sessions(read_state(path), time.time())
    sessions.pop(session, None)
    write_state(path, {"green_hash": None, "status": "unverified", "reason": note,
                       "sessions": sessions})
    sys.stderr.write("stop-gate: " + note + "\n")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    cwd = payload.get("cwd") or os.getcwd()
    root = git_root(cwd)
    if not root:
        return
    commands = gate_commands(root)
    if not commands:
        return
    if not trusted(root, cwd):
        sys.stderr.write("stop-gate: folder not trusted in Claude Code and not listed by "
                         "`citizen trust`; gate skipped. Run `citizen trust .` in this folder to "
                         "let it run the repository's own checks.\n")
        log_gate(payload, root, commands, "skipped", "untrusted")
        return

    current = tree_hash(root)
    path = state_path(root)
    state = read_state(path)
    if state.get("green_hash") == current:
        log_gate(payload, root, commands, "skipped", "passed")
        return

    session = payload.get("session_id") or ""
    try:
        failure = run_gate(root, commands)
    except subprocess.TimeoutExpired:
        release(path, session, f"gate ran past {BUDGET_SECONDS}s; letting the turn end")
        log_gate(payload, root, commands, "released", "timeout")
        return
    if failure is None:
        if tree_hash(root) != current:
            release(path, session, "working tree changed during the gate; result unverified")
            log_gate(payload, root, commands, "released", "unverified")
            return
        write_state(path, {"green_hash": current, "status": "passed", "sessions": {}})
        log_gate(payload, root, commands, "released", "passed")
        return

    now = time.time()
    sessions = live_sessions(read_state(path), now)
    prior = sessions.get(session, {}).get("blocks", 0)
    if prior:
        why = stated_reason(final_message(payload))
        if why is not None:
            release(path, session, "declined: " + why[:200])
            log_gate(payload, root, commands, "declined", "failed")
            return
    blocks = prior + 1
    if blocks >= MAX_BLOCKS:
        release(path, session, f"released after {MAX_BLOCKS} blocks; gate still red")
        log_gate(payload, root, commands, "released", "failed")
        return
    sessions[session] = {"blocks": blocks, "seen": now}
    write_state(path, {"green_hash": None, "status": "failed", "sessions": sessions})
    log_gate(payload, root, commands, "blocked", "failed")
    cmd, code, output = failure
    print(json.dumps({"decision": "block", "reason": reason(gate_file(root), cmd, code, output)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"decision": "block", "reason": "Gate is unverified: " + str(exc)}))
