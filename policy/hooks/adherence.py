#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Adherence events: each recommendation a hook emits, and whether the user followed it (AD-23).

`~/.local/state/agent-harness/adherence.jsonl`, beside `observation.jsonl`. Two kinds of row,
joined by `adherence_id` the way the decision log joins a decision to its outcome, and never
rewritten:

    {"kind": "emitted", "adherence_id": "…", "recommendation": "fresh-session",
     "module": "hooks/usage-feed", "session_id": "…", "turn": 7, "ts": "…",
     "profile_fingerprint": "…", "schema_version": 1}
    {"kind": "response", "adherence_id": "…", "recommendation": "fresh-session",
     "module": "hooks/usage-feed", "session_id": "…", "turn": 7, "outcome": "followed",
     "reason": "SessionEnd", "turns_after": 1, "window": 3, "ts": "…",
     "profile_fingerprint": "…", "schema_version": 1}

Adherence is observation, so an emitting hook's output is the same with recording on, off or
failing: `emit` returns nothing a caller could print and swallows every error. No row holds a
prompt, a tool input, a tool result or the recommendation's own text, so the rate per
recommendation is computed from identifiers alone.

**Whether a recommendation was followed is read from the observation ledger**, the
identifier-only row per hook event `harness_core.observer` writes. `turn` is the emitting hook's
count of the session's prompts, and the response looks at the session's observation rows after
its `turn`-th `UserPromptSubmit`, or after the last one stamped no later than the emission when
that is further on. Followed: one of the recommendation's `follow` events arrives
before the window of `window` further prompts has passed. Not followed: the session's prompt
`turn + window + 1` arrives first. Unknown: neither can be read yet. An emission is answered
`unknown` for good only once it is `UNKNOWN_AFTER` old, with `reason` saying why:
`unobserved` when no row covers its turn, and `window_open` otherwise. The lifecycle's session
start calls `settle`, so a live session writes each answer once it is due.

The observation entry point is an opt-in (`observation.enabled`), and with it off nothing
writes `observation.jsonl`, which left every emission `unobserved`. So the dispatcher also
records the two events a response reads, each prompt and each session end, in
`session-events.jsonl` beside it (`note_event`), in the observation row's shape. A session the
observation ledger holds is read from that ledger alone and any other session from this file,
so a session is never counted twice when both are written.

This module sits beside the hooks rather than in `lib/harness_core` for the reason `decisions.py`
gives: a hook is reached through `~/.claude/hooks/harness` and nothing above that resolves.
"""
import contextlib
import datetime
import importlib.util
import json
import os
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - a platform with no advisory locking
    fcntl = None

SCHEMA_KEY = "schema_version"
SCHEMA_VERSION = 1
FINGERPRINT_KEY = "profile_fingerprint"
LEDGER = "adherence.jsonl"
OBSERVATION = "observation.jsonl"
EVENTS = "session-events.jsonl"
# The events file holds a row per prompt, so it is capped: past this size it is moved to `.1`,
# replacing the one before. A day of rows is all `settle` needs, and this is many days of them.
EVENTS_MAX_BYTES = 2 * 1024 * 1024
OUTCOMES = ("followed", "not_followed", "unknown")
PROMPT = "UserPromptSubmit"
# An emission that neither outcome has reached in a day is not going to be read: a session idle
# that long was left, and the ledger that would say so is either not written or not complete.
UNKNOWN_AFTER = 86400
# How long `settle` polls for the ledger lock before leaving the answers to a later start, and
# how often. Settling is one read and a few appends, so ordinary contention is far shorter.
LOCK_BUDGET = 1.0
LOCK_POLL = 0.01

# Every recommendation a hook emits, the `hooks/<id>` that emits it, the prompts after the
# emitting one within which acting on it counts, and the observation events that count as
# acting. A kind not named here is never recorded, so a typo at a call site writes nothing.
KINDS = {
    # "Finish the task, write the handoff, start a fresh session": finishing and the handoff are
    # a turn or two each, so the session has three more prompts to end. `/clear` and an exit
    # both raise SessionEnd; a compaction does not, and continuing compacted is not following.
    "fresh-session": {"module": "hooks/usage-feed", "window": 3, "follow": ("SessionEnd",)},
    # The stop blocked past the hard threshold: the handoff is written in the turn the block
    # extends, so the session has two more prompts to end before it counts as not followed.
    # A headless run can stop before any prompt is counted, so its emission may be turn 0.
    "fresh-session-handoff": {"module": "hooks/usage-feed", "window": 2,
                              "follow": ("SessionEnd",), "first_turn": 0},
}

_POSTURE = []


def home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("HARNESS_HOME") or env.get("HOME") or Path.home())


def state_dir(env=None):
    return home(env) / ".local" / "state" / "agent-harness"


def path(env=None):
    return state_dir(env) / LEDGER


def observation_path(env=None):
    return state_dir(env) / OBSERVATION


def events_path(env=None):
    return state_dir(env) / EVENTS


def noted():
    """The events `note_event` keeps: a prompt, and every event a recommendation counts as acting."""
    names = {PROMPT}
    for spec in KINDS.values():
        names.update(spec["follow"])
    return names


def note_event(event, session_id, runtime="", env=None, now=None):
    """Record one prompt or session end for the response reading. Never raises; returns nothing.

    Identifiers only, as an observation row: the event's name, the session, the runtime and the
    time. Any other event is not written.
    """
    try:
        if event not in noted() or not isinstance(session_id, str) or not session_id:
            return
        target = events_path(env)
        try:
            if os.path.getsize(str(target)) >= EVENTS_MAX_BYTES:
                os.replace(str(target), str(target) + ".1")
        except OSError:
            pass
        _append({"ts": now_ts(now), "event": event, "session_id": session_id,
                 "runtime": runtime or "", "source": "lifecycle", SCHEMA_KEY: SCHEMA_VERSION},
                target)
    except Exception:
        pass


def observed_rows(env=None):
    """The rows a response is read from: the observation ledger, then the events file.

    A session with any row in the observation ledger is read from it alone, because there the
    observation entry point saw every event; the events file answers for the rest.
    """
    observed = read_rows(observation_path(env))
    covered = set(row.get("session_id") for row in observed)
    target = events_path(env)
    events = read_rows(str(target) + ".1") + read_rows(target)
    return observed + [row for row in events if row.get("session_id") not in covered]


def now_ts(now=None):
    moment = (datetime.datetime.now(datetime.timezone.utc) if now is None
              else datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    """Epoch seconds for a row's `ts`, or None for one this cannot read."""
    try:
        moment = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=datetime.timezone.utc).timestamp()


def profile_fingerprint():
    """The profile in force, from the `posture.py` beside this file, or None; see `decisions.py`."""
    if not _POSTURE:
        location = Path(os.path.realpath(__file__)).parent / "posture.py"
        try:
            spec = importlib.util.spec_from_file_location("harness_adherence_posture", str(location))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            module = None
        _POSTURE.append(module)
    try:
        return _POSTURE[0].fingerprint() if _POSTURE[0] else None
    except Exception:
        return None


def whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _append(row, target):
    """One line, one `write`, to a file only its owner can read."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def emission(recommendation, session_id, turn, now=None):
    """The `emitted` row for one recommendation, or None for a kind or an id this cannot record."""
    spec = KINDS.get(recommendation)
    if spec is None or not isinstance(session_id, str) or not session_id:
        return None
    if not whole(turn) or turn < spec.get("first_turn", 1):
        return None
    return {"kind": "emitted", "adherence_id": uuid.uuid4().hex, "recommendation": recommendation,
            "module": spec["module"], "session_id": session_id, "turn": turn, "ts": now_ts(now),
            FINGERPRINT_KEY: profile_fingerprint(), SCHEMA_KEY: SCHEMA_VERSION}


def emit(recommendation, session_id, turn, env=None, now=None):
    """Record that a hook emitted `recommendation` on the session's prompt `turn`.

    Returns None on every path, and never raises: the emitting hook's output must not depend on
    whether this worked.
    """
    try:
        row = emission(recommendation, session_id, turn, now)
        if row is not None:
            _append(row, path(env))
    except Exception:
        pass


def read_rows(target):
    """Every object row in a JSONL file, in order; a missing file or a torn line is skipped."""
    try:
        with open(str(target), "rb") as handle:
            raw = handle.read()
    except OSError:
        return []
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def prompts_by(ts, session, observed):
    """The session's observed prompts stamped no later than `ts`, or 0 when `ts` is unreadable.

    The feed's own count can fall behind the ledger's, when it skips a prompt it could not lock
    or resets a state file it could not read, and a turn that lags would close the window early.
    The next prompt cannot arrive before the emitting one's hook has run, so none is counted here.
    """
    moment = parse_ts(ts)
    if moment is None:
        return 0
    count = 0
    for item in observed:
        if item.get("session_id") == session and item.get("event") == PROMPT:
            stamped = parse_ts(item.get("ts"))
            if stamped is not None and stamped <= moment:
                count += 1
    return count


def respond(row, observed):
    """`(outcome, reason, turns_after)` for one emission against the session's observation rows.

    `observed` is the observation ledger's rows in file order; only `event`, `session_id` and
    `ts` are read. `turns_after` counts the prompts seen after the emitting one, capped at the window.
    """
    spec = KINDS.get(row.get("recommendation"))
    turn, session = row.get("turn"), row.get("session_id")
    if spec is None or not whole(turn) or turn < spec.get("first_turn", 1) \
            or not isinstance(session, str):
        return "unknown", "unrecognised", 0
    turn = max(turn, prompts_by(row.get("ts"), session, observed))
    prompts, seen = 0, False
    for item in observed:
        if item.get("session_id") != session:
            continue
        seen = True
        event = item.get("event")
        if event == PROMPT:
            prompts += 1
            if prompts > turn + spec["window"]:
                return "not_followed", PROMPT, spec["window"]
        elif prompts >= turn and event in spec["follow"]:
            return "followed", event, prompts - turn
    if prompts < turn or not seen:
        return "unknown", "unobserved", 0
    return "unknown", "window_open", prompts - turn


def responses(rows):
    """The first response to each emission, by `adherence_id`. A second one is a duplicate."""
    out = {}
    for row in rows:
        if row.get("kind") == "response" and isinstance(row.get("adherence_id"), str):
            out.setdefault(row["adherence_id"], row)
    return out


@contextlib.contextmanager
def ledger_lock(target):
    """Yield True while holding an exclusive lock on the ledger at `target`, else False.

    The lock is taken on the ledger itself, so settling creates no file, and a missing ledger
    yields False because there is nothing to answer. It is polled without blocking for at most
    `LOCK_BUDGET` seconds, so a holder that never lets go cannot stall session start; running out
    of budget, or any error, yields False, and the answers wait for a later start rather than
    risk a second row. A platform with no advisory locking yields False too: every supported one
    has it, and an answer never written beats one written twice.
    """
    if fcntl is None:
        yield False
        return
    stream = None
    try:
        stream = open(str(target), "rb")
        deadline = time.monotonic() + LOCK_BUDGET
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(LOCK_POLL)
    except OSError:
        if stream is not None:
            stream.close()
        stream = None
    try:
        yield stream is not None
    finally:
        if stream is not None:
            stream.close()


def settle(env=None, now=None):
    """Append a response for every emission a reading can now answer, and return those rows.

    A followed or not-followed reading is final when it is made. An unknown one is only written
    once the emission is `UNKNOWN_AFTER` old; before that it is left for a later reading. The
    read, the check for an existing answer and the appends run under `ledger_lock`, so two
    sessions starting together still write one response per emission.
    """
    with ledger_lock(path(env)) as held:
        return _settle(env, now) if held else []


def _settle(env, now):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp() if now is None else now
    rows = read_rows(path(env))
    answered = responses(rows)
    observed = observed_rows(env)
    written = []
    for row in rows:
        ident = row.get("adherence_id")
        if row.get("kind") != "emitted" or not isinstance(ident, str) or ident in answered:
            continue
        outcome, reason, after = respond(row, observed)
        if outcome == "unknown":
            emitted_at = parse_ts(row.get("ts"))
            if emitted_at is not None and now - emitted_at < UNKNOWN_AFTER:
                continue
        spec = KINDS.get(row.get("recommendation")) or {}
        answer = {"kind": "response", "adherence_id": ident,
                  "recommendation": row.get("recommendation"), "module": row.get("module"),
                  "session_id": row.get("session_id"), "turn": row.get("turn"),
                  "outcome": outcome, "reason": reason, "turns_after": after,
                  "window": spec.get("window"), "ts": now_ts(now),
                  FINGERPRINT_KEY: row.get(FINGERPRINT_KEY), SCHEMA_KEY: SCHEMA_VERSION}
        _append(answer, path(env))
        answered[ident] = answer
        written.append(answer)
    return written


def rates(rows):
    """Per recommendation: emitted, each outcome, pending, and the rate followed of those judged.

    `rate` is followed over followed plus not followed, and None while neither has happened: an
    unknown is neither, and counting it as either would invent an answer the ledger never gave.
    """
    answered = responses(rows)
    out = {}
    for row in rows:
        if row.get("kind") != "emitted" or not isinstance(row.get("adherence_id"), str):
            continue
        entry = out.setdefault(row.get("recommendation"), dict(
            {"emitted": 0, "pending": 0, "rate": None}, **{name: 0 for name in OUTCOMES}))
        entry["emitted"] += 1
        answer = answered.get(row["adherence_id"])
        outcome = answer.get("outcome") if answer else None
        if outcome in OUTCOMES:
            entry[outcome] += 1
        else:
            entry["pending"] += 1
    for entry in out.values():
        judged = entry["followed"] + entry["not_followed"]
        entry["rate"] = entry["followed"] / float(judged) if judged else None
    return out
