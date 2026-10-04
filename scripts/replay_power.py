#!/usr/bin/env python3
"""Size a replay task set for SM-2: k tasks, n of them long, and m trials per task and arm.

The pre-registration's "Power calculation" field names this command and pastes its output. It
calls no model; its inputs are pilot rows (`results.jsonl` from `cost_bench.py replay`) or the
variance figures stated directly.

**The model.** A normal approximation to the task-clustered bootstrap `replay_stats` runs, on the
log scale. For one task the harness-over-bare log Cost-of-Pass ratio varies between tasks with
variance `tau2`, and within a task each arm's log Cost-of-Pass over m trials varies with
`(cv2 + (1 - p) / p) / m`: the cost's squared coefficient of variation over m attempts, plus the
delta-method variance of a log pass rate. So for k tasks

    SE_ratio = sqrt((tau2 + 2 * (cv2 + (1 - p) / p) / m) / k)
    SE_diff  = sqrt((tau2_pass + 2 * p * (1 - p) / m) / k)

and, at a two-sided alpha with z = z(1 - alpha / 2):

- **ratio power**, the 95% interval on the ratio lying wholly below 1.0 when the true ratio is
  `1 - effect`: Phi(-log(1 - effect) / SE_ratio - z);
- **pass power**, the lower bound of the pass-rate difference above -delta (0.125) when the arms
  pass equally: Phi(delta / SE_diff - z);
- **long power**, the long subset's ratio interval wholly below 1.0, from the same formula over
  its n tasks with the long tasks' own variance `long_tau2`.

Decision power is ratio power times pass power and claim power is that times long power, treating
the tests as independent; their correlation is positive, so the product understates both. The
search returns the design with the fewest trials per arm, k times m, then the fewest tasks, whose
claim power and decision power both reach the target, with m at least five (SM-2). SM-2 caps the
minimum detectable effect at 15%, so a larger `--effect` is refused.

**No long-task variance, no claim power.** Long tasks may vary more than the set, so the whole
set's `tau2` cannot stand in for theirs. Without `long_tau2` (stated with `--long-tau2`, or read
from a pilot with at least three long tasks) long and claim power are unknown, never a number: the
design is sized on decision power alone, reported as not meeting the target, and the command
exits 1.

**From pilot rows.** `p` is the pooled pass rate of both arms. `cv2` is the pooled within-cell
variance of cost over the squared cell mean, across task-arm cells. `tau2` is the variance across
tasks of the per-task log Cost-of-Pass ratio less its within-task part, floored at zero; a task in
which either arm passed nothing has no log ratio and is counted, not used. `tau2_pass` is the
same for the per-task pass-rate difference. The pass outcome's intra-cluster correlation, tasks as
clusters, is reported per arm for the pre-registration's variance-source field.

**A pilot at the ceiling or the floor.** A pilot whose pass rate is 0 or 1 has no binomial variance
to size from, so the command refuses it unless the pre-registration states an assumed pass rate,
passed as `--assumed-pass-rate`. That rate then stands in for `p` in both standard errors, and the
output and the JSON say it was assumed and what the pilot showed; it is never applied silently.
The pilot's `tau2_pass`, zero at a ceiling, is kept, so the pass test assumes no between-task
variance in the pass-rate difference beyond the binomial term. `--mde` is the minimum detectable
effect, the same input as `--effect`.

A pilot at the floor, or one where fewer than two tasks passed in both arms, also has no per-task
log Cost-of-Pass ratio, so no `tau2`, and no assumed pass rate can recover it. The pre-registration
then states that variance too, passed as `--tau2` beside `--pilot`; it is accepted only when the
pilot measured none, and the output and the JSON (`tau2_assumed`) name it as an assumption.

Standard library only. Reading: docs/benchmarks.md.
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_stats  # noqa: E402  the row validator the analysis itself uses

ALPHA = 0.05
POWER = 0.8
EFFECT = 0.15  # SM-2: the expected ratio 0.85, and the largest minimum detectable effect allowed
DELTA = replay_stats.DELTA
MIN_REPS = 5  # SM-2's floor of trials per task and arm
MAX_REPS = 20
MAX_TASKS = 200
MIN_LONG_FOR_VARIANCE = 3  # fewer long tasks than this give no long-task variance, so no claim power


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


_Z = {}


def z_of(probability):
    """The standard normal quantile, by bisection on `phi`: exact enough and dependency-free."""
    if probability in _Z:
        return _Z[probability]
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if phi(mid) < probability:
            lo = mid
        else:
            hi = mid
    _Z[probability] = (lo + hi) / 2.0
    return _Z[probability]


def _within(cv2, pass_rate):
    return cv2 + (1.0 - pass_rate) / pass_rate


def se_ratio(k, m, tau2, cv2, pass_rate):
    return math.sqrt((tau2 + 2.0 * _within(cv2, pass_rate) / m) / k)


def se_diff(k, m, tau2_pass, pass_rate):
    return math.sqrt((tau2_pass + 2.0 * pass_rate * (1.0 - pass_rate) / m) / k)


def ratio_power(k, m, inputs, effect, alpha, which="all"):
    tau2 = inputs["long_tau2"] if which == "long" else inputs["tau2"]
    if tau2 is None:
        raise ValueError("the long subset's power needs the long tasks' own variance")
    se = se_ratio(k, m, tau2, inputs["cv2"], inputs["pass_rate"])
    return phi(-math.log(1.0 - effect) / se - z_of(1.0 - alpha / 2.0))


def pass_power(k, m, inputs, alpha, delta=DELTA):
    se = se_diff(k, m, inputs["tau2_pass"], inputs["pass_rate"])
    if se == 0:
        return 1.0
    return phi(delta / se - z_of(1.0 - alpha / 2.0))


def design_power(k, n, m, inputs, effect=EFFECT, alpha=ALPHA):
    """`{ratio, pass, decision, long, claim}` powers for k tasks, n long, m trials per arm; long and
    claim are None when `long_tau2` is unknown."""
    ratio = ratio_power(k, m, inputs, effect, alpha)
    passing = pass_power(k, m, inputs, alpha)
    if inputs.get("long_tau2") is None:
        long_power = claim = None
    else:
        long_power = ratio_power(n, m, inputs, effect, alpha, "long") if n else 0.0
        claim = ratio * passing * long_power
    return {"ratio": ratio, "pass": passing, "decision": ratio * passing, "long": long_power,
            "claim": claim}


def meets(powers, power=POWER):
    """Whether decision and claim power both reach `power`; never when claim power is unknown."""
    return powers["claim"] is not None and powers["decision"] >= power and powers["claim"] >= power


def mde(k, m, inputs, power=POWER, alpha=ALPHA):
    """The smallest saving (1 - ratio) the ratio test detects at `power` with k tasks and m trials."""
    se = se_ratio(k, m, inputs["tau2"], inputs["cv2"], inputs["pass_rate"])
    return 1.0 - math.exp(-(z_of(1.0 - alpha / 2.0) + z_of(power)) * se)


def size(inputs, effect=EFFECT, alpha=ALPHA, power=POWER, min_reps=MIN_REPS, max_reps=MAX_REPS,
         max_tasks=MAX_TASKS):
    """The design with the fewest trials per arm (k * m), then the fewest tasks, then the fewest
    long tasks, whose decision and claim power both reach `power`, with `meets` true; None when
    none does within the bounds. With no `long_tau2` it is the fewest-trials design by decision
    power alone, `n` None, claim power unknown and `meets` false."""
    check_inputs(inputs, effect, alpha, power)
    known = inputs.get("long_tau2") is not None
    candidates = []
    for m in range(max(min_reps, MIN_REPS), max_reps + 1):
        for k in range(1, max_tasks + 1):
            if design_power(k, 0, m, inputs, effect, alpha)["decision"] < power:
                continue
            if not known:
                candidates.append((k * m, k, None, m))
                break
            # Long power rises with n, so the first n that reaches the target is the fewest.
            n = next((n for n in range(1, k + 1)
                      if design_power(k, n, m, inputs, effect, alpha)["claim"] >= power), None)
            if n is not None:
                candidates.append((k * m, k, n, m))
                break
    if not candidates:
        return None
    _, k, n, m = min(candidates, key=lambda c: (c[0], c[1], c[2] or 0, c[3]))
    return {"k": k, "n": n, "m": m, "power": design_power(k, n or 0, m, inputs, effect, alpha),
            "mde": mde(k, m, inputs, power, alpha), "meets": known}


def check_inputs(inputs, effect, alpha, power):
    if not 0 < effect <= EFFECT:
        raise ValueError("the effect must be above 0 and at most %g: SM-2 caps the minimum detectable "
                         "effect at 15%%" % EFFECT)
    if not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("alpha and power must each lie between 0 and 1")
    if not 0 < inputs["pass_rate"] < 1:
        raise ValueError("the pass rate must lie strictly between 0 and 1; a pilot that passes "
                         "everything or nothing has no variance to size from, so state the "
                         "pre-registered pass rate with --assumed-pass-rate")
    for key in ("tau2", "cv2", "tau2_pass", "long_tau2"):
        if inputs.get(key) is not None and (not math.isfinite(inputs[key]) or inputs[key] < 0):
            raise ValueError("%s must be finite and must not be negative" % key)


def _sample_var(values):
    return statistics.variance(values) if len(values) > 1 else 0.0


def _icc(cells):
    """One-way ICC(1,1) of a 0/1 outcome with tasks as clusters; None when it is undefined."""
    groups = [g for g in cells if len(g) > 1]
    if len(groups) < 2:
        return None
    grand = statistics.mean(v for g in groups for v in g)
    m0 = statistics.mean(len(g) for g in groups)
    between = sum(len(g) * (statistics.mean(g) - grand) ** 2 for g in groups) / (len(groups) - 1)
    within_df = sum(len(g) - 1 for g in groups)
    within = sum((v - statistics.mean(g)) ** 2 for g in groups for v in g) / within_df if within_df else 0.0
    denominator = between + (m0 - 1) * within
    return None if denominator == 0 else (between - within) / denominator


def estimate(rows, arms=replay_stats.ARMS, assumed_tau2=None):
    """The variance inputs from pilot rows, by the rules in this module's docstring.
    `assumed_tau2` stands in for `tau2` only when the pilot has none to give."""
    attempts = replay_stats.attempts(rows, arms)
    if any(a["cost"] is None for a in attempts):
        raise ValueError("a pilot row has no cost; a cost variance cannot be read from it")
    cells = {}
    for a in attempts:
        cells.setdefault(a["task"], {arm: [] for arm in arms})[a["arm"]].append(a)
    tasks = sorted(cells)
    if len(tasks) < 2 or any(not cells[t][arm] for t in tasks for arm in arms):
        raise ValueError("the pilot needs at least two tasks, each run in both arms")
    pass_rate = statistics.mean(1.0 if a["passed"] else 0.0 for a in attempts)
    reps = statistics.mean(len(cells[t][arm]) for t in tasks for arm in arms)
    cv2_parts = []
    for t in tasks:
        for arm in arms:
            costs = [a["cost"] for a in cells[t][arm]]
            mean = statistics.mean(costs)
            if mean > 0 and len(costs) > 1:
                cv2_parts.append(_sample_var(costs) / mean ** 2)
    cv2 = statistics.mean(cv2_parts) if cv2_parts else 0.0

    def log_ratio(t):
        cop = []
        for arm in arms:
            cost = sum(a["cost"] for a in cells[t][arm])
            passes = sum(1 for a in cells[t][arm] if a["passed"])
            if not passes or cost <= 0:
                return None
            cop.append(cost / passes)
        return math.log(cop[1] / cop[0])

    def tau2_of(subset):
        ratios = [r for r in (log_ratio(t) for t in subset) if r is not None]
        if len(ratios) < 2:
            return None, len(subset) - len(ratios)
        within = 2.0 * _within(cv2, pass_rate) / reps
        return max(0.0, _sample_var(ratios) - within), len(subset) - len(ratios)

    tau2, unusable = tau2_of(tasks)
    tau2_assumed = tau2 is None and assumed_tau2 is not None
    if tau2 is None and assumed_tau2 is None:
        raise ValueError("fewer than two pilot tasks passed in both arms; no between-task variance, "
                         "so state the pre-registered tau2 with --tau2")
    if tau2 is not None and assumed_tau2 is not None:
        raise ValueError("the pilot measured tau2; --tau2 with --pilot is only for a pilot that has none")
    if tau2_assumed:
        if not math.isfinite(assumed_tau2) or assumed_tau2 < 0:
            raise ValueError("tau2 must be finite and must not be negative")
        tau2 = assumed_tau2
    diffs = [statistics.mean(1.0 if a["passed"] else 0.0 for a in cells[t][arms[1]])
             - statistics.mean(1.0 if a["passed"] else 0.0 for a in cells[t][arms[0]]) for t in tasks]
    tau2_pass = max(0.0, _sample_var(diffs) - 2.0 * pass_rate * (1.0 - pass_rate) / reps)
    long_tasks = sorted({a["task"] for a in attempts if a["long"]})
    long_tau2 = tau2_of(long_tasks)[0] if len(long_tasks) >= MIN_LONG_FOR_VARIANCE else None
    icc = dict((arm, _icc([[1.0 if a["passed"] else 0.0 for a in cells[t][arm]] for t in tasks]))
               for arm in arms)
    return {"tau2": tau2, "cv2": cv2, "pass_rate": pass_rate, "tau2_pass": tau2_pass,
            "long_tau2": long_tau2, "tasks": len(tasks), "long_tasks": len(long_tasks),
            "unusable_tasks": unusable, "reps": reps, "icc_pass": icc, "tau2_assumed": tau2_assumed}


def assume_pass_rate(inputs, assumed):
    """`inputs` with the pre-registered `assumed` pass rate standing in for the measured one; the
    measured rate is kept as `measured_pass_rate` and `pass_rate_assumed` is set, so every report
    says the figure was assumed."""
    if not 0 < assumed < 1:
        raise ValueError("--assumed-pass-rate must lie strictly between 0 and 1")
    return dict(inputs, pass_rate=assumed, measured_pass_rate=inputs.get("pass_rate"), pass_rate_assumed=True)


def read_rows(paths):
    rows = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            path = path / "results.jsonl"
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _num(value, places=4):
    return "n/a" if value is None else ("%.*f" % (places, value))


def render(result, inputs, effect, alpha, power, have=None):
    lines = ["Power for SM-2 at alpha %g two-sided, target power %g, minimum detectable effect %g "
             "(ratio %g), delta %g" % (alpha, power, effect, 1 - effect, DELTA),
             "inputs: tau2 %s, cv2 %s, pass rate %s, tau2_pass %s, long tau2 %s"
             % (_num(inputs["tau2"]), _num(inputs["cv2"]), _num(inputs["pass_rate"]),
                _num(inputs["tau2_pass"]), _num(inputs.get("long_tau2")) if inputs.get("long_tau2") is not None
                else "unknown (claim power is unavailable)")]
    if inputs.get("pass_rate_assumed") and "tasks" in inputs:
        lines.append("assumption: pass rate %s is the pre-registered assumption, not a measurement; the "
                     "measured pass rate is %s, and tau2_pass %s is the measured figure"
                     % (_num(inputs["pass_rate"]), _num(inputs.get("measured_pass_rate")),
                        _num(inputs["tau2_pass"])))
    elif inputs.get("pass_rate_assumed"):
        lines.append("assumption: pass rate %s is the pre-registered assumption, not a measurement; "
                     "tau2_pass %s is stated, not measured" % (_num(inputs["pass_rate"]), _num(inputs["tau2_pass"])))
    if inputs.get("tau2_assumed"):
        lines.append("assumption: tau2 %s is the pre-registered assumption, not a measurement; fewer "
                     "than two pilot tasks passed in both arms" % _num(inputs["tau2"]))
    if "tasks" in inputs:
        lines.append("pilot: %d task(s), %d long, %s trial(s) per cell, %d task(s) with no log ratio; "
                     "pass ICC %s" % (inputs["tasks"], inputs["long_tasks"], _num(inputs["reps"], 1),
                                      inputs["unusable_tasks"],
                                      ", ".join("%s %s" % (arm, _num(v, 3)) for arm, v in sorted(inputs["icc_pass"].items()))))
    if result is None:
        lines.append("no design within the bounds reaches the target; raise --max-tasks or --max-reps")
    else:
        p = result["power"]
        lines.append("design: k = %d task(s), n = %s long, m = %d trial(s) per task and arm, %d run(s) per arm"
                     % (result["k"], "unknown" if result["n"] is None else result["n"], result["m"],
                        result["k"] * result["m"]))
        lines.append("power: ratio %.3f, pass %.3f, decision %.3f, long subset %s, claim %s; MDE %.1f%%"
                     % (p["ratio"], p["pass"], p["decision"], _power(p["long"]), _power(p["claim"]),
                        100 * result["mde"]))
        if not result["meets"]:
            lines.append(NO_CLAIM)
    if have is not None:
        p = have["power"]
        lines.append("this set: k = %d, n = %d, m = %d: decision %.3f, claim %s, MDE %.1f%%: %s"
                     % (have["k"], have["n"], have["m"], p["decision"], _power(p["claim"]), 100 * have["mde"],
                        "meets the target" if have["meets"] else "below the target"))
    return "\n".join(lines)


NO_CLAIM = ("the design does not meet the target: claim power is unavailable without the long tasks' own "
            "variance; pass --long-tau2, or a pilot with at least %d long tasks" % MIN_LONG_FOR_VARIANCE)


def _power(value):
    return "unavailable" if value is None else "%.3f" % value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pilot", action="append", help="a pilot's results.jsonl, or the directory "
                        "holding one; repeatable. Or state the inputs below")
    parser.add_argument("--tau2", type=float, help="between-task variance of the log Cost-of-Pass ratio; "
                        "with --pilot, the pre-registered figure for a pilot that measured none")
    parser.add_argument("--cv2", type=float, help="within-cell squared coefficient of variation of cost")
    parser.add_argument("--pass-rate", type=float, help="pooled pass rate of both arms")
    parser.add_argument("--tau2-pass", type=float, help="between-task variance of the pass-rate difference")
    parser.add_argument("--long-tau2", type=float, help="the long tasks' own tau2; without it, or "
                        "a pilot with enough long tasks, claim power is unavailable")
    parser.add_argument("--assumed-pass-rate", type=float, help="the pre-registered pass rate to size "
                        "with in place of the measured one; required when a pilot passes everything "
                        "or nothing, and printed as an assumption")
    parser.add_argument("--effect", "--mde", dest="effect", type=float, default=EFFECT,
                        help="the minimum detectable effect, the saving 1 - ratio; at most and by "
                        "default %(default)s")
    parser.add_argument("--alpha", type=float, default=ALPHA, help="two-sided; default %(default)s")
    parser.add_argument("--power", type=float, default=POWER, help="target joint power; default %(default)s")
    parser.add_argument("--min-reps", type=int, default=MIN_REPS, help="default and floor %(default)s")
    parser.add_argument("--max-reps", type=int, default=MAX_REPS, help="default %(default)s")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS, help="default %(default)s")
    parser.add_argument("--have", nargs=3, type=int, metavar=("K", "N", "M"), help="also report whether "
                        "a set of K tasks, N long, at M trials meets the target; exit 1 when it does not")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    stated = (args.cv2, args.pass_rate, args.tau2_pass)
    try:
        if args.pilot:
            if any(v is not None for v in stated):
                raise ValueError("give --pilot or the stated inputs, not both; only --tau2, for a pilot "
                                 "with no between-task variance, goes with it")
            inputs = estimate(read_rows(args.pilot), assumed_tau2=args.tau2)
            if args.long_tau2 is not None:
                inputs["long_tau2"] = args.long_tau2
        elif args.pass_rate is not None and args.assumed_pass_rate is not None:
            raise ValueError("give --pass-rate or --assumed-pass-rate, not both")
        elif all(v is not None for v in (args.tau2, args.cv2, args.tau2_pass)) and \
                (args.pass_rate is not None or args.assumed_pass_rate is not None):
            inputs = {"tau2": args.tau2, "cv2": args.cv2, "pass_rate": args.pass_rate,
                      "tau2_pass": args.tau2_pass, "long_tau2": args.long_tau2}
        else:
            raise ValueError("give --pilot, or all of --tau2, --cv2, --pass-rate (or --assumed-pass-rate) "
                             "and --tau2-pass")
        if args.assumed_pass_rate is not None:
            inputs = assume_pass_rate(inputs, args.assumed_pass_rate)
        result = size(inputs, args.effect, args.alpha, args.power, args.min_reps, args.max_reps, args.max_tasks)
        have = None
        if args.have:
            k, n, m = args.have
            if not 0 < n <= k or m < MIN_REPS:
                raise ValueError("--have needs 0 < N <= K and M of at least %d" % MIN_REPS)
            powers = design_power(k, n, m, inputs, args.effect, args.alpha)
            have = {"k": k, "n": n, "m": m, "power": powers, "mde": mde(k, m, inputs, args.power, args.alpha),
                    "meets": meets(powers, args.power)}
    except (ValueError, OSError) as exc:
        print("replay-power: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"inputs": inputs, "effect": args.effect, "alpha": args.alpha, "power": args.power,
                          "design": result, "have": have}, indent=2, sort_keys=True))
    else:
        print(render(result, inputs, args.effect, args.alpha, args.power, have))
    if have is not None:
        return 0 if have["meets"] else 1
    return 0 if result is not None and result["meets"] else 1


if __name__ == "__main__":
    sys.exit(main())
