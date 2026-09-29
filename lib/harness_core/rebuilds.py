"""Attribute Claude Code prompt-cache rebuilds from main-session transcripts.

`usage --by prefix` answers how much of a session missed from the local usage ledger. This
module answers which individual request rebuilt the prefix and what happened between it and the
previous request. It is retrospective and read-only: no hook, event or ledger row is added.

The transcript markers are Claude Code internals observed on versions 2.0.0 through 2.0.21.
Unknown or removed markers remain `unexplained`; malformed input and unpriced models are counted
apart rather than turned into zeroes.
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
    if gap is not None and gap >= 300 and five_minute_share > 0.5:
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
    one_hour = _count((creation or {}).get("ephemeral_1h_input_tokens", 0))
    if one_hour is None or one_hour > values["cache_write"]:
        return None, "malformed cache creation tiers"
    values.update(cache_write_1h=one_hour, cache_write_5m=values["cache_write"] - one_hour)
    return dict(values, id=str(identity), timestamp=stamp,
                model=message.get("model") if isinstance(message.get("model"), str) else "",
                version=entry.get("version") if isinstance(entry.get("version"), str) else "",
                events=tuple(events)), None


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
    """`(calls, counts)` from one transcript, filtering every line by its own timestamp."""
    calls, events, seen = [], [], set()
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
            stamp = timestamp(entry.get("timestamp"))
            if stamp is not None and stamp < cutoff:
                counts["outside_window"] += 1
                continue
            if entry.get("isSidechain"):
                counts["sidechain_lines"] += 1
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
    one_hour = round(rewritten * call["cache_write_1h"] / writes) if writes else 0
    five_minute = rewritten - one_hour
    write = pricing.tokens_cost({"input": 0, "output": 0, "cache_read": 0,
                                 "cache_write": rewritten, "cache_write_5m": five_minute,
                                 "cache_write_1h": one_hour}, rate)
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
        for previous, call in zip(calls, calls[1:]):
            sent = previous["input"] + previous["cache_read"] + previous["cache_write"]
            shortfall = max(0, sent - call["cache_read"])
            if shortfall < MIN_SHORTFALL:
                continue
            rewritten = min(shortfall, call["cache_write"])
            writes = call["cache_write"]
            five_share = call["cache_write_5m"] / writes if writes else 0.0
            gap = call["timestamp"] - previous["timestamp"]
            label = cause(call["events"], gap, previous["model"], call["model"], five_share,
                          previous["version"], call["version"])
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
        for label, bucket in sorted(scope.pop("causes").items(),
                                    key=lambda item: (-item[1]["excess_usd"], item[0])):
            bucket = dict(bucket, cause=label,
                          known_spend_share=(bucket["excess_usd"] / spend if spend else None),
                          cost_per_break=(None if bucket["unpriced_breaks"] else
                                          bucket["excess_usd"] / bucket["breaks"]))
            causes.append(bucket)
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
        head = "{:<42}{:>8}{:>14}{:>14}{:>13}".format(
            "cause", "breaks", "rewritten", "known spend", "cost/break")
        lines.extend((head, "-" * len(head)))
        for row in scope["causes"]:
            share = "unknown" if row["known_spend_share"] is None else "{:.1%}".format(
                row["known_spend_share"])
            each = "unpriced" if row["cost_per_break"] is None else "${:.2f}".format(
                row["cost_per_break"])
            lines.append("{:<42}{:>8,}{:>14,}{:>14}{:>13}".format(
                row["cause"][:42], row["breaks"], row["rewritten_tokens"], share, each))
        lines.append("unpriced: %d call(s), %d break(s); unexplained: %d break(s)" %
                     (scope["unpriced_calls"], scope["unpriced_breaks"], scope["unknown_breaks"]))
    files = result["files"]
    lines.append("input: %d file(s) read once, %d duplicate path(s), %d malformed line(s), "
                 "%d untimed line(s), %d unreadable file(s), %d outside-window line(s)" %
                 (files.get("files_read", 0), files.get("duplicate_paths", 0),
                  files.get("malformed_lines", 0), files.get("untimed_lines", 0),
                  files.get("unreadable_files", 0), files.get("outside_window", 0)))
    return lines


def report(projects, cutoff, days, table, pricing, say):
    """Print the rebuild table through the caller's normal output path."""
    result = analyse(projects, cutoff, days, table, pricing)
    for line in render(result):
        say(line)
    return 0
