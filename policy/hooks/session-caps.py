#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hold a session to its fan-out cap and its web-search cap: count both, warn near, deny at.

Two caps, each read from the text that states it, so a stance change moves the number:

    fan-out      the cost variant's `switches.max_parallel` (balanced 6, frugal 3, max none)
    web search   the research rule's "capped per session — N calls" (200 as shipped); none
                 while that rule is switched off

**Counting.** Every hook is a process of its own and several run at once, so a session's counts
live in one append-only journal, `caps/<session>.jsonl` under the harness state directory, and
every count is recomputed from it. A subagent is live from its `SubagentStart` to its
`SubagentStop`, and a start with no stop decays after `RUNNING_TTL`, as the usage feed's does.
One message can launch many spawns before any of them has started, so an allowed spawn is also a
reservation that counts as live until a start consumes it or `RESERVE_TTL` passes. A search is
counted when it is allowed. A subagent's own tool calls carry its parent's `session_id`, so its
searches count against the parent session.

**A blocked stop.** Another `SubagentStop` hook can block the stop, and the subagent then runs
on. No hook can see that outcome: matching hooks run in parallel and no event reports a block.
So every stop is journalled, the one raised after a block (`stop_hook_active`) included, with
the size of the subagent's transcript at that moment, and before a spawn is decided a stopped
subagent whose transcript has since gained a turn timestamped after the stop counts as live
again until its next stop. Claude Code writes the block's reason into that transcript as soon
as the stop hooks return, so the count runs low only while the blocking hook itself runs. A
strictly conservative count, live until a stop known to be final, was rejected: only a
foreground `Agent` return marks a final stop, and subagents run in the background by default,
so most would count as live until `RUNNING_TTL`.

**Deciding.** A spawn when the live count is already at the cap is denied, and so is a search
when the session has made as many as the cap allows; the reason names the cap and the count.
Past 80% of a cap the call goes through with a warning, said on every spawn and once a session
for searches. A `Workflow` launch is denied at the fan-out cap too, but its `agent()` calls are
not tool calls and cannot be refused one by one: they count only as far as the runtime raises
`SubagentStart` for them. Every decision is a `session-caps` row in the decision log.

**Headless.** A `claude -p` run (`CLAUDE_CODE_ENTRYPOINT=sdk-cli`), which is what a benchmark
replay is, never has a spawn or a search denied: the row says `would-deny` and the call goes
through with a warning, so a replay measures the arm and not this guard.
`HARNESS_SESSION_CAPS_HEADLESS=enforce` turns the denial back on for a test that wants it.

Claude Code only: Codex raises no subagent lifecycle events, so a live count there could not
fall. A guard that cannot read its state lets the call through; it never denies by accident.
"""
import datetime
import importlib.util
import json
import math
import os
import re
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - a platform with no advisory locking
    fcntl = None

HOOKS = Path(__file__).resolve().parent
POINT = "session-caps"
FANOUT, SEARCH = "fan-out", "web-search"
RULE = "research-and-verification"
RULE_CAP = re.compile(r"[Ww]eb search is capped per session\W+(\d[\d,]*) calls")
WARN_SHARE = 0.8
# Same decay as the usage feed's: a start this old with no stop is not running.
RUNNING_TTL = 3 * 3600
# A spawn's start follows its PreToolUse within seconds; a reservation older than this belongs to
# a spawn that never started (refused later in the chain, or failed) and stops counting.
RESERVE_TTL = 60
HEADLESS_ENTRY = "sdk-cli"
HEADLESS_VARIABLE = "HARNESS_SESSION_CAPS_HEADLESS"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
MAX_LINE = 4096
# The chunk a transcript past a stop is read in, so memory stays bounded however large the final
# response flushed after the stop; a longer line is skipped, and the block's reason comes after it.
CONTINUED_READ = 256 * 1024
TRANSCRIPT_TURNS = ("user", "assistant")
LOCK_WAIT = 2.0
JOURNAL_TTL = 14 * 86400


def sibling(name):
    """A module beside this hook, or None. The guard never fails over an import."""
    try:
        spec = importlib.util.spec_from_file_location(
            "harness_" + name.replace("-", "_"), str(HOOKS / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


# --------------------------------------------------------------------------- the caps


def fanout_cap(env, posture=None):
    """The cost variant's `max_parallel`, or None when the variant sets none or cannot be read."""
    posture = posture or sibling("posture")
    try:
        width = (posture.cost_table(env).get("switches") or {}).get("max_parallel")
    except Exception:
        return None
    return width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else None


def rule_cap(text):
    """The number the research rule states as the per-session search cap, or None."""
    found = RULE_CAP.search(text or "")
    if not found:
        return None
    value = int(found.group(1).replace(",", ""))
    return value if value > 0 else None


def search_cap(env, posture=None):
    """The research rule's search cap, or None while the rule is switched off or unreadable."""
    posture = posture or sibling("posture")
    try:
        config = posture._user_config(env, False)
        if ((posture.selection(env, strict=False, config=config).get("rules") or {})
                .get(RULE) == "off"):
            return None
        roots = posture.primitive_roots(config, kind="rules")
    except Exception:
        return None
    for root in roots:
        try:
            return rule_cap((root / (RULE + ".md")).read_text(encoding="utf-8"))
        except OSError:
            continue
    return None


def past_warning(count, cap):
    return cap is not None and count > WARN_SHARE * cap


def headless(env):
    """Whether a denial is held back: a `claude -p` run nobody opted in."""
    return (env.get("CLAUDE_CODE_ENTRYPOINT") == HEADLESS_ENTRY
            and env.get(HEADLESS_VARIABLE) != "enforce")


# --------------------------------------------------------------------------- the journal


def caps_dir(env):
    base = env.get("HARNESS_HOME") or env.get("HOME") or str(Path.home())
    return Path(base) / ".local" / "state" / "agent-harness" / "caps"


def journal_path(session_id, env):
    if not isinstance(session_id, str) or not IDENTIFIER.match(session_id):
        return None
    return caps_dir(env) / (session_id + ".jsonl")


def append(path, record):
    """One line, one `os.write`, on an `O_APPEND` descriptor, so concurrent writers lose nothing."""
    data = (json.dumps(record, ensure_ascii=True) + "\n").encode("utf-8")
    if len(data) > MAX_LINE:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return False
    try:
        return os.write(handle, data) == len(data)
    except OSError:
        return False
    finally:
        os.close(handle)


def records(path):
    out = []
    try:
        with open(str(path), "rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                try:
                    record = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        pass
    return out


def stamp(value):
    """A transcript record's ISO-8601 `timestamp` as epoch seconds, or None."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def continued(stop):
    """Whether the subagent's transcript gained a turn after this journalled stop.

    Only a turn timestamped after the stop counts: the final response a stop can fire ahead of
    lands in the transcript later, but carries an earlier timestamp. An unreadable transcript
    reads as no turn, so the guard never holds a slot it cannot account for.
    """
    path, size, at = stop.get("path"), stop.get("size"), stop.get("at")
    if not isinstance(path, str) or not isinstance(at, (int, float)) or isinstance(at, bool):
        return False
    try:
        with open(path, "rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            start = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 \
                else max(0, end - CONTINUED_READ)
            if end <= start:
                return False
            handle.seek(start)
            oversized = False
            while True:
                raw = handle.readline(CONTINUED_READ)
                if not raw:
                    return False
                if oversized or (len(raw) == CONTINUED_READ and not raw.endswith(b"\n")):
                    oversized = not raw.endswith(b"\n")
                    continue
                try:
                    record = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") not in TRANSCRIPT_TURNS:
                    continue
                when = stamp(record.get("timestamp"))
                if when is not None and when > at:
                    return True
    except OSError:
        return False


def tally(entries, now=None, probe=None):
    """`{"live", "running", "reserved", "searches", "warned"}` from a session's journal.

    A start consumes the oldest reservation made before it, which is the spawn that caused it
    when spawns start in order and a harmless undercount for a moment when they do not. With
    `probe`, `continued` as a spawn passes it, a subagent whose last stop `probe` says was
    followed by a turn is running again from that stop.
    """
    now = time.time() if now is None else now
    running, reserved, searches, warned, stopped = {}, [], 0, set(), {}
    for entry in entries:
        kind, at = entry.get("t"), entry.get("at")
        at = at if isinstance(at, (int, float)) and not isinstance(at, bool) else 0
        if kind == "start" and isinstance(entry.get("id"), str):
            running[entry["id"]] = at
            stopped.pop(entry["id"], None)
            earlier = [r for r in reserved if r <= at]
            if earlier:
                reserved.remove(min(earlier))
        elif kind == "stop" and isinstance(entry.get("id"), str):
            if running.pop(entry["id"], None) is not None or entry["id"] in stopped:
                stopped[entry["id"]] = entry
        elif kind == "spawn":
            reserved.append(at)
        elif kind == "search":
            searches += 1
        elif kind == "warned" and isinstance(entry.get("cap"), str):
            warned.add(entry["cap"])
    if probe is not None:
        for agent_id, stop in stopped.items():
            at = stop.get("at")
            if isinstance(at, (int, float)) and now - at <= RUNNING_TTL and probe(stop):
                running[agent_id] = at
    live_running = sum(1 for at in running.values() if now - at <= RUNNING_TTL)
    live_reserved = sum(1 for at in reserved if now - at <= RESERVE_TTL)
    return {"live": live_running + live_reserved, "running": live_running,
            "reserved": live_reserved, "searches": searches, "warned": warned}


class Lock(object):
    """An exclusive `flock` beside the journal with a bounded wait; `held` False when it timed out.

    Check-then-append must be one step, or two spawns in one message both see room for one. A
    lock that cannot be had in time still lets the guard decide, on a count that may be one short.
    """

    def __init__(self, path):
        self.path, self.handle, self.held = path, None, False

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.handle = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return self
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.held = True
                return self
            except OSError:
                if time.time() >= deadline:
                    return self
                time.sleep(0.01)

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                if self.held:
                    fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                os.close(self.handle)
        return False


def prune(env, now=None):
    """Remove journals untouched for `JOURNAL_TTL`; done at a subagent start, at most daily."""
    now = time.time() if now is None else now
    directory = caps_dir(env)
    marker = directory / ".pruned"
    try:
        if marker.exists() and now - marker.stat().st_mtime < 86400:
            return
        for path in directory.iterdir():
            if path.suffix in (".jsonl", ".lock") and now - path.stat().st_mtime > JOURNAL_TTL:
                path.unlink()
        marker.touch()
    except OSError:
        pass


# --------------------------------------------------------------------------- the decisions


def log(answer, text, payload, env, fields):
    module = sibling("decisions")
    if module is not None:
        module.record(POINT, answer, text if isinstance(text, str) else "", payload,
                      env.get("HARNESS_RUNTIME", ""), fields=fields)


def deny(reason):
    return {"hookSpecificOutput": {"permissionDecision": "deny",
                                   "permissionDecisionReason": reason + " (session-caps)"}}


def warn(text):
    return {"hookSpecificOutput": {"additionalContext": "session-caps: " + text}}


def on_spawn(payload, env, path, workflow=False):
    """A spawn or a `Workflow` launch: deny at the fan-out cap, warn past 80% of it."""
    cap = fanout_cap(env)
    held = headless(env)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    text = tool_input.get("script") or tool_input.get("scriptPath") if workflow else tool_input.get("prompt")
    what = "Workflow launch" if workflow else "spawn"
    with Lock(path.with_suffix(".lock")):
        counts = tally(records(path), probe=continued)
        live = counts["live"]
        fields = {"cap": FANOUT, "limit": cap, "count": live, "tool": payload.get("tool_name"),
                  "headless": held}
        if cap is not None and live >= cap:
            reason = ("Fan-out cap reached: %d subagents are live in this session and the cost "
                      "variant's cap is %d. Wait for one to finish, or fold this work into one "
                      "that is running." % (live, cap))
            if workflow:
                reason += (" A workflow's agent() calls are spawns too, and none of them can be "
                           "refused once the launch is through.")
            log("would-deny" if held else "deny", text, payload, env, fields)
            if held:
                if not workflow:
                    append(path, {"t": "spawn", "at": time.time()})
                return warn("a headless run is past the fan-out cap (%d live, cap %d); this %s "
                            "was let through" % (live, cap, what))
            return deny(reason)
        if not workflow:
            append(path, {"t": "spawn", "at": time.time()})
        after = live if workflow else live + 1
        if past_warning(after, cap):
            log("warn", text, payload, env, fields)
            return warn("%d of the cost variant's %d concurrent subagents %s in use; the cap "
                        "denies further spawns once %d are live"
                        % (after, cap, "are" if after != 1 else "is", cap))
        log("allow", text, payload, env, fields)
    return {}


def on_search(payload, env, path):
    """A web search: deny past the research rule's cap, warn once past 80% of it."""
    cap = search_cap(env)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    text = tool_input.get("query")
    with Lock(path.with_suffix(".lock")):
        counts = tally(records(path))
        made = counts["searches"]
        fields = {"cap": SEARCH, "limit": cap, "count": made, "tool": payload.get("tool_name"),
                  "subagent": bool(payload.get("agent_id"))}
        if cap is not None and made >= cap:
            held = headless(env)
            fields["headless"] = held
            log("would-deny" if held else "deny", text, payload, env, fields)
            if held:
                append(path, {"t": "search", "at": time.time()})
                return warn("a headless run is past the web-search cap (%d made, cap %d); this "
                            "search was let through" % (made, cap))
            return deny("Web-search cap reached: this session, its subagents included, has made "
                        "%d searches and the research rule caps a session at %d. Continue from "
                        "what has been found, or start a fresh session for the rest." % (made, cap))
        append(path, {"t": "search", "at": time.time()})
        after = made + 1
        if past_warning(after, cap) and SEARCH not in counts["warned"]:
            append(path, {"t": "warned", "cap": SEARCH, "at": time.time()})
            log("warn", text, payload, env, fields)
            return warn("this session has made %d of the research rule's %d web searches, its "
                        "subagents included; further searches are denied at %d" % (after, cap, cap))
        log("allow", text, payload, env, fields)
    return {}


def on_subagent(payload, env, path, kind):
    agent_id = payload.get("agent_id")
    if not (isinstance(agent_id, str) and IDENTIFIER.match(agent_id)):
        return {}
    record = {"t": kind, "id": agent_id, "at": time.time()}
    if kind == "stop":
        if payload.get("stop_hook_active"):
            record["after_block"] = True
        transcript = payload.get("agent_transcript_path")
        if isinstance(transcript, str) and transcript and len(transcript) <= MAX_LINE // 2:
            transcript = os.path.expanduser(transcript)
            try:
                record["size"] = os.stat(transcript).st_size
            except OSError:
                pass
            record["path"] = transcript
    if len(json.dumps(record, ensure_ascii=True)) >= MAX_LINE:
        # `append` drops a record past MAX_LINE once escaped; the stop matters more than its path.
        record.pop("path", None)
        record.pop("size", None)
    append(path, record)
    if kind == "start":
        prune(env)
    return {}


def run(payload, env=None):
    env = os.environ if env is None else env
    path = journal_path(payload.get("session_id"), env)
    if path is None:
        return {}
    kind, tool = payload.get("hook_event_name"), payload.get("tool_name")
    if kind == "SubagentStart":
        return on_subagent(payload, env, path, "start")
    if kind == "SubagentStop":
        return on_subagent(payload, env, path, "stop")
    if kind != "PreToolUse":
        return {}
    if tool == "Agent":
        return on_spawn(payload, env, path)
    if tool == "Workflow":
        return on_spawn(payload, env, path, workflow=True)
    if tool == "WebSearch":
        return on_search(payload, env, path)
    return {}


def main():
    try:
        payload = json.load(sys.stdin)
        result = run(payload) if isinstance(payload, dict) else {}
    except Exception:
        return
    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
