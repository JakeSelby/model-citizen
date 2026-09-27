#!/usr/bin/env python3
"""Which rules fired in a replay run, read offline from its saved `-p` stream.

Every detector in `policy/hooks/rule-detectors.py` runs over each stream `--raw` kept, with no
model call, and each run gets one row per detector: the count and the turn of every firing. A
turn here is the run's model call, counted from 1 in the order the stream reports them, since a
headless run has one prompt and the transcript's own turn counter would put every firing on it.
A tool result takes the turn of the call that asked for it.

Two readings are deliberate. Every detector runs whatever its stance gate says, because the
bare arm has no stances and a gated detector would measure only one arm. A subagent's own
messages, which the stream tags with `parent_tool_use_id`, are that agent's work, not the run's,
so they make no event; its return still does, as the spawning call's tool result.

`cost_bench.py detect` is the command line; reading and limits are in docs/benchmarks.md.
"""
import importlib.util
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETECTORS_PATH = ROOT / "policy" / "hooks" / "rule-detectors.py"
DETECTIONS = "detections.jsonl"
RESULTS = "results.jsonl"
# Older Claude Code named the spawn tool `Task`; the detectors read `Agent`.
TOOL_ALIASES = {"Task": "Agent"}
# Where a set's raw streams may sit, relative to the directory holding its `results.jsonl`.
RAW_PLACES = (".", "../transcripts", "../raw", "raw", "transcripts")


def load_detectors(path=DETECTORS_PATH):
    """The rule-detector module, loaded by path as `bin/harness` loads it."""
    spec = importlib.util.spec_from_file_location("replay_rule_detectors", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ungated(module):
    """The module's whole registry with every stance gate lifted, in registry order."""
    return module.Registry([module.Detector(d.id, d.rule, d.event, d.fn, None)
                            for d in module.DETECTORS.values()])


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def stream_events(messages):
    """The detector event list for one run's messages. ValueError when they hold no model call,
    as a lone result document does: such a file says nothing about what fired."""
    events, names, use_turn, calls = [], {}, {}, {}
    turn, final = 0, None
    for message in messages:
        if not isinstance(message, dict) or message.get("parent_tool_use_id"):
            continue
        kind = message.get("type")
        if kind == "system":
            if message.get("subtype") == "compact_boundary":
                events.append({"kind": "compact", "turn": turn})
            continue
        body = message.get("message")
        body = body if isinstance(body, dict) else {}
        content = body.get("content")
        blocks = content if isinstance(content, list) else []
        if kind == "assistant":
            mid = body.get("id")
            if mid and mid in calls:
                current = calls[mid]
            else:
                turn += 1
                current = turn
                if mid:
                    calls[mid] = turn
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    final = {"kind": "assistant_text", "turn": current, "text": block.get("text") or "",
                             "final": False, "model": body.get("model") or ""}
                    events.append(final)
                elif block.get("type") == "tool_use":
                    use_id = block.get("id") or ""
                    if use_id and use_id in use_turn:
                        continue
                    name = block.get("name") or ""
                    name = TOOL_ALIASES.get(name, name)
                    names[use_id], use_turn[use_id] = name, current
                    events.append({"kind": "tool_use", "turn": current, "id": use_id, "name": name,
                                   "input": block.get("input")})
        elif kind == "user":
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    use_id = block.get("tool_use_id") or ""
                    events.append({"kind": "tool_result", "turn": use_turn.get(use_id, turn),
                                   "tool_use_id": use_id, "tool_name": names.get(use_id, ""),
                                   "text": _result_text(block.get("content"))})
    if not turn:
        raise ValueError("no model call in the stream")
    if final is not None:
        final["final"] = True
    return events


def detect_messages(messages, module, registry=None):
    """`{detector_id: [turn, ...]}` for every detector, empty lists included, and the errors.
    ValueError when the messages hold no model call."""
    events = stream_events(messages)
    registry = registry or ungated(module)
    errors = []
    hits = module._run(events, None, registry=registry, errors=errors)
    failed = dict((e["detector"], e["error"]) for e in errors)
    if "analysis" in failed:
        raise ValueError("the analysis failed: %s" % failed["analysis"])
    out = {}
    for detector in registry:
        if detector.id in failed:
            out[detector.id] = failed[detector.id]
        else:
            out[detector.id] = sorted(h.turn for h in hits.get(detector.id, ()))
    return out


def run_rows(identity, text, reader, module, registry=None):
    """One run's rows, one per detector. A stream that cannot be read, or a detector that
    raised, is a row with `count` null and the reason in `error`: unknown, never zero."""
    registry = registry or ungated(module)
    try:
        found = detect_messages(reader(text)[0], module, registry)
    except (TypeError, ValueError) as exc:
        found = dict((d.id, "unreadable: %s" % exc) for d in registry)
    rows = []
    for detector in registry:
        value = found[detector.id]
        row = dict(identity, detector=detector.id, rule=detector.rule)
        if isinstance(value, list):
            row.update(count=len(value), turns=value)
        else:
            row.update(count=None, turns=None, error=value)
        rows.append(row)
    return rows


def missing_rows(identity, reason, registry):
    return [dict(identity, detector=d.id, rule=d.rule, count=None, turns=None, error=reason)
            for d in registry]


def raw_name(task, arm, rep):
    """The runner's `--raw` file name for one run: `<task>-<arm>-<rep>.json`."""
    return "%s-%s-%s.json" % (task, arm, rep)


def parse_name(name, arms):
    """(task, arm, rep) from a `--raw` file name, or None for any other file, the preflights
    included. The arm is matched from the right, so a task id may carry hyphens."""
    match = re.match(r"^(.+)-(%s)-(\d+)\.json$" % "|".join(re.escape(a) for a in arms), name)
    return (match.group(1), match.group(2), int(match.group(3))) if match else None


def detect_dir(raw_dir, arms, reader, module):
    """(rows, runs) for every run file directly in `raw_dir`, in name order."""
    registry = ungated(module)
    rows, runs = [], 0
    for path in sorted(Path(raw_dir).iterdir()):
        parsed = parse_name(path.name, arms)
        if not parsed or not path.is_file():
            continue
        task, arm, rep = parsed
        runs += 1
        rows.extend(run_rows({"task": task, "arm": arm, "rep": rep, "source": path.name},
                             path.read_text(encoding="utf-8", errors="replace"), reader, module, registry))
    return rows, runs


def raw_candidates(raw_dirs, row):
    """Every existing raw file for one result row across `raw_dirs` and their subdirectories."""
    name = raw_name(row.get("task"), row.get("arm"), row.get("rep"))
    found = []
    for base in raw_dirs:
        base = Path(base)
        if not base.is_dir():
            continue
        for directory in [base] + sorted(p for p in base.iterdir() if p.is_dir()):
            if (directory / name).is_file() and (directory / name).resolve() not in found:
                found.append((directory / name).resolve())
    return found


IDENTITY = ("task", "arm", "rep", "tag", "harness_version")


def detect_rows(rows, raw_dirs, reader, module):
    """The rows for a set's `results.jsonl` rows, each run's stream found in `raw_dirs`.

    A run whose stream is in none of them, or in more than one, gets error rows rather than a
    guess: two files of one name are two different runs."""
    registry = ungated(module)
    out = []
    for row in rows:
        identity = dict((key, row.get(key)) for key in IDENTITY if key in row)
        found = raw_candidates(raw_dirs, row)
        if len(found) != 1:
            reason = "no raw output" if not found else "ambiguous raw output: %d files" % len(found)
            out.extend(missing_rows(identity, reason, registry))
            continue
        identity["source"] = found[0].name
        out.extend(run_rows(identity, found[0].read_text(encoding="utf-8", errors="replace"),
                            reader, module, registry))
    return out


def results_files(root):
    """Every `results.jsonl` under `root`, in path order."""
    found = []
    for directory, dirs, files in os.walk(str(root)):
        dirs.sort()
        if RESULTS in files:
            found.append(Path(directory) / RESULTS)
    return sorted(found)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def backfill(root, reader, module):
    """Write `detections.jsonl` beside every `results.jsonl` under `root`; read the results
    and never write them. Returns `[(detections path, runs, runs without a stream)]`."""
    report = []
    for results in results_files(root):
        rows = read_jsonl(results)
        raw_dirs = [(results.parent / place).resolve() for place in RAW_PLACES]
        seen, dirs = set(), []
        for d in raw_dirs:
            if d not in seen:
                seen.add(d)
                dirs.append(d)
        detections = detect_rows(rows, dirs, reader, module)
        target = results.parent / DETECTIONS
        write_jsonl(target, detections)
        unread = len(set((r.get("task"), r.get("arm"), r.get("rep")) for r in detections
                         if r.get("count") is None))
        report.append((target, len(rows), unread))
    return report


def mechanisms(detections, arm="harness"):
    """Per task, which detectors fired in `arm` and how often across its reps:
    `{task: {"runs": runs measured, "fired": {detector: {"runs": runs it fired in, "hits": n}}}}`.
    A run whose stream could not be read is not a run measured."""
    runs, fired = {}, {}
    for row in detections:
        if row.get("arm") != arm or row.get("count") is None:
            continue
        task = row.get("task")
        runs.setdefault(task, set()).add(row.get("rep"))
        if row["count"]:
            cell = fired.setdefault(task, {}).setdefault(row["detector"], {"runs": 0, "hits": 0})
            cell["runs"] += 1
            cell["hits"] += row["count"]
    return dict((task, {"runs": len(reps), "fired": fired.get(task, {})}) for task, reps in runs.items())


def render_mechanisms(result):
    """The history lines for one row's `mechanisms`, one per task."""
    lines = []
    for task, cell in sorted(result.items(), key=lambda item: str(item[0])):
        fired = cell.get("fired") or {}
        if fired:
            text = ", ".join("%s in %d of %d run(s), %d hit(s)" % (d, c["runs"], cell["runs"], c["hits"])
                             for d, c in sorted(fired.items()))
        else:
            text = "none in %d run(s)" % cell["runs"]
        lines.append("    fired in harness arm, %s: %s" % (task or "n/a", text))
    return lines
