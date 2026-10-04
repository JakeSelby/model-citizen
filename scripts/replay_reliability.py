#!/usr/bin/env python3
"""Reliability and joint rule compliance of a replay, from its saved rows and detections alone.

A mean pass rate hides a flaky task, and a per-rule rate hides whether the rules hold together, so
this module adds two readings beside them.

pass^k, per task and arm, is the chance that k independent trials of the task all pass. Each cell
reports whether every trial it ran passed, and the unbiased estimate from its n trials and c
passes, C(c, k) / C(n, k), which uses every trial rather than only the first k. k defaults to the
fewest trials any task-and-arm cell ran, so every cell is estimated at the same k; a cell with
fewer than k trials has no estimate. Per arm the report gives the share of tasks whose every trial
passed and the mean estimate over tasks, beside pass^1, the mean per-task pass rate. Attempts are
validated as SM-2 reads them (`replay_stats.attempts`), so an errored run is a failed trial.

The all-rules-at-once rate is the share of runs in which no rule-violation detector fired, read
from the `detections.jsonl` the runner writes (`replay_detect`). A run is clean when every
detector read it and none fired, hit when any fired, and unknown when none fired but one could not
read it, or when the run has no detection rows at all: an unread run is never counted as clean.
The detectors expected of every run are those the set's detections name anywhere, so a run missing
one of their rows was not read by it and is unknown unless another detector fired.
The rate is clean over clean plus hit, with a Wilson interval; each detector's own compliance rate
sits beside it, over the runs that detector read. The joint rate can be no higher than the lowest
per-rule rate, and the gap between them is what the per-rule view hides.

Standard library only, and no model call. `reliability_section` is the one entry point a summary
needs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_detect  # noqa: E402  the detections file name and its reader
import replay_stats  # noqa: E402  the attempt validation and the Wilson interval SM-2 uses

CLEAN, HIT, UNKNOWN = "clean", "hit", "unknown"


def _comb(n, k):
    """C(n, k) without `math.comb`, kept to the floor the tests run under."""
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    out = 1
    for i in range(1, k + 1):
        out = out * (n - k + i) // i
    return out


def pass_hat_k(n, c, k):
    """The unbiased pass^k estimate from `n` trials with `c` passes, C(c, k) / C(n, k); None when
    fewer than `k` trials ran. ValueError on counts that cannot be."""
    if type(n) is not int or type(c) is not int or type(k) is not int or k < 1 or not 0 <= c <= n:
        raise ValueError("pass^k needs 0 <= passes <= trials and k >= 1, not n=%r c=%r k=%r" % (n, c, k))
    if n < k:
        return None
    return _comb(c, k) / _comb(n, k)


def _arms(rows):
    """Every arm the rows name, SM-2's pair first in its order, then the rest sorted."""
    named = set(row.get("arm") for row in rows)
    return tuple(a for a in replay_stats.ARMS if a in named) + tuple(
        sorted((a for a in named if a not in replay_stats.ARMS), key=str))


def _mean(values):
    return sum(values) / len(values) if values else None


def pass_k(rows, k=None):
    """`{"k", "arms": {arm: {...}}}`: pass^k per task and arm, and per arm the share of tasks whose
    every trial passed, the mean pass^k estimate and pass^1. ValueError on malformed rows or a k
    below 1."""
    atts = replay_stats.attempts(rows, _arms(rows)) if rows else []
    cells = {}
    for att in atts:
        cell = cells.setdefault(att["arm"], {}).setdefault(att["task"], [0, 0])
        cell[0] += 1
        cell[1] += 1 if att["passed"] else 0
    if k is None:
        k = min((n for tasks in cells.values() for n, _ in tasks.values()), default=1)
    if type(k) is not int or k < 1:
        raise ValueError("pass^k needs k >= 1, not %r" % (k,))
    out = {}
    for arm in _arms(rows):
        tasks, estimates, rates = {}, [], []
        for task, (n, c) in sorted(cells.get(arm, {}).items(), key=lambda item: str(item[0])):
            estimate = pass_hat_k(n, c, k)
            tasks[task] = {"trials": n, "passes": c, "all_passed": c == n, "pass_hat_k": estimate}
            rates.append(c / n)
            if estimate is not None:
                estimates.append(estimate)
        all_passed = sum(1 for cell in tasks.values() if cell["all_passed"])
        out[arm] = {"tasks": len(tasks), "all_passed": all_passed,
                    "all_passed_rate": all_passed / len(tasks) if tasks else None,
                    "pass_hat_k": _mean(estimates), "estimated_tasks": len(estimates),
                    "pass_1": _mean(rates), "per_task": tasks}
    return {"k": k, "arms": out}


def _run_key(row):
    return (row.get("task"), row.get("arm"), row.get("rep", row.get("trial")))


def classify_run(detections, roster=()):
    """One run's reading from its detection rows: `clean`, `hit` or `unknown`. A detector in
    `roster` with no row for the run did not read it."""
    counts = [row.get("count") for row in detections]
    if any(type(count) is int and count > 0 for count in counts):
        return HIT
    if not counts or any(type(count) is not int for count in counts):
        return UNKNOWN
    if set(roster) - set(row.get("detector") for row in detections):
        return UNKNOWN
    return CLEAN


def joint_compliance(rows, detections):
    """`{"arms": {arm: {...}}, "unmatched_runs"}`: per arm the all-rules-at-once rate over the
    runs `rows` hold, its Wilson interval, the clean, hit and unknown counts, and each detector's
    own compliance rate. `unmatched_runs` counts detected runs no row holds; they are left out."""
    by_run = {}
    roster = {}
    for row in detections:
        by_run.setdefault(_run_key(row), []).append(row)
        roster.setdefault(row.get("detector"), row.get("rule"))
    runs = []
    for row in rows:
        key = _run_key(row)
        if key not in runs:
            runs.append(key)
    out = {}
    for arm in _arms(rows):
        tally = {CLEAN: 0, HIT: 0, UNKNOWN: 0}
        rules = {}
        for key in (k for k in runs if k[1] == arm):
            found = by_run.get(key, [])
            tally[classify_run(found, roster)] += 1
            read = set(det.get("detector") for det in found)
            for detector in roster:
                if found and detector not in read:
                    rules.setdefault(detector, {"rule": roster[detector], "measured": 0, "fired": 0,
                                                "unknown": 0})["unknown"] += 1
            for det in found:
                cell = rules.setdefault(det.get("detector"), {"rule": det.get("rule"), "measured": 0,
                                                              "fired": 0, "unknown": 0})
                count = det.get("count")
                if type(count) is not int:
                    cell["unknown"] += 1
                    continue
                cell["measured"] += 1
                cell["fired"] += 1 if count > 0 else 0
        for cell in rules.values():
            cell["rate"] = (cell["measured"] - cell["fired"]) / cell["measured"] if cell["measured"] else None
        measured = tally[CLEAN] + tally[HIT]
        low, high = replay_stats.wilson(tally[CLEAN], measured)
        out[arm] = {"runs": sum(tally.values()), "clean": tally[CLEAN], "hit": tally[HIT],
                    "unknown": tally[UNKNOWN], "rate": tally[CLEAN] / measured if measured else None,
                    "interval": None if low is None else [low, high],
                    "per_rule": dict(sorted(rules.items(), key=lambda item: str(item[0])))}
    known = set(runs)
    return {"arms": out, "unmatched_runs": len(set(by_run) - known)}


def joint_summary(joint):
    """The form an evidence bundle carries of a `joint_compliance` result: per arm the run counts,
    the rate and its interval, without the per-rule cells or any raw detection."""
    return {"arms": dict((arm, dict((key, cell[key]) for key in SUMMARY_KEYS))
                         for arm, cell in joint["arms"].items())}


SUMMARY_KEYS = ("runs", "clean", "hit", "unknown", "rate", "interval")


def summary_problems(rows, summary):
    """Every way a carried `joint_summary` disagrees with `rows` or with itself: the arms the rows
    name, each arm's run count, and the rate and Wilson interval its counts give. Empty when it
    holds. The split between clean, hit and unknown cannot be re-derived without detections."""
    if not isinstance(summary, dict) or set(summary) != {"arms"} or not isinstance(summary["arms"], dict):
        return ["joint summary must be an object holding only `arms`"]
    problems = []
    arms = _arms(rows)
    if set(summary["arms"]) != set(arms):
        problems.append("joint summary arms %s differ from the rows' %s" % (
            sorted(summary["arms"], key=str), list(arms)))
    runs = {}
    for row in rows:
        runs.setdefault(row.get("arm"), set()).add(_run_key(row))
    for arm in sorted(summary["arms"], key=str):
        cell = summary["arms"][arm]
        if not isinstance(cell, dict) or set(cell) != set(SUMMARY_KEYS):
            problems.append("joint summary %s must hold exactly %s" % (arm, ", ".join(SUMMARY_KEYS)))
            continue
        counts = [cell[key] for key in ("runs", "clean", "hit", "unknown")]
        if any(type(count) is not int or count < 0 for count in counts):
            problems.append("joint summary %s counts are not non-negative integers" % arm)
            continue
        if cell["runs"] != len(runs.get(arm, ())) or cell["runs"] != sum(counts[1:]):
            problems.append("joint summary %s counts %d run(s) as %d clean, %d hit and %d unknown; "
                            "the rows hold %d" % (arm, cell["runs"], cell["clean"], cell["hit"],
                                                  cell["unknown"], len(runs.get(arm, ()))))
            continue
        measured = cell["clean"] + cell["hit"]
        low, high = replay_stats.wilson(cell["clean"], measured)
        if cell["rate"] != (cell["clean"] / measured if measured else None) \
                or cell["interval"] != (None if low is None else [low, high]):
            problems.append("joint summary %s rate or interval is not what its counts give" % arm)
    return problems


def detections_beside(results_path):
    """The `detections.jsonl` rows beside a `results.jsonl`, or None when the set has none."""
    path = Path(results_path).parent / replay_detect.DETECTIONS
    return replay_detect.read_jsonl(path) if path.is_file() else None


def _num(value):
    return "undefined" if value is None else "%.3f" % value


def render(section):
    """The section as text lines."""
    reliability = section["pass_k"]
    k = reliability["k"]
    lines = ["", "Reliability: pass^%d per task and arm, the unbiased estimate from each cell's n trials" % k]
    for arm, cell in reliability["arms"].items():
        lines.append("  %s: every trial passed in %d of %d task(s) (%s); mean pass^%d %s over %d task(s), "
                     "pass^1 %s" % (arm, cell["all_passed"], cell["tasks"], _num(cell["all_passed_rate"]), k,
                                    _num(cell["pass_hat_k"]), cell["estimated_tasks"], _num(cell["pass_1"])))
        for task, t in cell["per_task"].items():
            lines.append("    %s: %d of %d passed, pass^%d %s" % (task, t["passes"], t["trials"], k,
                                                                 _num(t["pass_hat_k"])))
    joint = section["joint"]
    if joint is None:
        lines.append("All rules at once: no detections recorded for this set")
        return lines
    lines.append("All rules at once: share of runs with no rule-violation detector hit, unknown runs excluded")
    for arm, cell in joint["arms"].items():
        interval = cell["interval"]
        lines.append("  %s: %s, interval %s; %d clean, %d hit, %d unknown of %d run(s)" % (
            arm, _num(cell["rate"]), "undefined" if interval is None else "[%s, %s]" % (
                _num(interval[0]), _num(interval[1])), cell["clean"], cell["hit"], cell["unknown"], cell["runs"]))
        for detector, rule in cell["per_rule"].items():
            lines.append("    %s: %s (%d fired of %d measured, %d unknown)" % (
                detector, _num(rule["rate"]), rule["fired"], rule["measured"], rule["unknown"]))
    if joint["unmatched_runs"]:
        lines.append("  %d detected run(s) match no saved row and are left out" % joint["unmatched_runs"])
    return lines


def reliability_section(rows, detections=None, k=None):
    """`(section, text)`: the JSON-ready `{"pass_k", "joint"}` and its text, newline-terminated.
    `joint` is None when `detections` is None. ValueError on malformed rows."""
    section = {"pass_k": pass_k(rows, k),
               "joint": None if detections is None else joint_compliance(rows, detections)}
    return section, "\n".join(render(section)) + "\n"
