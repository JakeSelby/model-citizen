#!/usr/bin/env python3
"""SM-2's analysis of a finished two-arm replay, from its saved rows alone.

Each arm's Cost-of-Pass pools the set: the total cost of every attempt over the total passes. By
intention to treat every attempt counts, so an errored, crashed or timed-out run is a failed
attempt whose cost is in the figure. The harness-over-bare ratio and the pass-rate difference
(harness minus bare) carry paired, task-clustered 95% intervals from a percentile bootstrap that
resamples tasks with replacement and keeps both arms' trials of a task together; it is
deterministic for a seed, and the seed and resample count are recorded with the result. Each
arm's own pass rate carries a Wilson interval, which is descriptive only. The verdict applies
SM-2's decision rule; reading and limits: docs/benchmarks.md and docs/evidence-standard.md. A run
with config arms adds every arm's figures and its paired comparisons (`analyse_arms`).

Standard library only, and no model call: every figure here re-derives from `results.jsonl`.
"""
import math
import random
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from harness_core.intervals import Z95, wilson  # noqa: E402,F401  re-exported for callers

ARMS = ("bare", "harness")
CONFIDENCE = 0.95
DELTA = 0.125  # SM-2's non-inferiority margin on the pass-rate difference
MAGNITUDE = 0.85  # "at least 15% cheaper" needs the ratio's upper bound at or below this
SEED = 795
RESAMPLES = 10000
METHOD = "task-clustered paired percentile bootstrap"
SUPPORTED, NOT_SUPPORTED, INCONCLUSIVE = "supported", "not supported", "inconclusive"


def attempts(rows, arms=ARMS):
    """One validated `{task, arm, trial, passed, cost, long}` per saved row.

    ValueError names the first malformed or contradictory row. An errored row is a fail by
    intention to treat. The trial is the row's `rep`. `arms` is the `(reference, treatment)`
    pair the rows may name, bare and harness unless a caller compares another pair.
    """
    rows = list(rows)  # read twice below, and a caller may pass a one-shot iterable
    strata = sorted({str(row.get("stratum")) for row in rows if isinstance(row, dict)})
    if len(strata) > 1:
        # Each model of a multi-model run is its own stratum (`replay_strata`); pooling them is a
        # pre-registered analysis that relabels the rows first, never a silent default.
        raise ValueError("the rows hold %d strata (%s); SM-2 is computed per stratum"
                         % (len(strata), ", ".join(strata)))
    out, seen, long_by_task = [], set(), {}
    for index, row in enumerate(rows, 1):
        missing = [k for k in ("task", "arm", "cost_usd") if k not in row]
        if "rep" not in row and "trial" not in row:
            missing.append("rep")
        if missing:
            raise ValueError("row %d has no %s" % (index, ", ".join(missing)))
        if row["arm"] not in arms:
            raise ValueError("row %d names an unknown arm %r" % (index, row["arm"]))
        trial = row.get("rep", row.get("trial"))
        if type(trial) is not int or trial < 1:
            raise ValueError("row %d has an invalid trial id %r" % (index, trial))
        identity = (row["task"], row["arm"], trial)
        if identity in seen:
            raise ValueError("row %d duplicates task %r, arm %s, trial %d" %
                             (index, row["task"], row["arm"], trial))
        seen.add(identity)
        error = row.get("error", False)
        if type(error) is not bool:
            raise ValueError("row %d has a non-boolean error" % index)
        saved_passed = row.get("passed")
        if saved_passed is not None and type(saved_passed) is not bool:
            raise ValueError("row %d has a non-boolean passed value" % index)
        if error and saved_passed is True:
            raise ValueError("row %d records both an error and a pass" % index)
        if error:
            passed = False
        elif type(saved_passed) is bool:
            passed = saved_passed
        else:
            raise ValueError("row %d (%s, %s) records neither a pass nor a fail" % (index, row["task"], row["arm"]))
        outcome = row.get("outcome")
        if outcome is not None:
            if outcome not in ("pass", "fail") or not isinstance(outcome, str):
                raise ValueError("row %d has an invalid outcome %r" % (index, outcome))
            if (outcome == "pass") != passed:
                raise ValueError("row %d has contradictory outcome and pass fields" % index)
        task_long = row.get("task_long", False)
        if type(task_long) is not bool:
            raise ValueError("row %d has a non-boolean task_long" % index)
        if row["task"] in long_by_task and long_by_task[row["task"]] != task_long:
            raise ValueError("row %d gives task %r inconsistent task_long values" % (index, row["task"]))
        long_by_task[row["task"]] = task_long
        cost = row["cost_usd"]
        if type(cost) is bool:
            raise ValueError("row %d has a boolean cost_usd" % index)
        try:
            cost = None if cost is None else float(cost)
        except (TypeError, ValueError) as exc:
            raise ValueError("row %d has a nonnumeric cost_usd" % index) from exc
        if cost is not None and (not math.isfinite(cost) or cost < 0):
            raise ValueError("row %d has a non-finite or negative cost_usd" % index)
        out.append({"task": row["task"], "arm": row["arm"], "trial": trial,
                    "passed": passed, "cost": cost, "error": error,
                    "long": task_long})
    return out


def cost_of_pass(cost, passes):
    """Total cost over total passes; None when nothing passed or a cost is unreadable."""
    if cost is None or not passes:
        return None
    return cost / passes


def _cells(atts, arms=ARMS):
    """Per task, per arm: `[cost or None, passes, attempts]`, and the sorted task ids. ValueError
    when a task lacks attempts in any of `arms`, or its arms hold different trial ids, since a
    paired interval cannot use it. Two arms or more; the unit-by-economy grid passes five."""
    cells, trial_ids = {}, {}
    for a in atts:
        cell = cells.setdefault(a["task"], {arm: [0.0, 0, 0] for arm in arms})[a["arm"]]
        trial_ids.setdefault(a["task"], {arm: set() for arm in arms})[a["arm"]].add(a["trial"])
        cell[0] = None if cell[0] is None or a["cost"] is None else cell[0] + a["cost"]
        cell[1] += 1 if a["passed"] else 0
        cell[2] += 1
    unpaired = sorted(t for t, c in cells.items() if not all(c[arm][2] for arm in arms))
    if unpaired and len(arms) == 2:
        raise ValueError("task %s has attempts in one arm only; a paired interval needs both" % unpaired[0])
    if unpaired:
        empty = [arm for arm in arms if not cells[unpaired[0]][arm][2]]
        raise ValueError("task %s has no attempts in the %s arm; a paired interval needs every arm"
                         % (unpaired[0], empty[0]))
    mismatched = sorted(t for t in cells if any(trial_ids[t][arms[0]] != trial_ids[t][arm] for arm in arms[1:]))
    if mismatched:
        raise ValueError("task %s has different trial ids in its %s arms"
                         % (mismatched[0], "two" if len(arms) == 2 else "%d" % len(arms)))
    tasks = sorted(cells)
    if tasks and any(trial_ids[t][arms[0]] != trial_ids[tasks[0]][arms[0]] for t in tasks):
        raise ValueError("tasks have different trial ids; a fixed sample needs one trial set")
    return cells, tasks


def _totals(cells, tasks, arms=ARMS):
    out = {}
    for arm in arms:
        costs = [cells[t][arm][0] for t in tasks]
        total = None if None in costs else sum(costs)
        if total is not None and not math.isfinite(total):
            raise ValueError("the %s arm has a non-finite aggregate cost" % arm)
        out[arm] = (total, sum(cells[t][arm][1] for t in tasks),
                    sum(cells[t][arm][2] for t in tasks))
    return out


def _ratio(totals, arms=ARMS):
    """(value, reason): treatment over reference, `arms[1]` over `arms[0]`. The value is None,
    with the reason, when it is undefined."""
    unreadable = [arm for arm in arms if totals[arm][0] is None]
    if unreadable:
        return None, "the %s arm has a run with no readable cost" % unreadable[0]
    nothing = [arm for arm in arms if not totals[arm][1]]
    if nothing:
        return None, "the %s arm passed nothing" % " and the ".join(nothing)
    harness, bare = (cost_of_pass(totals[arm][0], totals[arm][1]) for arm in (arms[1], arms[0]))
    if not bare:
        return None, "the %s arm's cost is zero" % arms[0]
    ratio = harness / bare
    if not math.isfinite(ratio):
        raise ValueError("the cost-of-pass ratio is non-finite")
    return ratio, None


def _rank(values, q):
    """The nearest-rank percentile of sorted `values`: always one of the resampled values."""
    fraction = Fraction(q).limit_denominator(1000000)
    ceiling = (fraction.numerator * len(values) + fraction.denominator - 1) // fraction.denominator
    index = min(len(values) - 1, max(0, ceiling - 1))
    return values[index]


def _bound(sample):
    """Turn an ordered bootstrap sample into a bound, retaining a valid zero ratio."""
    return sample[1] if sample[0] == 1 else None


def bootstrap(cells, tasks, seed=SEED, resamples=RESAMPLES, arms=ARMS):
    """(ratio interval, difference interval, undefined ratio resamples) over `tasks`.

    Each resample draws len(tasks) tasks with replacement and pools both arms over the same
    draws, which is the pairing. A resample in which only the harness arm passed sorts below valid
    ratios; one in which only the bare arm passed sorts above them. An interval reaching
    either sentinel has that bound undefined rather than invented. The ratio interval is None
    when any cost is unreadable."""
    if type(resamples) is not int or resamples < 2:
        raise ValueError("resamples must be an integer of at least 2")
    rng = random.Random(seed)
    alpha = (1 - CONFIDENCE) / 2
    priced = all(cells[t][arm][0] is not None for t in tasks for arm in arms)
    ratios, diffs, undefined = [], [], 0
    for _ in range(resamples):
        picks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        totals = _totals(cells, picks, arms)
        (hc, hp, hn), (bc, bp, bn) = totals[arms[1]], totals[arms[0]]
        diffs.append(hp / hn - bp / bn)
        if not priced:
            continue
        value, _ = _ratio(totals, arms)
        if value is None:
            undefined += 1
            ratios.append((0, None) if hp else (2, None))
        else:
            ratios.append((1, value))
    diffs.sort()
    ratios.sort(key=lambda sample: (sample[0], sample[1] if sample[1] is not None else 0.0))
    ratio_ci = [_bound(_rank(ratios, alpha)), _bound(_rank(ratios, 1 - alpha))] if priced else None
    diff_ci = [_rank(diffs, alpha), _rank(diffs, 1 - alpha)]
    return ratio_ci, diff_ci, undefined


def _below(interval, limit):
    return bool(interval) and interval[1] is not None and interval[0] is not None and interval[1] < limit


def decide(ratio, ratio_ci, diff_ci, long_ci=None, has_long=False):
    """(verdict, reason, claim). SM-2's rule: supported only when the ratio's interval lies wholly
    below 1.0 and the difference's lower bound is above -DELTA. Not supported when the data rule
    that out, with the ratio's interval wholly at or above 1.0 or the difference's wholly below
    -DELTA; inconclusive otherwise. A saving claim needs support and a marked long-task subset
    whose interval lies wholly below 1.0; "at least 15% cheaper" needs the ratio's upper bound at
    or below MAGNITUDE."""
    if diff_ci[1] < -DELTA:
        return NOT_SUPPORTED, "the pass-rate difference lies wholly below -%g" % DELTA, None
    if ratio_ci and ratio_ci[0] is not None and ratio_ci[0] >= 1.0:
        return NOT_SUPPORTED, "the Cost-of-Pass ratio's interval lies wholly at or above 1.0", None
    if ratio is None:
        return INCONCLUSIVE, "the Cost-of-Pass ratio is undefined; a pass-rate result only", None
    if not _below(ratio_ci, 1.0):
        return INCONCLUSIVE, "the Cost-of-Pass ratio's interval does not lie wholly below 1.0", None
    if diff_ci[0] <= -DELTA:
        return INCONCLUSIVE, "the pass-rate difference's lower bound is not above -%g" % DELTA, None
    if not has_long:
        return SUPPORTED, "supported on the whole set; no long-task subset is marked, so no saving " \
                          "is claimed", None
    if not _below(long_ci, 1.0):
        return SUPPORTED, "supported on the whole set; the long-task subset's interval does not lie " \
                          "wholly below 1.0, so no saving is claimed", None
    claim = "at least 15% cheaper" if ratio_ci[1] <= MAGNITUDE else "cheaper"
    return SUPPORTED, "both conditions of the decision rule hold", claim


def analyse(rows, seed=SEED, resamples=RESAMPLES, arms=ARMS):
    """The whole SM-2 result for a finished two-arm row set; ValueError when it cannot be derived.

    `arms` is `(reference, treatment)`: the ratio is treatment over reference and the difference
    treatment minus reference. The verdict is SM-2's rule, which is defined for harness against
    bare only, so a caller comparing another pair reads the intervals and not the verdict."""
    pair = arms
    atts = attempts(rows, pair)
    cells, tasks = _cells(atts, pair)
    if not tasks:
        raise ValueError("no rows")
    totals = _totals(cells, tasks, pair)
    arms = {}
    for arm in pair:
        cost, passes, n = totals[arm]
        low, high = wilson(passes, n)
        arms[arm] = {"attempts": n, "passes": passes,
                     "errors": sum(1 for a in atts if a["arm"] == arm and a["error"]),
                     "cost_usd": None if cost is None else round(cost, 6),
                     "cost_of_pass": None if cost_of_pass(cost, passes) is None else round(cost / passes, 6),
                     "mean_cost_per_attempt": None if cost is None else round(cost / n, 6),
                     "pass_rate": round(passes / n, 4),
                     "pass_rate_interval_descriptive": [round(low, 4), round(high, 4)]}
    ratio, undefined_reason = _ratio(totals, pair)
    ratio_ci, diff_ci, undefined = bootstrap(cells, tasks, seed, resamples, pair)
    long_tasks = sorted({a["task"] for a in atts if a["long"]})
    long = None
    if long_tasks:
        long_ratio, long_reason = _ratio(_totals(cells, long_tasks, pair), pair)
        long = {"tasks": long_tasks, "ratio": None if long_ratio is None else round(long_ratio, 4),
                "ratio_undefined": long_reason,
                "ratio_interval": bootstrap(cells, long_tasks, seed, resamples, pair)[0]}
    verdict, reason, claim = decide(ratio, ratio_ci, diff_ci, long and long["ratio_interval"], bool(long_tasks))
    min_trials = min(cells[task][arm][2] for task in tasks for arm in pair)
    eligible = min_trials >= 5
    limitation = None
    if not eligible:
        limitation = "fewer than five paired trials per task and arm; exploratory diagnostic only"
        verdict, reason, claim = INCONCLUSIVE, limitation, None
    return {"method": METHOD, "seed": seed, "resamples": resamples, "confidence": CONFIDENCE,
            "delta": DELTA, "tasks": len(tasks), "arms": arms,
            "ratio": None if ratio is None else round(ratio, 4), "ratio_undefined": undefined_reason,
            "ratio_interval": ratio_ci, "undefined_resamples": undefined,
            "difference": round(totals[pair[1]][1] / totals[pair[1]][2] -
                                totals[pair[0]][1] / totals[pair[0]][2], 4),
            "difference_interval": diff_ci, "long": long, "sm2_eligible": eligible,
            "limitation": limitation,
            "verdict": verdict, "reason": reason, "claim": claim}


# --- Every declared arm (#1228) --------------------------------------------------------------------
#
# A run with config arms (`--arm-config`) holds bare, harness and one arm per config. Each arm is
# reported on its own, and each is compared against bare and against the harness default with the
# same task-clustered bootstrap as SM-2. Harness against bare stays the SM-2 comparison; a config
# arm's comparison is secondary unless the pre-registration names it primary.

PRIMARY, SECONDARY = "primary", "secondary"


def arm_names(rows):
    """Every arm the rows name: SM-2's pair first in its order, then the rest sorted."""
    named = set(row.get("arm") for row in rows if isinstance(row, dict))
    return tuple(a for a in ARMS if a in named) + tuple(sorted((a for a in named if a not in ARMS), key=str))


def comparison_label(reference, treatment):
    return "%s vs %s" % (treatment, reference)


def comparison_pairs(names):
    """`(reference, treatment)` pairs: every arm against bare, then every config arm against harness."""
    return [("bare", arm) for arm in names if arm != "bare"] + \
           [("harness", arm) for arm in names if arm not in ARMS]


def analyse_arms(rows, seed=SEED, resamples=RESAMPLES, primary=()):
    """Every arm's own figures and its paired comparisons; ValueError when they cannot be derived.

    Every task must hold the same trial ids in every arm, as SM-2 requires of its two. Each
    comparison carries the Cost-of-Pass ratio (treatment over reference) and the pass-rate
    difference (treatment minus reference) with task-clustered 95% intervals, and its `role`:
    harness against bare is `primary`, being SM-2's own; any other is `secondary` unless its label
    (`comparison_label`) is in `primary`, the comparisons a pre-registration names. No comparison
    but SM-2's has a verdict."""
    rows = list(rows)
    names = arm_names(rows)
    for arm in ARMS:
        if arm not in names:
            raise ValueError("the rows hold no %s arm; every arm is compared against bare and the harness "
                             "default" % arm)
    _cells(attempts(rows, names), names)  # every task in every arm, on one trial set
    pairs = comparison_pairs(names)
    labels = [comparison_label(*pair) for pair in pairs]
    unknown = sorted(set(primary) - set(labels))
    if unknown:
        raise ValueError("the pre-registration names %s primary, which this run does not compare; it "
                         "compares %s" % (", ".join(unknown), ", ".join(labels)))
    arms, comparisons = {}, []
    for (reference, treatment), label in zip(pairs, labels):
        mine = analyse([r for r in rows if r.get("arm") in (reference, treatment)], seed, resamples,
                       (reference, treatment))
        arms.update(mine["arms"])
        sm2 = (reference, treatment) == ARMS
        comparisons.append({"reference": reference, "treatment": treatment, "label": label,
                            "role": PRIMARY if sm2 or label in primary else SECONDARY, "sm2": sm2,
                            "tasks": mine["tasks"], "ratio": mine["ratio"],
                            "ratio_undefined": mine["ratio_undefined"], "ratio_interval": mine["ratio_interval"],
                            "undefined_resamples": mine["undefined_resamples"],
                            "difference": mine["difference"], "difference_interval": mine["difference_interval"],
                            "sm2_eligible": mine["sm2_eligible"]})
    return {"method": METHOD, "seed": seed, "resamples": resamples, "confidence": CONFIDENCE,
            "arm_names": list(names), "arms": {arm: arms[arm] for arm in names},
            "primary_named": sorted(primary), "comparisons": comparisons}


def render_arms(result):
    """Every arm's figures, then each paired comparison with its role, as text."""
    lines = ["", "Every arm: %d arm(s), %s, seed %d, %d resamples, %d%% intervals"
             % (len(result["arm_names"]), result["method"], result["seed"], result["resamples"],
                round(result["confidence"] * 100))]
    for arm in result["arm_names"]:
        a = result["arms"][arm]
        lines.append("  %s: %d/%d passed (%d errored), pass rate %s, Wilson %s (descriptive), "
                     "Cost-of-Pass %s USD, total %s USD"
                     % (arm, a["passes"], a["attempts"], a["errors"], _num(a["pass_rate"]),
                        _interval(a["pass_rate_interval_descriptive"]), _num(a["cost_of_pass"], 4),
                        _num(a["cost_usd"], 4)))
    lines.append("Paired comparisons, task-clustered; only SM-2's has a verdict:")
    for c in result["comparisons"]:
        ratio = _num(c["ratio"])
        if c["ratio_undefined"]:
            ratio += " (%s)" % c["ratio_undefined"]
        lines.append("  %s (%s%s): Cost-of-Pass ratio %s, interval %s; pass-rate difference %s, interval %s"
                     "%s" % (c["label"], c["role"], ", SM-2" if c["sm2"] else "", ratio,
                             _interval(c["ratio_interval"]), _num(c["difference"]),
                             _interval(c["difference_interval"]),
                             "" if c["sm2_eligible"] else "; fewer than five paired trials, exploratory"))
    return "\n".join(lines) + "\n"


def pareto(result, names=ARMS):
    """Per arm `(arm, mean cost per attempt, pass rate, status)`, status `frontier` or `dominated
    by <arm>`: dominated when another arm costs no more per attempt and passes at least as often,
    better on one. An arm with an unreadable cost is `unpriced`. `names` are the arms of
    `result["arms"]` to place, in order; a three-arm view passes three."""
    arms = result["arms"]
    out = []
    for arm in names:
        mine = arms[arm]
        status = "frontier"
        if mine["mean_cost_per_attempt"] is None:
            status = "unpriced"
        for other in names:
            theirs = arms[other]
            if other == arm or status == "unpriced" or theirs["mean_cost_per_attempt"] is None:
                continue
            no_worse = theirs["mean_cost_per_attempt"] <= mine["mean_cost_per_attempt"] \
                and theirs["pass_rate"] >= mine["pass_rate"]
            better = theirs["mean_cost_per_attempt"] < mine["mean_cost_per_attempt"] \
                or theirs["pass_rate"] > mine["pass_rate"]
            if no_worse and better:
                status = "dominated by " + other
        out.append((arm, mine["mean_cost_per_attempt"], mine["pass_rate"], status))
    return out


def pareto_svg(result):
    """A standalone plot of the two arms; unknown cost has no plotted coordinate."""
    points = pareto(result)
    maximum = max([cost for _, cost, _, _ in points if cost is not None] + [0.01]) * 1.2
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420" viewBox="0 0 640 420" role="img" aria-labelledby="title desc">',
             '<title id="title">Cost and pass rate by arm</title>',
             '<desc id="desc">Lower cost and higher pass rate are preferable. Unknown costs are not plotted. Descriptive comparison; the paired intervals govern the verdict.</desc>',
             '<rect width="640" height="420" fill="white"/>',
             '<g font-family="sans-serif" font-size="12" fill="#222">',
             '<path d="M80 40V330H560" fill="none" stroke="#333"/>',
             '<text x="320" y="380" text-anchor="middle">Mean USD per attempt</text>',
             '<text transform="translate(22 185) rotate(-90)" text-anchor="middle">Pass rate</text>']
    for n in range(6):
        x, y = 80 + 96 * n, 330 - 58 * n
        lines.append('<text x="%g" y="350" text-anchor="middle">%.3f</text>' % (x, maximum * n / 5))
        lines.append('<text x="68" y="%g" text-anchor="end">%d%%</text>' % (y + 4, n * 20))
    for index, (arm, cost, rate, status) in enumerate(points):
        color = ('#476582', '#a84432')[index]
        label = '%s: %s' % (arm, status)
        lines.append('<text x="80" y="%d" fill="%s">%s</text>' % (400 + 15 * index, color, label))
        if cost is not None:
            x, y = 80 + 480 * cost / maximum, 330 - 290 * rate
            lines.append('<circle data-arm="%s" data-cost="%s" data-pass-rate="%s" cx="%g" cy="%g" r="6" fill="%s"><title>%s, %.4f USD, %.1f%%</title></circle>'
                         % (arm, cost, rate, x, y, color, label, cost, rate * 100))
            lines.append('<text x="%g" y="%g" fill="%s">%s</text>' % (x + 9, y + (-12 if index else 18), color, arm))
    return "\n".join(lines + ['</g></svg>']) + "\n"


def _num(value, places=3):
    return "undefined" if value is None else "%.*f" % (places, value)


# --- Ablation arms against control (#514) ---------------------------------------------------------
#
# An ablation run has bare, control (`harness`, the harness at the tag) and one arm per removed or
# set entry. Each arm is reported against control on six measures separately, each with n, spread
# and a task-clustered paired interval, so an entry that wastes tokens and one that breaks a task
# never arrive as one number; Cost-of-Pass is one measure among them, never the headline. Reading
# and limits: docs/benchmarks.md, "Ablation runs".

CONTROL = "harness"
MIN_TRIALS = 5
PREFIX_TOLERANCE = 0.02  # same arm, task and date: every first-call prompt within 2% of the median
POWER = 0.8
EXPLORATORY = "exploratory"
# (key, label, kind): a ratio of means against control, or a difference in pass rate.
MEASURES = (("cost_usd", "cost", "ratio"), ("output_tokens", "output tokens", "ratio"),
            ("first_call_context", "standing prefix tokens", "ratio"), ("turns", "turns", "ratio"),
            ("tool_calls", "tool calls", "ratio"), ("pass_rate", "pass rate", "difference"),
            ("cost_per_passed", "cost per passed attempt", "pooled"))


def minimum_detectable_effect(attempts_per_arm, cv, comparisons=1, power=POWER, confidence=CONFIDENCE):
    """The smallest relative change in a per-attempt mean one arm-against-control comparison can
    resolve: `exp((z(1 - alpha / 2m) + z(power)) * cv * sqrt(2 / n)) - 1`, for `n` attempts per
    arm, a per-attempt coefficient of variation `cv` and `m` comparisons under Bonferroni. A
    planning figure from an assumed `cv`, never a measurement; None when n or cv is not positive."""
    from statistics import NormalDist
    if not attempts_per_arm or attempts_per_arm < 1 or not cv or cv <= 0:
        return None
    alpha = (1 - confidence) / max(1, comparisons)
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    return math.exp(z * cv * math.sqrt(2.0 / attempts_per_arm)) - 1


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def prefix_clean(rows, tolerance=PREFIX_TOLERANCE):
    """`{arm: {"usable", "worst_spread", "reason"}}`: whether an arm's first-call prompt figures
    (`first_call_context`) agree with themselves. For each (arm, task, date) every run must lie
    within `tolerance` of the group's median; a run that lacks the figure makes the arm unusable,
    since a missing field is unknown, never a smaller prefix. `worst_spread` is the largest
    relative distance from a median seen."""
    groups, missing = {}, {}
    for row in rows:
        arm = row.get("arm")
        value = row.get("first_call_context")
        if type(value) is not int or value < 0:
            missing[arm] = missing.get(arm, 0) + 1
            groups.setdefault((arm, None, None), [])
            continue
        groups.setdefault((arm, row.get("task"), row.get("date")), []).append(value)
    out = {}
    for (arm, _task, _date), values in sorted(groups.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
        entry = out.setdefault(arm, {"usable": True, "worst_spread": 0.0, "reason": None})
        if not values:
            continue
        median = _median(values)
        spread = max(abs(v - median) for v in values) / median if median else (0.0 if not any(values) else math.inf)
        entry["worst_spread"] = max(entry["worst_spread"], round(spread, 4))
    for arm, entry in out.items():
        if missing.get(arm):
            entry.update(usable=False, reason="%d run(s) lack a first-call prompt figure" % missing[arm])
        elif entry["worst_spread"] > tolerance:
            entry.update(usable=False, reason="same-arm runs differ by %.1f%%, more than %.0f%%"
                         % (100 * entry["worst_spread"], 100 * tolerance))
    return out


def _value(row, key):
    if key == "tool_calls":
        counts = row.get("tool_counts")
        if not isinstance(counts, dict):
            return None
        return sum(v for v in counts.values() if type(v) is int)
    value = row.get(key)
    return None if type(value) is bool or not isinstance(value, (int, float)) else float(value)


def _soft_prefix(row):
    attribution = row.get("context_attribution")
    modules = attribution.get("modules") if isinstance(attribution, dict) else None
    return float(sum(modules.values())) if isinstance(modules, dict) else None


def _cluster(rows, atts, arm, key):
    """`{task: [values]}` for one arm and measure; the pass rate reads the validated outcome."""
    out = {}
    passed = {(a["task"], a["arm"], a["trial"]): a["passed"] for a in atts}
    for row in rows:
        if row.get("arm") != arm:
            continue
        if key == "pass_rate":
            value = 1.0 if passed[(row["task"], arm, row.get("rep", row.get("trial")))] else 0.0
        elif key == "soft_prefix":
            value = _soft_prefix(row)
        else:
            value = _value(row, key)
        out.setdefault(row["task"], []).append(value)
    return out


def _pooled(values_by_task, tasks):
    values = [v for t in tasks for v in values_by_task[t]]
    return (sum(values) / len(values)) if values else None


def _estimate(kind, arm_vals, ctl_vals, tasks, arm_cost=None, ctl_cost=None, arm_pass=None, ctl_pass=None):
    """The point estimate of `kind` over `tasks`: a ratio of means, a difference, or a ratio of
    pooled Cost-of-Pass. None when it is undefined on these tasks."""
    if kind == "difference":
        return _pooled(arm_vals, tasks) - _pooled(ctl_vals, tasks)
    if kind == "pooled":
        cost = lambda vals: sum(v for t in tasks for v in vals[t])
        passes = lambda vals: sum(v for t in tasks for v in vals[t])
        if not passes(arm_pass) or not passes(ctl_pass) or not cost(ctl_cost):
            return None
        return (cost(arm_cost) / passes(arm_pass)) / (cost(ctl_cost) / passes(ctl_pass))
    control = _pooled(ctl_vals, tasks)
    return None if not control else _pooled(arm_vals, tasks) / control


def _spread(values_by_task):
    values = [v for vals in values_by_task.values() for v in vals]
    sd = None
    if len(values) > 1:
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    ratios = [max(vals) / min(vals) for vals in values_by_task.values() if vals and min(vals) > 0]
    return {"sd": None if sd is None else round(sd, 6),
            "max_over_min": round(max(ratios), 4) if ratios else None}


def _measure(rows, atts, arm, control, key, kind, tasks, seed, resamples, confidence):
    """One measure of one arm against control, with n, spread, interval and its reading."""
    source = "soft_prefix" if key == "soft_prefix" else key
    if kind == "pooled":
        arm_cost, ctl_cost = _cluster(rows, atts, arm, "cost_usd"), _cluster(rows, atts, control, "cost_usd")
        arm_pass, ctl_pass = _cluster(rows, atts, arm, "pass_rate"), _cluster(rows, atts, control, "pass_rate")
        arm_vals, ctl_vals = arm_cost, ctl_cost
    else:
        arm_vals, ctl_vals = _cluster(rows, atts, arm, source), _cluster(rows, atts, control, source)
        arm_cost = ctl_cost = arm_pass = ctl_pass = None
    n = sum(len(arm_vals.get(t, [])) for t in tasks)
    out = {"n": n, "control_n": sum(len(ctl_vals.get(t, [])) for t in tasks), "kind": kind}
    lacking = sum(1 for t in tasks for v in arm_vals.get(t, []) + ctl_vals.get(t, []) if v is None)
    if lacking:
        return dict(out, estimate=None, interval=None, spread=None, reading="unavailable",
                    reason="%d attempt(s) lack %s" % (lacking, key))
    estimate = _estimate(kind, arm_vals, ctl_vals, tasks, arm_cost, ctl_cost, arm_pass, ctl_pass)
    rng = random.Random(seed)
    samples, undefined = [], 0
    for _ in range(resamples):
        picks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        value = _estimate(kind, arm_vals, ctl_vals, picks, arm_cost, ctl_cost, arm_pass, ctl_pass)
        if value is None:
            undefined += 1
        else:
            samples.append(value)
    alpha = (1 - confidence) / 2
    interval = None
    if samples and undefined == 0:
        samples.sort()
        interval = [_rank(samples, alpha), _rank(samples, 1 - alpha)]
    null = 0.0 if kind == "difference" else 1.0
    effect = None if estimate is None else estimate - null
    effect_interval = None if interval is None else [round(interval[0] - null, 4), round(interval[1] - null, 4)]
    if effect_interval is None:
        reading, reason = INCONCLUSIVE, ("undefined in %d resample(s)" % undefined if undefined
                                         else "the estimate is undefined")
    elif effect_interval[0] <= 0 <= effect_interval[1]:
        reading, reason = INCONCLUSIVE, "the interval spans no effect"
    else:
        reading, reason = ("higher" if effect_interval[0] > 0 else "lower"), None
    return dict(out, estimate=None if estimate is None else round(estimate, 4),
                effect=None if effect is None else round(effect, 4), interval=effect_interval,
                resolvable=None if effect_interval is None
                else round((effect_interval[1] - effect_interval[0]) / 2, 4),
                spread=_spread(arm_vals), reading=reading, reason=reason)


def compare(rows, control=CONTROL, seed=SEED, resamples=RESAMPLES, correction=None, removed=None):
    """Every ablation arm against `control`, from saved rows; ValueError on malformed rows.

    Bare and control are not ranked. Each other arm reports `MEASURES` separately: ratios of
    per-attempt means (cost, output tokens, standing prefix, turns, tool calls) and the pass-rate
    difference, then Cost-of-Pass last. Each carries n, spread (SD and the worst per-task max over
    min) and a paired interval from the task-clustered bootstrap, which resamples tasks and keeps
    both arms' trials of a task together. An interval spanning no effect reads `inconclusive`.
    Fewer than `MIN_TRIALS` paired trials per task, or several arms with no `correction`, label
    the arm exploratory; `correction="bonferroni"` widens every interval to 1 - alpha/m. The
    prefix is the first-call prompt only where `prefix_clean` finds the arm and control usable;
    otherwise it is the summed `context_attribution`, labelled a soft estimate. Arms are ranked
    by the size of their cost effect. `removed` maps an arm to the entry it removed or set."""
    if correction not in (None, "bonferroni"):
        raise ValueError("correction %r is not bonferroni" % correction)
    names = []
    for row in rows:
        if row.get("arm") not in names:
            names.append(row.get("arm"))
    if control not in names:
        raise ValueError("no %s (control) rows" % control)
    atts = attempts(rows, tuple(names))
    arms_ = [a for a in names if a not in (control, "bare")]
    m = len(arms_)
    confidence = 1 - (1 - CONFIDENCE) / m if correction == "bonferroni" and m else CONFIDENCE
    clean = prefix_clean(rows)
    results = []
    for arm in arms_:
        tasks = sorted({a["task"] for a in atts if a["arm"] == arm} & {a["task"] for a in atts if a["arm"] == control})
        if not tasks:
            raise ValueError("arm %s shares no task with control" % arm)
        trials = min(sum(1 for a in atts if a["arm"] == name and a["task"] == t)
                     for t in tasks for name in (arm, control))
        reasons = []
        if trials < MIN_TRIALS:
            reasons.append("fewer than %d paired trials per task (%d)" % (MIN_TRIALS, trials))
        if m > 1 and correction is None:
            reasons.append("%d arms compared with no correction named" % m)
        measures = {}
        for key, label, kind in MEASURES:
            source = key
            estimand = "measured"
            if key == "first_call_context" and not (clean.get(arm, {}).get("usable") and clean.get(control, {}).get("usable")):
                source, estimand = "soft_prefix", "soft estimate"
            measures[key] = dict(_measure(rows, atts, arm, control, source, kind, tasks, seed, resamples, confidence),
                                 label=label, estimand=estimand)
        results.append({"arm": arm, "removed": (removed or {}).get(arm), "tasks": len(tasks),
                        "trials": trials, "exploratory": bool(reasons), "exploratory_reasons": reasons,
                        "measures": measures})
    results.sort(key=lambda r: (r["measures"]["cost_usd"].get("effect") is None,
                                -abs(r["measures"]["cost_usd"].get("effect") or 0.0), r["arm"]))
    return {"method": METHOD, "control": control, "seed": seed, "resamples": resamples,
            "confidence": round(confidence, 6), "correction": correction, "comparisons": m,
            "prefix": clean, "arms": results}


def _pct(value):
    return "undefined" if value is None else "%+.1f%%" % (100 * value)


def render_compare(result):
    """The ablation report as text: each arm against control, ranked by cost effect, every
    measure on its own line, and Cost-of-Pass last."""
    lines = ["Ablation result: %d arm(s) against control %s, ranked by cost effect; %s, seed %d, %d resamples, "
             "%s%% intervals%s" % (result["comparisons"], result["control"], result["method"], result["seed"],
                                   result["resamples"], ("%.4f" % (100 * result["confidence"])).rstrip("0").rstrip("."),
                                   ", Bonferroni over %d" % result["comparisons"] if result["correction"] else "")]
    for rank, arm in enumerate(result["arms"], 1):
        lines.append("  %d. %s%s: %d task(s), %d paired trial(s) per task%s"
                     % (rank, arm["arm"], " (changes %s)" % arm["removed"] if arm["removed"] else "", arm["tasks"],
                        arm["trials"], "; %s: %s" % (EXPLORATORY, "; ".join(arm["exploratory_reasons"]))
                        if arm["exploratory"] else ""))
        for key, _label, kind in MEASURES:
            m = arm["measures"][key]
            if m["reading"] == "unavailable":
                lines.append("     %s: unavailable, %s" % (m["label"], m["reason"]))
                continue
            spread = m["spread"] or {}
            what = "difference" if kind == "difference" else "effect"
            lines.append("     %s%s: %s %s, interval %s, n %d vs %d, SD %s, worst per-task max/min %s: %s%s"
                         % (m["label"], " (%s)" % m["estimand"] if m["estimand"] != "measured" else "", what,
                            _pct(m["effect"]), "undefined" if not m["interval"] else "[%s, %s]"
                            % (_pct(m["interval"][0]), _pct(m["interval"][1])), m["n"], m["control_n"],
                            _num(spread.get("sd"), 4), _num(spread.get("max_over_min"), 2), m["reading"],
                            "" if not m["reason"] else " (%s)" % m["reason"]))
    for arm, entry in sorted(result["prefix"].items(), key=lambda kv: str(kv[0])):
        if not entry["usable"]:
            lines.append("  prefix figures of %s unusable: %s; the soft estimate stands in" % (arm, entry["reason"]))
    return "\n".join(lines) + "\n"


def _interval(interval):
    return "undefined" if not interval else "[%s]" % ", ".join(
        "undefined" if value is None else repr(value) for value in interval)


def render(result):
    """The report as text: each arm, the ratio and difference with their intervals, the verdict,
    and the Pareto table of cost against pass rate."""
    lines = ["SM-2 result over %d task(s): %s, seed %d, %d resamples, %d%% intervals"
             % (result["tasks"], result["method"], result["seed"], result["resamples"],
                round(result["confidence"] * 100))]
    for arm in ARMS:
        a = result["arms"][arm]
        lines.append("  %s: %d/%d passed (%d errored), pass rate %s, Wilson %s (descriptive), "
                     "Cost-of-Pass %s USD, total %s USD"
                     % (arm, a["passes"], a["attempts"], a["errors"], _num(a["pass_rate"]),
                        _interval(a["pass_rate_interval_descriptive"]), _num(a["cost_of_pass"], 4),
                        _num(a["cost_usd"], 4)))
    ratio = _num(result["ratio"])
    if result["ratio_undefined"]:
        ratio += " (%s)" % result["ratio_undefined"]
    lines.append("  Cost-of-Pass ratio, harness over bare: %s, interval %s" % (ratio, _interval(result["ratio_interval"])))
    lines.append("  pass-rate difference, harness minus bare: %s, interval %s, margin -%g"
                 % (_num(result["difference"]), _interval(result["difference_interval"]), result["delta"]))
    if result["long"]:
        lines.append("  long-task subset (%s): ratio %s, interval %s"
                     % (", ".join(result["long"]["tasks"]), _num(result["long"]["ratio"]),
                        _interval(result["long"]["ratio_interval"])))
    else:
        lines.append("  long-task subset: no task is marked long")
    lines.append("  SM-2 eligibility: %s%s" %
                 ("eligible" if result["sm2_eligible"] else "exploratory only",
                  "; %s" % result["limitation"] if result["limitation"] else ""))
    lines.append("  verdict: %s, because %s%s" % (result["verdict"], result["reason"],
                                                   "; claim: %s" % result["claim"] if result["claim"] else ""))
    lines += ["", "Pareto view, mean cost per attempt against pass rate:", "",
              "| Arm | Mean USD per attempt | Pass rate | Pareto |", "| --- | --- | --- | --- |"]
    for arm, cost, rate, status in pareto(result):
        lines.append("| %s | %s | %s | %s |" % (arm, _num(cost, 4), _num(rate), status))
    return "\n".join(lines) + "\n"
