#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SessionEnd hook: record a session's token usage in ~/.local/state/agent-harness/usage.jsonl.

One local file, and nothing over the network unless a `telemetry` block turns export on — see
`telemetry.py` and docs/telemetry.md. SessionEnd shares a 1.5-second budget, so the hook
spawns a detached worker and returns; the worker streams the transcript line by line and upserts
one row for the session, one for each subagent it spawned and one for each recent role-run
worker. Read it with `harness usage`.

The same pass builds the event list `rule-detectors.py` documents, so the rule telemetry costs
one read of the transcript rather than two: the record gains `rules`, `counts` and `stances`.
A registry that will not import costs the record its `rules` key and nothing else.

Codex is read from its rollout files instead, by `scan_codex`, and the two runtimes disagree
about what a parent's tokens mean: see `codex_totals` and `cmd_usage` in `bin/harness`.

Every row names the `harness_version` that wrote it, a session row names the `effort` that
covered most of its output, and a session row carries per-day slices in `days` so a session
that ran for a fortnight is not charged to the day it ended. See `harness_version`, `dominant`
and `daily`.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FIELDS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cache_read", "cache_read_input_tokens"),
    ("cache_write", "cache_creation_input_tokens"),
)

# A cache write is priced by its time to live — 1.25x base input for five minutes, 2x for an
# hour — and Claude Code reports the split under `cache_creation` beside the single
# `cache_creation_input_tokens` total. The row records both tiers so `harness usage` can price
# each at its own rate. The keys are additive and only written when a tier is non-zero: a row
# from before this release carries neither and is priced at the 5-minute rate, which understates
# a 1-hour write. See policy/prices.json.
CACHE_TIERS = (("cache_write_5m", "ephemeral_5m_input_tokens"),
               ("cache_write_1h", "ephemeral_1h_input_tokens"))

# A tool result worth keeping the text of: the two the detectors read. 64 KB is far past any
# brief or fenced block and far short of a transcript's largest result.
TEXT_KEPT_FOR = ("Bash", "Agent")
MAX_RESULT_TEXT = 64 * 1024


def sibling(name, required=True):
    """A module beside this one. Raises when required, so the caller can record why it is absent."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    try:
        spec = importlib.util.spec_from_file_location("harness_" + name.replace("-", "_"), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        if required:
            raise
        return None


def detectors():
    """The sibling detector registry. Raises, so the caller can record why it is absent."""
    return sibling("rule-detectors")


def rule_hits(module, events, stances, cwd, errors):
    """`{detector_id: hit count}` for one session: the registry's detectors plus the
    declarative ones in the `.ruleprobe/detectors.yaml` of the session's repository.

    A detector file's bad entries are skipped, never fatal, and are not `rules_errors`: that
    field drops the whole session from the report, and a typo in one entry must not unmeasure
    every other detector. `usage --rules` names them with their line instead. Every loaded
    declarative id is recorded, zero included, so a report read from another repository still
    shows its line. With no detector file the call is the registry's alone, unchanged.
    """
    extra = []
    loader = getattr(module, "declarative", None)
    if cwd and loader is not None and os.path.isdir(cwd):
        extra = loader(cwd)[0]
    hits = (module.run(events, stances, errors=errors, extra=extra) if extra
            else module.run(events, stances, errors=errors))
    counts = dict((detector.id, 0) for detector in extra)
    counts.update((did, len(found)) for did, found in hits.items())
    return counts


def stances(env=None):
    """The resolved `{dimension: variant}` map, from `posture.py` and nowhere else.

    A copy of this hook running away from its sibling resolver records no stances rather
    than a second opinion about them; the record keeps every other field.
    """
    module = sibling("posture", required=False)
    return module.resolve(env, strict=False)["stances"] if module else {}


# Re-exported, not re-declared: the defaults are `posture.py`'s, and a reader of a usage record
# should not have to know which file holds them.
DEFAULT_STANCES = getattr(sibling("posture", required=False), "DEFAULT_STANCES", {})

# The keys a subagent row carries its soft budget under, so an overrun is a subtraction on one
# row rather than a join against the cost table as it stands today. Written as `null` when
# nothing prices the role: a zero would say the spawn was budgeted nothing.
BUDGET_KEYS = ("budget_output_tokens", "budget_tool_calls")
_COST = []

# The two things a return is measured for, on the same row the budget sits on: whether it handed
# back a path a reader can open instead of the payload, and whether it stayed inside the word cap
# its brief stated. Both are `null` when the scan could not measure them — no parent call to join
# on, no return text, no cap it can know, a runtime that reports no return at all.
RETURN_KEYS = ("return_path", "return_over_budget")
# Why a return was not measured, when the reason is one a reader would otherwise mistake for a
# short return: a result the scan kept only the first 64 KB of is not a return it can count the
# words of, and saying so beats a figure taken over part of the text.
MEASURED_KEY = "return_measured"
# A path as a return writes one: inside a fence, inside backticks, or bare in prose. The three
# differ in what proves a token is a path at all. Quoted text is taken at its word; bare prose
# is not, because `pass/fail`, `24/7`, `2026/09/22` and `they/them` are prose and every one of
# them carries a separator. There a token counts only when it carries a path's own shape.
QUOTED = re.compile(r"`{3,}[^\n]*\n(.*?)(?:`{3,}|\Z)|`([^`\n]+)`", re.S)
PATH_TOKEN = re.compile(r"[^\s`'\"<>|*?,;:()\[\]{}]*/[^\s`'\"<>|*?,;:()\[\]{}]*")
# Trailing only: a leading `.` is `./notes`, and stripping it would make the path absolute and
# send it looking in the root of the filesystem.
PATH_TRIM = ".,;:!?'\")]}>"
PATH_ROOTS = ("/", "./", "../", "~/")
PATH_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}\Z")
# A URL names someone else's file, so it contributes nothing at all — not even its path part.
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
# What counts as a word when a return is measured against its cap: a token carrying a letter or
# a digit. A fence line, a bullet's `-` and a `·` separator are punctuation, and counting them
# would put a return over a cap it kept.
WORD = re.compile(r"[A-Za-z0-9]")
# The number a cap states is the one beside the word `words`, not the first in the sentence:
# "cap each of the 3 sections at 200 words" is a 200-word cap.
CAP_NUMBER = re.compile(r"(?i)(\d+)\s*[- ]?words?|word\s+cap\s*(?:of\s+)?(\d+)")
# How many candidates one return is checked against the filesystem. A return that named forty
# paths and resolved none of them is not answered differently by its forty-first.
MAX_CANDIDATES = 40
# A number this large is prose about something else, not a return bound.
MAX_WORD_CAP = 100000
_RETURN_RULES = []


def budget_fields(role):
    """The soft budget a spawn of `role` ran under, as the row's own keys.

    The figures are `posture.py`'s, read from the same table `brief-guard` prices a brief with,
    once per scan: a rescan of a hundred transcripts must not walk every sidecar a hundred
    times. The table is today's, which is what the row can know — the brief the spawn was given
    is not recorded anywhere the scan can read — so a row written after the variant changed
    names the budget the role carries now. A missing sibling, an unpriced role and a table that
    will not build are all `null`.
    """
    fields = dict((key, None) for key in BUDGET_KEYS)
    if not _COST:
        module = sibling("posture", required=False)
        try:
            _COST.append((module, module.cost_table() if module else {}))
        except Exception:
            _COST.append((None, {}))
    module, table = _COST[0]
    if module is None or not role:
        return fields
    try:
        fields.update(module.budget_figures(module.row_for(table, role)))
    except Exception:
        return dict((key, None) for key in BUDGET_KEYS)
    return fields


def harness_version():
    """The version `harness --version` prints, read from the same `VERSION` file at the root.

    The hook runs as a standalone script, so it walks up from its own real path — through the
    `claude/hooks -> ../policy/hooks` symlink and through `~/.claude/hooks/harness` — to the
    checkout that holds both a `VERSION` file and `bin/harness`, and never imports the CLI.
    A copy of this hook running outside a checkout records no version rather than a guess.
    """
    here = Path(os.path.realpath(__file__)).parent
    for parent in [here] + list(here.parents):
        marker = parent / "VERSION"
        if marker.is_file() and (parent / "bin" / "harness").exists():
            try:
                return marker.read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
    return None


def stamped_version(rescan):
    """The version to stamp on a row: none at all when the row is a backfill.

    A rescan reads a transcript written by whatever version was installed at the time, which is
    unknowable from the file, so the row carries `null` beside its `stances_source: "rescan"`.
    Stamping the current version would make every past session look like today's release.
    """
    return None if rescan else harness_version()


def usage_path():
    return Path.home() / ".local" / "state" / "agent-harness" / "usage.jsonl"


# The ledger grows compatibly: a change adds a field, a rename ships a fold, and nothing is
# removed in place. Every row written from this version on names the schema it was written
# under; a row without the key predates it and is read as version 0. Bump the version with any
# change to what a row carries, and add a rename to FIELD_FOLDS as `old name: new name`, never
# by rewriting old rows. See docs/usage.md, "Ledger schema".
SCHEMA_KEY = "schema_version"
# Version 1 is first released in v0.14.0 and carries every field that release adds,
# `profile_fingerprint` among them.
SCHEMA_VERSION = 1
FIELD_FOLDS = {}
FINGERPRINT_KEY = "profile_fingerprint"
_POSTURE = []


def profile_fingerprint():
    """The fingerprint of the profile in force, from `posture.py`; None when it cannot be had.

    A copy of this hook away from its resolver, or a resolver that fails, stamps null: an
    unattributed row, never a guessed one. The resolver remembers the answer for the process.
    """
    if not _POSTURE:
        _POSTURE.append(sibling("posture", required=False))
    try:
        return _POSTURE[0].fingerprint() if _POSTURE[0] else None
    except Exception:
        return None


def stamped(record):
    """A copy of `record` naming the schema and the profile. The caller's dict is untouched.

    A record that already names its profile keeps it, null included: a worker's row carries the
    profile its run started under, and a backfilled row carries only what the ledger already
    knew, so neither is stamped with the profile of whoever happens to write it.
    """
    out = dict(record, **{SCHEMA_KEY: SCHEMA_VERSION})
    if FINGERPRINT_KEY not in out:
        out[FINGERPRINT_KEY] = profile_fingerprint()
    return out


ATTRIBUTION_KEY = "context_attribution"


def context_attribution():
    """Per-module context tokens for the selection in force, from `posture.py`; None without it.

    A soft estimate, labelled with its method: see `posture.context_attribution`.
    """
    if not _POSTURE:
        _POSTURE.append(sibling("posture", required=False))
    try:
        return _POSTURE[0].context_attribution() if _POSTURE[0] else None
    except Exception:
        return None


def attributed(record, prior=None, rescan=False):
    """Give a session row its context attribution: the ledger's own, a live read, or none.

    The same rule as the fingerprint's. A transcript does not say which modules its session
    loaded, so a rescan keeps what the ledger already holds for that session and otherwise
    leaves the field out, rather than attributing a past session to this minute's selection.
    """
    known = prior.get(ATTRIBUTION_KEY) if isinstance(prior, dict) else None
    if isinstance(known, dict):
        record[ATTRIBUTION_KEY] = known
    elif not rescan:
        value = context_attribution()
        if value is not None:
            record[ATTRIBUTION_KEY] = value
    return record


def fold(row, folds=None):
    """A copy of `row` with every renamed field under its current name.

    A field whose current name is already present keeps that value: the row was written after
    the rename, and the old key is only a leftover. Unknown fields pass through untouched, so a
    row from a newer writer reads with everything it carries.
    """
    folds = FIELD_FOLDS if folds is None else folds
    out = dict(row)
    for old, new in folds.items():
        if old in out:
            value = out.pop(old)
            out.setdefault(new, value)
    return out


def ledger_rows(text, folds=None):
    """The rows a ledger's text holds, folded, oldest first.

    A line that is not a JSON object is skipped rather than fatal, and a row is never refused
    for a field or a schema version this reader does not know.
    """
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(fold(row, folds))
    return rows


def projects_dir():
    return Path.home() / ".claude" / "projects"


def git(cwd, *args):
    try:
        out = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _result_parts(content, tool_name):
    """A tool result's text and whether it was cut, from a string or a block list alike.

    Only the two tools a detector reads keep their text, and only the first 64 KB of it: the
    event list is held whole in memory, and a `Read` of a large file would otherwise be carried
    through the entire scan for nothing. Whether the cut bit is returned beside the text,
    because a measurement taken over the head of a result is not a measurement of the result.
    """
    if tool_name not in TEXT_KEPT_FOR:
        return "", False
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    else:
        return "", False
    return text[:MAX_RESULT_TEXT], len(text) > MAX_RESULT_TEXT


def _result_text(content, tool_name):
    """The text alone, for a caller that does not care whether it was cut."""
    return _result_parts(content, tool_name)[0]


def merge_slot(per_message, old_key, new_key):
    """Fold one slot into another field-wise, the way `record_usage` keeps a figure.

    Used when a request id that had opened a slot of its own turns out to name a message id,
    which happens whenever the id-bearing records of a call are read after its id-less ones.
    """
    slot = per_message.pop(old_key, None)
    if slot is None:
        return
    target = per_message.get(new_key)
    if target is None:
        per_message[new_key] = slot
        return
    for name in ("day", "model"):
        if slot.get(name) and not target.get(name):
            target[name] = slot[name]
    for name in [field for field, _ in FIELDS] + [field for field, _ in CACHE_TIERS]:
        value = slot.get(name) or 0
        if value > (target.get(name) or 0):
            target[name] = value


def usage_key(links, maps, mid, request_id):
    """The slot an assistant record's usage is counted under, and the slot that key replaces.

    A message id is the key, as ever. A record with no id but a `requestId` keys on that
    instead, unscoped by file: the id names one API call, so the same call written into both a
    session file and a subagent file is one response, and a call whose other records do carry a
    message id joins their slot rather than opening a second one. `links` remembers which key a
    request id resolved to, and `maps` are the slot maps to fold a superseded slot into, because
    the files are not read in the order they were written. A record with neither id is unknown
    rather than a duplicate, so it is not deduplicated at all and the caller counts it.
    """
    request_id = request_id.strip() if isinstance(request_id, str) else ""
    if not mid and not request_id:
        return None, None
    if not request_id:
        return mid, None
    linked = links.get(request_id)
    key = mid or linked or ("request", request_id)
    superseded = None
    # Only a slot this function opened is ever folded away; two message ids under one request
    # id are two messages, whatever the runtime meant by it.
    if isinstance(linked, tuple) and linked != key:
        superseded = linked
        for per_message in maps:
            merge_slot(per_message, linked, key)
    links[request_id] = key
    return key, superseded


def record_usage(per_message, key, usage, day="", model=""):
    """Keep the largest figure a message id ever reported for each field.

    One API response is written as several records. The early ones carry a partial streaming
    `output_tokens` and the last carries the true figure, so taking the first undercounts the
    response badly — on a real subagent transcript, 7,126 output tokens against 40,868. The
    field-wise maximum keeps the final figure without trusting the file's order, which a
    reordered or truncated tail would otherwise lower.

    `day` is that record's UTC date, kept on the slot so the per-day slices are cut from the
    same deduplicated map the totals are summed over and cannot disagree with them. The first
    date a message id is seen under is the one that holds: a response written across midnight
    is one message and belongs to one day.

    `model` is kept the same way and for the same reason: the per-model breakdown is cut from
    this one deduplicated map, so it cannot disagree with the totals summed over it.
    """
    slot = per_message.setdefault(key, dict([(name, 0) for name, _ in FIELDS]
                                            + [("day", ""), ("model", "")]))
    if day and not slot.get("day"):
        slot["day"] = day
    if model and not slot.get("model"):
        slot["model"] = model
    for name, field in FIELDS:
        try:
            value = int(usage.get(field) or 0)
        except (TypeError, ValueError):
            continue
        if value > slot[name]:
            slot[name] = value
    tiers = usage.get("cache_creation")
    if isinstance(tiers, dict):
        for name, field in CACHE_TIERS:
            try:
                value = int(tiers.get(field) or 0)
            except (TypeError, ValueError):
                continue
            if value > slot.get(name, 0):
                slot[name] = value


# What a row says when nothing measured its raw figure: a Codex row, a worker row, a row
# written before this release. Never 1.0 by default — that would claim the deduplication
# removed nothing, which is a measurement nobody made.
RAW_UNKNOWN = "unknown"


def add_raw(raw, usage):
    """Sum one record's usage as written, before any deduplication. See `inflation`."""
    if raw is None:
        return
    for name, field in FIELDS:
        try:
            value = int(usage.get(field) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            raw[name] = raw.get(name, 0) + value


def inflation(raw, totals):
    """The raw per-line sum over the deduplicated total, across the four token fields together.

    One ratio rather than one per field: the fields are deduplicated by the same slots, so four
    figures would be four views of one measurement, and the row already carries every field for
    a reader who wants them apart. A transcript with nothing to remove measures 1.0, which is
    the finding — not the default, which is `RAW_UNKNOWN`.
    """
    if not raw:
        return RAW_UNKNOWN
    counted = sum(max(int(totals.get(name) or 0), 0) for name, _ in FIELDS)
    if counted <= 0:
        return RAW_UNKNOWN
    return round(sum(max(int(raw.get(name) or 0), 0) for name, _ in FIELDS) / float(counted), 3)


def summed(per_message):
    """The four token totals over the messages, each counted once at its largest figure.

    The cache-write tiers ride along when any message reported one, so a row that can be priced
    tier by tier says so and one that cannot carries neither key rather than a pair of zeros
    that would read as writes at the cheaper rate.
    """
    totals = {name: sum(slot[name] for slot in per_message.values()) for name, _ in FIELDS}
    tiers = {name: sum(slot.get(name) or 0 for slot in per_message.values())
             for name, _ in CACHE_TIERS}
    if any(tiers.values()):
        totals.update(tiers)
    return totals


def empty_slice():
    return dict([(name, 0) for name, _ in FIELDS] + [("turns", 0)])


# Every token field a per-model part can carry: the four columns and the two cache-write tiers.
PART_FIELDS = tuple(name for name, _ in FIELDS) + tuple(name for name, _ in CACHE_TIERS)


def by_model(per_message):
    """Token totals per model id, cut from the same map the row's totals are summed over.

    A session that switched models — a compaction on a cheaper one, a subagent on another —
    holds one set of totals and several rates, so without this map it can only be reported in
    tokens. A record naming no model at all makes the map unattributable rather than short, so
    the whole map is dropped: `harness usage` would rather report the row unpriced than price
    part of it.
    """
    out = {}
    for slot in per_message.values():
        name = slot.get("model") or ""
        if not name:
            return {}
        part = out.setdefault(name, dict((field, 0) for field, _ in FIELDS))
        for field in PART_FIELDS:
            value = slot.get(field) or 0
            if value:
                part[field] = part.get(field, 0) + value
    return out


def models_agree(parts, totals):
    """Whether a per-model breakdown adds up to the row's own totals, field by field.

    The same test `slices_agree` applies to the day slices, for the same reason: a breakdown
    that disagreed with the row it sits on would price part of a session twice or not at all.
    A field the runtime never reported is unknown on both sides and is not compared.
    """
    if not parts:
        return False
    for name, _ in FIELDS:
        total = totals.get(name)
        if total is None:
            continue
        if sum(part.get(name) or 0 for part in parts.values()) != total:
            return False
    return True


def daily(per_message, turns_by_day, fallback=""):
    """The `days` map: four token totals and a turn count per UTC date.

    Cut from the same message-id map the row's totals are summed over, so for Claude Code a
    day's slice **includes that day's subagent tokens** exactly as the session total does —
    the session row has one meaning, and a slice that excluded them would not add up to it.
    A message whose record carried no timestamp falls to `fallback`, the session's end date,
    rather than being left out of every slice; with no fallback either there are no slices,
    because a partial one would read as a day that cost less than it did.
    """
    days = {}
    for slot in per_message.values():
        day = slot.get("day") or fallback
        if not day:
            return {}
        row = days.setdefault(day, empty_slice())
        for name, _ in FIELDS:
            row[name] += slot.get(name) or 0
    for day, turns in turns_by_day.items():
        key = day or fallback
        if key:
            days.setdefault(key, empty_slice())["turns"] += turns
    return days


def slices_agree(days, totals):
    """Whether the slices add up to the row's own totals, field by field.

    Checked before the map is written, never after: a `days` map that disagrees with the row it
    sits on would be read as the truth about a date and silently double or lose a day's spend.
    A row whose slices do not agree carries none and falls back to its end date in the report.
    """
    if not days:
        return False
    for name, _ in FIELDS:
        if sum(day.get(name) or 0 for day in days.values()) != (totals.get(name) or 0):
            return False
    return True


def dominant(weights):
    """The key covering the most output tokens, or "" when nothing was weighed.

    Effort changes mid-session in both runtimes — 14 of 112 Claude Code transcripts and 4 of 44
    Codex rollouts measured on one machine — so a row records the value that covered the most
    output rather than the first or the last, and `effort_source` names where it was read.
    Ties break on the name so two reads of one transcript agree.
    """
    weights = dict((key, value) for key, value in weights.items() if key)
    if not weights:
        return ""
    return max(sorted(weights), key=lambda key: weights[key])


def reported_model(counts):
    """The model a transcript's assistant records name most often; the later one on a tie.

    A record is one vote, so a response written as several records weighs as much as it cost to
    write. The tie-break is the last model seen, because a session that changed model mid-run
    ran most recently on the later one.
    """
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def note_model(counts, name, order):
    """One assistant record's model against the tally: `{name: (hits, last seen)}`."""
    if not isinstance(name, str) or not name:
        return
    hits = counts.get(name, (0, 0))[0]
    counts[name] = (hits + 1, order)


def workflow_of(path):
    """The Workflow run a subagent transcript belongs to, `wf_<id>`, or None for a spawned agent.

    The Workflow tool writes its agents one directory deeper, under `subagents/workflows/wf_<id>/`;
    the parent directory's name is the whole test. The usage feed classifies its live lines with
    this same function, so the ledger and the feed cannot disagree about which agent is which.
    """
    name = Path(str(path)).parent.name if path else ""
    return name if name.startswith("wf_") else None


def _agent_row(path, shared=None, budget=None, max_bytes=None, version=None, links=None,
               raw=None):
    """One `kind: "subagent"` row from one `agent-<id>.jsonl`, or None when it holds no turn.

    The sibling `agent-<id>.meta.json` names the agent type and the spawn depth; the transcript
    carries the tokens, the tool calls, the model and, on some records, the effort. Counts only:
    no prompt text and no command text reaches the record.

    The model is the transcript's, not the meta file's: a routed spawn's meta carries the alias
    the spawn hook asked for while a directly spawned agent's carries the full id, and
    one model under two names splits `usage --by model` in half. The alias is the fallback for
    an agent that recorded no model at all.

    `shared` is the session's message-id map. The row keeps its own total, but the session's
    total is taken over that shared map, so a message id written both here and as a sidechain
    line in the session file is one message and is paid for once. `raw` is that map's
    undeduplicated counterpart: this file's records are in the session's totals, so they are in
    the session's `raw_vs_deduped` too.

    `budget` in seconds and `max_bytes` from the tail are for a caller working against a hook
    timeout: the detached `SessionEnd` worker has all the time in the world and passes neither,
    while a live hook cannot be killed halfway through a very large agent's file. When either
    bites, the row carries `partial: True` and its totals are of the part that was read.
    """
    per_message, idless = {}, 0
    links = {} if links is None else links
    maps = [per_message] if shared is None else [per_message, shared]
    partial = False
    try:
        meta = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    seen, tools, models = set(), set(), {}
    calls = turns = records = 0
    effort = started = ended = ""
    try:
        handle = path.open("rb")
    except OSError:
        return None
    deadline = None if budget is None else time.monotonic() + budget
    with handle:
        if max_bytes:
            try:
                size = os.fstat(handle.fileno()).st_size
            except OSError:
                size = 0
            if size > max_bytes:
                # The tail, because the last responses carry the largest figures and a file
                # this size will not be finished inside a hook's timeout either way.
                handle.seek(size - max_bytes)
                handle.readline()
                partial = True
        for index, line in enumerate(handle):
            if deadline is not None and not index % 256 and time.monotonic() > deadline:
                partial = True
                break
            try:
                entry = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            stamp = entry.get("timestamp") or ""
            if stamp:
                started = stamp if not started or stamp < started else started
                ended = stamp if stamp > ended else ended
            if not effort and isinstance(entry.get("effort"), str):
                effort = entry["effort"].strip()
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            mid = message.get("id")
            records += 1
            note_model(models, message.get("model"), records)
            for index, block in enumerate(message.get("content") or []):
                # The same block-repetition the session scan guards against: one API response
                # is written as several lines that repeat its blocks.
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                key = block.get("id") or (mid, block.get("apiBlockIndex", index))
                if key in tools:
                    continue
                tools.add(key)
                calls += 1
            usage = message.get("usage") or {}
            key, superseded = usage_key(links, maps, mid, entry.get("requestId"))
            if key is None:
                # Nothing identifies this record, so nothing may be merged into it. Its key
                # names the file and the line, as it always has, and it is counted as unknown.
                idless += 1
                key = ("line", str(path), idless)
            record_usage(per_message, key, usage, stamp[:10], message.get("model") or "")
            add_raw(raw, usage)
            if shared is not None:
                record_usage(shared, key, usage, stamp[:10], message.get("model") or "")
            # A slot folded into another keeps the turn it was already counted for.
            if superseded is not None and superseded in seen:
                seen.discard(superseded)
                seen.add(key)
            if key in seen:
                continue
            seen.add(key)
            turns += 1
    if not turns:
        # Nothing readable, whether the file held no turn or the budget stopped before one:
        # the caller records that as spend unknown rather than as zero.
        return None
    workflow = workflow_of(path)
    row = {"kind": "subagent", "runtime": "claude-code", "harness_version": version,
           "session_id": "", "repo": "",
           "agent_id": path.stem[len("agent-"):],
           # A Workflow-tool agent may have no meta file at all; unnamed is a fact about the
           # record, and "unknown" says so where an empty string would read as a missing field.
           "agent_type": meta.get("agentType") or "unknown",
           "model": reported_model(models) or meta.get("model") or "",
           "effort": effort or meta.get("effort") or "",
           "tool_calls": calls, "spawn_depth": meta.get("spawnDepth"), "workflow": workflow,
           # `mark_reroutes` fills these from the parent's record of the call, joined on this id.
           "tool_use_id": meta.get("toolUseId") or "",
           "requested_type": "", "rerouted": False,
           # `mark_returns` fills these from the parent's record of the return, joined on the
           # same id. Null is "not measured", never "measured and found nothing", and
           # `return_measured` names the reason where one would otherwise be mistaken for it.
           "return_path": None, "return_over_budget": None, "return_measured": None,
           "turns": turns, "started": started, "ended": ended}
    if partial:
        row["partial"] = True
    # Records this row's totals include that nothing identified — neither a message id nor a
    # request id — so a reader can tell a row that was deduplicated from one that could not be.
    if idless:
        row["idless_records"] = idless
    if workflow:
        # A Workflow-tool agent is launched by the tool, not spawned: no spawn hook routed it and
        # no brief stated it a budget, so a role name it happens to carry prices it at nothing.
        row["unconfined"] = True
        row.update(dict((key, None) for key in BUDGET_KEYS))
    else:
        row.update(budget_fields(row["agent_type"]))
    row.update(summed(per_message))
    return row


# The two spellings of "this spawn named no agent definition"; they are one request, so a spawn
# that ran as `general-purpose` after asking for nothing was not rerouted.
UNNAMED_TYPES = ("", "general-purpose")
# A requested type is model-authored text. Only a name the tool could actually have resolved is
# kept; anything else is recorded as the fact that it was something else, because a usage row is
# a count and must not become a place free text is stored.
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def refused_spawns(agents, errored):
    """The `Agent` call ids in `errored` that never ran: no subagent row names them.

    A spawn a `PreToolUse` hook denied comes back as an error result and writes no subagent
    transcript, so counting it as a subagent reports work nobody did. A spawn that ran and then
    failed also comes back as an error, but it left a transcript whose meta names the call, and
    it stays counted. A subagent file whose meta names no call is still one row in `agents`,
    and the session's count is never below that, so it is counted either way.
    """
    ran = set(row.get("tool_use_id") for row in agents or [] if row.get("tool_use_id"))
    return set(use_id for use_id in errored if use_id not in ran)


def mark_reroutes(agents, requested):
    """Fill `requested_type` and `rerouted` from the parent's `Agent` inputs, joined on tool use id.

    A transcript records a tool input as the model wrote it, before any `PreToolUse` hook
    rewrote it, while the subagent's `.meta.json` records the type it actually ran as. The two
    disagreeing is the reroute — measured from what happened, never announced by the hook that
    did it, so orchestrator compliance is a number and not a claim. Verified on a real
    transcript: the parent recorded `general-purpose`, the subagent's meta said `Explore`,
    under the one tool use id.
    """
    for row in agents:
        use_id = row.get("tool_use_id")
        if not use_id or use_id not in requested:
            continue
        asked = requested[use_id]
        asked = asked.strip() if isinstance(asked, str) else ""
        ran = row.get("agent_type") or ""
        row["requested_type"] = asked if not asked or AGENT_NAME.fullmatch(asked) else "other"
        if ran and ran != "unknown":
            row["rerouted"] = asked != ran and not (asked in UNNAMED_TYPES and ran in UNNAMED_TYPES)


def return_rules():
    """`(cap pattern, agents whose definition carries the cap, the default cap)`, read once.

    All three are the siblings' own: `rule-detectors` decides what counts as a stated bound and
    which agents need not repeat one, and `brief-guard`'s `BOUND` is the cap it appends to every
    brief that states none. A second copy here would sooner or later measure returns against a
    cap no brief ever carried. A sibling that will not import leaves the cap unknown, which the
    row records as unmeasured.
    """
    if not _RETURN_RULES:
        rules = sibling("rule-detectors", required=False)
        guard = sibling("brief-guard", required=False)
        default = re.search(r"\d+", getattr(guard, "BOUND", "") or "")
        _RETURN_RULES.append((getattr(rules, "WORD_CAP_RE", None),
                              getattr(rules, "CAPPED_AGENTS", frozenset()),
                              int(default.group(0)) if default else None))
    return _RETURN_RULES[0]


def return_cap(brief, agent_type):
    """The word cap a return was owed, or None when the scan cannot know one.

    A brief that states a cap is measured against the number beside the word `words`, which is
    not always the first number in the sentence. A brief that states none was capped by
    `brief-guard` at its own default before it reached the agent — the transcript records the
    call as the model wrote it, not as the hook rewrote it (#324) — so that default is the cap.

    Two briefs get no cap at all, because the hook appends none to them: an empty prompt, and a
    spawn of an agent whose own definition carries the cap. `agent_type` there is the type the
    call asked for, as the hook reads it, and not the type the spawn ran as — a reroute must not
    move a return onto a cap its brief never carried.
    """
    pattern, capped, default = return_rules()
    if pattern is None or not (brief or "").strip():
        return None
    match = pattern.search(brief)
    if match is None:
        return None if (agent_type or "") in capped else default
    number = CAP_NUMBER.search(match.group(0))
    if number is None:
        return None
    cap = int(number.group(1) or number.group(2))
    return cap if 0 < cap <= MAX_WORD_CAP else None


def return_roots(cwd, top):
    """Where a path a return names may resolve: the worktree it ran in, and the scratchpad.

    The scratchpad is the temporary directory, which is where `transcript-hygiene` says the long
    version goes. A path that resolves outside both — a system file, another checkout — is not
    the detail this return was asked to write down, so it does not count as one.
    """
    roots = []
    for base in (top, cwd, tempfile.gettempdir()):
        if not base:
            continue
        try:
            real = os.path.realpath(os.path.expanduser(str(base)))
        except (OSError, ValueError):
            continue
        if real not in roots and os.path.isdir(real):
            roots.append(real)
    return roots


def path_shaped(token):
    """True when a token carries a path's own shape rather than a slash between two words."""
    return token.startswith(PATH_ROOTS) or bool(PATH_EXTENSION.search(token.rsplit("/", 1)[-1]))


def path_candidates(text):
    """Every path-shaped token in a return, in order, without repeats.

    Quoted text — a fenced block or inline backticks — is taken at its word: a token written
    inside it with a separator in it was written as a path. Bare prose has to look like one.
    """
    body = URL.sub(" ", text or "")
    quoted = [(m.start(), m.end()) for m in QUOTED.finditer(body)]
    seen, found = set(), []
    for match in PATH_TOKEN.finditer(body):
        token = match.group(0).rstrip(PATH_TRIM)
        if len(token) < 2 or "/" not in token or token in seen:
            continue
        if not (path_shaped(token)
                or any(start <= match.start() and match.end() <= end
                       for start, end in quoted)):
            continue
        seen.add(token)
        found.append(token)
        if len(found) >= MAX_CANDIDATES:
            break
    return found


def resolves(token, roots):
    """True when `token` names something that exists now under one of `roots`.

    A relative path is tried against each root, which is how a return that wrote
    `notes/dimension-a.md` is read. Resolution is taken at the moment the ledger row is written:
    a path that has since been deleted did not resolve, and the row says so rather than
    guessing what was there when the agent returned.
    """
    try:
        expanded = os.path.expanduser(token)
        tries = ([expanded] if os.path.isabs(expanded)
                 else [os.path.join(root, expanded) for root in roots])
        for candidate in tries:
            real = os.path.realpath(candidate)
            if not os.path.exists(real):
                continue
            if any(real == root or real.startswith(root + os.sep) for root in roots):
                return True
    except (OSError, ValueError):
        return False
    return False


def word_count(text):
    """The words a cap counts: tokens carrying a letter or a digit, and no punctuation alone."""
    return sum(1 for token in (text or "").split() if WORD.search(token))


def path_state(text, roots):
    """`"resolvable"`, `"unresolvable"` or `"none"` for one return's text.

    A return that named no path at all carries none. That is a fact about the return and not a
    failure — a one-line verdict owes no file — and the report counts it apart from a return
    whose path went nowhere.
    """
    candidates = path_candidates(text)
    if not candidates:
        return "none"
    return "resolvable" if any(resolves(t, roots) for t in candidates) else "unresolvable"


def mark_returns(agents, briefs, returns, roots):
    """Fill `return_path` and `return_over_budget` from the parent's `Agent` call and its result.

    Deterministic throughout: a string match for the paths, `os.path.exists` for whether one
    resolves, a word count against the cap the brief stated. Nothing here judges what the return
    said — that is #157's question, and these two fields are the labelled input it needs.

    A row whose parent call is not in this transcript keeps both fields `null`. So does a return
    that arrived empty, and one the scan kept only the head of: a truncated result is recorded
    as `return_measured: "truncated"` rather than measured over the part that was read.
    """
    for row in agents:
        use_id = row.get("tool_use_id")
        if not use_id or use_id not in returns:
            continue
        text, truncated = returns[use_id]
        if truncated:
            row[MEASURED_KEY] = "truncated"
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        row["return_path"] = path_state(text, roots)
        cap = return_cap(briefs.get(use_id) or "", row.get("requested_type") or "")
        if cap:
            row["return_over_budget"] = word_count(text) > cap


def agent_rows(transcript, session_id="", shared=None, version=None, links=None, raw=None):
    """Every subagent row belonging to one session transcript, by path.

    Claude Code writes each subagent to `<session>/subagents/agent-<id>.jsonl` beside the
    session's own `<session>.jsonl`, and a Workflow-tool agent one level deeper still, under
    `subagents/workflows/wf_<id>/`. The walk is recursive for that reason. Those tokens were
    spent by this session, so the session row counts them too; the per-agent rows are what
    makes `usage --by role` true.
    """
    path = Path(os.path.expanduser(str(transcript)))
    rows = []
    try:
        files = sorted((path.with_suffix("") / "subagents").rglob("agent-*.jsonl"))
    except OSError:
        return rows
    for file in files:
        row = _agent_row(file, shared, version=version, links=links, raw=raw)
        if row:
            row["session_id"] = session_id or path.stem
            rows.append(row)
    return rows


def scan_all(transcript, session_id="", cwd="", prior=None, rescan=False):
    """Every row one transcript yields: the session first, then one row per subagent."""
    shared, links, raw = {}, {}, {}
    agents = agent_rows(transcript, session_id, shared, version=stamped_version(rescan),
                        links=links, raw=raw)
    record = scan(transcript, session_id, cwd, prior, rescan, agents=agents, shared=shared,
                  links=links, raw=raw)
    if record is None:
        return []
    if record.get("runtime") != "claude-code":
        return [record]
    for row in agents:
        row["session_id"] = record["session_id"]
        row["repo"] = record.get("repo", "")
    return [record] + agents


def scan(transcript, session_id="", cwd="", prior=None, rescan=False, agents=None, shared=None,
         links=None, raw=None):
    """One record from one transcript, or None when there is nothing worth recording.

    `prior` is the record this session already has, when there is one; `rescan` says the read
    is a backfill rather than the session's own end. Together they decide the `stances` field,
    which a backfill can only guess at. `agents` is the subagent rows when the caller has
    already read them, so `scan_all` reads each subagent file once rather than twice, and
    `shared` is the message-id map those reads filled.

    The session's totals are taken over that one map, never as a sum of two sources. Older
    Claude Code wrote a subagent's turns into the session file as sidechain lines while newer
    Claude Code writes them to the subagent's own file; a transcript carrying both would pay
    for every delegated token twice if the two were added.
    """
    per_message = {} if shared is None else shared
    links = {} if links is None else links
    # The same records as `per_message`, summed per line instead of per slot: the row reports
    # the two against each other as `raw_vs_deduped` rather than discarding the raw figure.
    raw = {} if raw is None else raw
    idless = 0
    models, agent_calls, seen, requested = [], set(), set(), {}
    # `Agent` calls whose result came back as an error: a spawn a hook refused, or one that
    # failed after it ran. `refused_spawns` tells the two apart when the row is counted, but
    # only by subagent files: a session file holding sidechain lines is the older format, where
    # a spawn that ran and failed has no file either, so there every errored call stays counted.
    errored_calls = set()
    legacy_sidechains = False
    briefs = {}
    started = ended = branch = ""
    turns = 0
    turns_by_day, efforts = {}, {}
    events, tool_names, blocks_seen = [], {}, set()
    turn, pending_final = 0, None
    try:
        handle = open(os.path.expanduser(str(transcript)), encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        first = handle.readline()
        handle.seek(0)
        if '"session_meta"' in first:
            return scan_codex(transcript, session_id, cwd, prior, rescan)
        if agents is None:
            agents = agent_rows(transcript, session_id, per_message,
                                version=stamped_version(rescan), links=links, raw=raw)
        for line in handle:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            # A subagent has its own transcript file today, but older Claude Code wrote its
            # turns into this one as sidechain lines. They are that agent's work, so they make
            # no event here; their tokens were spent by this session and are summed as ever.
            sidechain = bool(entry.get("isSidechain"))
            legacy_sidechains = legacy_sidechains or sidechain
            stamp = entry.get("timestamp") or ""
            if stamp:
                started = stamp if not started or stamp < started else started
                ended = stamp if stamp > ended else ended
            session_id = session_id or entry.get("sessionId") or ""
            cwd = cwd or entry.get("cwd") or ""
            branch = entry.get("gitBranch") or branch
            kind = entry.get("type")
            message = entry.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            mid = message.get("id") if isinstance(message, dict) else None
            if kind == "system":
                if entry.get("subtype") == "compact_boundary" and not sidechain:
                    events.append({"kind": "compact", "turn": turn})
                continue
            if kind == "user":
                blocks = content if isinstance(content, list) else []
                results = [b for b in blocks
                           if isinstance(b, dict) and b.get("type") == "tool_result"]
                if sidechain:
                    continue
                for block in results:
                    tool_use_id = block.get("tool_use_id") or ""
                    name = tool_names.get(tool_use_id, "")
                    if name == "Agent" and tool_use_id and block.get("is_error") is True:
                        errored_calls.add(tool_use_id)
                    text, cut = _result_parts(block.get("content"), name)
                    events.append({"kind": "tool_result", "turn": turn,
                                   "tool_use_id": tool_use_id, "tool_name": name,
                                   "text": text, "truncated": cut})
                if results or entry.get("isMeta") or entry.get("isCompactSummary"):
                    continue
                turn += 1
                if pending_final is not None:
                    pending_final["final"] = True
                    pending_final = None
                events.append({"kind": "user_prompt", "turn": turn})
                continue
            if kind != "assistant":
                continue
            # A record whose `message` is not a dict holds no usage, no model and no blocks,
            # and reading one as a mapping used to abort the scan of the whole transcript;
            # `_agent_row` has always skipped it.
            if not isinstance(message, dict):
                continue
            model = message.get("model")
            for index, block in enumerate(content or []):
                # One API response is written as several lines that repeat the same message id,
                # each carrying one block; a block seen twice is one block, not two events.
                if sidechain or not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text") or ""
                    key = ("text", mid, block.get("apiBlockIndex", index), text)
                    if key in blocks_seen:
                        continue
                    blocks_seen.add(key)
                    pending_final = {"kind": "assistant_text", "turn": turn, "text": text,
                                     "final": False, "model": model or ""}
                    events.append(pending_final)
                elif block.get("type") == "tool_use":
                    use_id = block.get("id") or ""
                    key = ("tool_use", mid, block.get("apiBlockIndex", index), use_id)
                    if key in blocks_seen:
                        continue
                    blocks_seen.add(key)
                    tool_names[use_id] = block.get("name") or ""
                    events.append({"kind": "tool_use", "turn": turn, "id": use_id,
                                   "name": block.get("name") or "", "input": block.get("input")})
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" \
                        and block.get("name") == "Agent":
                    agent_calls.add(block.get("id") or len(agent_calls))
                    called = block.get("input")
                    if block.get("id") and isinstance(called, dict):
                        requested.setdefault(block["id"], called.get("subagent_type") or "")
                        # The brief as the model wrote it, kept only long enough to read the
                        # word cap off it. No row holds it: see `mark_returns`.
                        brief = called.get("prompt")
                        briefs.setdefault(block["id"], brief if isinstance(brief, str) else "")
            # The same repetition is why the token sums are taken once per message id, not once
            # per line, and at that id's largest figure rather than its first: the early lines
            # of one response carry a partial streaming count.
            key, superseded = usage_key(links, [per_message], mid, entry.get("requestId"))
            if key is None:
                # Nothing identifies this record, so nothing may be merged into it. Its key
                # names the line it came from, and it is counted as unknown rather than
                # silently deduplicated against a record it may have nothing to do with.
                idless += 1
                key = ("line", "session", idless)
            usage = message.get("usage") or {}
            record_usage(per_message, key, usage, stamp[:10], model or "")
            add_raw(raw, usage)
            # Claude Code writes the effort in force on every assistant record, as `effort` and
            # again as `perTurnEffort`; a sidechain line carries the subagent's, not this
            # session's, so only the session's own records are weighed.
            if mid and not sidechain:
                chosen = entry.get("effort") or entry.get("perTurnEffort")
                if isinstance(chosen, str) and chosen.strip():
                    efforts.setdefault(mid, chosen.strip())
            # A slot folded into another keeps the turn it was already counted for.
            if superseded is not None and superseded in seen:
                seen.discard(superseded)
                seen.add(key)
            if key in seen:
                continue
            seen.add(key)
            turns += 1
            # Counted exactly where `turns` is, so the slices' turn counts add up to the row's.
            turns_by_day[stamp[:10]] = turns_by_day.get(stamp[:10], 0) + 1
            if model and model not in models:
                models.append(model)
    if pending_final is not None:
        pending_final["final"] = True
    mark_reroutes(agents, requested)
    if not session_id or not turns:
        return None
    totals = summed(per_message)
    top = git(cwd, "rev-parse", "--show-toplevel") if cwd and os.path.isdir(cwd) else ""
    # After `top`, because a return's paths are resolved against the worktree it ran in. The
    # results are the ones the event list already kept for the detectors, so measuring a return
    # costs no second read of the transcript.
    mark_returns(agents, briefs,
                 dict((e["tool_use_id"], (e["text"], e.get("truncated")))
                      for e in events
                      if e["kind"] == "tool_result" and e.get("tool_name") == "Agent"),
                 return_roots(cwd, top))
    record = {
        "kind": "session",
        "runtime": "claude-code",
        "runtime_version": None,
        "harness_version": stamped_version(rescan),
        "session_id": session_id,
        "repo": os.path.basename(top or str(cwd).rstrip("/")),
        "branch": (git(cwd, "rev-parse", "--abbrev-ref", "HEAD") if top else "") or branch,
        "models": models,
        "started": started,
        "ended": ended,
    }
    # A subagent's tokens are the session's bill, so the session row carries them — once,
    # because `totals` is taken over the one map both reads filled. The per-agent rows carry
    # the same tokens again, attributed, which is why no grouping sums both.
    for name, _ in FIELDS:
        record[name] = totals[name]
    for name, _ in CACHE_TIERS:
        if name in totals:
            record[name] = totals[name]
    refused = set() if legacy_sidechains else refused_spawns(agents, errored_calls)
    record["subagents"] = max(len(agents), len(agent_calls - refused))
    record["turns"] = turns
    # Every record these totals include that nothing identified — neither a message id nor a
    # request id — this session's own and those of the subagent files folded into it, since
    # the totals include both. A row without the key was deduplicated whole.
    unknown = idless + sum(int(row.get("idless_records") or 0) for row in agents or [])
    if unknown:
        record["idless_records"] = unknown
    # How much the deduplication above removed, over the same slots: the totals include the
    # subagent files, so the raw figure does too.
    record["raw_vs_deduped"] = inflation(raw, totals)
    weights = {}
    for mid, chosen in efforts.items():
        weights[chosen] = weights.get(chosen, 0) + ((per_message.get(mid) or {}).get("output") or 0)
    record["effort"] = dominant(weights) or None
    record["effort_source"] = "transcript" if record["effort"] else None
    days = daily(per_message, turns_by_day, (ended or started)[:10])
    if slices_agree(days, totals):
        record["days"] = days
    parts = by_model(per_message)
    if models_agree(parts, totals):
        record["by_model"] = parts
    # A live SessionEnd write knows the stances the session actually ran under. A rescan does
    # not — the environment it reads is this minute's — so it keeps whatever the record already
    # carries, and stamps a record that has none as a guess, which the report then excludes.
    prior_stances = (prior or {}).get("stances") if isinstance(prior, dict) else None
    if isinstance(prior_stances, dict) and prior_stances:
        record["stances"] = prior_stances
        if (prior or {}).get("stances_source"):
            record["stances_source"] = prior["stances_source"]
    else:
        record["stances"] = stances()
        if rescan:
            record["stances_source"] = "rescan"
    attributed(record, prior, rescan)
    try:
        module = detectors()
        record["counts"] = module.counts(events)
        errors = []
        record["rules"] = rule_hits(module, events, record["stances"], cwd, errors)
        if errors:
            record["rules_errors"] = errors
    except Exception as exc:
        # A registry that is missing, broken or a version apart costs the record its rule
        # fields and nothing else; the gap is named so a report never reads it as a quiet zero.
        record.pop("counts", None)
        record.pop("rules", None)
        record["rules_error"] = "{}: {}".format(type(exc).__name__, exc).split("\n")[0][:200]
    return record


# Codex names its token fields differently from Claude Code's and reports `input_tokens`
# inclusive of the cached part, so the mapping lives in one place and `codex_totals` is the only
# reader of it.
CODEX_FIELDS = (("input", "input_tokens"), ("output", "output_tokens"),
                ("cache_read", "cached_input_tokens"),
                ("cache_write", "cache_write_input_tokens"))


def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def codex_spawn(meta):
    """The `thread_spawn` record of a Codex subagent rollout, or None for a top-level session.

    Codex writes a subagent to a rollout file of its own rather than beside its parent's, so
    the `session_meta` is the only thing that tells the two apart: a top-level rollout's
    `payload.source` is a string naming the front end — `"vscode"`, `"cli"`, `"exec"` — while a
    subagent's is the object `{"subagent": {"thread_spawn": {...}}}`. Measured on one machine,
    307 of 438 rollouts are subagent threads, every one of them recorded as a session until
    this test existed.

    The meta's own `parent_thread_id` under `thread_source: "subagent"` is the fallback, so a
    Codex that moves or renames `source` degrades to a joined row rather than to a false
    session; the depth it cannot supply is recorded as unknown rather than guessed at 1.
    """
    source = meta.get("source")
    if isinstance(source, dict):
        spawn = (source.get("subagent") or {}).get("thread_spawn")
        if isinstance(spawn, dict):
            return spawn
    parent = meta.get("parent_thread_id")
    if isinstance(parent, str) and parent and meta.get("thread_source") == "subagent":
        return {"parent_thread_id": parent, "depth": None,
                "agent_nickname": meta.get("agent_nickname"), "agent_role": None}
    return None


def codex_totals(record, totals):
    """Fill a Codex row's token fields from the last `total_token_usage` snapshot.

    `input_tokens` is inclusive of `cached_input_tokens` and `total_tokens` is input plus
    output: checked over the 349 rollouts on one machine that carry a typed split, with no
    exception. `reasoning_output_tokens` is part of `output_tokens` rather than beside it, so
    it is never added anywhere.

    Codex Desktop often writes a snapshot whose typed fields are all zero and whose
    `total_tokens` alone is set — 85 of 107 top-level Desktop rollouts here. Reading that as a
    session that spent nothing would be an error in the direction of free, so the row keeps
    `total` alone, carries `partial`, and leaves every typed field unknown for the report to
    exclude from its sums.
    """
    if not isinstance(totals, dict):
        return
    total = totals.get("total_tokens")
    record["total"] = total if isinstance(total, int) else None
    values = {}
    for name, field in CODEX_FIELDS:
        value = totals.get(field)
        values[name] = value if isinstance(value, int) else None
    if not any(values.values()):
        if record.get("total"):
            record["partial"] = True
        return
    if isinstance(values["input"], int) and isinstance(values["cache_read"], int):
        values["input"] = max(0, values["input"] - values["cache_read"])
    record.update(values)


def codex_days(raw_days, turns_by_day, record):
    """A Codex row's `days` map, from the deltas between its cumulative snapshots.

    A row whose typed fields are unknown — the Codex Desktop snapshot carrying `total_tokens`
    alone — gets no slices at all: there is nothing to slice, and a map of zeros would read as
    days that cost nothing. `input` is made net of the cached part per day, exactly as
    `codex_totals` makes the row's own.
    """
    if any(not isinstance(record.get(name), int) for name, _ in FIELDS):
        return {}
    days = {}
    for day, raw in raw_days.items():
        if not day:
            continue
        slice_ = empty_slice()
        for name, _ in FIELDS:
            slice_[name] = raw.get(name) or 0
        slice_["input"] -= slice_["cache_read"]
        days[day] = slice_
    for day, turns in turns_by_day.items():
        if day:
            days.setdefault(day, empty_slice())["turns"] += turns
    return days


def codex_by_model(raw_models, record):
    """A Codex row's per-model breakdown, from the deltas between its cumulative snapshots.

    `input` is made net of the cached part per model, exactly as `codex_totals` makes the row's
    own, so a model's part is charged the same way the row is. A field the rollout never
    reported — `cache_write`, on every Codex rollout measured — is left off the parts as it is
    left off the row, rather than written as a zero the row does not claim. A row whose typed
    fields are all unknown gets no breakdown: there is nothing to attribute.
    """
    fields = [name for name, _ in FIELDS if isinstance(record.get(name), int)]
    if not fields:
        return {}
    parts = {}
    for model, raw in raw_models.items():
        if not model:
            continue
        part = dict((name, raw.get(name) or 0) for name in fields)
        if "input" in part and "cache_read" in part:
            part["input"] = max(part["input"] - part["cache_read"], 0)
        parts[model] = part
    return parts


def scan_codex(transcript, session_id="", cwd="", prior=None, rescan=False):
    """One row from one Codex rollout: a session, or a subagent thread when it was spawned.

    Which of the two it is comes from `codex_spawn` and nothing else. A subagent row is shaped
    like the Claude Code one `_agent_row` builds, so `usage --by role` reads both without
    knowing which runtime wrote them.
    """
    events, models, totals, meta = [], [], None, {}
    started = ended = effort = ""
    turn = 0
    tool_names = {}
    malformed = 0
    # Codex writes a cumulative snapshot rather than a per-turn figure, so a day's spend and an
    # effort's are the differences between consecutive snapshots, attributed to the date of the
    # snapshot that closed them and to the effort in force when it was written.
    weights, raw_days, turns_by_day, last = {}, {}, {}, dict((name, 0) for name, _ in CODEX_FIELDS)
    # The same delta, attributed a second way: to the model `turn_context` last named. Codex
    # changes model mid-thread, and a thread that did cannot be priced from its totals alone.
    raw_models, model_now = {}, ""
    with open(transcript, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
                payload = item.get("payload") or {}
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
            except (ValueError, AttributeError):
                malformed += 1
                continue
            timestamp = item.get("timestamp") or ""
            started = started or timestamp
            ended = timestamp or ended
            if item.get("type") == "session_meta":
                # The first `session_meta` is this rollout's own. A subagent that inherited its
                # parent's history carries the parent's meta further down — 36 of 307 here —
                # and reading that one would hand the child the parent's id and the parent's
                # string `source`, which is how a subagent was last classified as a session.
                if meta:
                    continue
                meta = payload
                session_id = session_id or meta.get("id", "")
                cwd = cwd or meta.get("cwd", "")
            elif item.get("type") == "turn_context":
                turn += 1
                turns_by_day[timestamp[:10]] = turns_by_day.get(timestamp[:10], 0) + 1
                if payload.get("model"):
                    model_now = payload["model"]
                    if model_now not in models:
                        models.append(model_now)
                # The effort in force from here on: Codex records it per turn and it changes
                # mid-session, `ultra` and `max` among the values seen.
                if isinstance(payload.get("effort"), str) and payload["effort"].strip():
                    effort = payload["effort"].strip()
            elif item.get("type") == "event_msg" and payload.get("type") == "token_count":
                value = (payload.get("info") or {}).get("total_token_usage")
                if isinstance(value, dict):
                    totals = value  # Cumulative snapshot; summing snapshots double counts usage.
                    slice_ = raw_days.setdefault(timestamp[:10], empty_slice())
                    part = raw_models.setdefault(model_now, empty_slice()) if model_now else None
                    for name, field in CODEX_FIELDS:
                        now = value.get(field)
                        if not isinstance(now, int):
                            continue
                        slice_[name] += now - last[name]
                        if part is not None:
                            part[name] += now - last[name]
                        if name == "output":
                            weights[effort] = weights.get(effort, 0) + (now - last[name])
                        last[name] = now
            elif item.get("type") == "response_item":
                kind = payload.get("type")
                call_id = payload.get("call_id", "")
                if kind in ("function_call", "custom_tool_call"):
                    name = payload.get("name", "")
                    name = {"exec_command": "Bash", "spawn_agent": "Agent"}.get(name, name)
                    arguments = payload.get("arguments", payload.get("input", {}))
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            arguments = {"command": arguments}
                    if isinstance(arguments, dict) and "cmd" in arguments:
                        arguments = dict(arguments, command=arguments["cmd"])
                    tool_names[call_id] = name
                    events.append({"kind": "tool_use", "turn": turn, "id": call_id,
                                   "name": name, "input": arguments})
                elif kind in ("function_call_output", "custom_tool_call_output"):
                    name = tool_names.get(call_id, "")
                    events.append({"kind": "tool_result", "turn": turn, "tool_use_id": call_id,
                                   "tool_name": name, "text": _result_text(payload.get("output"), name)})
                elif kind == "message" and payload.get("role") == "assistant":
                    text = "\n".join(x.get("text", "") for x in payload.get("content", []) if isinstance(x, dict))
                    events.append({"kind": "assistant_text", "turn": turn, "text": text,
                                   "final": payload.get("phase") == "final_answer"})
    if not session_id:
        return None
    spawn = codex_spawn(meta)
    # A subagent whose parent cannot be named would join to nothing and appear in no report, so
    # it is kept as the session it was recorded as rather than turned into an invisible row.
    parent = (spawn or {}).get("parent_thread_id") or meta.get("session_id") or ""
    if spawn and parent and parent != session_id:
        row = {
            "kind": "subagent", "runtime": "codex",
            "runtime_version": meta.get("cli_version"),
            "harness_version": stamped_version(rescan),
            # `session_id` is the thread that spawned this one, which at depth 1 is the session
            # and deeper is another subagent; `spawn_depth` is what says which.
            "session_id": parent, "agent_id": session_id,
            "repo": Path(cwd).name,
            # Codex leaves `agent_role` null and names the thread on every rollout measured
            # here, so the nickname is the fallback that actually carries the report.
            "agent_type": spawn.get("agent_role") or spawn.get("agent_nickname")
                          or meta.get("agent_nickname") or "unknown",
            "model": models[-1] if models else "",
            "effort": dominant(weights) or effort,
            "tool_calls": sum(1 for e in events if e["kind"] == "tool_use"),
            "spawn_depth": spawn.get("depth"), "workflow": None,
            # Codex records no parent-side tool use id on the child, so there is nothing to
            # join a reroute on; null is that absence, not a measurement of no reroute.
            "tool_use_id": None, "requested_type": None, "rerouted": False,
            # Codex raises no subagent-return event on the parent thread and writes the child to
            # a rollout of its own, so no return is joined to this row and none is measured;
            # `adapters/codex/capabilities.json` names the gap.
            "return_path": None, "return_over_budget": None, "return_measured": None,
            "turns": turn, "started": started, "ended": ended,
            "parse_failures": malformed,
        }
        codex_totals(row, totals)
        return row
    record = {"kind": "session", "runtime": "codex",
              "runtime_version": meta.get("cli_version"),
              "harness_version": stamped_version(rescan), "session_id": session_id,
              "repo": Path(cwd).name, "branch": git(cwd, "rev-parse", "--abbrev-ref", "HEAD") if cwd else "",
              "models": models, "started": started, "ended": ended, "turns": turn,
              "subagents": sum(e.get("name") == "Agent" and e["kind"] == "tool_use" for e in events),
              "stances": (prior or {}).get("stances") or stances(), "input": None, "output": None,
              "cache_read": None, "cache_write": None, "parse_failures": malformed}
    if rescan and not (prior or {}).get("stances"):
        record["stances_source"] = "rescan"
    attributed(record, prior, rescan)
    codex_totals(record, totals)
    chosen = dominant(weights) or effort
    record["effort"] = chosen or None
    record["effort_source"] = "turn_context" if chosen else None
    # Codex reports cumulative snapshots, not a figure per record, so there is no per-line sum
    # to measure a deduplication against and none is invented.
    record["raw_vs_deduped"] = RAW_UNKNOWN
    days = codex_days(raw_days, turns_by_day, record)
    if slices_agree(days, record):
        record["days"] = days
    parts = codex_by_model(raw_models, record)
    if models_agree(parts, record):
        record["by_model"] = parts
    errors = []
    try:
        module = detectors()
        record["counts"] = module.counts(events)
        record["rules"] = rule_hits(module, events, record["stances"], cwd, errors)
        if errors:
            record["rules_errors"] = errors
    except Exception as exc:
        record["rules_error"] = type(exc).__name__
    return record


def row_key(row):
    """What identifies a row. A row written before `kind` existed is a session, as it was."""
    return (row.get("session_id"), row.get("runtime", "claude-code"),
            row.get("kind") or "session", row.get("agent_id") or "")


def upsert(record, path=None, drop=()):
    """Replace the rows these records identify, or append them. Takes one record or many.

    A batch keeps the last record for a key, so one locked rewrite is what a whole rescan costs.

    `drop` is the keys to delete outright. A row that changes `kind` changes its key, so an
    upsert alone would leave the old row beside the new one and the ledger would carry the same
    thread twice; naming the stale key is how a reclassification migrates rather than doubles.
    """
    records = [stamped(record)] if isinstance(record, dict) else list(
        {row_key(r): stamped(r) for r in record}.values())
    drop = set(drop)
    if not records and not drop:
        return Path(path) if path else usage_path()
    path = Path(path) if path else usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire(path)
    if lock is None:
        raise RuntimeError("usage lock unavailable; no record was overwritten")
    held = True
    try:
        rows = []
        replaced = {row_key(r) for r in records} | drop
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # An existing row is kept exactly as it was written, and matched on its folded key:
        # the rewrite replaces records, it does not migrate anyone else's.
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row_key(fold(row)) not in replaced:
                rows.append(row)
        rows.extend(records)
        tmp = path.with_name("{}.{}.tmp".format(path.name, os.getpid()))
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        release(lock, held)
    return path


def acquire(path):
    """The lock file beside the ledger, held, or None when another writer would not let go."""
    lock = path.with_name(path.name + ".lock")
    for _ in range(20):
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return lock
        except FileExistsError:
            time.sleep(0.05)
        except OSError:
            return None
    return None


def release(lock, held=True):
    if held and lock is not None:
        try:
            lock.unlink()
        except OSError:
            pass


def errors_path(path=None):
    """`usage.errors.jsonl` beside the ledger this path names."""
    return (Path(path) if path else usage_path()).with_suffix(".errors.jsonl")


def record_error(error, path=None, where=""):
    """Append one swallowed failure beside the ledger. Never raises.

    The same file this hook's own crash lands in, because a write that failed silently is
    unknown rather than absent: a report with no rows in it has somewhere to be explained. The
    exception type, never its message — a message can carry a path or a value.
    """
    entry = {"time": time.time(), "error": type(error).__name__ if isinstance(error, BaseException)
             else str(error)}
    if where:
        entry["where"] = where
    try:
        target = errors_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
    except OSError:
        return False
    return True


def append_row(record, path=None):
    """Append one row to the ledger without rewriting it, and return its path.

    For a row nothing ever replaces. `upsert` reads and rewrites the whole file, which is right
    for a session record refreshed while the session runs and wrong for a row written once
    inside a hook's budget: a ledger of tens of thousands of lines would be re-read and
    rewritten on every provider call. The append is one write of one line, under the same lock,
    so a concurrent rewrite can neither interleave with it nor drop it.
    """
    path = Path(path) if path else usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire(path)
    if lock is None:
        raise RuntimeError("usage lock unavailable; the record was not appended")
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(stamped(record)) + "\n")
    finally:
        release(lock)
    return path


def export(records):
    """Offer rows to a configured OTLP endpoint, after the ledger already holds them.

    Off by default, and silent in every failure mode: the row is on disk, so a collector that
    is down, slow or misconfigured costs a line in `usage.errors.jsonl` and nothing else.
    `harness usage export --since` replays what was missed. See `telemetry.py`.
    """
    module = sibling("telemetry", required=False)
    if module is None:
        return 0, 0
    try:
        return module.export_rows(records, version=harness_version() or "",
                                  errors_path=usage_path().with_suffix(".errors.jsonl"))
    except Exception:
        return 0, 0


def recorded(path=None):
    """The records already on file, by session id, so a rescan can keep what it cannot know."""
    try:
        text = (Path(path) if path else usage_path()).read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for row in ledger_rows(text):
        if row.get("session_id") and (row.get("kind") or "session") == "session":
            out[row["session_id"]] = row
    return out


def workers_dir():
    return Path.home() / ".local" / "state" / "agent-harness" / "workers"


def stamp(epoch):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(float(epoch)))
    except (TypeError, ValueError):
        return ""


def worker_rows(cutoff=0.0):
    """One `kind: "worker"` row per `harness role run` worker, from its own `status.json`.

    A worker is an isolated CLI session whose runtime reports its own token totals; `workers.py`
    writes them into the status record. A worker that reported none keeps its row and leaves the
    token fields unknown, which the report then excludes from its sums rather than reading as
    zero. The role name is the agent type, so a worker and a subagent group the same way.

    Only a completed run is recorded: a run that timed out, failed or is still running has no
    total worth comparing against another role's. The window is applied by file modification
    time before the file is opened, so a sweep reads the recent runs and not the archive, and
    a record whose own timestamps are unusable is dated by that same mtime rather than by a
    stamp the report could never place in a window.
    """
    rows = []
    try:
        paths = sorted(workers_dir().glob("*/status.json"))
    except OSError:
        return rows
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not record.get("id") or not record.get("role"):
            continue
        if record.get("status") != "completed":
            continue
        ended = stamp(record.get("finished_at") or record.get("started_at")) or stamp(mtime)
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        row = {"kind": "worker", "runtime": record.get("runtime") or "claude-code",
               # Stamped by `workers.py` when the run started, so a sweep months later still
               # names the version that ran it rather than the version reading the file.
               "harness_version": record.get("harness_version"),
               # The same for the profile; a run from before the field is unattributed.
               FINGERPRINT_KEY: record.get(FINGERPRINT_KEY),
               "session_id": record["id"], "agent_id": record["id"],
               "agent_type": record["role"], "repo": os.path.basename(str(record.get("workspace") or "").rstrip("/")),
               "model": record.get("model") or "", "effort": record.get("effort") or "",
               "tool_calls": usage.get("tool_calls"), "spawn_depth": 1, "rerouted": False,
               # A worker is launched by name from the CLI, so there is no requested type and no
               # parent tool call to join on; null is that absence, not an empty answer. Present
               # so every non-session row carries the same keys.
               "requested_type": None, "tool_use_id": None,
               "status": record.get("status"), "stances": record.get("stances") or {},
               "started": stamp(record.get("started_at")) or ended, "ended": ended}
        for name, _ in FIELDS:
            row[name] = usage.get(name)
        rows.append(row)
    return rows


def backup(path):
    """A copy of the ledger beside it, taken before a rescan rewrites or deletes any row."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    target = path.with_name(path.name + ".bak")
    try:
        target.write_bytes(data)
    except OSError:
        return None
    return target


def rescan(days=30):
    """Re-read every transcript in the window and rewrite the file once.

    A backfill of a month reads hundreds of transcripts. Upserting each one separately would
    take the lock and rewrite the whole file that many times, so the rows are collected and
    written in a single locked pass; `SessionEnd` keeps the one-session path.

    Codex is read from both `sessions/` and `archived_sessions/`, because Codex moves a rollout
    to the second directory without changing a byte of it: 96 of the 131 top-level rollouts on
    one machine lived only there, which is most of the capture gap this walk closes.
    """
    cutoff = time.time() - max(days, 0) * 86400
    prior = recorded()
    found, batch = 0, []
    codex = codex_home()
    paths = list(projects_dir().glob("*/*.jsonl"))
    for folder in ("sessions", "archived_sessions"):
        paths += list((codex / folder).rglob("*.jsonl"))
    for path in sorted(paths):
        # A Claude Code subagent transcript is read from its session, never as one: it carries
        # no session id of its own, so recording it here would invent a session that never ran.
        # A Codex subagent is the opposite — its own rollout, named like any other — so it is
        # walked here and told apart by `codex_spawn` once its first line has been read.
        if path.name.startswith("agent-") or path.parent.name == "subagents":
            continue
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        # A transcript is named for its session, which is how a backfill finds the record it
        # is refreshing before it has read a line of the file.
        ident = path.stem
        try:
            with path.open() as stream:
                first = json.loads(stream.readline())
            if first.get("type") == "session_meta":
                ident = first.get("payload", {}).get("id", ident)
        except (OSError, ValueError):
            pass
        records = scan_all(path, prior=prior.get(ident), rescan=True)
        # A transcript does not say which profile ran it. A session the ledger already holds
        # keeps the fingerprint its live row was written with, and its subagents ran under the
        # same profile; a session it does not is unattributed.
        known = (prior.get(ident) or {}).get(FINGERPRINT_KEY)
        for record in records:
            record[FINGERPRINT_KEY] = known
        if records:
            batch.extend(records)
            found += 1
    batch.extend(worker_rows(cutoff))
    children = {}
    for row in batch:
        if row.get("kind") == "subagent" and row.get("runtime") == "codex":
            children[row["session_id"]] = children.get(row["session_id"], 0) + 1
    # Codex counts a session's subagents from its own `spawn_agent` calls, which misses a spawn
    # whose rollout this walk found but whose parent call was compacted away; the larger of the
    # two is the one supported by a file on disk.
    for row in batch:
        if row.get("kind") == "session" and row.get("runtime") == "codex":
            row["subagents"] = max(row.get("subagents") or 0, children.get(row["session_id"], 0))
    drop = set((row["agent_id"], "codex", "session", "")
               for row in batch
               if row.get("kind") == "subagent" and row.get("runtime") == "codex")
    if batch or drop:
        backup(usage_path())
        upsert(batch, drop=drop)
    return found


def main(argv):
    if argv and argv[0] == "--worker":
        transcript, session_id, cwd = (list(argv[1:]) + ["", "", ""])[:3]
        # Role-run workers have no session of their own to end, so the detached worker that
        # records this session also sweeps the recent ones into rows.
        records = scan_all(transcript, session_id, cwd) + worker_rows(time.time() - 30 * 86400)
        # Stamped here as well as in `upsert`, so what is offered live is what a replay reads.
        records = [stamped(r) for r in records]
        if records:
            upsert(records)
            export(records)
        return 0
    if argv and argv[0] == "--rescan":
        try:
            days = int(argv[1]) if len(argv) > 1 else 30
        except ValueError:
            days = 30
        print("recorded {} session(s) from transcripts of the last {} day(s)".format(rescan(days), days))
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    # Claude Code names the file `transcript_path`. Codex's SessionEnd payload has not been
    # observed here — no Codex CLI is installed on the machine this was measured on — so the
    # two names it could plausibly use are accepted and the rescan remains the path Codex
    # capture is actually known to travel. See `docs/usage.md`.
    payload = payload or {}
    transcript = (payload.get("transcript_path") or payload.get("rollout_path")
                  or payload.get("session_path") or "")
    if not transcript:
        return 0
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker", str(transcript),
         payload.get("session_id") or "", payload.get("cwd") or ""],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        record_error(exc)
        sys.exit(1)
