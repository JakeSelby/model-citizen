#!/usr/bin/env python3
"""The long-session tier: one scripted multi-turn session per scenario, arm and rep.

A long-session scenario (`replay_pack.load_scenarios`) is a fixed script of user turns with
checkpoints, kept in the evaluator pack. `run_session` drives one session through a `driver`, a
callable that runs one user turn and returns its stream-json, so the same sequencing runs against
the replay containers (`cost_bench.long_session_driver`) and against a fake CLI in the tests:

- **One session.** Turn 1 starts it and each later turn resumes it; the driver is told which.
- **Branches.** A `branch` turn sends its `pass` or `fail` prompt by an earlier checkpoint's
  verdict, so the turn count never changes and runs stay comparable.
- **Caps.** At most `max_user_turns` turns are sent; each turn's own agent turns are capped by the
  driver at `max_agent_turns_per_user_turn`; and the session stops before a turn once its reported
  spend reaches the per-session cap. Each turn is given what is left of the cap as its budget
  (`turn_budget`), and a session given what is left of the whole run's stop ends before a turn
  whose budget could cross it. The CLI holds a turn to its budget only between API calls, so a
  turn can still overshoot it by up to one call.
- **Checkpoints.** After a checkpoint's turn, `checker` scores the tree with the segment's stream:
  every turn since the previous checkpoint, appended in order, and the session's totals as they
  stood before the segment's first turn. A checkpoint never reached is not passed and its metrics
  are null.
- **Cost.** A result reports the session's running totals (`session_totals`), so a turn's figures
  are the change since the previous result and the session's spend is the latest total.

The rows are one per checkpoint and one per session; their fields and identity are in
docs/benchmarks.md. `summarise` reads them back with scenario-clustered intervals. Standard
library only; nothing here starts a container or calls a model.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_metrics  # noqa: E402  the named-metric fields a checkpoint row carries
import replay_pair  # noqa: E402  model classes, for cost by tier
import replay_stats  # noqa: E402  the percentile rank and the seed every interval here shares

TIER = "long-session"
CHECKPOINT, SESSION = "checkpoint", "session"
TOKEN_KINDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
MODEL_USAGE_KEYS = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
# The `modelUsage` keys that are session running totals. The rest describe the model
# (`contextWindow`, `maxOutputTokens`, `canonicalModel`, `costBasis`, `provider`) and may change or
# vanish between turns.
RUNNING_TOTAL_KEYS = MODEL_USAGE_KEYS + ("thinkingTokens", "webSearchRequests", "costUSD")
UNRANKED = "unranked"
BUDGET_STOP = "error_max_budget_usd"
TURN_CAP_STOP = "error_max_turns"
# A resumed turn whose result is another session's, or whose running totals fell: the resume lost
# the session, so the turn's own figures are unknown.
RESUME_LOST = "resume-lost"
# Why a session stopped before its script ended.
STOP_CAP, STOP_USER_TURNS, STOP_ERROR, STOP_SPEND = "cap", "max_user_turns", "error", "spend_cap"
# The least `--max-budget-usd` a turn is launched with, so a session just short of its cap still
# gets a budget the CLI can act on; it is why a session's worst case is its cap plus this.
MIN_TURN_BUDGET_USD = 0.01
CONTROL = "bare"
METHOD = "scenario-clustered percentile bootstrap"


def session_cap(scenario, run_cap=None):
    """The hard spend cap of one session: `--run-cap` when it is lower, else the scenario's own
    `max_cost_usd_hint`."""
    hint = float(scenario["caps"]["max_cost_usd_hint"])
    return hint if run_cap is None else min(float(run_cap), hint)


def turn_budget(cap, spent):
    """A turn's `--max-budget-usd`: what is left of the session's cap, at least
    `MIN_TURN_BUDGET_USD`."""
    return round(max(cap - spent, MIN_TURN_BUDGET_USD), 6)


def session_ceiling(scenario, run_cap=None):
    """The most one session can spend if each turn holds to its budget: its last turn starts
    below the cap with `turn_budget`, so it ends below the cap plus `MIN_TURN_BUDGET_USD`."""
    return round(session_cap(scenario, run_cap) + MIN_TURN_BUDGET_USD, 6)


def ceiling_usd(scenarios, reps, arms, run_cap=None, preflight_cap=0.0):
    """The most a set can report while each turn holds to its budget: every session at its
    `session_ceiling`, and every arm's preflight at its own cap."""
    return round(sum(session_ceiling(s, run_cap) for s in scenarios) * reps * arms + arms * preflight_cap, 6)


def choose_prompt(turn, verdicts):
    """(prompt, branch record or None) for one scripted turn. A branch on a checkpoint that did not
    pass, or has no verdict, takes `fail`."""
    branch = turn.get("branch")
    if branch is None:
        return turn["prompt"], None
    taken = "pass" if verdicts.get(branch["on"]) is True else "fail"
    return branch[taken], {"on": branch["on"], "taken": taken}


def events(stdout):
    """The JSON objects of a stream-json output, one per line; anything else is skipped."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    out = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _context(usage):
    if not isinstance(usage, dict):
        return None
    parts = [usage.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    if any(not isinstance(p, (int, float)) for p in parts):
        return None
    return int(sum(parts))


def tier_of(model, tiers):
    return replay_pair.model_class(model, tiers) or UNRANKED


# The session's totals before its first turn: what turn 1, and the first segment, count against.
ZERO_TOTALS = {"total_cost_usd": 0, "modelUsage": {}}


def session_totals(result):
    """A `result` event's session totals, `{"total_cost_usd", "modelUsage"}`, or None unpriced.

    A resumed `claude -p --resume` turn reports `total_cost_usd` and `modelUsage` as the session's
    running totals, not the turn's own; only its top-level `usage` is the turn's alone. So a turn's
    cost, tokens and cost by tier are the change in these totals since the previous turn's result,
    and the session's spend is the latest total."""
    if not isinstance(result, dict) or not isinstance(result.get("total_cost_usd"), (int, float)):
        return None
    models = {m: dict(u) for m, u in (result.get("modelUsage") or {}).items() if isinstance(u, dict)}
    return {"total_cost_usd": result["total_cost_usd"], "modelUsage": models}


def _number(value):
    return value if isinstance(value, (int, float)) else 0


def totals_fell(previous, current):
    """Whether any running total in `current` is below `previous`: `total_cost_usd`, or any
    `RUNNING_TOTAL_KEYS` entry of any model's `modelUsage`, a model missing from `current` counting
    as zero."""
    if _number(current.get("total_cost_usd")) < _number(previous.get("total_cost_usd")):
        return True
    now = current.get("modelUsage") or {}
    return any(_number((now.get(model) or {}).get(key)) < _number(usage.get(key))
               for model, usage in (previous.get("modelUsage") or {}).items() for key in RUNNING_TOTAL_KEYS)


def turn_usage(stdout, tiers, previous=None):
    """One turn's figures from its stream, as the change in the session's totals (`session_totals`)
    from `previous`, the last result before it (`ZERO_TOTALS` when None): the cost, the four token
    kinds and the cost per model class from `modelUsage` model by model, subagents included; each
    main-thread call's context (`input + cache write + cache read`); and the result's verdict.
    `totals` is this result's own session totals, the next turn's `previous`. `cost_usd` is None
    when no priced result arrived. A result without `modelUsage` takes its tokens from its own
    top-level `usage`, which is the turn's alone."""
    previous = previous or ZERO_TOTALS
    stream = events(stdout)
    results = [e for e in stream if e.get("type") == "result"]
    contexts = [c for c in (_context((e.get("message") or {}).get("usage")) for e in stream
                            if e.get("type") == "assistant" and e.get("parent_tool_use_id") is None)
                if c is not None]
    session_ids = [e.get("session_id") for e in stream if isinstance(e.get("session_id"), str)]
    out = {"cost_usd": None, "tokens": {k: None for k in TOKEN_KINDS}, "cost_by_tier": {},
           "main_contexts": contexts, "is_error": None, "subtype": "", "agent_turns": None,
           "session_id": session_ids[-1] if session_ids else None, "totals": None}
    totals = session_totals(results[-1]) if results else None
    if totals is None:
        return out
    result = results[-1]
    out.update(cost_usd=round(float(totals["total_cost_usd"]) - float(previous["total_cost_usd"]), 6),
               is_error=bool(result.get("is_error")), subtype=str(result.get("subtype") or ""),
               agent_turns=result.get("num_turns"), totals=totals)
    per_model, before = totals["modelUsage"], previous.get("modelUsage") or {}
    if per_model:
        deltas = {m: {key: _number(u.get(key)) - _number((before.get(m) or {}).get(key))
                      for key in MODEL_USAGE_KEYS + ("costUSD",)} for m, u in per_model.items()}
        out["tokens"] = {kind: int(sum(d[key] for d in deltas.values()))
                         for kind, key in zip(TOKEN_KINDS, MODEL_USAGE_KEYS)}
        for model, delta in sorted(deltas.items()):
            cls = tier_of(model, tiers)
            out["cost_by_tier"][cls] = round(out["cost_by_tier"].get(cls, 0.0) + float(delta["costUSD"]), 6)
    else:
        usage = result.get("usage") or {}
        out["tokens"] = {kind: int(usage.get(kind) or 0) for kind in TOKEN_KINDS}
    return out


def _add(total, part):
    """Sum two token or tier dicts; a None on either side stays None."""
    keys = set(total) | set(part)
    out = {}
    for key in keys:
        a, b = total.get(key, 0), part.get(key, 0)
        out[key] = None if a is None or b is None else round(a + b, 6) if isinstance(a, float) or isinstance(b, float) else a + b
    return out


def slope(values):
    """The least-squares slope of `values` on turn numbers 1..n, or None for fewer than two."""
    points = [(i, v) for i, v in enumerate(values, 1) if v is not None]
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(i for i, _ in points) / n
    my = sum(v for _, v in points) / n
    sxx = sum((i - mx) ** 2 for i, _ in points)
    return round(sum((i - mx) * (v - my) for i, v in points) / sxx, 6) if sxx else None


def _segment_fields(turns, cumulative):
    tokens, tiers, contexts, cost = {k: 0 for k in TOKEN_KINDS}, {}, [], 0.0
    for usage in turns:
        tokens = _add(tokens, usage["tokens"])
        tiers = _add(tiers, usage["cost_by_tier"])
        contexts += usage["main_contexts"]
        cost = None if cost is None or usage["cost_usd"] is None else cost + usage["cost_usd"]
    return dict({k: tokens.get(k) for k in TOKEN_KINDS}, cost_usd=None if cost is None else round(cost, 6),
                main_peak_context_tokens=max(contexts) if contexts else None,
                cost_by_tier=dict(sorted(tiers.items())), cumulative_cost_usd=round(cumulative, 6))


def run_session(scenario, base, cap, driver, checker, tiers, run_left=None):
    """The rows of one session: one per checkpoint in turn order, then the session's own.

    `driver(number, prompt, budget, resume)` runs one user turn and returns `{"stdout": ...}` with
    optional `returncode`, `timeout` (the turn ran out of time; its cost is unknown) and
    `error_kind` (the caller refused the turn, e.g. it read the installed checkout). `checker(name,
    stream)` scores checkpoint `name` on the tree, `stream` being the segment's stream-json text, and
    returns `(passed, detail)` or `(passed, detail, recorded metrics)`; it is called with a third
    argument, `baseline`, the session totals of the last result before the segment (`ZERO_TOTALS`
    for the first). `base` is copied into every row. Spend that no result reported, a timed-out
    turn's, counts at its whole budget (`turn_budget`), as does a turn whose resume lost the session
    (`RESUME_LOST`): its result names another session, or a running total fell (`totals_fell`).
    `run_left` is what the whole run may still spend when the session starts; a turn whose
    budget could take the session past it is not sent, and the session stops as `STOP_SPEND`."""
    caps = scenario["caps"]
    order = scenario["checkpoint_order"]
    verdicts, records = {}, {}
    usages, branches, segment, segment_text = [], [], [], []
    spent, stopped, error_kind = 0.0, None, ""
    latest = segment_baseline = ZERO_TOTALS
    session_id = None
    agent_cap_hits = 0
    for number, turn in enumerate(scenario["turns"], 1):
        if number > caps["max_user_turns"]:
            stopped = STOP_USER_TURNS
            break
        if spent >= cap:
            stopped = STOP_CAP
            break
        budget = turn_budget(cap, spent)
        if run_left is not None and spent + budget > run_left:
            stopped, error_kind = STOP_SPEND, STOP_SPEND  # cut by the run, not the arm: no measurement
            break
        prompt, branch = choose_prompt(turn, verdicts)
        if branch is not None:
            branches.append(dict(branch, turn=number))
        done = driver(number, prompt, budget, number > 1)
        stdout = done.get("stdout") or ""
        usage = turn_usage(stdout, tiers, latest)
        usages.append(usage)
        segment.append(usage)
        segment_text.append(stdout if stdout.endswith("\n") or not stdout else stdout + "\n")
        if done.get("timeout") or usage["cost_usd"] is None:
            usage["cost_usd"] = budget
            spent = round(spent + budget, 6)
            stopped, error_kind = STOP_ERROR, ("timeout" if done.get("timeout") else
                                               "exit %s: no priced result" % done.get("returncode"))
            break
        lost = (session_id is not None and usage["session_id"] not in (None, session_id)) or \
            totals_fell(latest, usage["totals"])
        if lost:  # never a negative cost: like a timeout, the turn counts at its whole budget
            usage.update(cost_usd=budget, tokens={k: None for k in TOKEN_KINDS}, cost_by_tier={})
            spent = round(spent + budget, 6)
            stopped, error_kind = STOP_ERROR, RESUME_LOST
            break
        session_id = session_id or usage["session_id"]
        latest = usage["totals"]
        spent = float(latest["total_cost_usd"])
        if done.get("error_kind"):
            stopped, error_kind = STOP_ERROR, done["error_kind"]
            break
        if usage["subtype"] == BUDGET_STOP:
            stopped = STOP_CAP
            break
        if usage["subtype"] == TURN_CAP_STOP:
            agent_cap_hits += 1  # the turn's own agent cap ended it; the user's next turn still comes
        elif usage["is_error"] or done.get("returncode"):
            stopped, error_kind = STOP_ERROR, usage["subtype"] or "exit %s" % done.get("returncode")
            break
        name = turn.get("checkpoint")
        if name is None:
            continue
        try:
            scored = checker(name, "".join(segment_text), segment_baseline)
        except Exception as exc:  # a check that cannot run says nothing about the agent's work
            stopped, error_kind = STOP_ERROR, "check %s: %s" % (name, type(exc).__name__)
            break
        verdicts[name] = bool(scored[0])
        records[name] = {"turn": number, "passed": bool(scored[0]), "detail": str(scored[1] or ""),
                         "recorded": scored[2] if len(scored) > 2 else None,
                         "segment": _segment_fields(segment, spent),
                         "segment_turns": [number - len(segment) + 1, number]}
        segment, segment_text, segment_baseline = [], [], latest
    rows = []
    for index, name in enumerate(order, 1):
        record = records.get(name)
        declared = scenario["checkpoints"][name]["metrics"]
        row = dict(base, row_kind=CHECKPOINT, checkpoint=name, checkpoint_index=index, error=False,
                   error_kind="", **oracle_metrics.row_fields(declared))
        if record is None:
            row.update(reached=False, turn=None, passed=False, outcome="fail", check_detail="",
                       segment_turns=None, cost_usd=None, main_peak_context_tokens=None, cost_by_tier={},
                       cumulative_cost_usd=None, **{k: None for k in TOKEN_KINDS})
        else:
            row.update(record["segment"], reached=True, turn=record["turn"], passed=record["passed"],
                       outcome="pass" if record["passed"] else "fail", check_detail=record["detail"],
                       segment_turns=record["segment_turns"])
            if record["recorded"] is not None:
                row.update(record["recorded"])
        rows.append(row)
    per_turn = [u["cost_usd"] for u in usages]
    cumulative, running = [], 0.0
    for cost in per_turn:
        running += cost
        cumulative.append(round(running, 6))
    contexts = [c for u in usages for c in u["main_contexts"]]
    totals = _segment_fields(usages, spent)
    passed = sum(1 for r in rows if r["passed"])
    error = stopped in (STOP_ERROR, STOP_SPEND)
    rows.append(dict(base, row_kind=SESSION, checkpoint=None, checkpoint_index=None,
                     **{k: totals[k] for k in TOKEN_KINDS},
                     cost_usd=round(spent, 6), cost_by_tier=totals["cost_by_tier"],
                     main_peak_context_tokens=totals["main_peak_context_tokens"],
                     main_mean_context_tokens=round(sum(contexts) / len(contexts), 1) if contexts else None,
                     user_turns_planned=len(scenario["turns"]), user_turns_run=len(usages),
                     stopped=stopped, error=error, error_kind=error_kind, session_cap_usd=cap,
                     agent_turn_cap_hits=agent_cap_hits,
                     checkpoints_total=len(order), checkpoints_passed=passed,
                     passed=None if error else passed == len(order),
                     outcome="pass" if passed == len(order) and not error else "fail",
                     cost_per_turn=[round(c, 6) for c in per_turn], cumulative_cost_usd=cumulative,
                     main_peak_context_per_turn=[max(u["main_contexts"]) if u["main_contexts"] else None
                                                 for u in usages],
                     agent_turns_per_turn=[u["agent_turns"] for u in usages],
                     cost_per_turn_slope=slope(per_turn), branches=branches))
    return rows


# --- Reading the rows back ---------------------------------------------------------------------

def is_long_session(rows):
    return any(r.get("tier") == TIER and r.get("row_kind") in (CHECKPOINT, SESSION) for r in rows)


def cheaper_cost(by_tier, main_class):
    """The cost on classes ranking below `main_class`; unranked models count as not cheaper."""
    if main_class not in replay_pair.CLASS_ORDER:
        return 0.0
    rank = replay_pair.CLASS_ORDER.index(main_class)
    return sum(v for k, v in (by_tier or {}).items()
               if k in replay_pair.CLASS_ORDER and replay_pair.CLASS_ORDER.index(k) < rank)


def _ratio_of_sums(items, num, den):
    top, bottom = sum(num(i) for i in items), sum(den(i) for i in items)
    return top / bottom if bottom else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def clustered(by_cluster, statistic, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES,
              confidence=replay_stats.CONFIDENCE):
    """(estimate, interval) of `statistic` over every item, with a percentile interval from
    resampling clusters with replacement and pooling the items of the clusters drawn. The interval
    is None when the statistic is undefined in any resample or there is one cluster only."""
    clusters = sorted(by_cluster)
    items = [i for c in clusters for i in by_cluster[c]]
    estimate = statistic(items) if items else None
    if estimate is None or len(clusters) < 2:
        return estimate, None
    rng = random.Random(seed)
    samples = []
    for _ in range(resamples):
        picks = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        value = statistic([i for c in picks for i in by_cluster[c]])
        if value is None:
            return estimate, None
        samples.append(value)
    samples.sort()
    alpha = (1 - confidence) / 2
    return estimate, [replay_stats._rank(samples, alpha), replay_stats._rank(samples, 1 - alpha)]


def _rounded(pair):
    estimate, interval = pair
    return {"estimate": None if estimate is None else round(estimate, 6),
            "interval": None if interval is None else [round(v, 6) for v in interval]}


def _mean_curve(curves):
    length = max((len(c) for c in curves), default=0)
    return [round(_mean([c[i] for c in curves if i < len(c)]), 6) for i in range(length)]


def summarise(rows, main_model=None, tiers=None, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """Per arm: checkpoint pass rates, cost per session, the cost-per-turn slope, peak context and
    the share of cost on tiers cheaper than the main model's, each with a scenario-clustered
    interval; the mean cost-per-turn curve per scenario; and each other arm against `bare` on cost
    per session and checkpoint pass rate, paired by scenario. ValueError on rows that are not a
    long-session set."""
    sessions = [r for r in rows if r.get("row_kind") == SESSION]
    checkpoints = [r for r in rows if r.get("row_kind") == CHECKPOINT]
    if not sessions:
        raise ValueError("no session rows")
    tiers = replay_pair.load_tiers() if tiers is None else tiers
    main_model = main_model or sessions[0].get("model")
    main_class = replay_pair.model_class(main_model, tiers)
    seen = list(dict.fromkeys(r["arm"] for r in sessions))
    arm_order = sorted(seen, key=lambda a: (a != CONTROL, seen.index(a)))
    scenarios = sorted({r["scenario"] for r in sessions})

    def by_scenario(items, arm):
        out = {}
        for item in items:
            if item["arm"] == arm:
                out.setdefault(item["scenario"], []).append(item)
        return out

    pass_rate = lambda items: _ratio_of_sums(items, lambda r: 1.0 if r["passed"] else 0.0, lambda r: 1.0)
    measures = {
        "cost_per_session_usd": lambda items: _mean([r["cost_usd"] for r in items]),
        "cost_per_turn_slope_usd": lambda items: _mean([r.get("cost_per_turn_slope") for r in items]),
        "main_peak_context_tokens": lambda items: _mean([r.get("main_peak_context_tokens") for r in items]),
        "cheaper_tier_cost_share": lambda items: _ratio_of_sums(
            items, lambda r: cheaper_cost(r.get("cost_by_tier"), main_class), lambda r: r["cost_usd"] or 0.0),
    }
    out = {"tier": TIER, "method": METHOD, "clusters": scenarios, "main_model": main_model,
           "main_class": main_class, "seed": seed, "resamples": resamples, "arms": {}, "against": {}}
    for arm in arm_order:
        arm_sessions, arm_cps = by_scenario(sessions, arm), by_scenario(checkpoints, arm)
        entry = {"sessions": sum(len(v) for v in arm_sessions.values()),
                 "errored_sessions": sum(1 for v in arm_sessions.values() for r in v if r.get("error")),
                 "checkpoint_pass_rate": _rounded(clustered(arm_cps, pass_rate, seed, resamples))}
        for name, statistic in measures.items():
            entry[name] = _rounded(clustered(arm_sessions, statistic, seed, resamples))
        per_checkpoint = {}
        for scenario, items in sorted(arm_cps.items()):
            names = list(dict.fromkeys(r["checkpoint"] for r in sorted(items, key=lambda r: r["checkpoint_index"])))
            per_checkpoint[scenario] = {n: round(pass_rate([r for r in items if r["checkpoint"] == n]), 4)
                                        for n in names}
        entry["checkpoints"] = per_checkpoint
        entry["cost_per_turn_curve"] = {s: _mean_curve([r.get("cost_per_turn") or [] for r in items])
                                        for s, items in sorted(arm_sessions.items())}
        out["arms"][arm] = entry
    if CONTROL in out["arms"]:
        for arm in arm_order:
            if arm == CONTROL:
                continue
            pairs = {}
            for scenario in scenarios:
                pairs[scenario] = [{"arm": [r for r in sessions if r["arm"] == arm and r["scenario"] == scenario],
                                    "control": [r for r in sessions if r["arm"] == CONTROL and r["scenario"] == scenario],
                                    "arm_cp": [r for r in checkpoints if r["arm"] == arm and r["scenario"] == scenario],
                                    "control_cp": [r for r in checkpoints
                                                   if r["arm"] == CONTROL and r["scenario"] == scenario]}]

            def cost_ratio(items):
                a = _mean([r["cost_usd"] for i in items for r in i["arm"]])
                c = _mean([r["cost_usd"] for i in items for r in i["control"]])
                return None if a is None or not c else a / c

            def pass_diff(items):
                a = pass_rate([r for i in items for r in i["arm_cp"]])
                c = pass_rate([r for i in items for r in i["control_cp"]])
                return None if a is None or c is None else a - c

            out["against"][arm] = {"control": CONTROL,
                                   "cost_per_session_ratio": _rounded(clustered(pairs, cost_ratio, seed, resamples)),
                                   "checkpoint_pass_rate_difference": _rounded(clustered(pairs, pass_diff, seed,
                                                                                         resamples))}
    return out


def _span(value):
    estimate, interval = value["estimate"], value["interval"]
    if estimate is None:
        return "undefined"
    text = "%.4g" % estimate
    return text + (" [%.4g, %.4g]" % tuple(interval) if interval else " [no interval]")


def render(result):
    lines = ["long-session tier: %s on %s (%s class); %s over %d scenario(s), seed %d"
             % ("per arm", result["main_model"], result["main_class"] or "unranked", result["method"],
                len(result["clusters"]), result["seed"])]
    for arm, entry in result["arms"].items():
        lines.append("  %s: %d session(s), %d errored" % (arm, entry["sessions"], entry["errored_sessions"]))
        lines.append("    checkpoint pass rate     %s" % _span(entry["checkpoint_pass_rate"]))
        lines.append("    cost per session (USD)   %s" % _span(entry["cost_per_session_usd"]))
        lines.append("    cost-per-turn slope      %s" % _span(entry["cost_per_turn_slope_usd"]))
        lines.append("    main peak context        %s" % _span(entry["main_peak_context_tokens"]))
        lines.append("    cost on cheaper tiers    %s" % _span(entry["cheaper_tier_cost_share"]))
        for scenario, rates in entry["checkpoints"].items():
            lines.append("    %s: %s" % (scenario, ", ".join("%s %.2f" % kv for kv in rates.items())))
        for scenario, curve in entry["cost_per_turn_curve"].items():
            lines.append("    %s cost per turn: %s" % (scenario, " ".join("%.3f" % v for v in curve)))
    for arm, entry in result["against"].items():
        lines.append("  %s against %s: cost per session ratio %s; checkpoint pass rate difference %s"
                     % (arm, entry["control"], _span(entry["cost_per_session_ratio"]),
                        _span(entry["checkpoint_pass_rate_difference"])))
    return "\n".join(lines) + "\n"
