#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Feed measured spend back to the orchestrator: one turn line, one line per finished subagent.

Four events, all on the parent thread. `SubagentStart` and `SubagentStop` record when an agent
began and what it cost. `PostToolUse` on `Agent` reports a synchronous return the moment it lands,
and says how many agents are running when that is past the posture's width. `UserPromptSubmit`
reports the turn and every subagent that finished since the previous prompt — which is how a
background spawn, whose `PostToolUse` fires at launch with no totals, is reported at all.

Seven facts shape the whole file:

- **A tool response's token figure describes only the subagent's last response.** Measured on a
  live return: `tool_response.usage.output_tokens` said 3,143 against 10,575 actually spent over
  nineteen responses. The real figure is summed from the subagent's own transcript, once per
  message id at the field-wise maximum, by `usage-log.py`'s per-agent row function and not by a
  second copy of that logic here.
- **These hooks run concurrently, as separate processes.** So state is split in two. A subagent
  event is one line under 4 KB appended to `<session>.events.jsonl` through an `O_APPEND`
  descriptor, which no handler ever rewrites and which therefore cannot lose a record. Everything
  the main thread read-modify-writes lives in `<session>.json`, under an exclusive `flock` on
  `<session>.lock` with a bounded wait. No lock, no write, and nothing emitted.
- **Nothing slow happens under the lock.** `SubagentStart` and `SubagentStop` never take it at
  all, and the two main-thread events sum a subagent's transcript before acquiring it. A stop
  that took four seconds to read while holding the lock would starve the prompt waiting behind
  it, and that prompt would silently lose its line.
- **A record that cannot be computed is still a record.** A stop is journalled in a `finally`,
  with null totals when the sum failed and `partial` when a budget cut it short. An agent whose
  stop went missing would otherwise count as running for the rest of the session and the width
  line would fire falsely forever; a start with no stop also decays after three hours.
- **A subagent's spend is summed when it is reported, not when it stops.** Measured live: a
  `SubagentStop` fired while the agent's transcript still held nothing but `user` records, the
  stop was journalled with null totals, and the one line the session ever printed for that agent
  said `spend unknown` — while replaying the same payload a moment later yielded 297. So a stop
  that carries no figure, or one a response was still being written into, is summed again at the
  moment it is about to be named, before the lock, inside one wall-clock budget shared by every
  agent that event reports. The return also waits a bounded moment for the last response to
  finish being written, says `(so far)` when it never does, and the stop the journal brings later
  raises the session totals without the agent being announced a second time.
- **A line is worth saying once.** `spend unknown` names its agent and is said once, because no
  later event can put a figure on it and a session that repeats it teaches the orchestrator to
  skip the feed. What the figures measure is said once too, before the first of them. An agent
  resumed with a follow-up message, on the other hand, stops once per round against one agent id,
  and each of those rounds is a completion the feed owes a line — cumulative, because one
  transcript covers them all. Whether such a round has landed is not a question the transcript's
  shape can answer: it ends on the previous round's finished response either way. So a later
  round is settled by its figure passing the one already reported, and is re-summed at each
  event, silently, until it does.
- **Reads are bounded everywhere.** The parent transcript is read from a saved offset, trusted
  only while the inode and the hash of the first record still match, and from 8 MiB before the
  end on a cold start. The journal is read from its own saved offset, so a long session's totals
  can only grow. A subagent's transcript is capped by bytes and by the clock.

No budget, threshold, model name or role name lives here: every number comes from the cost
table, every switch from `switches.turn_feed`, `switches.nudge_at`, `switches.session_nudge_at`
and `switches.max_parallel`.
A variant that sets none of them feeds nothing. Any failure at all emits nothing and exits 0,
and no line the feed emits is ever a decision. The fresh-session nudge is a recommendation, so
saying it also records an adherence event, which `adherence.py` later answers.
"""
import errno
import hashlib
import importlib.util
import json
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
PREFIX = "usage-feed: "
# A journal line is one `os.write`. Far under PIPE_BUF, which is what makes an append atomic.
MAX_LINE = 4096
# How many message ids stay open for a later line to raise, newest kept and oldest evicted. One
# API response is written as several lines repeating its id, and a response whose id is evicted
# before its final, largest figure arrives would be counted twice; a tail this long is far past
# that window.
OPEN_TAIL = 64
MAX_LISTED = 5
# Stops waiting for a line, and ids whose spend is already in the totals. Both bound what one
# session's state file can grow to, and both are far past any real fan-out.
MAX_PENDING = 200
MAX_COUNTED = 1000
# A cold start reads this much of the transcript's tail, not the whole file.
COLD_TAIL = 8 * 1024 * 1024
READ_BUDGET = 3.0
# What one subagent's transcript may cost a hook that has ten seconds for everything.
AGENT_BUDGET = 4.0
AGENT_BYTES = 8 * 1024 * 1024
# How long a synchronous return may wait for the subagent's last response to finish being
# written, and how often it looks. Both are well inside the hook's timeout, and the wait is over
# a 256 KB tail rather than the transcript.
SETTLE_BUDGET = 1.0
SETTLE_STEP = 0.15
SETTLE_TAIL = 256 * 1024
# What summing at report time may cost one event, shared by every agent that event names. With
# several agents pending, the ones it does not reach keep their place and are summed next time.
REPORT_BUDGET = 4.0
# The least of that budget one agent is attempted with. Under it the agent waits for the next
# event rather than being summed in a sliver of time and reported as having no figure.
REPORT_SLICE = 0.5
# How many events may try to sum one stop that still holds no response. A transcript that never
# gains one is a fact, not a race, and retrying it forever would spend the budget on nothing.
UNSUMMED_TRIES = 3
LOCK_WAIT = 2.0
# A start with no stop this old is not running; something ended it without saying so.
RUNNING_TTL = 3 * 3600
FEED_TTL = 14 * 86400
PRUNE_EVERY = 86400
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
# What an agent type may look like before it is allowed into injected text or the journal.
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
UNNAMED = "other"
MODES = ("off", "thresholds", "every-turn")


def sibling(name):
    """A module beside this hook, or None. A feed never fails loudly over an import."""
    try:
        spec = importlib.util.spec_from_file_location(
            "harness_" + name.replace("-", "_"), str(HOOKS / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _open(path):
    """The one place the parent transcript is opened, so a test can measure what a read costs."""
    return open(str(path), "rb")


def plural(number, noun):
    """`1 tool call`, `15 tool calls`, `135,000 output tokens`. Every emitted number reads."""
    return "{:,}".format(number) + " " + noun + ("" if number == 1 else "s")


# --------------------------------------------------------------------------- paths


def home(env):
    return Path(env.get("HARNESS_HOME") or env.get("HOME") or Path.home())


def feed_dir(env):
    return home(env) / ".local" / "state" / "agent-harness" / "feed"


def paths(session_id, env):
    """`(state, journal, lock)` for one session, or None when the id is not a name we would write."""
    if not isinstance(session_id, str) or not IDENTIFIER.match(session_id):
        return None
    directory = feed_dir(env)
    return (directory / (session_id + ".json"), directory / (session_id + ".events.jsonl"),
            directory / (session_id + ".lock"))


def ensure_dir(directory):
    try:
        if not directory.is_dir():
            os.makedirs(str(directory), 0o700)
        return True
    except OSError:
        return directory.is_dir()


# --------------------------------------------------------------------------- the journal


def journal_append(path, record):
    """One line, one `os.write`, on an `O_APPEND` descriptor. Never read-modify-write."""
    data = (json.dumps(record, ensure_ascii=True) + "\n").encode("utf-8")
    if len(data) > MAX_LINE or not ensure_dir(path.parent):
        return False
    try:
        handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return False
    try:
        written = os.write(handle, data)
        if written == len(data):
            return True
        # A short write leaves a fragment. Terminating it is all that is owed: the reader drops
        # an unparseable line, and the next record then starts on a line of its own.
        if not data[:written].endswith(b"\n"):
            os.write(handle, b"\n")
        return False
    except OSError:
        return False
    finally:
        os.close(handle)


# --------------------------------------------------------------------------- the reader's state


def new_state():
    return {"version": 3, "offset": 0, "size": 0, "inode": None, "head": None, "partial": False,
            "session": {"output": 0, "tool_calls": 0},
            "turn": {"output": 0, "tool_calls": 0},
            "previous_turn": {"output": 0, "tool_calls": 0},
            "subagents": {"output": 0, "tool_calls": 0, "count": 0, "unknown": 0},
            "journal_offset": 0, "running": {}, "pending": [], "counted": [],
            "figures": {}, "unsummed": {}, "open": [], "pruned": 0, "said_turn": None,
            "rounds": {}, "said_unknown": [], "said_measure": False,
            "context": None, "said_nudge": [], "turns": 0}


def load_state(path):
    """The session's state, or a fresh one. A file we cannot read is a file we start over from."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return new_state()
    if not isinstance(data, dict) or data.get("version") != 3:
        return new_state()
    state = new_state()
    state.update(data)
    for key in ("session", "turn", "previous_turn", "subagents"):
        value = state.get(key)
        state[key] = value if isinstance(value, dict) else new_state()[key]
        for name, blank in new_state()[key].items():
            if not isinstance(state[key].get(name), int) or isinstance(state[key].get(name), bool):
                state[key][name] = blank
    for key in ("offset", "size", "journal_offset", "pruned", "turns"):
        if not isinstance(state.get(key), int) or isinstance(state.get(key), bool):
            state[key] = 0
    if not isinstance(state.get("running"), dict):
        state["running"] = {}
    figures = state.get("figures")
    state["figures"] = figures if isinstance(figures, dict) else {}
    for agent, pair in list(state["figures"].items()):
        if not (isinstance(pair, list) and len(pair) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in pair)):
            del state["figures"][agent]
    unsummed = state.get("unsummed")
    state["unsummed"] = unsummed if isinstance(unsummed, dict) else {}
    for agent, entry in list(state["unsummed"].items()):
        # `[journalled path, tries so far]`. A shape this does not recognise is a retry it
        # cannot make, and dropping it only leaves the agent counted unknown.
        if not (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str)
                and isinstance(entry[1], int) and not isinstance(entry[1], bool)):
            del state["unsummed"][agent]
    rounds = state.get("rounds")
    state["rounds"] = rounds if isinstance(rounds, dict) else {}
    for agent, value in list(state["rounds"].items()):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            del state["rounds"][agent]
    state["said_measure"] = bool(state.get("said_measure"))
    for key in ("pending", "counted", "open", "said_unknown", "said_nudge"):
        if not isinstance(state.get(key), list):
            state[key] = []
    state["said_unknown"] = [v for v in state["said_unknown"] if isinstance(v, str)]
    state["said_nudge"] = [v for v in state["said_nudge"]
                           if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    size = state.get("context")
    if not (isinstance(size, int) and not isinstance(size, bool) and size > 0):
        state["context"] = None
    return state


def save_state(path, state):
    """Atomic and private. Called before the slow read as well as after it."""
    state["pending"] = state.get("pending", [])[-MAX_PENDING:]
    state["counted"] = state.get("counted", [])[-MAX_COUNTED:]
    state["said_unknown"] = state.get("said_unknown", [])[-MAX_COUNTED:]
    for stale in list(state.get("rounds", {}))[:max(0, len(state.get("rounds", {}))
                                                    - MAX_COUNTED)]:
        del state["rounds"][stale]
    for stale in list(state.get("unsummed", {}))[:max(0, len(state.get("unsummed", {}))
                                                      - MAX_PENDING)]:
        del state["unsummed"][stale]
    if not ensure_dir(path.parent):
        return
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(handle, json.dumps(state).encode("utf-8"))
        finally:
            os.close(handle)
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


class Lock(object):
    """An exclusive `flock` with a bounded wait. Unavailable or contended means emit nothing.

    Every writer of the reader's state is a hook process with ten seconds for everything, and
    nothing slow is ever done while this is held. Waiting longer than a couple of seconds for a
    figure the next prompt will recompute anyway is worse than skipping the line.
    """

    def __init__(self, path, wait=LOCK_WAIT):
        self.path = path
        self.wait = wait
        self.handle = None

    def __enter__(self):
        if fcntl is None or not ensure_dir(self.path.parent):
            return False
        try:
            self.handle = os.open(str(self.path), os.O_WRONLY | os.O_CREAT, 0o600)
        except OSError:
            return False
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # A lock file is never written, so without this its age is its creation and the
                # sweep below would eventually delete the file a live session is holding.
                try:
                    os.utime(str(self.path), None)
                except OSError:
                    pass
                return True
            except (IOError, OSError) as exc:
                if getattr(exc, "errno", None) not in (errno.EACCES, errno.EAGAIN):
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        os.close(self.handle)
        self.handle = None
        return False

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                os.close(self.handle)
                self.handle = None
        return False


def prune(directory, state, keep, now=None):
    """Once a day at most, drop the files of sessions nothing has touched in a fortnight.

    A session's three files go together or not at all, judged by the newest of them: a state
    file rewritten every prompt beside a journal nobody appended to for a month is one live
    session. `keep` is this session, which is never a candidate however old its files look.
    """
    now = time.time() if now is None else now
    if now - state.get("pruned", 0) < PRUNE_EVERY:
        return
    state["pruned"] = int(now)
    sessions = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        stem = entry.name.split(".", 1)[0]
        if stem == keep:
            continue
        try:
            if not entry.is_file():
                continue
            sessions.setdefault(stem, []).append((entry, entry.stat().st_mtime))
        except OSError:
            continue
    for files in sessions.values():
        if now - max(mtime for _, mtime in files) <= FEED_TTL:
            continue
        for entry, _ in files:
            try:
                entry.unlink()
            except OSError:
                continue


# --------------------------------------------------------------------------- the parent transcript


def _slot(state, mid):
    """The open maximum for one message id, newest last, oldest evicted past the tail."""
    entries = state["open"]
    if mid:
        for item in entries:
            if item[0] == mid:
                return item
    item = [mid, 0, []]
    entries.append(item)
    del entries[:max(0, len(entries) - OPEN_TAIL)]
    return item


def _whole(value):
    """A token figure as a whole number, or 0. Nothing a transcript can hold raises out of here.

    `OverflowError` is the one that matters: JSON admits `1e400`, Python reads it as an infinity,
    and `int()` on that raises. A record is read once, the offset past it is saved, and an
    uncaught raise there would silence the feed for the rest of the session.
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


#: What a response read, across the three fields it is reported in. They do not overlap:
#: `input_tokens` is what was sent uncached, and the other two are the prefix read from the
#: cache and the prefix written into it, so the context is their sum and not any one of them.
CONTEXT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _context(usage):
    """One response's context size. Zero when the fields are absent, which is not a size."""
    total = 0
    for key in CONTEXT_KEYS:
        total += max(0, _whole(usage.get(key)))
    return total


def _apply(state, entry):
    """One transcript line against the running totals. Sidechain lines belong to a subagent."""
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return
    kind = entry.get("type")
    message = entry.get("message")
    message = message if isinstance(message, dict) else {}
    if kind == "user":
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
            return
        if entry.get("isMeta") or entry.get("isCompactSummary"):
            return
        # A real prompt closes the turn. An empty one is not a turn worth remembering, so it
        # never displaces the last turn that spent anything.
        if state["turn"]["output"] or state["turn"]["tool_calls"]:
            state["previous_turn"] = state["turn"]
        state["turn"] = {"output": 0, "tool_calls": 0}
        return
    if kind != "assistant":
        return
    mid = message.get("id") if isinstance(message.get("id"), str) else ""
    usage = message.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    context = _context(usage)
    if context:
        # What the next response will re-read, as of the newest response on file. Not summed
        # and not a maximum: a context that shrank because the session was compacted has
        # shrunk, and the older, larger figure describes a session that no longer exists.
        state["context"] = context
    slot = _slot(state, mid)
    output = _whole(usage.get("output_tokens"))
    if output > slot[1]:
        # Only the rise is added, so a partial streaming count followed by the true figure is
        # one message counted once at its largest.
        for name in ("session", "turn"):
            state[name]["output"] += output - slot[1]
        slot[1] = output
    for index, block in enumerate(message.get("content") or []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        key = block.get("id") or str(block.get("apiBlockIndex", index))
        if key in slot[2]:
            continue
        slot[2].append(key)
        for name in ("session", "turn"):
            state[name]["tool_calls"] += 1


def _align(handle, offset, size):
    """The next line start at or after `offset`. A saved offset mid-line re-aligns forward."""
    if offset <= 0:
        return 0
    if offset >= size:
        return size
    handle.seek(offset - 1)
    if handle.read(1) == b"\n":
        return offset
    handle.seek(offset)
    raw = handle.readline()
    return size if not raw.endswith(b"\n") else offset + len(raw)


def _identity(handle):
    """`(inode, head hash, size)`. A replaced transcript of any size fails this, not just a shorter one.

    The hash covers the transcript's first line rather than a fixed 512 bytes, because a young
    file shorter than that is still being appended to inside the window a fixed slice would
    cover, and its identity would change under it every turn. A first record is written once.
    """
    stat = os.fstat(handle.fileno())
    handle.seek(0)
    head = handle.read(512)
    cut = head.find(b"\n")
    if cut >= 0:
        head = head[:cut]
    return stat.st_ino, hashlib.sha256(head).hexdigest()[:16], stat.st_size


def advance(state, transcript, save=None, budget=READ_BUDGET):
    """Read from the settled offset to EOF, bounded by `COLD_TAIL` and by the clock.

    `save` is called once the offset and the file identity are settled and before a single line
    is parsed: a hook killed at its timeout must not leave the next one to repeat the same work
    forever. `timed_out` on the returned state says the read gave up, and the caller stays quiet.
    """
    path = Path(os.path.expanduser(str(transcript)))
    try:
        handle = _open(path)
    except OSError:
        if save:
            save(state)
        return state
    with handle:
        try:
            inode, head, size = _identity(handle)
        except OSError:
            return state
        if (state.get("inode") != inode or state.get("head") != head
                or state["offset"] > size or size < state["size"]):
            # Compaction, a rotation, a replacement — of any size. What came before is unknowable
            # about the transcript; what the journal recorded is still true and stays, and so is
            # what has already been said. A reset that really did shrink the context re-arms the
            # session nudge through the size itself, not by forgetting the line was fed.
            seen = state.get("inode") is not None
            kept = {key: state[key] for key in
                    ("journal_offset", "running", "pending", "counted", "figures", "unsummed",
                     "subagents", "pruned", "rounds", "said_unknown", "said_nudge", "turns")}
            state = dict(new_state(), **kept)
            state["inode"], state["head"] = inode, head
            state["offset"] = max(0, size - COLD_TAIL)
            state["partial"] = seen or state["offset"] > 0
        try:
            state["offset"] = _align(handle, state["offset"], size)
        except OSError:
            return state
        state["size"] = size
        if save:
            save(state)
        if state["offset"] >= size:
            return state
        position = state["offset"]
        deadline = time.monotonic() + budget
        handle.seek(position)
        for counted, raw in enumerate(handle):
            # A line still being written is not a line; leaving it unconsumed is what makes the
            # next read pick it up whole.
            if not raw.endswith(b"\n"):
                break
            position += len(raw)
            try:
                _apply(state, json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                pass
            if not counted % 256 and time.monotonic() > deadline:
                state["partial"] = True
                state["timed_out"] = True
                break
    state["offset"] = position
    return state


# --------------------------------------------------------------------------- subagents


def agent_transcript(transcript_path, session_id, agent_id):
    """`<dirname(transcript)>/<session>/subagents/agent-<id>.jsonl`, or None.

    A Workflow-tool agent sits one level deeper, under `subagents/workflows/wf_*/`. Both places
    are named, rather than walked: a recursive search of a session's whole subagent tree is
    unbounded work for a question with two possible answers.
    """
    if not (isinstance(agent_id, str) and IDENTIFIER.match(agent_id)):
        return None
    if not (isinstance(session_id, str) and IDENTIFIER.match(session_id)):
        return None
    if not transcript_path:
        return None
    base = Path(os.path.expanduser(str(transcript_path))).parent / session_id / "subagents"
    name = "agent-" + agent_id + ".jsonl"
    try:
        if (base / name).is_file():
            return base / name
        found = sorted(base.glob("workflows/*/" + name))
    except OSError:
        return None
    return found[0] if found else None


def readable(path):
    """Whether a subagent transcript is there to be summed at all.

    The one thing that separates `spend unknown` from `spend not yet recorded`: a file that is
    not there is never going to yield a figure, while a file that is there and holds no response
    yet is a flush this hook fired inside of.
    """
    if not path:
        return False
    try:
        return os.path.isfile(str(path)) and os.access(str(path), os.R_OK)
    except OSError:
        return False


def redact(path, env):
    """A subagent transcript path fit to journal: the home prefix becomes `~`, or nothing at all.

    The journal is a record of counts, and a path under the user's home carries their account
    name. A path that is not under this home is not written down; the reader re-derives it from
    the session id instead, which is what `agent_transcript` already does.
    """
    if not path:
        return ""
    text, root = str(path), str(home(env))
    return "~" + text[len(root):] if text.startswith(root + os.sep) else ""


def expand(value, env, agent_id):
    """The path a journalled `~/…` names, when it still names this agent's own transcript.

    The name is checked against the record's own id so that a journal line, whatever wrote it,
    can only ever point this hook at the file it claims to be about.
    """
    if not (isinstance(value, str) and value.startswith("~/")
            and isinstance(agent_id, str) and IDENTIFIER.match(agent_id)):
        return None
    path = home(env) / value[2:]
    return path if path.name == "agent-" + agent_id + ".jsonl" else None


def agent_totals(path, budget=None):
    """`{output, tool_calls, agent_type, partial}` from the subagent's own transcript, or None.

    The sum is `usage-log.py`'s per-agent row function, reused rather than reimplemented: it is
    the code that already counts one message id once at its largest figure, which is the only
    way past the last-response figure a tool response reports. It is given a byte cap and a
    clock here, because this runs inside a hook timeout and a very large agent would otherwise
    take the whole process down with it.
    """
    module = sibling("usage-log")
    if module is None or not path:
        return None
    budget = AGENT_BUDGET if budget is None else budget
    try:
        row = module._agent_row(Path(path), budget=budget, max_bytes=AGENT_BYTES)
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    return {"output": int(row.get("output") or 0), "tool_calls": int(row.get("tool_calls") or 0),
            "agent_type": agent_name(row.get("agent_type")), "partial": bool(row.get("partial"))}


def tail_state(path, max_bytes=SETTLE_TAIL):
    """`(a response is there, it has finished being written)` from the transcript's tail.

    Claude Code writes one API response as several records repeating its message id. The early
    ones carry `stop_reason: null` and a partial streaming `output_tokens`; the record that ends
    the response carries a reason. Counted over 1,418 message ids in real subagent transcripts,
    the record carrying a reason held that id's largest figure every single time, and the files
    whose last record carried none were runs that had been interrupted — so a reason on the last
    record is the response being complete, and its absence is a response still arriving.

    A record with no `stop_reason` key at all came from a writer whose streaming this cannot
    judge, and is taken as it stands rather than waited on. Only the tail is read, so asking
    costs the same on a large transcript as on a small one.

    The first half of the pair is the one the flush race turns on: a transcript holding only
    `user` and `attachment` records has no response to judge, which is not the same fact as a
    response that has finished. An unreadable file reports neither — its caller asks `readable`.
    """
    try:
        with open(str(path), "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            lines = handle.read().splitlines()
    except OSError:
        return False, True
    for raw in reversed(lines):
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue  # A line still being written is not a line.
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        message = message if isinstance(message, dict) else {}
        if "stop_reason" not in message:
            return True, True
        return True, bool(message.get("stop_reason"))
    return False, True


def tail_settled(path, max_bytes=SETTLE_TAIL):
    """Whether a subagent's transcript ends on a response that has finished being written."""
    return tail_state(path, max_bytes)[1]


def settled_totals(path, budget=None, step=None, clock=None, sleep=None, read_budget=None):
    """`agent_totals` with `settled`, after a bounded wait for a finished response to land.

    Only the cheap tail is polled while waiting; the transcript is summed once, afterwards, so
    the whole wait costs one read and at most `budget` seconds however many times it looked. A
    figure that stops moving is not taken as the end of the response — a partial streaming count
    can repeat — so the response ending is the only thing that stops the wait early, and a wait
    that runs out leaves the caller a figure to mark `(so far)`.

    No response at all yet is waited on the same way, and is what the wait is mostly for: a stop
    fires while the agent's transcript still holds only the records the parent wrote into it.
    A transcript that is not there is not waited on — no wait makes a missing file appear — and
    a readable one that never gains a response returns None, which its caller reports as spend
    not yet recorded rather than as spend unknown.
    """
    if not readable(path):
        return None
    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    budget = SETTLE_BUDGET if budget is None else budget
    step = SETTLE_STEP if step is None else step
    seen, settled = tail_state(path)
    deadline = clock() + budget
    while not (seen and settled) and clock() + step <= deadline:
        sleep(step)
        seen, settled = tail_state(path)
    totals = agent_totals(path, budget=read_budget)
    if totals is not None:
        totals["settled"] = settled
    return totals


#: What one agent's report-time sum came to. A totals dict is a figure; these two are not.
PENDING = "pending"
ABSENT = "absent"


def settle_many(items, budget=None, clock=None, sleep=None):
    """`{agent id: totals | PENDING | ABSENT}` for as many of `items` as one budget allows.

    This is the whole cost of summing at report time, and it is spent once per event rather than
    once per agent: a turn that retires eight subagents must not wait eight times. Each agent is
    given what is left of the budget, and an agent the budget never reaches is simply absent from
    the result — its stop keeps its place and is summed at the next event instead of this one
    blowing the hook's timeout. Called before the lock is taken, never under it.
    """
    clock = time.monotonic if clock is None else clock
    budget = REPORT_BUDGET if budget is None else budget
    deadline = clock() + budget
    out = {}
    for agent_id, path in items:
        if agent_id in out:
            continue
        if not readable(path):
            out[agent_id] = ABSENT
            continue
        left = deadline - clock()
        # An agent is given at most half of what is left to wait and half to read, so the whole
        # pass lands inside the budget whatever order the agents came in. Below the floor it is
        # not attempted at all: a sum cut off after a tenth of a second would report a figure
        # that exists as one that has not landed yet, which is worse than reporting it later.
        if left < REPORT_SLICE and out:
            break
        slice_ = max(left, REPORT_SLICE) / 2.0
        totals = settled_totals(path, budget=min(SETTLE_BUDGET, slice_), clock=clock, sleep=sleep,
                                read_budget=min(AGENT_BUDGET, slice_))
        out[agent_id] = PENDING if totals is None else totals
    return out


def agent_name(value, fallback="unknown"):
    """An agent type fit to inject and to journal. Free text becomes `other`, never itself."""
    if value is None or value == "":
        return fallback
    if isinstance(value, str) and AGENT_NAME.match(value):
        return value
    return UNNAMED


# --------------------------------------------------------------------------- the journal, ingested


def journal_records(journal_file, offset):
    """Every whole record past `offset`, read and nothing else.

    `ingest` folds the same bytes into the locked state. This is the read that happens before
    the lock, so the event knows which stops it is about to name and can sum them while nobody
    is waiting on it.
    """
    out = []
    try:
        handle = open(str(journal_file), "rb")
    except OSError:
        return out
    with handle:
        handle.seek(offset if offset > 0 else 0)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            try:
                record = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                out.append(record)
    return out


def internal(state, record):
    """Whether a stop is Claude Code's own end-of-turn agent rather than a subagent anyone spawned.

    Such a stop fires within seconds of the main session's `Stop`, with no `SubagentStart` before
    it, no agent type and no transcript written anywhere, so no lookup can ever give it a figure.
    Reported, it read `unknown finished, spend unknown` once a turn and was most of the feed's
    unknown lines. The stop journals the first two facts as `internal`; the third is that no start
    was seen for it, which keeps a spawned agent whose transcript went missing reported.
    """
    return bool(record.get("internal")) and record.get("id") not in state["running"]


def needs_sum(record):
    """Whether a journalled stop's figure is one to take as final.

    Two stops are not: the one whose sum could not be made when it fired — the flush race, where
    no response had been written yet — and the one whose figure was read out of a response still
    being written or out of a transcript a cap cut short. Both are summed again when the agent
    is reported.
    """
    if record.get("t") != "stop":
        return False
    return not record.get("summed") or bool(record.get("partial"))


def record_path(record, payload, env):
    """Where a journalled stop's transcript is: what it wrote down, or where it would be."""
    agent_id = record.get("id")
    return expand(record.get("path"), env, agent_id) or agent_transcript(
        payload.get("transcript_path"), payload.get("session_id"), agent_id)


def to_settle(state, journal_file, payload, env, first=None):
    """`[(agent id, path)]` worth summing before this event takes the lock, `first` at the head.

    Three sources, in the order they are worth the budget: the agent this event is returning,
    the stops of earlier events that are still without a figure, and the stops this event is
    about to ingest. A retry that has had its tries is dropped rather than asked again.
    """
    items = [first] if first and first[0] else []
    seen = {agent for agent, _ in items}
    for record in state.get("pending") or []:
        agent_id = record.get("id")
        if (not record.get("awaiting") or agent_id in seen
                or record.get("tries", 0) >= UNSUMMED_TRIES):
            continue
        seen.add(agent_id)
        items.append((agent_id, record_path(record, payload, env)))
    for agent_id, entry in (state.get("unsummed") or {}).items():
        if agent_id in seen or entry[1] >= UNSUMMED_TRIES:
            continue
        seen.add(agent_id)
        items.append((agent_id, expand(entry[0], env, agent_id) or agent_transcript(
            payload.get("transcript_path"), payload.get("session_id"), agent_id)))
    rounds = dict(state.get("rounds") or {})
    seen_running = {"running": dict(state.get("running") or {})}
    for record in journal_records(journal_file, state.get("journal_offset", 0)):
        agent_id = record["id"]
        if record.get("t") == "start":
            seen_running["running"][agent_id] = 0
        if internal(seen_running, record):
            continue
        repeat = False
        if record.get("t") == "stop":
            rounds[agent_id] = rounds.get(agent_id, 0) + 1
            repeat = rounds[agent_id] > 1
        if agent_id in seen or not (needs_sum(record) or repeat):
            continue
        seen.add(agent_id)
        items.append((agent_id, record_path(record, payload, env)))
    return items


def settle_before_lock(state_file, journal_file, payload, env, first=None):
    """The whole of the slow work one main-thread event does, done with no lock held."""
    return settle_many(to_settle(load_state(state_file), journal_file, payload, env, first))


def apply_settled(record, outcome):
    """One report-time sum onto the stop it belongs to. A figure replaces a figure; nothing else
    takes one away — a journalled partial is better than no number at all."""
    if isinstance(outcome, dict):
        record["output"], record["tool_calls"] = outcome["output"], outcome["tool_calls"]
        record["partial"] = bool(outcome.get("partial"))
        record["so_far"] = not outcome.get("settled", True)
        record["summed"] = True
        record.pop("not_yet", None)
        return record
    if record.get("output") is None and record.get("tool_calls") is None:
        # A transcript that is not there is the only outcome that is final. Readable and still
        # holding no response, and an agent the budget never reached, are both worth asking again.
        record["not_yet"] = outcome != ABSENT
    return record


def ingest(state, journal_file, resolved=None):
    """Fold the journal's new bytes into the locked state: running, pending and the totals.

    Only new bytes, from a saved offset, because a session long enough to outgrow one read is
    exactly the session whose totals must not start going down. A half-written last line is left
    unconsumed and read whole next time.

    `resolved` is what `settle_before_lock` summed for this event. A stop that needed summing
    carries that figure into the totals and into the line; one that is still without a figure is
    counted unknown and remembered, so a later event can reconcile it without naming it twice.
    """
    resolved = resolved or {}
    try:
        size = journal_file.stat().st_size
        handle = open(str(journal_file), "rb")
    except OSError:
        return state
    offset = state["journal_offset"]
    if offset > size:
        # A journal replaced under us: re-read it rather than trust an offset into another file.
        offset = 0
    position = offset
    with handle:
        handle.seek(offset)
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            position += len(raw)
            try:
                record = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                continue
            agent_id = record["id"]
            if record.get("t") == "start":
                state["running"][agent_id] = int(record.get("at") or 0)
                continue
            if record.get("t") != "stop" or internal(state, record):
                continue
            state["running"].pop(agent_id, None)
            round_number = bump_round(state, agent_id)
            # A later round's stop is never taken at the figure it was journalled with. The
            # transcript it was read from still ends on the previous round's finished response,
            # so `summed` says final about a round whose own responses are not on disk yet.
            if needs_sum(record) or round_number > 1:
                apply_settled(record, resolved.get(agent_id))
            if agent_id in state["counted"]:
                if agent_id in state["unsummed"] and record.get("output") is not None:
                    # Announced with no figure, and the journal brought one: the totals take it
                    # and the agent is not named again.
                    resolve_unknown(state, agent_id, figures_of(record))
                    del state["unsummed"][agent_id]
                elif round_number < 2:
                    reconcile(state, record)
                if round_number > 1:
                    open_round(state, record, round_number)
                continue
            if agent_id in (state.get("said_unknown") or []):
                # The reader's record of this agent was lost, not the fact of it: it has been
                # counted once and named once, and neither is owed a second time.
                state["counted"].append(agent_id)
                if has_figure(record):
                    resolve_unknown(state, agent_id, figures_of(record))
                continue
            state["counted"].append(agent_id)
            count(state, record)
            if record.get("not_yet"):
                remember_unsummed(state, agent_id, record.get("path"))
            state["pending"].append(record)
    state["journal_offset"] = position
    return state


def figures_of(record):
    """`[output, tool_calls]` as whole numbers, whatever the record put there."""
    out = []
    for key in ("output", "tool_calls"):
        try:
            out.append(int(record.get(key) or 0))
        except (TypeError, ValueError):
            out.append(0)
    return out


def risen(record):
    """Whether a later round's cumulative figure has passed the one already reported.

    It is the only thing that says a resumed agent's round is on disk. The sum covers every
    round the agent has run, and the transcript ends on a finished response either way, so a
    total that has not moved is a round whose responses have not been flushed yet.
    """
    return has_figure(record) and figures_of(record)[0] > record.get("floor", 0)


def open_round(state, record, round_number):
    """One completion of a resumed agent: kept until its own spend has landed, then named.

    Until it has, the record is re-summed at every event and nothing at all is said about it —
    a line repeating the previous round's figure would be worse than a line a prompt later.
    """
    record["round"] = round_number
    record["floor"] = (state["figures"].get(record.get("id")) or [0, 0])[0]
    record["tries"] = 0
    if risen(record):
        reconcile(state, record)
    else:
        record["awaiting"] = True
    state["pending"].append(record)


def reconcile_rounds(state, resolved):
    """Fold this event's sums into the later rounds whose own spend had not landed yet.

    A round is accepted the moment its figure passes the one already reported, and is named from
    the pending list like any other. A transcript that has gone, and a round that has had its
    tries, are dropped: nothing was ever said about either, so nothing has to be taken back.
    """
    for record in list(state.get("pending") or []):
        if not record.get("awaiting"):
            continue
        outcome = resolved.get(record.get("id"))
        if isinstance(outcome, dict) and outcome["output"] > record.get("floor", 0):
            apply_settled(record, outcome)
            record.pop("awaiting", None)
            reconcile(state, record)
        elif outcome == ABSENT or record.get("tries", 0) >= UNSUMMED_TRIES:
            state["pending"].remove(record)
        elif outcome is not None:
            record["tries"] = record.get("tries", 0) + 1


def has_figure(record):
    """Whether a stop carries a number at all. Null counts are a sum that could not be made."""
    return not (record.get("output") is None and record.get("tool_calls") is None)


def bump_round(state, agent_id):
    """Which completion of this agent a journalled stop is, counting from one.

    An agent resumed with a follow-up message stops once per round, against one agent id and one
    transcript, so every round after the first was folded into the totals and never named: only
    its first completion fed a line. The stop count is what tells a resumed round apart from the
    settled copy of a round already reported — that one is still the same, first, stop.
    """
    rounds = state.setdefault("rounds", {})
    number = rounds.get(agent_id)
    number = number + 1 if isinstance(number, int) and not isinstance(number, bool) else 1
    rounds[agent_id] = number
    return number


def count(state, record):
    """One finished agent against the session's subagent totals, exactly once."""
    totals = state["subagents"]
    totals["count"] += 1
    if record.get("output") is None and record.get("tool_calls") is None:
        totals["unknown"] += 1
        return
    figures = figures_of(record)
    totals["output"] += figures[0]
    totals["tool_calls"] += figures[1]
    agent_id = record.get("id")
    if isinstance(agent_id, str):
        state["figures"][agent_id] = figures
        for stale in list(state["figures"])[:max(0, len(state["figures"]) - MAX_COUNTED)]:
            del state["figures"][stale]


def reconcile(state, record):
    """Raise the totals when a later, settled figure for an already-reported agent is larger.

    A synchronous return is reported at the moment it lands, which may be before the subagent's
    last response was fully written. The journal's stop, computed later, is the settled figure:
    saying the agent's line again would cost the orchestrator context for a number it already
    has, so only the difference is added, and the session total is therefore never below the sum
    of the final figures.
    """
    agent_id = record.get("id")
    before = state["figures"].get(agent_id)
    if not isinstance(before, list) or len(before) != 2:
        return
    settled = figures_of(record)
    for index, key in enumerate(("output", "tool_calls")):
        value = settled[index]
        if value > before[index]:
            state["subagents"][key] += value - before[index]
            before[index] = value


def remember_unsummed(state, agent_id, path):
    """Keep an agent reported without a figure on the list a later event retries.

    It has been counted — as unknown, so the session line says `(partial)` — and it has been
    named, so it must never be named again. What is left is its number, and the retry exists so
    that a transcript which gains its response a second later still reaches the session totals.
    """
    entry = state["unsummed"].get(agent_id)
    tries = entry[1] + 1 if isinstance(entry, list) else 1
    state["unsummed"][agent_id] = [path if isinstance(path, str) else "", tries]


def resolve_unknown(state, agent_id, figures):
    """A figure that landed after its agent was counted unknown. The totals rise; nothing is said."""
    totals = state["subagents"]
    if totals["unknown"] > 0:
        totals["unknown"] -= 1
    totals["output"] += figures[0]
    totals["tool_calls"] += figures[1]
    state["figures"][agent_id] = list(figures)


def reconcile_unsummed(state, resolved):
    """Fold this event's sums into the agents earlier events could not put a number on.

    None of them is named again: they were announced when they finished. A transcript that has
    turned out not to exist stops being asked about, and so does one that has been asked about
    `UNSUMMED_TRIES` times; both stay counted unknown, which is what the `(partial)` on the
    session line is for.
    """
    for agent_id, entry in list(state["unsummed"].items()):
        outcome = resolved.get(agent_id)
        if isinstance(outcome, dict):
            resolve_unknown(state, agent_id, [outcome["output"], outcome["tool_calls"]])
            for record in state["pending"]:
                if record.get("id") == agent_id:
                    apply_settled(record, outcome)
            del state["unsummed"][agent_id]
        elif outcome == ABSENT or entry[1] >= UNSUMMED_TRIES:
            del state["unsummed"][agent_id]
        elif outcome == PENDING:
            entry[1] += 1


def running_now(state, now=None):
    """The agents still in flight, forgetting a start whose stop never came."""
    now = time.time() if now is None else now
    stale = [agent for agent, at in state["running"].items() if now - (at or 0) > RUNNING_TTL]
    for agent in stale:
        del state["running"][agent]
    return list(state["running"])


# --------------------------------------------------------------------------- the lines


def settings(env):
    """`(table, mode, nudges, width)` from the active cost variant, or the silent default."""
    module = sibling("posture")
    if module is None:
        return None, "off", [], None
    try:
        table = module.cost_table(env)
    except Exception:
        return None, "off", [], None
    switches = table.get("switches") if isinstance(table, dict) else None
    switches = switches if isinstance(switches, dict) else {}
    mode = switches.get("turn_feed")
    mode = mode if mode in MODES else "off"
    nudges = sorted(v for v in switches.get("nudge_at") or []
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0)
    width = switches.get("max_parallel")
    width = width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else None
    return table, mode, nudges, width


def session_nudges(table):
    """The context sizes the posture calls a full session, smallest first; empty means silent."""
    switches = table.get("switches") if isinstance(table, dict) else None
    switches = switches if isinstance(switches, dict) else {}
    return sorted(v for v in switches.get("session_nudge_at") or []
                  if isinstance(v, int) and not isinstance(v, bool) and v > 0)


def budgets(row):
    """The row's two soft budgets, each only when it is a positive whole number."""
    if not isinstance(row, dict):
        return None, None
    out = []
    for key in ("budget_output_tokens", "budget_tool_calls"):
        value = row.get(key)
        out.append(value if isinstance(value, int) and not isinstance(value, bool) and value > 0
                   else None)
    return out[0], out[1]


def agent_line(agent_type, output, calls, row, nudges, partial=False, provisional=False,
               not_yet=False, agent_id=None, round_number=1):
    """`(line, ratio)` for one finished subagent; ratio is None when there is nothing to compare.

    Null counts are what a sum that could not be computed leaves behind, and the line says so:
    an agent reported at zero would read as an agent that did nothing. Which of the two things
    it says is the difference between a transcript that is not there — `spend unknown`, final —
    and one that is there and had no response written into it yet, whose figure a later turn can
    still reconcile into the session totals. A row that budgets one
    half of the unit names that half, because `n / None` would read as a figure to act on.
    `provisional` is the figure of a response still being written: `(so far)`, never a number
    presented as exact. `(partial)` subsumes it — a sum that was cut short is the larger caveat.

    `spend unknown` names the agent it is about, because it is the one line that carries no figure
    to tell two of them apart: a session that emitted it once an hour and a session emitting it
    every turn read identically until the id was in it.

    A round past the first is one completion of a resumed agent, and its figure is its whole
    transcript rather than that round alone, so the line says `(cumulative)`.
    """
    if output is None and calls is None:
        if not_yet:
            return PREFIX + agent_type + " finished, spend not yet recorded", None
        named = (", no transcript found for agent " + agent_id) if agent_id else ""
        return PREFIX + agent_type + " finished, spend unknown" + named, None
    text = (PREFIX + agent_type + " finished" + ("" if round_number < 2 else
            " round " + "{:,}".format(round_number)) + " at " + plural(output or 0, "output token")
            + " and " + plural(calls or 0, "tool call"))
    marks = ["cumulative"] if round_number > 1 else []
    if partial:
        marks.append("partial")
    elif provisional:
        marks.append("so far")
    if marks:
        text += " (" + ", ".join(marks) + ")"
    budget_out, budget_calls = budgets(row)
    ratios, halves = [], []
    if budget_out:
        ratios.append((output or 0) / float(budget_out))
        halves.append(plural(budget_out, "output token"))
    if budget_calls:
        ratios.append((calls or 0) / float(budget_calls))
        halves.append(plural(budget_calls, "tool call"))
    if not ratios:
        return text, None
    ratio = max(ratios)
    if budget_out and budget_calls:
        halves = ["{:,}".format(budget_out) + " / " + "{:,}".format(budget_calls)]
    clause = "{:.1f}".format(ratio) + "× its budget of " + " and ".join(halves)
    if any(ratio >= level for level in nudges):
        clause = "over budget " + clause
    return text + " — " + clause, ratio


def width_line(running, width):
    """One line when more agents are in flight than the posture's width. Never a decision."""
    if width is None or len(running) <= width:
        return None
    return (PREFIX + plural(len(running), "subagent") + " running against a posture width of "
            + "{:,}".format(width))


def turn_line(state):
    """The turn and the session so far, or None when it would say nothing new.

    A session's first prompt has no turn behind it, and a background agent's completion arrives
    as a prompt of its own — several in a row, all reporting the turn before them. Repeating a
    line the orchestrator has already read costs context and teaches it to skip the feed, so the
    figures last printed are remembered and an unchanged turn is silence.
    """
    last = state["turn"] if (state["turn"]["output"] or state["turn"]["tool_calls"]) \
        else state["previous_turn"]
    figures = [last["output"], last["tool_calls"]]
    if not any(figures) or state.get("said_turn") == figures:
        return None
    state["said_turn"] = figures
    totals = state["subagents"]
    text = (PREFIX + "last turn " + plural(last["output"], "output token") + ", "
            + plural(last["tool_calls"], "tool call") + " · session "
            + "{:,}".format(state["session"]["output"] + totals["output"]) + " output, "
            + plural(state["session"]["tool_calls"] + totals["tool_calls"], "tool call") + ", "
            + plural(totals["count"], "subagent"))
    if totals["unknown"] or state.get("partial"):
        text += " (partial)"
    return text


def session_line(state, thresholds):
    """One line the first time the session's context passes a threshold, or None.

    The turn line reports what a turn produced. What a long session costs is mostly the context
    every further turn re-reads, which no figure in the feed shows, so this is the one line that
    says continuing here is the expensive choice. It is soft: nothing is blocked.

    Once per threshold, never once per turn. Every threshold at or below the current size is
    marked said, so a session that stays above one is silent until it reaches the next, and a
    resume reads the same marks out of the same state file. A threshold the context has since
    fallen back under is unmarked, because a compaction that halved the session and an hour of
    work that filled it again is a crossing the orchestrator has not been told about.

    A size no transcript line has supplied yet is not a crossing: the line would name a
    threshold nothing was measured against, and reporting the context of an unread transcript
    as zero would be a lie either way.
    """
    size = state.get("context")
    if not thresholds or not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    said = state.setdefault("said_nudge", [])
    said[:] = [level for level in said if level <= size]
    fresh = [level for level in thresholds if size >= level and level not in said]
    if not fresh:
        return None
    said.extend(fresh)
    # The largest of the ones newly crossed, which is not the largest passed: a threshold
    # already said is not news, and naming it would read as a line repeating itself.
    return (PREFIX + "session context " + plural(size, "token") + ", past the fresh-session "
            "threshold of " + "{:,}".format(fresh[-1]) + " — finish the task, write the "
            "handoff, start a fresh session")


def record_adherence(recommendation, session_id, turn, env):
    """Record a recommendation this feed emitted. Nothing it does can change what the feed says."""
    module = sibling("adherence")
    if module is None:
        return
    try:
        module.emit(recommendation, session_id, turn, env)
    except Exception:
        pass


def shows(mode, ratio, nudges):
    """Whether a subagent's line is worth a line. `thresholds` wants the smallest nudge met."""
    if mode == "every-turn":
        return True
    if mode != "thresholds" or not nudges or ratio is None:
        return False
    return ratio >= nudges[0]


def row_for(table, agent_type):
    module = sibling("posture")
    try:
        return module.row_for(table, agent_type) if module else None
    except Exception:
        return None


def workflow_agent(path):
    """Whether a subagent transcript is a Workflow-tool agent's, by `usage-log.py`'s own rule.

    Such an agent is launched by the tool, not spawned: no spawn hook routed it and no brief
    stated it a budget, so `usage.jsonl` records it `unconfined` with both budgets null. The feed
    leaves it unbudgeted for the same reason, whatever role name its type happens to carry, so
    the live line and `citizen usage --by role` agree about the same agent. It is asked at stop
    time of the path the event was handed, before `redact` can blank one outside this home, and
    the answer is journalled as `workflow`. A ledger that will not import answers no.
    """
    module = sibling("usage-log")
    try:
        return bool(module is not None and path and module.workflow_of(path))
    except Exception:
        return False


def stop_line(table, nudges, record):
    agent_id = record.get("id")
    round_number = record.get("round")
    row = None if record.get("workflow") is True else row_for(table, record.get("type"))
    return agent_line(agent_name(record.get("type")), record.get("output"),
                      record.get("tool_calls"), row, nudges,
                      bool(record.get("partial")), bool(record.get("so_far")),
                      bool(record.get("not_yet")),
                      agent_id if isinstance(agent_id, str) else None,
                      round_number if isinstance(round_number, int) else 1)


def unknown_final(record):
    """A stop whose spend nothing is going to recover: `spend unknown`, not `not yet recorded`."""
    return not has_figure(record) and not record.get("not_yet")


def said_unknown(state, record):
    """Whether this agent's `spend unknown` has already been fed, so it is never fed twice.

    The line carries no figure and cannot be reconciled, so nothing about the agent will ever
    change it. Repeating it turn after turn — what a session whose reader state was rebuilt used
    to do — spends the orchestrator's context on a fact it read the first time.
    """
    return unknown_final(record) and record.get("id") in (state.get("said_unknown") or [])


def note_unknown(state, record):
    """Remember a `spend unknown` that has just been fed, bounded like every other list here."""
    if not unknown_final(record) or not isinstance(record.get("id"), str):
        return
    already = state.setdefault("said_unknown", [])
    if record["id"] not in already:
        already.append(record["id"])


#: What the feed's numbers are, said once per session so they cannot be read as another measure.
#: Reported live: one agent's line said 31,121 output tokens beside a task notification's
#: `subagent_tokens 102398`, and both were right about different things.
MEASURE = (PREFIX + "figures above are output tokens and tool calls summed from each agent's own "
           "transcript — not the task notification's subagent_tokens, which is another measure.")


def legend(state):
    """The measure line, the first time this session feeds a figure, and never again."""
    if state.get("said_measure"):
        return None
    state["said_measure"] = True
    return MEASURE


# --------------------------------------------------------------------------- the events


def on_subagent_event(payload, env, kind):
    """Journal one subagent lifecycle event. Never emits and never takes the lock.

    `SubagentStop`'s own `additionalContext` would reach the agent that has just finished, so
    there is nothing to say here even when there is something to record. The stop is written in
    a `finally`: an agent whose stop never landed would be counted as running for the rest of
    the session, so a stop with nothing in it beats no stop at all.

    Nothing here waits. A stop fires the instant the agent ends, which can be before a single one
    of its responses has been flushed to its transcript, and this event has no one to tell. So
    the figure it can see is recorded as the figure it can see, and `summed` says whether that is
    a number to trust: an empty read, or one taken out of a response still being written, is
    journalled as not yet summed and the report-time sum makes it good. The transcript is
    written down with the home prefix redacted so the reporter can find it again.
    """
    if payload.get("stop_hook_active"):
        return None
    agent_id = payload.get("agent_id")
    if not (isinstance(agent_id, str) and IDENTIFIER.match(agent_id)):
        return None
    _, mode, _, _ = settings(env)
    if mode == "off":
        return None
    found = paths(payload.get("session_id"), env)
    if found is None:
        return None
    record = {"t": kind, "id": agent_id, "type": agent_name(payload.get("agent_type")),
              "at": int(time.time())}
    if kind == "start":
        journal_append(found[1], record)
        return None
    record["output"], record["tool_calls"], record["partial"] = None, None, False
    record["summed"], record["path"] = False, ""
    try:
        path = payload.get("agent_transcript_path") or agent_transcript(
            payload.get("transcript_path"), payload.get("session_id"), agent_id)
        record["path"] = redact(path, env)
        if not payload.get("agent_type") and not readable(path):
            record["internal"] = True
        if workflow_agent(path):
            record["workflow"] = True
        totals = agent_totals(path)
        if totals is not None:
            record["type"] = agent_name(payload.get("agent_type"), totals["agent_type"])
            record["output"], record["tool_calls"] = totals["output"], totals["tool_calls"]
            record["partial"] = totals["partial"]
            record["summed"] = tail_settled(path) and not totals["partial"]
    finally:
        journal_append(found[1], record)
    return None


def emit(state, lines, record, line):
    """Add one subagent's line, unless it is a `spend unknown` this session has already said.

    Returns whether the line carried a figure, which is what the measure line is owed to.
    """
    if said_unknown(state, record):
        return False
    note_unknown(state, record)
    lines.append(line)
    return has_figure(record)


def refresh(state_file, journal_file, resolved):
    """The session's state with this event's sums folded in: the retries first, then the journal.

    In that order because the two lists must not touch each other's work. A retry belongs to an
    agent an earlier event already named; a stop the journal brings now has never been named.
    Folding the journal first would hand a stop's brand-new retry entry straight to the retry
    pass, which would count its one attempt twice.
    """
    state = load_state(state_file)
    reconcile_unsummed(state, resolved)
    reconcile_rounds(state, resolved)
    return ingest(state, journal_file, resolved)


def fresh_record(agent_id, response, outcome):
    """The stop record a synchronous return makes for itself, or None when there is nothing to say.

    `ABSENT` is the None: the return derives the transcript path from the session id, while the
    stop that follows it is handed the path outright, so a file this one cannot find is a file
    the journal may well find. Saying `spend unknown` here would retire the agent and throw that
    away.
    """
    record = {"id": agent_id, "output": None, "tool_calls": None, "partial": False,
              "type": agent_name(response.get("agentType"))}
    if isinstance(outcome, dict):
        record["type"] = agent_name(response.get("agentType"), outcome["agent_type"])
        return apply_settled(record, outcome)
    if outcome == ABSENT or outcome is None:
        return None
    return apply_settled(record, outcome)


def on_agent_return(payload, env):
    """A synchronous `Agent` completion, reported once, plus the width note on any Agent call."""
    response = payload.get("tool_response")
    response = response if isinstance(response, dict) else {}
    table, mode, nudges, width = settings(env)
    if mode == "off":
        return None
    found = paths(payload.get("session_id"), env)
    if found is None:
        return None
    state_file, journal_file, lock_file = found
    agent_id = response.get("agentId")
    # A background spawn's PostToolUse fires at launch with no totals at all; only the width
    # note applies to it.
    synchronous = (not response.get("isAsync") and response.get("status") == "completed"
                   and isinstance(agent_id, str) and IDENTIFIER.match(agent_id))
    # Before the lock, always: this is the one slow thing either main-thread event does, and a
    # prompt waiting behind it would run out its wait and lose its line. The returning agent goes
    # first, and whatever is left of the budget sums the stops this event is about to name. A
    # launch names none of them — its only line is the width note — so it sums nothing and stays
    # as quick as it was; what it ingests meanwhile is reconciled by the prompt that reports it.
    first, resolved = None, {}
    if synchronous:
        first = (agent_id, agent_transcript(payload.get("transcript_path"),
                                            payload.get("session_id"), agent_id))
        resolved = settle_before_lock(state_file, journal_file, payload, env, first)
    with Lock(lock_file) as held:
        if not held:
            return None
        state = refresh(state_file, journal_file, resolved)
        lines, figured = [], False
        if synchronous:
            record = next((r for r in state["pending"]
                           if r.get("id") == agent_id and not r.get("awaiting")), None)
            if record is not None:
                line, ratio = stop_line(table, nudges, record)
                if shows(mode, ratio, nudges):
                    state["pending"].remove(record)
                    figured = emit(state, lines, record, line)
            elif agent_id not in state["counted"]:
                # The stop has not been journalled yet. Reporting it now means counting it now,
                # so the journal's copy is skipped when it arrives. An outcome of `ABSENT` is
                # left to that copy instead: it is the one that was handed the transcript path.
                fresh = fresh_record(agent_id, response, resolved.get(agent_id))
                line, ratio = stop_line(table, nudges, fresh) if fresh else (None, None)
                if fresh and shows(mode, ratio, nudges):
                    state["counted"].append(agent_id)
                    count(state, fresh)
                    if fresh.get("not_yet"):
                        remember_unsummed(state, agent_id, redact(first[1], env))
                    state["running"].pop(agent_id, None)
                    figured = emit(state, lines, fresh, line)
        note = width_line(running_now(state), width)
        if note:
            lines.append(note)
        if figured:
            measure = legend(state)
            if measure:
                lines.append(measure)
        save_state(state_file, state)
        return lines or None


def on_prompt(payload, env):
    """The turn line, the session nudge, the width note, then the subagents that have finished."""
    table, mode, nudges, width = settings(env)
    if mode == "off":
        return None
    found = paths(payload.get("session_id"), env)
    if found is None:
        return None
    state_file, journal_file, lock_file = found
    # Outside the lock, like the return's: the agents this prompt is about to name are summed
    # before anything is held, within one budget for the lot of them.
    resolved = settle_before_lock(state_file, journal_file, payload, env)
    with Lock(lock_file) as held:
        if not held:
            return None
        state = refresh(state_file, journal_file, resolved)
        # The session's prompt count, which an adherence event names as its turn. Before the
        # read, so a read that gives up still counts the prompt it gave up on.
        state["turns"] += 1
        prune(state_file.parent, state, state_file.name.split(".", 1)[0])
        state = advance(state, payload.get("transcript_path"),
                        save=lambda current: save_state(state_file, current))
        if state.get("timed_out"):
            save_state(state_file, state)
            return None
        turn = turn_line(state) if mode == "every-turn" else None
        lines = [turn] if turn else []
        # Not a subagent's line and not a figure to compare: it is said under `thresholds` too.
        nudge = session_line(state, session_nudges(table))
        if nudge:
            lines.append(nudge)
            record_adherence("fresh-session", payload.get("session_id"), state["turns"], env)
        note = width_line(running_now(state), width)
        if note:
            lines.append(note)
        said = []
        for record in list(state["pending"]):
            if record.get("awaiting"):
                continue     # a resumed round whose own spend has not landed yet
            line, ratio = stop_line(table, nudges, record)
            if not shows(mode, ratio, nudges):
                continue
            if said_unknown(state, record):
                # Nothing will ever put a figure on it and the orchestrator has read it once.
                state["pending"].remove(record)
                continue
            said.append((record, line))
        # Only the agents this turn actually names are retired. The cap bounds how much is said
        # at once, so the rest are named at the next prompt rather than dropped unsaid.
        figured = False
        for record, line in said[:MAX_LISTED]:
            state["pending"].remove(record)
            figured = emit(state, lines, record, line) or figured
        if len(said) > MAX_LISTED:
            lines.append("… and " + "{:,}".format(len(said) - MAX_LISTED) + " more")
        if figured:
            measure = legend(state)
            if measure:
                lines.append(measure)
        save_state(state_file, state)
    return lines or None


def run(payload, env=None):
    """The lines one event produces, or None. The parent thread is the only place a feed runs."""
    env = os.environ if env is None else env
    kind = payload.get("hook_event_name") or ""
    if kind in ("SubagentStop", "SubagentStart"):
        # The only events whose `agent_id` names somebody else: they fire in the parent's hooks.
        return on_subagent_event(payload, env, "stop" if kind == "SubagentStop" else "start")
    if payload.get("agent_id"):
        return None
    if kind == "UserPromptSubmit":
        return on_prompt(payload, env)
    if kind == "PostToolUse" and payload.get("tool_name") == "Agent":
        return on_agent_return(payload, env)
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        lines = run(payload)
    except Exception:
        return
    if lines:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name") or "",
            "additionalContext": "\n".join(lines)}}))


if __name__ == "__main__":
    main()
