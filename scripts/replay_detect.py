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
import hashlib
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


def path_rows(identity, path, reader, module, registry):
    """One existing raw path as detector rows; an I/O failure remains an unknown run."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return missing_rows(identity, "unreadable: %s" % type(exc).__name__, registry)
    return run_rows(identity, text, reader, module, registry)


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
        rows.extend(path_rows({"task": task, "arm": arm, "rep": rep, "source": path.name},
                              path, reader, module, registry))
    return rows, runs


def raw_candidates(raw_dirs, row):
    """Every existing raw file for one result row directly in one of `raw_dirs`."""
    name = raw_name(row.get("task"), row.get("arm"), row.get("rep"))
    found = []
    for directory in raw_dirs:
        path = Path(directory) / name
        if path.is_file() and path.resolve() not in found:
            found.append(path.resolve())
    return found


IDENTITY = ("task", "arm", "rep", "tag", "harness_version")


def _identity(row):
    return dict((key, row.get(key)) for key in IDENTITY if key in row)


def detect_rows(rows, raw_dirs, reader, module, shared=None):
    """The rows for a set's `results.jsonl` rows, each run's stream found in `raw_dirs`.

    A run whose stream is in none of them, in more than one, or in a directory `shared` maps to
    the number of sets that search it, gets error rows rather than a guess: stream names carry no
    tag, so two files of one name are two different runs, and one file where two sets look may
    be either set's."""
    registry = ungated(module)
    shared = shared or {}
    out = []
    for row in rows:
        identity = _identity(row)
        found = raw_candidates(raw_dirs, row)
        if len(found) != 1:
            reason = "no raw output" if not found else "ambiguous raw output: %d files" % len(found)
            out.extend(missing_rows(identity, reason, registry))
            continue
        if found[0].parent in shared:
            out.extend(missing_rows(identity, "ambiguous raw output: %s is searched by %d sets"
                                    % (found[0].parent.name, shared[found[0].parent]), registry))
            continue
        identity["source"] = found[0].name
        out.extend(path_rows(identity, found[0], reader, module, registry))
    return out


def detect_saved(rows, streams, reader, module):
    """The rows for a replay's own set, each run read only from the stream it saved itself:
    `streams` maps `(task, arm, rep)` to `(path, sha256)`, as `cost_bench.save_stream` records
    them. A run that saved none, a timeout among them, or whose file has changed since, gets
    error rows: another run's stream under the same name is never read."""
    registry = ungated(module)
    out = []
    for row in rows:
        identity = _identity(row)
        saved = streams.get((row.get("task"), row.get("arm"), row.get("rep")))
        if saved is None:
            reason = "no stream saved by this run"
            if row.get("error_kind"):
                reason += " (%s)" % row["error_kind"]
            out.extend(missing_rows(identity, reason, registry))
            continue
        path, digest = Path(saved[0]), saved[1]
        identity["source"] = path.name
        try:
            data = path.read_bytes()
        except OSError as exc:
            out.extend(missing_rows(identity, "unreadable: %s" % type(exc).__name__, registry))
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            out.extend(missing_rows(identity, "stream changed since this run saved it", registry))
            continue
        out.extend(run_rows(identity, data.decode("utf-8", errors="replace"), reader, module, registry))
    return out


def place_dirs(results_dir):
    """The directories, resolved and in `RAW_PLACES` order, where a set's streams may sit."""
    out = []
    for place in RAW_PLACES:
        directory = (Path(results_dir) / place).resolve()
        if directory not in out:
            out.append(directory)
    return out


def _holds_set(directory):
    return (Path(directory) / RESULTS).is_file()


def search_dirs(results_dir):
    """[(directory, place)] a set's streams are looked for in: each existing place and its
    immediate subdirectories, less any subdirectory that is a set of its own."""
    out, seen = [], set()
    for place in place_dirs(results_dir):
        if not place.is_dir():
            continue
        subdirs = sorted(p.resolve() for p in place.iterdir() if p.is_dir() and not _holds_set(p))
        for directory in [place] + subdirs:
            if directory not in seen:
                seen.add(directory)
                out.append((directory, place))
    return out


def claimants(directory):
    """Every set directory with `directory` among its places, wherever the backfill root is:
    such a set is `directory` itself, its parent, or one of its siblings."""
    directory = Path(directory).resolve()
    parent = directory.parent
    candidates = [directory, parent]
    if parent.is_dir():
        candidates.extend(p for p in sorted(parent.iterdir()) if p.is_dir())
    return set(c.resolve() for c in candidates if _holds_set(c) and directory in place_dirs(c))


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


def unreadable(rows, key):
    """How many runs, grouped by `key`, had no readable stream: every one of their rows is unknown.
    A run where one detector raised still read its stream, so it is not counted."""
    missing = {}
    for row in rows:
        missing.setdefault(key(row), []).append(row.get("count") is None)
    return sum(1 for flags in missing.values() if all(flags))


def backfill(root, reader, module, overwrite=False):
    """Write `detections.jsonl` beside every `results.jsonl` under `root`; read the results
    and never write them, and leave an existing `detections.jsonl` alone unless `overwrite`.
    A stream directory another set also searches, a sibling set sharing `../raw` for one, is
    ambiguous for both. Returns `[(detections path, runs, runs without a stream)]`, the counts
    None for a set left alone."""
    report = []
    for results in results_files(root):
        target = results.parent / DETECTIONS
        if target.exists() and not overwrite:
            report.append((target, None, None))
            continue
        rows = read_jsonl(results)
        own = results.parent.resolve()
        dirs, shared = [], {}
        for directory, place in search_dirs(own):
            dirs.append(directory)
            sets = claimants(place) | claimants(directory) | {own}
            if len(sets) > 1:
                shared[directory] = len(sets)
        detections = detect_rows(rows, dirs, reader, module, shared)
        write_jsonl(target, detections)
        unread = unreadable(detections, lambda r: (r.get("task"), r.get("arm"), r.get("rep")))
        report.append((target, len(rows), unread))
    return report


def mechanisms(detections, arm="harness"):
    """Per task, which detectors fired in `arm` and how often across its reps:
    total runs, each firing detector's measured denominator, and every detector error. Unknown
    detector rows remain in the total and error counts rather than shrinking a denominator."""
    runs, measured, fired, errors = {}, {}, {}, {}
    for row in detections:
        if row.get("arm") != arm:
            continue
        task, detector, rep = row.get("task"), row.get("detector"), row.get("rep")
        runs.setdefault(task, set()).add(row.get("rep"))
        if row.get("count") is None:
            reason = str(row.get("error") or "unknown")
            cell = errors.setdefault(task, {}).setdefault(detector, {"runs": set(), "reasons": {}})
            cell["runs"].add(rep)
            cell["reasons"].setdefault(reason, set()).add(rep)
            continue
        measured.setdefault(task, {}).setdefault(detector, set()).add(rep)
        if row["count"]:
            cell = fired.setdefault(task, {}).setdefault(detector, {"runs": set(), "hits": 0})
            cell["runs"].add(rep)
            cell["hits"] += row["count"]
    out = {}
    for task, reps in runs.items():
        fired_cells = fired.get(task, {})
        for detector, cell in fired_cells.items():
            cell["runs"] = len(cell["runs"])
            cell["measured_runs"] = len(measured.get(task, {}).get(detector, ()))
        error_cells = {}
        for detector, cell in errors.get(task, {}).items():
            error_cells[detector] = {
                "runs": len(cell["runs"]),
                "reasons": dict((reason, len(reason_reps))
                                for reason, reason_reps in cell["reasons"].items()),
            }
        out[task] = {"runs": len(reps), "fired": fired_cells, "errors": error_cells}
    return out


def render_mechanisms(result):
    """The history lines for one row's `mechanisms`, one per task."""
    lines = []
    for task, cell in sorted(result.items(), key=lambda item: str(item[0])):
        fired, errors = cell.get("fired") or {}, cell.get("errors") or {}
        if fired:
            text = ", ".join("%s in %d of %d measured run(s), %d hit(s)"
                             % (d, c["runs"], c["measured_runs"], c["hits"])
                             for d, c in sorted(fired.items()))
        else:
            text = "none recorded" + ("" if errors else " across %d run(s)" % cell["runs"])
        if errors:
            detail = ", ".join("%s in %d run(s) (%s)" % (
                detector, error["runs"], ", ".join("%s: %d" % item
                                                    for item in sorted(error["reasons"].items())))
                               for detector, error in sorted(errors.items()))
            text += "; %d total run(s); errors: %s" % (cell["runs"], detail)
        lines.append("    fired in harness arm, %s: %s" % (task or "n/a", text))
    return lines
