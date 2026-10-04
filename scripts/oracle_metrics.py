#!/usr/bin/env python3
"""Named metrics an oracle check may return beside pass or fail, and their per-arm report.

A task declares its metrics and which way each improves: `"metrics": {"<name>": "higher" |
"lower"}` in a pack's `task.json` or a task of `benchmarks/tasks.json`. Its check's `check(root)`
then returns either the original verdict, a list of error strings that passes when empty, or

    {"pass": <bool>, "metrics": {"<name>": <number or null>}, "errors": [<str>]}

with `metrics` and `errors` optional. A malformed verdict, an undeclared metric or a metric
reported by a task that declares none is a broken check, so `verdict` raises and the attempt is
recorded as a check error. A declared metric that is null, not reported, not a number or not
finite is unknown: recorded as null, with the reason in `metric_errors` for every case but an
explicit null, and never as 0. A task that declares no metrics gets no metric field on its rows.
A check written `check(root, stream=None)` also gets the run's saved stream-json, and its row's
`metric_stream` says whether it did (`cost_bench.ORACLE_DRIVER`).

`summarise` reports each metric per arm and per task, and the difference between the arms with
the paired, task-clustered percentile bootstrap SM-2 uses (`replay_stats`). Contract and reading:
docs/benchmarks.md, "Oracle metrics". Standard library only; nothing here calls a model.
"""
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_stats  # noqa: E402  the seed, resample count and percentile rank SM-2 uses

DIRECTIONS = ("higher", "lower")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
VERDICT_KEYS = ("pass", "metrics", "errors")
BETTER, WORSE = "better", "worse"


def declaration_errors(declared, where):
    """Why a present `metrics` declaration is unusable, or []. An omitted key is fine, so callers
    check for it first; a present null is not a declaration and is refused here."""
    if not isinstance(declared, dict) or not declared:
        return ["%s: metrics must be a non-empty object of name to higher or lower" % where]
    errors = []
    for name, direction in sorted(declared.items()):
        if not NAME.match(name):
            errors.append("%s: metric name %r is not lower_snake_case" % (where, name))
        if direction not in DIRECTIONS:
            errors.append("%s: metric %s has unknown direction %r; use higher or lower" % (where, name, direction))
    return errors


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:  # an integer too large for a float
        return False


def verdict(value, declared=None):
    """`(passed, detail, recorded)` from what a check returned.

    `recorded` is None for a task that declares no metrics, else `{"metrics": {name: number or
    None}, "metric_errors": [str]}` over every declared name. ValueError for a broken check."""
    declared = declared or {}
    if isinstance(value, list):
        if not all(isinstance(e, str) for e in value):
            raise ValueError("the check returned a list that is not all strings")
        passed, detail, reported = not value, "; ".join(value[:3]), {}
    elif isinstance(value, dict):
        extra = sorted(set(value) - set(VERDICT_KEYS))
        if extra:
            raise ValueError("the check returned unknown key(s) %s" % ", ".join(extra))
        if type(value.get("pass")) is not bool:
            raise ValueError("the check's pass is not true or false")
        errors = value.get("errors", [])
        if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
            raise ValueError("the check's errors are not a list of strings")
        reported = value.get("metrics", {})
        if not isinstance(reported, dict):
            raise ValueError("the check's metrics are not an object")
        undeclared = sorted(set(reported) - set(declared))
        if undeclared:
            raise ValueError("the check reported undeclared metric(s) %s" % ", ".join(undeclared))
        passed, detail = value["pass"], "; ".join(errors[:3])
    else:
        raise ValueError("the check returned %s, not a list or an object" % type(value).__name__)
    if not declared:
        return passed, detail, None
    metrics, problems = {}, []
    for name in sorted(declared):
        raw = reported.get(name)
        if name not in reported:
            problems.append("%s: not reported" % name)
        elif raw is not None and not _finite(raw):
            problems.append("%s: %r is not a finite number" % (name, raw))
        metrics[name] = float(raw) if raw is not None and _finite(raw) else None
    return passed, detail, {"metrics": metrics, "metric_errors": problems}


def row_fields(declared):
    """The fields a row of a task declaring `declared` starts with: every metric unknown."""
    if not declared:
        return {}
    return {"metrics": {name: None for name in sorted(declared)},
            "metric_directions": dict(sorted(declared.items())), "metric_errors": [], "metric_stream": False}


def _mean(values):
    """The mean, or None when there are no values. Finite values never overflow to infinity here:
    on overflow the sum is taken over each value divided by the count."""
    if not values:
        return None
    try:
        return math.fsum(values) / len(values)
    except OverflowError:
        return math.fsum(v / len(values) for v in values)


def _round(value):
    return None if value is None else round(value, 6)


def _collect(rows, arms):
    """`{metric: (direction, {task: {arm: [value or None]}})}`; ValueError on a malformed row."""
    directions, values = {}, {}
    for index, row in enumerate(rows, 1):
        declared = row.get("metric_directions")
        if declared is None:
            continue
        if declaration_errors(declared, "row %d" % index):
            raise ValueError("row %d has malformed metric_directions" % index)
        recorded = row.get("metrics")
        if not isinstance(recorded, dict) or set(recorded) != set(declared):
            raise ValueError("row %d's metrics do not match its metric_directions" % index)
        if row.get("arm") not in arms:
            continue
        for name, direction in declared.items():
            if directions.setdefault(name, direction) != direction:
                raise ValueError("row %d gives metric %s inconsistent directions" % (index, name))
            value = recorded[name]
            if value is not None and not _finite(value):
                raise ValueError("row %d records metric %s as %r, not a finite number or null"
                                 % (index, name, value))
            cell = values.setdefault(name, {}).setdefault(row["task"], {arm: [] for arm in arms})
            cell[row["arm"]].append(value)
    return {name: (directions[name], values[name]) for name in sorted(values)}


def _pooled(by_task, tasks, arm):
    return _mean([v for t in tasks for v in by_task[t][arm] if v is not None])


def _difference(by_task, tasks, arms):
    return _pooled(by_task, tasks, arms[1]) - _pooled(by_task, tasks, arms[0])


def _describe(values):
    known = [v for v in values if v is not None]
    return {"mean": _round(_mean(known)), "n": len(known), "unknown": len(values) - len(known)}


def _metric(direction, by_task, arms, seed, resamples):
    tasks = sorted(by_task)
    out = {"direction": direction,
           "tasks": {t: {arm: _describe(by_task[t][arm]) for arm in arms} for t in tasks},
           "arms": {arm: _describe([v for t in tasks for v in by_task[t][arm]]) for arm in arms}}
    paired = [t for t in tasks if all(any(v is not None for v in by_task[t][arm]) for arm in arms)]
    out["paired_tasks"] = len(paired)
    if not paired:
        return dict(out, difference=None, difference_interval=None, reading="unavailable",
                    reason="no task has a known value in both arms")
    rng = random.Random(seed)
    alpha = (1 - replay_stats.CONFIDENCE) / 2
    point = _difference(by_task, paired, arms)
    samples = sorted(_difference(by_task, [paired[rng.randrange(len(paired))] for _ in paired], arms)
                     for _ in range(resamples))
    if not all(_finite(v) for v in [point] + samples):  # finite means whose difference overflows
        return dict(out, difference=None, difference_interval=None, reading="unavailable",
                    reason="the difference between the arms is not a finite number")
    interval = [_round(replay_stats._rank(samples, alpha)), _round(replay_stats._rank(samples, 1 - alpha))]
    if interval[0] <= 0 <= interval[1]:
        reading, reason = replay_stats.INCONCLUSIVE, "the interval spans no difference"
    else:
        up = interval[0] > 0
        reading, reason = (BETTER if up == (direction == "higher") else WORSE), None
    return dict(out, difference=_round(point), difference_interval=interval,
                reading=reading, reason=reason)


def summarise(rows, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES, arms=replay_stats.ARMS):
    """Each metric per arm and per task, and the treatment-minus-reference difference over the
    tasks with a known value in both arms, with a paired task-clustered percentile interval and
    its reading against the metric's direction. None when no row carries metrics, so a set
    without them reports exactly as before. ValueError on a malformed row."""
    if type(resamples) is not int or resamples < 2:
        raise ValueError("resamples must be an integer of at least 2")
    collected = _collect(rows, arms)
    if not collected:
        return None
    return {"method": replay_stats.METHOD, "seed": seed, "resamples": resamples,
            "confidence": replay_stats.CONFIDENCE, "reference": arms[0], "treatment": arms[1],
            "metrics": {name: _metric(direction, by_task, arms, seed, resamples)
                        for name, (direction, by_task) in collected.items()}}


def _num(value):
    return "undefined" if value is None else "%.4g" % value


def render(result):
    """The metrics report as text, or "" when there is none."""
    if not result:
        return ""
    reference, treatment = result["reference"], result["treatment"]
    lines = ["", "Oracle metrics: %s, seed %d, %d resamples, %s minus %s"
             % (result["method"], result["seed"], result["resamples"], treatment, reference)]
    for name, metric in result["metrics"].items():
        arms = ", ".join("%s %s (n %d, unknown %d)" % (arm, _num(metric["arms"][arm]["mean"]),
                                                       metric["arms"][arm]["n"], metric["arms"][arm]["unknown"])
                         for arm in (reference, treatment))
        interval = metric["difference_interval"]
        lines.append("  %s (%s is better): %s; difference %s, interval %s over %d paired task(s): %s%s"
                     % (name, metric["direction"], arms, _num(metric["difference"]),
                        "undefined" if interval is None else "[%s, %s]" % (_num(interval[0]), _num(interval[1])),
                        metric["paired_tasks"], metric["reading"],
                        ", because %s" % metric["reason"] if metric["reason"] else ""))
        for task, cell in metric["tasks"].items():
            lines.append("    %s: %s" % (task, ", ".join("%s %s (n %d, unknown %d)"
                                                          % (arm, _num(cell[arm]["mean"]), cell[arm]["n"],
                                                             cell[arm]["unknown"])
                                                          for arm in (reference, treatment))))
    return "\n".join(lines) + "\n"
