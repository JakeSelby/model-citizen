#!/usr/bin/env python3
"""SM-2's analysis of a finished two-arm replay, from its saved rows alone.

Each arm's Cost-of-Pass pools the set: the total cost of every attempt over the total passes. By
intention to treat every attempt counts, so an errored, crashed or timed-out run is a failed
attempt whose cost is in the figure. The harness-over-bare ratio and the pass-rate difference
(harness minus bare) carry paired, task-clustered 95% intervals from a percentile bootstrap that
resamples tasks with replacement and keeps both arms' trials of a task together; it is
deterministic for a seed, and the seed and resample count are recorded with the result. Each
arm's own pass rate carries a Wilson interval, which is descriptive only. The verdict applies
SM-2's decision rule; reading and limits: docs/benchmarks.md and docs/evidence-standard.md.

Standard library only, and no model call: every figure here re-derives from `results.jsonl`.
"""
import math
import random
from fractions import Fraction

ARMS = ("bare", "harness")
CONFIDENCE = 0.95
Z95 = 1.959963984540054  # the two-sided 95% normal quantile
DELTA = 0.125  # SM-2's non-inferiority margin on the pass-rate difference
MAGNITUDE = 0.85  # "at least 15% cheaper" needs the ratio's upper bound at or below this
SEED = 795
RESAMPLES = 10000
METHOD = "task-clustered paired percentile bootstrap"
SUPPORTED, NOT_SUPPORTED, INCONCLUSIVE = "supported", "not supported", "inconclusive"


def attempts(rows):
    """One validated `{task, arm, trial, passed, cost, long}` per saved row.

    ValueError names the first malformed or contradictory row. An errored row is a fail by
    intention to treat. The trial is the row's `rep`.
    """
    out, seen, long_by_task = [], set(), {}
    for index, row in enumerate(rows, 1):
        missing = [k for k in ("task", "arm", "cost_usd") if k not in row]
        if "rep" not in row and "trial" not in row:
            missing.append("rep")
        if missing:
            raise ValueError("row %d has no %s" % (index, ", ".join(missing)))
        if row["arm"] not in ARMS:
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


def wilson(passes, n, z=Z95):
    """The Wilson score interval on a proportion, `(low, high)`; `(None, None)` for no attempts."""
    if not n:
        return None, None
    p = passes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _cells(atts):
    """Per task, per arm: `[cost or None, passes, attempts]`, and the sorted task ids. ValueError
    when a task has attempts in one arm only, since a paired interval cannot use it."""
    cells, trial_ids = {}, {}
    for a in atts:
        cell = cells.setdefault(a["task"], {arm: [0.0, 0, 0] for arm in ARMS})[a["arm"]]
        trial_ids.setdefault(a["task"], {arm: set() for arm in ARMS})[a["arm"]].add(a["trial"])
        cell[0] = None if cell[0] is None or a["cost"] is None else cell[0] + a["cost"]
        cell[1] += 1 if a["passed"] else 0
        cell[2] += 1
    unpaired = sorted(t for t, c in cells.items() if not all(c[arm][2] for arm in ARMS))
    if unpaired:
        raise ValueError("task %s has attempts in one arm only; a paired interval needs both" % unpaired[0])
    mismatched = sorted(t for t in cells if trial_ids[t]["bare"] != trial_ids[t]["harness"])
    if mismatched:
        raise ValueError("task %s has different trial ids in its two arms" % mismatched[0])
    tasks = sorted(cells)
    if tasks and any(trial_ids[t]["bare"] != trial_ids[tasks[0]]["bare"] for t in tasks):
        raise ValueError("tasks have different trial ids; a fixed sample needs one trial set")
    return cells, tasks


def _totals(cells, tasks):
    out = {}
    for arm in ARMS:
        costs = [cells[t][arm][0] for t in tasks]
        total = None if None in costs else sum(costs)
        if total is not None and not math.isfinite(total):
            raise ValueError("the %s arm has a non-finite aggregate cost" % arm)
        out[arm] = (total, sum(cells[t][arm][1] for t in tasks),
                    sum(cells[t][arm][2] for t in tasks))
    return out


def _ratio(totals):
    """(value, reason). The value is None, with the reason, when it is undefined."""
    unreadable = [arm for arm in ARMS if totals[arm][0] is None]
    if unreadable:
        return None, "the %s arm has a run with no readable cost" % unreadable[0]
    nothing = [arm for arm in ARMS if not totals[arm][1]]
    if nothing:
        return None, "the %s arm passed nothing" % " and the ".join(nothing)
    harness, bare = (cost_of_pass(totals[arm][0], totals[arm][1]) for arm in ("harness", "bare"))
    if not bare:
        return None, "the bare arm's cost is zero"
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


def bootstrap(cells, tasks, seed=SEED, resamples=RESAMPLES):
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
    priced = all(cells[t][arm][0] is not None for t in tasks for arm in ARMS)
    ratios, diffs, undefined = [], [], 0
    for _ in range(resamples):
        picks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        totals = _totals(cells, picks)
        (hc, hp, hn), (bc, bp, bn) = totals["harness"], totals["bare"]
        diffs.append(hp / hn - bp / bn)
        if not priced:
            continue
        value, _ = _ratio(totals)
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


def analyse(rows, seed=SEED, resamples=RESAMPLES):
    """The whole SM-2 result for a finished two-arm row set; ValueError when it cannot be derived."""
    atts = attempts(rows)
    cells, tasks = _cells(atts)
    if not tasks:
        raise ValueError("no rows")
    totals = _totals(cells, tasks)
    arms = {}
    for arm in ARMS:
        cost, passes, n = totals[arm]
        low, high = wilson(passes, n)
        arms[arm] = {"attempts": n, "passes": passes,
                     "errors": sum(1 for a in atts if a["arm"] == arm and a["error"]),
                     "cost_usd": None if cost is None else round(cost, 6),
                     "cost_of_pass": None if cost_of_pass(cost, passes) is None else round(cost / passes, 6),
                     "mean_cost_per_attempt": None if cost is None else round(cost / n, 6),
                     "pass_rate": round(passes / n, 4),
                     "pass_rate_interval_descriptive": [round(low, 4), round(high, 4)]}
    ratio, undefined_reason = _ratio(totals)
    ratio_ci, diff_ci, undefined = bootstrap(cells, tasks, seed, resamples)
    long_tasks = sorted({a["task"] for a in atts if a["long"]})
    long = None
    if long_tasks:
        long_ratio, long_reason = _ratio(_totals(cells, long_tasks))
        long = {"tasks": long_tasks, "ratio": None if long_ratio is None else round(long_ratio, 4),
                "ratio_undefined": long_reason,
                "ratio_interval": bootstrap(cells, long_tasks, seed, resamples)[0]}
    verdict, reason, claim = decide(ratio, ratio_ci, diff_ci, long and long["ratio_interval"], bool(long_tasks))
    min_trials = min(cells[task][arm][2] for task in tasks for arm in ARMS)
    eligible = min_trials >= 5
    limitation = None
    if not eligible:
        limitation = "fewer than five paired trials per task and arm; exploratory diagnostic only"
        verdict, reason, claim = INCONCLUSIVE, limitation, None
    return {"method": METHOD, "seed": seed, "resamples": resamples, "confidence": CONFIDENCE,
            "delta": DELTA, "tasks": len(tasks), "arms": arms,
            "ratio": None if ratio is None else round(ratio, 4), "ratio_undefined": undefined_reason,
            "ratio_interval": ratio_ci, "undefined_resamples": undefined,
            "difference": round(totals["harness"][1] / totals["harness"][2] -
                                totals["bare"][1] / totals["bare"][2], 4),
            "difference_interval": diff_ci, "long": long, "sm2_eligible": eligible,
            "limitation": limitation,
            "verdict": verdict, "reason": reason, "claim": claim}


def pareto(result):
    """Per arm `(arm, mean cost per attempt, pass rate, status)`, status `frontier` or `dominated
    by <arm>`: dominated when another arm costs no more per attempt and passes at least as often,
    better on one. An arm with an unreadable cost is `unpriced`."""
    arms = result["arms"]
    out = []
    for arm in ARMS:
        mine = arms[arm]
        status = "frontier"
        if mine["mean_cost_per_attempt"] is None:
            status = "unpriced"
        for other in ARMS:
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
