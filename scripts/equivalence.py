#!/usr/bin/env python3
"""Equivalence verdicts: whether a paired interval rules out every effect larger than a
pre-registered margin, so a null result has bounds.

The rule is interval-inside-bounds on the paired, task-clustered 95% interval `replay_stats`
already reports. A metric is **equivalent** when its interval lies strictly inside the margin,
**not equivalent** when it lies wholly at or beyond one bound, and **inconclusive** otherwise,
including when the interval or one of its bounds is undefined. Inside-bounds on a 95% interval is
two one-sided tests (TOST) at 0.025 a side, stricter than the 90% interval the TOST convention
uses at 0.05; the analysis keeps one interval per metric rather than computing a second.

A margin is `(lower, upper)` on the metric's own scale: a ratio's margin brackets 1.0 and is
positive, a difference's brackets 0. Margins come from the pre-registration's "Equivalence
margins" section (`margins_from_plan`), one `- **<metric>:** <lower> to <upper>` field each; a
field reading "none" registers no margin. The verdicts stand but each is labelled exploratory, with
its reason, when the analysis carries a limitation (fewer than five paired trials per task and arm),
when the rows do not all record the `--plan` file as their pre-registration (`registration_limitation`),
or, for a behaviour score, when a task and arm has fewer than five known values
(`coverage_limitations`). `Cost-of-Pass ratio` and `Pass-rate difference` read
`replay_stats.analyse`'s intervals; any other field is a behaviour score, an oracle metric named
as `oracle_metrics.summarise` reports it, judged from that metric's paired treatment-minus-reference
difference interval, so its margin brackets 0 too. How a plan states them:
docs/pre-registration-template.md.

Standard library only, and no model call.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_protocol  # noqa: E402  the plan's section and field reader
import oracle_metrics  # noqa: E402  behaviour scores' intervals
import replay_stats  # noqa: E402

EQUIVALENT, NOT_EQUIVALENT, INCONCLUSIVE = "equivalent", "not equivalent", "inconclusive"
SECTION = "Equivalence margins"
RATIO, DIFFERENCE = "Cost-of-Pass ratio", "Pass-rate difference"
# metric -> (the analyse() key holding its interval, its null value)
ANALYSED = {RATIO: ("ratio_interval", 1.0), DIFFERENCE: ("difference_interval", 0.0)}
_MARGIN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)\s*(?:[.;,(].*)?$")


def check_margin(metric, margin):
    """`(lower, upper)` as floats; ValueError when the margin is empty or excludes the null."""
    lower, upper = float(margin[0]), float(margin[1])
    if not lower < upper:
        raise ValueError("the %s margin's lower bound must be below its upper bound" % metric)
    null = ANALYSED[metric][1] if metric in ANALYSED else 0.0  # a behaviour score is a difference
    if not lower < null < upper:
        raise ValueError("the %s margin must contain %g, no effect" % (metric, null))
    if metric == RATIO and lower <= 0:
        raise ValueError("the %s margin must be positive" % metric)
    return lower, upper


def verdict(interval, margin):
    """(verdict, reason) for one interval against one `(lower, upper)` margin."""
    lower, upper = margin
    if not interval or interval[0] is None or interval[1] is None:
        return INCONCLUSIVE, "the interval is undefined"
    low, high = interval
    if lower < low and high < upper:
        return EQUIVALENT, "the interval lies inside [%g, %g]" % (lower, upper)
    if high <= lower or low >= upper:
        return NOT_EQUIVALENT, "the interval lies wholly outside [%g, %g]" % (lower, upper)
    return INCONCLUSIVE, "the interval crosses a bound of [%g, %g]" % (lower, upper)


def intervals_of(result, metrics=None):
    """`{metric: interval}` for the metrics `replay_stats.analyse` reports, and for each behaviour
    score in `metrics`, an `oracle_metrics.summarise` result (None when no row carries one)."""
    out = dict((metric, result.get(key)) for metric, (key, _) in ANALYSED.items())
    for name, metric in ((metrics or {}).get("metrics") or {}).items():
        out.setdefault(name, metric.get("difference_interval"))
    return out


def assess(intervals, margins):
    """`{metric: {margin, interval, verdict, reason}}` for every registered margin. A margin with no
    interval in `intervals` is inconclusive, never dropped."""
    out = {}
    for metric in sorted(margins):
        margin = check_margin(metric, margins[metric])
        interval = intervals.get(metric)
        if interval is None:
            found, reason = INCONCLUSIVE, "no interval was supplied for this metric"
        else:
            found, reason = verdict(interval, margin)
        out[metric] = {"margin": list(margin), "interval": interval, "verdict": found, "reason": reason}
    return out


def coverage_limitations(metrics):
    """`{score: reason}` for each behaviour score in an `oracle_metrics.summarise` result whose
    known values fall short of `replay_stats.MIN_TRIALS` in some task and arm. An interval can come
    from a single known value, so attempts counted per cell say nothing about a score's coverage."""
    out = {}
    for name, metric in ((metrics or {}).get("metrics") or {}).items():
        short = sorted("%s/%s %d" % (task, arm, cell["n"])
                       for task, cells in (metric.get("tasks") or {}).items()
                       for arm, cell in cells.items() if cell["n"] < replay_stats.MIN_TRIALS)
        if short:
            out[name] = ("fewer than %d known values per task and arm for this score (%s)"
                         % (replay_stats.MIN_TRIALS, ", ".join(short)))
    return out


def registration_limitation(rows, plan):
    """None when every row records the `plan` file as its pre-registration, else the reason the
    verdicts cannot be cited. A row records the plan's repository-relative path; `plan` matches it
    when its resolved path ends with that path, so a plan from another checkout still matches."""
    recorded = set(r.get("pre_registration") for r in rows)
    if not rows or any(r.get("evidence") != experiment_protocol.PREREGISTERED for r in rows) \
            or None in recorded or "" in recorded:
        return "the rows do not all record a pre-registration; exploratory diagnostic only"
    if len(recorded) > 1:
        return "the rows record more than one pre-registration (%s)" % ", ".join(sorted(recorded))
    want = Path(next(iter(recorded))).parts
    if Path(plan).expanduser().resolve().parts[-len(want):] != want:
        return "the rows record %s as their pre-registration, not %s" % ("/".join(want), plan)
    return None


def label(assessed, limitation, per_metric=None):
    """`assessed` with each verdict marked `exploratory` and carrying its limitations: `limitation`,
    the run's reason it cannot be cited, and its own entry in `per_metric`, joined; None when it
    can be cited. The verdicts themselves are kept."""
    for metric, item in assessed.items():
        reasons = [r for r in (limitation, (per_metric or {}).get(metric)) if r]
        item["exploratory"] = bool(reasons)
        item["limitation"] = "; ".join(reasons) or None
    return assessed


def parse_margin(metric, text):
    """`(lower, upper)` from `<lower> to <upper>`, or None for "none"."""
    if text.strip().lower().startswith("none"):
        return None
    match = _MARGIN.match(text)
    if not match:
        raise ValueError("the %s margin %r is not '<lower> to <upper>'" % (metric, text))
    return check_margin(metric, (match.group(1), match.group(2)))


def margins_from_plan(text):
    """`{metric: (lower, upper)}` from a plan's Equivalence margins section; ValueError when the
    section is missing, a field is unfilled, or none registers a margin."""
    body = experiment_protocol.sections(text).get(SECTION)
    if body is None:
        raise ValueError("the plan has no '%s' section" % SECTION)
    margins = {}
    for metric, value in experiment_protocol.fields(body).items():
        if not value or experiment_protocol.PLACEHOLDER.search(value):
            raise ValueError("the %s margin is unfilled" % metric)
        margin = parse_margin(metric, value)
        if margin is not None:
            margins[metric] = margin
    if not margins:
        raise ValueError("the plan registers no equivalence margin")
    return margins


def render(assessed):
    lines = []
    for metric, item in sorted(assessed.items()):
        interval = item["interval"]
        shown = "undefined" if not interval else "[%s, %s]" % tuple(
            "undefined" if v is None else "%.4g" % v for v in interval)
        lines.append("%s: %s%s; interval %s, margin [%g, %g]; %s%s"
                     % (metric, item["verdict"], " (exploratory)" if item.get("exploratory") else "", shown,
                        item["margin"][0], item["margin"][1], item["reason"],
                        "; %s" % item["limitation"] if item.get("limitation") else ""))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rows", help="a finished replay's results.jsonl, or the directory holding one")
    parser.add_argument("--plan", required=True, help="the pre-registration whose margins apply")
    parser.add_argument("--seed", type=int, default=replay_stats.SEED)
    parser.add_argument("--resamples", type=int, default=replay_stats.RESAMPLES)
    parser.add_argument("--json", action="store_true", help="print the verdicts as JSON")
    args = parser.parse_args(argv)
    try:
        margins = margins_from_plan(Path(args.plan).read_text(encoding="utf-8"))
        path = Path(args.rows)
        if path.is_dir():
            path = path / "results.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        result = replay_stats.analyse(rows, args.seed, args.resamples)
        scores = oracle_metrics.summarise(rows, args.seed, args.resamples)
        run_limits = [r for r in (result.get("limitation"), registration_limitation(rows, args.plan)) if r]
        assessed = label(assess(intervals_of(result, scores), margins), "; ".join(run_limits) or None,
                         coverage_limitations(scores))
    except (ValueError, OSError) as exc:
        print("equivalence: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(assessed, indent=2, sort_keys=True) if args.json else render(assessed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
