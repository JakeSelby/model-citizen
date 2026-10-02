"""Attribute Claude Code prompt-cache rebuilds from main-session transcripts.

`usage --by prefix` answers how much of a session missed from the local usage ledger. This
module answers which individual request rebuilt the prefix and what happened between it and the
previous request. It is retrospective and read-only: no hook, event or ledger row is added.

The transcript markers are Claude Code internals; the fixtures that pin them are synthetic
transcripts stamped with client version 2.0.20. Unknown or removed markers remain `unexplained`;
malformed input and unpriced models are counted apart rather than turned into zeroes, and a
cause with any unpriced break has no dollar figure or share at all.
"""
import collections
import datetime
import json
import os
import re
from pathlib import Path

MIN_SHORTFALL = 20000
LONG_CALLS = 200
BOOKKEEPING = {
    "queue-operation", "last-prompt", "atis-latch", "bridge-session", "ai-title", "pr-link",
    "custom-title", "frame-link", "agent-name", "file-history-snapshot", "file-history-delta",
    "artifact-autoreact-ledger", "relocated", "artifact-comment-monitor", "cost-state",
}
EVENT_CAUSES = (
    ("system prompt rebuilt", ("att:prompt_snapshot",)),
    ("output style changed", ("att:output_style_instructions",)),
    ("tool, agent or skill list changed", ("att:deferred_tools_delta", "att:mcp_instructions_delta",
                                             "att:agent_listing_delta", "att:skill_listing")),
    ("instructions or environment reloaded", ("att:instructions", "att:environment",
                                                 "att:session_context")),
    ("date changed", ("att:date", "att:date_change")),
    ("session reloaded (mode marker)", ("mode",)),
    ("effort change", ("att:ultra_effort_enter", "att:ultra_effort_exit")),
    ("plan mode enter or exit", ("att:plan_mode", "att:plan_mode_exit")),
    ("auto mode or permissions change", ("att:auto_mode", "att:command_permissions")),
    ("after an API error", ("sys:api_error",)),
    ("remote session change", ("att:remote_session_change",)),
)
COMMAND = re.compile(r"<command-name>(/[\w:-]+)")
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def timestamp(value):
    """A transcript ISO timestamp as epoch seconds, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def event_kinds(entry):
    """The attribution markers one non-assistant transcript entry carries, in source order."""
    kind = entry.get("type")
    attachment = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
    if kind in BOOKKEEPING:
        return []
    if kind == "attachment":
        return ["att:" + str(attachment.get("type"))]
    if kind == "system":
        return ["sys:" + str(entry.get("subtype"))]
    if kind == "mode":
        return ["mode"]
    if kind != "user":
        return []
    content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
    entrypoint = ["ep:" + str(entry["entrypoint"])] if entry.get("entrypoint") else []
    if isinstance(content, str):
        match = COMMAND.search(content)
        return (["cmd:" + match.group(1)] if match else ["user-text"]) + entrypoint
    if isinstance(content, list):
        found = []
        for block in content:
            if isinstance(block, dict):
                found.append("tool_result" if block.get("type") == "tool_result"
                             else "user-" + str(block.get("type")))
        if entry.get("isCompactSummary"):
            found.append("compact-summary")
        return found + entrypoint
    return entrypoint


def is_prompt(entry):
    """A main-session user prompt, counted as the usage feed counts a turn: a user entry that is
    not a tool result, a meta entry or a compaction summary."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    return not (isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content))


def cause(events, gap, previous_model, model, five_minute_share, previous_version="", version=""):
    """The first matching rebuild cause in AH-S227's fixed attribution order."""
    names = set(events)
    if previous_model and model and previous_model != model:
        return "model switch after /model" if "cmd:/model" in names else "model switch, no /model"
    if "compact-summary" in names or "sys:compact_boundary" in names:
        return "compaction"
    if gap is not None and gap >= 3600:
        return "idle over 1h (TTL expiry)"
    if previous_version and version and previous_version != version:
        return "Claude Code version changed (restart)"
    # An unknown tier split (None) cannot show a five-minute TTL; such a gap falls through to
    # the events and then to "idle 5-60 min, no event", which the evidence still supports.
    if gap is not None and gap >= 300 and five_minute_share is not None and five_minute_share > 0.5:
        return "idle over 5m on 5m TTL"
    for label, markers in EVENT_CAUSES:
        if any(marker in names for marker in markers):
            return label
    command = next((event[4:] for event in events if event.startswith("cmd:")), None)
    if command:
        return "slash command " + command
    if gap is not None and gap >= 300:
        return "idle 5-60 min, no event"
    return "unexplained"


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def call_from(entry, events):
    """One validated API call from an assistant entry, or `(None, reason)`."""
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    identity = entry.get("requestId") or message.get("id")
    stamp = timestamp(entry.get("timestamp"))
    if not identity or usage is None or stamp is None:
        return None, "missing id, timestamp or usage"
    values = {name: _count(usage.get(key)) for name, key in (
        ("input", "input_tokens"), ("output", "output_tokens"),
        ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens"))}
    if any(value is None for value in values.values()):
        return None, "malformed token count"
    creation = usage.get("cache_creation")
    if creation is not None and not isinstance(creation, dict):
        return None, "malformed cache creation tiers"
    if creation is None or "ephemeral_1h_input_tokens" not in creation:
        # No split reported: the tiers are unknown, not zero, so neither is read as measured.
        values.update(cache_write_1h=None, cache_write_5m=None)
    else:
        one_hour = _count(creation.get("ephemeral_1h_input_tokens"))
        if one_hour is None or one_hour > values["cache_write"]:
            return None, "malformed cache creation tiers"
        values.update(cache_write_1h=one_hour, cache_write_5m=values["cache_write"] - one_hour)
    return dict(values, id=str(identity), timestamp=stamp,
                model=message.get("model") if isinstance(message.get("model"), str) else "",
                version=entry.get("version") if isinstance(entry.get("version"), str) else "",
                events=tuple(events)), None


def _synthetic(entry):
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    model = message.get("model")
    return isinstance(model, str) and model.startswith("<")


def transcript_paths(projects):
    """Unique main-session JSONL paths below the Claude projects directory."""
    seen, found, duplicates = set(), [], 0
    for path in sorted(Path(projects).glob("*/*.jsonl")):
        real = os.path.realpath(str(path))
        if real in seen:
            duplicates += 1
            continue
        seen.add(real)
        found.append(path)
    return found, duplicates


def read_calls(path, cutoff):
    """`(calls, counts)` from one transcript, filtering every line by its own timestamp.

    Each call's `prompt` is the ordinal of the user prompt it follows (`is_prompt`), counted from
    the start of the transcript even when earlier lines fall outside the window.
    """
    calls, events, seen, prompts = [], [], set(), 0
    counts = collections.Counter()
    try:
        stream = Path(path).open(encoding="utf-8", errors="replace")
    except OSError:
        counts["unreadable_files"] += 1
        return calls, counts
    with stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                counts["malformed_lines"] += 1
                continue
            if not isinstance(entry, dict):
                counts["malformed_lines"] += 1
                continue
            if is_prompt(entry):
                prompts += 1
            stamp = timestamp(entry.get("timestamp"))
            if stamp is not None and stamp < cutoff:
                counts["outside_window"] += 1
                continue
            if entry.get("isSidechain"):
                counts["sidechain_lines"] += 1
                continue
            if entry.get("type") == "assistant" and _synthetic(entry):
                # A client-generated turn (`<synthetic>`) is no API request: it neither sent nor
                # read a prefix, so the next real call is compared with the last real one and
                # inherits every marker recorded since.
                counts["synthetic_lines"] += 1
                continue
            if entry.get("type") == "assistant":
                call, reason = call_from(entry, events)
                if call is None:
                    counts["malformed_lines"] += 1
                    continue
                if call["id"] in seen:
                    counts["duplicate_call_lines"] += 1
                    continue
                seen.add(call["id"])
                call["prompt"] = prompts
                calls.append(call)
                events = []
            else:
                if stamp is None:
                    # Claude Code emits valid bookkeeping and mode records without timestamps.
                    # Keep their marker in stream order; the first in-window call clears any
                    # marker that preceded the window, so one cannot leak into a comparison.
                    counts["untimed_lines"] += 1
                events.extend(event_kinds(entry))
    return calls, counts


def annotate(calls):
    """Mark each call's cache rebuild on the call itself and return the list.

    A call is a rebuild when it read at least `MIN_SHORTFALL` fewer tokens than the previous
    call sent; it then carries `rewritten` (the shortfall it wrote back, at most its own write)
    and `cause`. Every other call, the first included, carries `rewritten` 0 and `cause` None.
    """
    previous = None
    for call in calls:
        call["rewritten"], call["cause"] = 0, None
        if previous is not None:
            sent = previous["input"] + previous["cache_read"] + previous["cache_write"]
            shortfall = max(0, sent - call["cache_read"])
            if shortfall >= MIN_SHORTFALL:
                writes = call["cache_write"]
                if call["cache_write_5m"] is None:
                    five_share = None
                else:
                    five_share = call["cache_write_5m"] / writes if writes else 0.0
                gap = call["timestamp"] - previous["timestamp"]
                call["rewritten"] = min(shortfall, writes)
                call["cause"] = cause(call["events"], gap, previous["model"], call["model"],
                                      five_share, previous["version"], call["version"])
        previous = call
    return calls


def session_transcript(projects, session_id):
    """The main transcript `<session_id>.jsonl` in any project folder, or None.

    Only a plain name is looked up, so an id can never widen the glob or leave the folder.
    """
    if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
        return None
    found = sorted(Path(projects).glob("*/" + session_id + ".jsonl"))
    return found[0] if found else None


def session_calls(projects, session_id):
    """One session's annotated calls from its whole transcript, or None when it cannot be read."""
    path = session_transcript(projects, session_id)
    if path is None:
        return None
    calls, counts = read_calls(path, 0)
    if counts.get("unreadable_files"):
        return None
    return annotate(calls)


def _scope():
    return {"sessions": 0, "calls": 0, "comparisons": 0, "priced_spend_usd": 0.0,
            "unpriced_calls": 0, "unpriced_breaks": 0, "causes": collections.defaultdict(
                lambda: {"breaks": 0, "rewritten_tokens": 0, "excess_usd": 0.0,
                         "unpriced_breaks": 0})}


def _call_cost(call, table, pricing):
    rate = pricing.price_for(table, call["model"])
    return None if rate is None else pricing.tokens_cost(call, rate)


def _break_cost(call, rewritten, table, pricing):
    rate = pricing.price_for(table, call["model"])
    if rate is None:
        return None
    writes = call["cache_write"]
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": rewritten}
    if call["cache_write_1h"] is not None:
        one_hour = round(rewritten * call["cache_write_1h"] / writes) if writes else 0
        tokens.update(cache_write_5m=rewritten - one_hour, cache_write_1h=one_hour)
    # With no split, `tokens_cost` charges the whole write at the base rate, as it does ledger rows.
    write = pricing.tokens_cost(tokens, rate)
    read = pricing.tokens_cost({"input": 0, "output": 0, "cache_read": rewritten,
                                "cache_write": 0}, rate)
    return write - read


def analyse(projects, cutoff, days, table, pricing):
    """Structured rebuild attribution for the window; no output and no writes."""
    scopes = {"all": _scope(), "long": _scope()}
    paths, duplicate_paths = transcript_paths(projects)
    counts = collections.Counter(files_seen=len(paths) + duplicate_paths,
                                 files_read=len(paths), duplicate_paths=duplicate_paths)
    for path in paths:
        calls, read_counts = read_calls(path, cutoff)
        counts.update(read_counts)
        if len(calls) < 2:
            continue
        names = ["all"] + (["long"] if len(calls) >= LONG_CALLS else [])
        for name in names:
            scope = scopes[name]
            scope["sessions"] += 1
            scope["calls"] += len(calls)
            scope["comparisons"] += len(calls) - 1
            for call in calls:
                cost = _call_cost(call, table, pricing)
                if cost is None:
                    scope["unpriced_calls"] += 1
                else:
                    scope["priced_spend_usd"] += cost
        for call in annotate(calls):
            label, rewritten = call["cause"], call["rewritten"]
            if label is None:
                continue
            cost = _break_cost(call, rewritten, table, pricing)
            for name in names:
                scope, bucket = scopes[name], scopes[name]["causes"][label]
                bucket["breaks"] += 1
                bucket["rewritten_tokens"] += rewritten
                if cost is None:
                    bucket["unpriced_breaks"] += 1
                    scope["unpriced_breaks"] += 1
                else:
                    bucket["excess_usd"] += cost
    result = {"schema": 1, "days": days, "shortfall_tokens": MIN_SHORTFALL,
              "long_session_calls": LONG_CALLS, "files": dict(counts), "scopes": {}}
    for name, scope in scopes.items():
        spend = scope.pop("priced_spend_usd")
        causes = []
        for label, bucket in scope.pop("causes").items():
            # One unpriced break makes the cause's dollars unknown: a partial sum would read as
            # a measured figure, and an all-unpriced cause would read as $0.
            known = not bucket["unpriced_breaks"]
            excess = bucket["excess_usd"] if known else None
            causes.append(dict(bucket, cause=label, excess_usd=excess,
                               known_spend_share=(excess / spend if known and spend else None),
                               cost_per_break=(excess / bucket["breaks"] if known else None)))
        causes.sort(key=lambda row: (row["excess_usd"] is None, -(row["excess_usd"] or 0.0),
                                     row["cause"]))
        result["scopes"][name] = dict(scope, priced_spend_usd=spend, causes=causes,
                                      unknown_breaks=sum(row["breaks"] for row in causes
                                                         if row["cause"] == "unexplained"))
    return result


def render(result):
    """Human-readable lines for `harness usage --by rebuild`."""
    lines = ["Cache rebuild attribution is retrospective; it changes no session or cache."]
    for name in ("long", "all"):
        scope = result["scopes"][name]
        label = "long sessions (200+ calls)" if name == "long" else "all sessions"
        lines.append("[%s] %d session(s), $%.2f known priced spend" %
                     (label, scope["sessions"], scope["priced_spend_usd"]))
        head = "{:<42}{:>8}{:>14}{:>14}{:>13}{:>10}".format(
            "cause", "breaks", "rewritten", "known spend", "cost/break", "unpriced")
        lines.extend((head, "-" * len(head)))
        for row in scope["causes"]:
            if row["unpriced_breaks"]:
                share = each = "unpriced"
            else:
                share = "-" if row["known_spend_share"] is None else "{:.1%}".format(
                    row["known_spend_share"])
                each = "${:.2f}".format(row["cost_per_break"])
            lines.append("{:<42}{:>8,}{:>14,}{:>14}{:>13}{:>10,}".format(
                row["cause"][:42], row["breaks"], row["rewritten_tokens"], share, each,
                row["unpriced_breaks"]))
        lines.append("unpriced: %d call(s), %d break(s); unexplained: %d break(s)" %
                     (scope["unpriced_calls"], scope["unpriced_breaks"], scope["unknown_breaks"]))
    files = result["files"]
    lines.append("input: %d file(s) read once, %d duplicate path(s), %d malformed line(s), "
                 "%d untimed line(s), %d unreadable file(s), %d outside-window line(s), "
                 "%d synthetic turn(s)" %
                 (files.get("files_read", 0), files.get("duplicate_paths", 0),
                  files.get("malformed_lines", 0), files.get("untimed_lines", 0),
                  files.get("unreadable_files", 0), files.get("outside_window", 0),
                  files.get("synthetic_lines", 0)))
    return lines

