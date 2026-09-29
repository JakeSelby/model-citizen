#!/usr/bin/env python3
"""A one-policy replay pair: one harness image run twice, as a reference and a treatment arm that
differ in exactly one session-scoped selection, with the bare arm beside them.

An ablation manifest (`benchmarks/ablations/<name>.json`, schema 1, AD-12) names the harness ref
and the one factor: a `HARNESS_STANCE_<DIMENSION>` variable or `HARNESS_MODE`, with the value each
harness arm is given by value (`null` leaves the ref's default). `parity` refuses any other
difference between the two harness arms' launch specs before anything is spent, and `summarise`
compares their loaded surfaces trial by trial after the run.

Each arm is scored on pooled Cost-of-Pass (`replay_stats`), workers alone and with the arm's own
decision-provider calls, which are copied out of each harness container's usage ledger, joined to
the attempt by session id and priced from the price table. An unpriced call, or a ledger that could
not be read, leaves the with-decisions figure undefined and is named; it is never counted as zero.
Rows are keyed by arm name and carry the selection they ran with, and their `ablation` record
names the design's factors as a list, so a runner of more factors or more arms writes the same
row keys; `is_pair` recognises the rows by that record, whichever arms ran. Reading and limits: docs/benchmarks.md, "Pairs".

Standard library only, and no model call.
"""
import hashlib
import importlib.util
import json
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import lifecycle  # noqa: E402  the spawn guard's own brief fingerprint
sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_stats  # noqa: E402  Cost-of-Pass, Wilson and the paired intervals

SCHEMA = 1
BARE, REFERENCE, TREATMENT = "bare", "reference", "treatment"
ARMS = (BARE, REFERENCE, TREATMENT)
HARNESS_ARMS = (REFERENCE, TREATMENT)
FACTOR = re.compile(r"^HARNESS_STANCE_[A-Z_]+$")
MODE_FACTOR = "HARNESS_MODE"
MANIFEST_KEYS = ("schema", "name", "tag", "factor", "reference", "treatment")
# Model classes from weakest to strongest; `adapters/claude-code/bindings.json` maps each to a
# model family. One table serves every arm, so ranking cannot differ between them.
CLASS_ORDER = ("light", "standard", "strong", "frontier")
BINDINGS = Path("adapters") / "claude-code" / "bindings.json"
# The usage ledger under the image user's home (`usage_path` in policy/hooks/usage-log.py).
LEDGER = ".local/state/agent-harness/usage.jsonl"
DECISIONS = "decisions"
LEDGER_READ, LEDGER_ABSENT, LEDGER_UNKNOWN = "read", "absent", "unknown"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    """The ablation manifest at `path` with its `sha256`, or SystemExit naming what is wrong."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("replay-pair: cannot read the ablation manifest %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise SystemExit("replay-pair: %s is not a JSON object" % path)
    errors = []
    missing = [key for key in MANIFEST_KEYS if key not in data]
    if missing:
        errors.append("missing %s" % ", ".join(missing))
    extra = sorted(set(data) - set(MANIFEST_KEYS))
    if extra:
        errors.append("unknown key(s) %s" % ", ".join(extra))
    if data.get("schema") != SCHEMA:
        errors.append("schema must be %d" % SCHEMA)
    if not isinstance(data.get("name"), str) or not re.match(r"^[a-z0-9][a-z0-9-]*$", data.get("name") or ""):
        errors.append("name must be a lower-case slug")
    tag = data.get("tag")
    if not isinstance(tag, str) or not tag.strip() or any(c.isspace() for c in tag):
        errors.append("tag must be one ref")
    factor = data.get("factor")
    if not isinstance(factor, str) or not (FACTOR.match(factor) or factor == MODE_FACTOR):
        errors.append("factor %r is not HARNESS_STANCE_<DIMENSION> or %s" % (factor, MODE_FACTOR))
    for key in HARNESS_ARMS:
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append("%s must be a non-empty string or null" % key)
    if data.get(REFERENCE) == data.get(TREATMENT):
        errors.append("reference and treatment are equal (%r); a pair must differ in its factor"
                      % data.get(REFERENCE))
    if errors:
        raise SystemExit("replay-pair: refusing the ablation manifest %s:\n  %s" % (path, "\n  ".join(errors)))
    return dict(data, sha256=sha256(path))


def selections(manifest):
    """Each arm's session-scoped selection, by value: none for bare, the factor for the others.
    A `null` value leaves the variable unset, so that arm runs the ref's default."""
    return {BARE: {}, REFERENCE: {manifest["factor"]: manifest[REFERENCE]},
            TREATMENT: {manifest["factor"]: manifest[TREATMENT]}}


def row_stamp(manifest, arm):
    """What every pair row adds: the ablation it answers, with its design, and the selection its
    arm ran with. The design is `factors`, a list, and each arm's selection maps every factor to
    its value, so a design of more factors or more arms writes the same row keys."""
    return {"ablation": {"name": manifest["name"], "sha256": manifest["sha256"], "schema": SCHEMA,
                         "factors": [manifest["factor"]]},
            "selection": dict(selections(manifest)[arm])}


def ablation_of(rows):
    """The first row's `ablation` record, or None when no row carries one."""
    return next((r["ablation"] for r in rows if isinstance(r.get("ablation"), dict)), None)


def factors_of(rows):
    """The factors a pair's rows varied: the ablation's `factors`, or the scalar `factor` a row
    written before the list carried."""
    ablation = ablation_of(rows) or {}
    if isinstance(ablation.get("factors"), list):
        return list(ablation["factors"])
    legacy = next((r.get("factor") for r in rows if r.get("factor")), None)
    return [legacy] if legacy else []


def effective_difference(reference, treatment):
    """A refusal when the resolver cannot tell the two selections apart, or cannot resolve one.
    The arguments are each harness arm's profile fingerprint (`cost_bench.arm_profile`)."""
    if reference is None or treatment is None:
        return "the profile fingerprint of the %s arm cannot be resolved, so the factor's effect " \
               "is unknown" % (REFERENCE if reference is None else TREATMENT)
    if reference == treatment:
        return "the factor selects nothing the resolver sees: both harness arms resolve to profile %s" \
               % reference
    return None


def parity(reference, treatment, factor):
    """One reason for every way two harness arms' launch specs differ beyond `factor`, and one
    when `factor` itself does not differ. Empty when the pair may run. Each spec is a dict whose
    `env` holds the variables passed by value."""
    reasons = []
    for key in sorted((set(reference) | set(treatment)) - {"env"}):
        if reference.get(key) != treatment.get(key):
            reasons.append("%s: reference %r, treatment %r" % (key, reference.get(key), treatment.get(key)))
    left, right = reference.get("env") or {}, treatment.get("env") or {}
    for key in sorted((set(left) | set(right)) - {factor}):
        if left.get(key) != right.get(key):
            reasons.append("env %s: reference %r, treatment %r" % (key, left.get(key), right.get(key)))
    if left.get(factor) == right.get(factor):
        reasons.append("env %s: both arms %r; the factor must differ" % (factor, left.get(factor)))
    return reasons


def admit(reference, treatment, factor):
    """SystemExit listing every `parity` reason; None when the pair may run."""
    reasons = parity(reference, treatment, factor)
    if reasons:
        raise SystemExit("replay-pair: refusing the pair: the harness arms differ by more than %s:\n  %s"
                         % (factor, "\n  ".join(reasons)))


# --- Re-spawns at a stronger model class ----------------------------------------------------------

def load_tiers(root=ROOT):
    """`{class: model family}` from the bindings; empty when they cannot be read, which leaves
    every spawn unranked rather than ranked by a guess."""
    try:
        tiers = json.loads((Path(root) / BINDINGS).read_text(encoding="utf-8")).get("tiers") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {name: family for name, family in tiers.items() if name in CLASS_ORDER and isinstance(family, str)}


def model_class(model, tiers):
    """The class whose family name the model id contains, or None."""
    name = (model or "").lower()
    found = [cls for cls, family in tiers.items() if family and family.lower() in name]
    return found[0] if len(found) == 1 else None


def respawn_counts(spawns, tiers):
    """(re-spawns at a stronger class, unranked spawns) over one run's spawns, in order.

    Each spawn is `{fingerprint, model}`, the model its thread ran on. A later spawn of the same
    brief (`lifecycle.same_work`) whose class ranks above an earlier one's is one re-spawn up. A
    spawn whose model matches no class, or whose thread produced no message, is unranked: counted
    here and never ranked higher or lower."""
    ranked, up, unranked = [], 0, 0
    for spawn in spawns:
        cls = model_class(spawn.get("model"), tiers)
        if cls is None:
            unranked += 1
            continue
        rank = CLASS_ORDER.index(cls)
        if any(lifecycle.same_work(earlier, spawn.get("fingerprint")) and rank > earlier_rank
               for earlier, earlier_rank in ranked):
            up += 1
        ranked.append((spawn.get("fingerprint"), rank))
    return up, unranked


def stream_spawns(messages, tools=("Task", "Agent")):
    """Each spawn in a CLI stream as `{fingerprint, requested_model, model}`, in order, and the
    stream's distinct session ids. The fingerprint is the spawn guard's reduction of the brief; it
    stays in memory and never reaches a row."""
    spawns, threads, sessions = [], {}, []
    for message in messages:
        if not isinstance(message, dict):
            continue
        session = message.get("session_id")
        if isinstance(session, str) and session and session not in sessions:
            sessions.append(session)
        if message.get("type") != "assistant":
            continue
        body = message.get("message") or {}
        thread = message.get("parent_tool_use_id")
        if thread and thread not in threads and body.get("model"):
            threads[thread] = body["model"]
        for block in body.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" \
                    and block.get("name") in tools:
                given = block.get("input") if isinstance(block.get("input"), dict) else {}
                spawns.append({"id": block.get("id"), "fingerprint": lifecycle.fingerprint(given.get("prompt")),
                               "requested_model": given.get("model")})
    for spawn in spawns:
        spawn["model"] = threads.get(spawn.pop("id"))
    return spawns, sessions


# --- The decision ledger, copied out of each harness container ------------------------------------

def ledger_path(record):
    """Where the image user's usage ledger sits inside the arm's container, or None when its
    manifest names no home root."""
    home = ((record.get("manifest") or {}).get("roots") or {}).get("home")
    return posixpath.join(home, LEDGER) if isinstance(home, str) and home.startswith("/") else None


def decisions_file(directory, task, arm, rep):
    return Path(directory) / ("%s-%s-%s.jsonl" % (task, arm, rep))


def read_decisions(directory, rows):
    """`{(task, arm, rep): [decision rows]}` for every harness-arm row whose ledger was read. A row
    whose saved file is missing is left out, and the roll-up names it unknown."""
    out = {}
    for row in rows:
        if row.get("arm") not in HARNESS_ARMS or row.get("decision_ledger") != LEDGER_READ:
            continue
        path = decisions_file(directory, row.get("task"), row.get("arm"), row.get("rep"))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out[(row.get("task"), row.get("arm"), row.get("rep"))] = [
            json.loads(line) for line in text.splitlines() if line.strip()]
    return out


def load_pricing(root=ROOT):
    """`policy/hooks/pricing.py`, loaded by path as the scripts load their siblings."""
    spec = importlib.util.spec_from_file_location("replay_pair_pricing", str(Path(root) / "policy" / "hooks" / "pricing.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpriced_reason(decision):
    """Why a decision row has no price. A `partial` row that says its call never reached the
    provider over the network (`decisions.ledger` writes status `unavailable`, error `network`)
    is labelled as blocked: a harness arm's only way out is the model-API allowlist, so a remote
    decision provider cannot be reached from it. Any other cause the row names is kept."""
    if not decision.get("partial"):
        return "no price for %s" % (decision.get("model") or "an unnamed model")
    status, error = decision.get("status"), decision.get("error")
    if status == "unavailable" and error == "network":
        return "partial: blocked by egress (unavailable: network)"
    if status and status != "ok":
        return "partial (%s%s)" % (status, ": %s" % error if error else "")
    return "partial"


def decision_rollup(rows, decisions, table, pricing=None):
    """Per harness arm: `{calls, priced_usd, unpriced, unmatched, unknown, decision_seconds}`.

    `decisions` maps `(task, arm, rep)` to the decision rows copied out of that attempt's
    container. A row joins its attempt by `session_id`; one whose session is not the attempt's
    still came from that arm's container, so it counts in the arm and is named `unmatched`. Each is
    priced with `pricing.row_cost`; None is unpriced, named with its reason, never zero. An
    attempt whose ledger was not read is named `unknown`. `decision_seconds` is the calls' own
    latency, which ran inside the attempt's wall time and is never added to it."""
    pricing = pricing or load_pricing()
    out = {}
    for arm in HARNESS_ARMS:
        roll = {"calls": 0, "priced_usd": 0.0, "unpriced": [], "unmatched": [], "unknown": [],
                "decision_seconds": 0.0}
        for row in (r for r in rows if r.get("arm") == arm):
            key = (row.get("task"), arm, row.get("rep"))
            where = {"task": row.get("task"), "rep": row.get("rep")}
            status = row.get("decision_ledger")
            if status != LEDGER_READ:
                roll["unknown"].append(dict(where, reason=status or "no decision_ledger on the row"))
                continue
            if key not in decisions:
                roll["unknown"].append(dict(where, reason="the saved decision rows are missing"))
                continue
            sessions = set(row.get("session_ids") or [])
            for decision in decisions[key]:
                roll["calls"] += 1
                named = dict(where, point=decision.get("point"), model=decision.get("model") or "")
                if decision.get("session_id") not in sessions:
                    roll["unmatched"].append(dict(named, session_id=decision.get("session_id")))
                ms = decision.get("ms")
                if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                    roll["decision_seconds"] += ms / 1000.0
                cost = pricing.row_cost(decision, table)
                if cost is None:
                    roll["unpriced"].append(dict(named, reason=unpriced_reason(decision)))
                else:
                    roll["priced_usd"] += cost
        roll["priced_usd"] = round(roll["priced_usd"], 6)
        roll["decision_seconds"] = round(roll["decision_seconds"], 3)
        out[arm] = roll
    return out


def attempt_decision_cost(row, decisions, table, pricing):
    """The priced decision cost of one attempt, or None when any of it is unknown or unpriced."""
    if row.get("arm") == BARE:
        return 0.0
    key = (row.get("task"), row.get("arm"), row.get("rep"))
    if row.get("decision_ledger") != LEDGER_READ or key not in decisions:
        return None
    costs = [pricing.row_cost(d, table) for d in decisions[key]]
    return None if None in costs else sum(costs)


# --- The pair summary -----------------------------------------------------------------------------

def is_pair(rows):
    """True when the rows answer an ablation, whichever of its arms ran: a pair stopped at its
    spend cap before every arm ran is still a pair, and its decision costs are still reported."""
    return ablation_of(rows) is not None


def incomplete(rows):
    """One line per trial some arm of the pair did not run, as when the spend cap stopped it."""
    ran = {}
    for row in rows:
        ran.setdefault((row.get("task"), row.get("rep")), set()).add(row.get("arm"))
    return ["%s rep %s: %s did not run" % (task, rep, ", ".join(a for a in ARMS if a not in arms))
            for (task, rep), arms in sorted(ran.items(), key=lambda k: (str(k[0][0]), k[0][1] or 0))
            if set(ARMS) - arms]


def default_surface(row):
    """A row's loaded-surface record (#482): the `init_*` fields and the observed effort."""
    if row.get("init_surface_source") is None:
        return None
    return {k: v for k, v in row.items() if k.startswith("init_") or k == "observed_effort"}


def surface_parity(rows, surface=default_surface):
    """One reason per trial whose reference and treatment surfaces differ. A trial that lacks one
    arm compares nothing and is named by `incomplete` instead."""
    by = {(r.get("task"), r.get("rep"), r.get("arm")): r for r in rows if r.get("arm") in HARNESS_ARMS}
    reasons = []
    for task, rep in sorted({(t, p) for t, p, _ in by}, key=lambda k: (str(k[0]), k[1] or 0)):
        ref, treat = by.get((task, rep, REFERENCE)), by.get((task, rep, TREATMENT))
        if ref is None or treat is None:
            continue
        left, right = surface(ref), surface(treat)
        if left != right:
            left, right = left or {}, right or {}
            fields = sorted(k for k in set(left) | set(right) if left.get(k) != right.get(k)) or ["init event"]
            reasons.append("%s rep %s: loaded surface differs in %s" % (task, rep, ", ".join(fields)))
    return reasons


def _total(values):
    return None if not values or None in values else sum(values)


def _per_attempt(value, n):
    return None if value is None or not n else round(value / n, 4)


def _intervals(rows, pair, seed, resamples):
    """`replay_stats.analyse` for one pair of arms, without SM-2's verdict, which is defined for
    harness against bare only; `{"unavailable": reason}` when it cannot be derived."""
    try:
        result = replay_stats.analyse([r for r in rows if r.get("arm") in pair], seed, resamples, arms=pair)
    except ValueError as exc:
        return {"unavailable": str(exc)}
    for key in ("verdict", "reason", "claim"):
        result.pop(key, None)
    result["compared"] = "%s over %s" % (pair[1], pair[0])
    return result


def summarise(rows, decisions, table, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES,
              surface=default_surface, pricing=None):
    """The whole pair result from saved rows and saved decision rows; ValueError on malformed rows.

    Per arm: attempts, passes and a Wilson interval; pooled Cost-of-Pass for workers alone and with
    the arm's priced decision calls; wall time; re-spawns at a stronger class; and the decision
    roll-up. Beside them, the paired intervals for treatment over reference and each harness arm
    over bare, and the post-run surface parity."""
    pricing = pricing or load_pricing()
    atts = replay_stats.attempts(rows, ARMS)
    rollup = decision_rollup(rows, decisions, table, pricing)
    arms = {}
    for arm in ARMS:
        mine = [a for a in atts if a["arm"] == arm]
        raw = [r for r in rows if r.get("arm") == arm]
        passes, n = sum(1 for a in mine if a["passed"]), len(mine)
        workers = _total([a["cost"] for a in mine])
        low, high = replay_stats.wilson(passes, n)
        walls = [r.get("wall_seconds") for r in raw]
        respawns, unranked = _total([r.get("respawns_up") for r in raw]), _total([r.get("spawns_unranked") for r in raw])
        entry = {"attempts": n, "passes": passes, "errors": sum(1 for a in mine if a["error"]),
                 "pass_rate": round(passes / n, 4) if n else None,
                 "pass_rate_interval_descriptive": [None if low is None else round(low, 4),
                                                    None if high is None else round(high, 4)],
                 "cost_usd": None if workers is None else round(workers, 6),
                 "cost_of_pass": None if replay_stats.cost_of_pass(workers, passes) is None
                 else round(workers / passes, 6),
                 "mean_cost_per_attempt": None if workers is None or not n else round(workers / n, 6),
                 "wall_seconds": None if _total(walls) is None else round(_total(walls), 1),
                 "wall_seconds_per_attempt": _per_attempt(_total(walls), n),
                 "respawns_up": respawns, "respawns_up_per_attempt": _per_attempt(respawns, n),
                 "spawns_unranked": unranked, "spawns_unranked_per_attempt": _per_attempt(unranked, n)}
        if arm == BARE:
            entry["decisions"] = {"ledger": LEDGER_ABSENT}
            entry["cost_with_decisions_usd"], entry["cost_of_pass_with_decisions"] = entry["cost_usd"], entry["cost_of_pass"]
            entry["with_decisions_undefined"] = None
        else:
            roll = rollup[arm]
            causes = [] if n else ["the arm did not run"]
            if roll["unknown"]:
                causes.append("%d attempt(s) with no readable decision ledger" % len(roll["unknown"]))
            if roll["unpriced"]:
                causes.append("%d unpriced decision call(s)" % len(roll["unpriced"]))
            if workers is None and n:
                causes.append("a worker run with no readable cost")
            whole = None if causes else workers + roll["priced_usd"]
            if whole is not None and not passes:
                causes.append("the arm passed nothing")
            entry["decisions"] = dict(roll, share_of_wall=None if not entry["wall_seconds"]
                                      else round(roll["decision_seconds"] / entry["wall_seconds"], 4))
            entry["cost_with_decisions_usd"] = None if whole is None else round(whole, 6)
            entry["cost_of_pass_with_decisions"] = None if whole is None or not passes else round(whole / passes, 6)
            entry["with_decisions_undefined"] = "; ".join(causes) or None
        arms[arm] = entry
    with_decisions = []
    for row in rows:
        extra = attempt_decision_cost(row, decisions, table, pricing)
        cost = row.get("cost_usd")
        with_decisions.append(dict(row, cost_usd=None if extra is None or cost is None else cost + extra))
    comparisons = {}
    for pair in ((REFERENCE, TREATMENT), (BARE, REFERENCE), (BARE, TREATMENT)):
        name = "%s_over_%s" % (pair[1], pair[0])
        comparisons[name] = {"workers": _intervals(rows, pair, seed, resamples),
                             "with_decisions": _intervals(with_decisions, pair, seed, resamples)}
    reasons = surface_parity(rows, surface)
    return {"ablation": ablation_of(rows), "factors": factors_of(rows),
            "selections": {arm: next((r.get("selection") for r in rows if r.get("arm") == arm), None) for arm in ARMS},
            "seed": seed, "resamples": resamples, "arms": arms, "comparisons": comparisons,
            "pareto": replay_stats.pareto({"arms": arms}, ARMS), "incomplete": incomplete(rows),
            "parity": {"ok": not reasons, "reasons": reasons}}


def _num(value, places=3):
    return "undefined" if value is None else "%.*f" % (places, value)


def _span(interval):
    return "undefined" if not interval else "[%s]" % ", ".join(_num(v, 4) for v in interval)


def render(result):
    """The pair report as text. No SM-2 verdict is printed: its rule is harness against bare."""
    ablation = result.get("ablation") or {}
    factors = result.get("factors") or []

    def chosen(arm):
        selection = result["selections"].get(arm) or {}
        if len(factors) == 1:
            return repr(selection.get(factors[0]))
        return "{%s}" % ", ".join("%s: %r" % (f, selection.get(f)) for f in factors)

    lines = ["Pair %s: factor %s, reference %s, treatment %s; seed %d, %d resamples"
             % (ablation.get("name"), ", ".join(factors) or "unknown", chosen(REFERENCE), chosen(TREATMENT),
                result["seed"], result["resamples"])]
    for item in result.get("incomplete") or []:
        lines.append("  incomplete trial, the run stopped before it: %s" % item)
    for arm in ARMS:
        a = result["arms"][arm]
        lines.append("  %s: %d/%d passed (%d errored), pass rate %s, Wilson %s (descriptive)"
                     % (arm, a["passes"], a["attempts"], a["errors"], _num(a["pass_rate"]),
                        _span(a["pass_rate_interval_descriptive"])))
        lines.append("    Cost-of-Pass: workers %s USD, with decisions %s USD%s"
                     % (_num(a["cost_of_pass"], 4), _num(a["cost_of_pass_with_decisions"], 4),
                        " (%s)" % a["with_decisions_undefined"] if a["with_decisions_undefined"] else ""))
        lines.append("    wall time %s s, %s s per attempt; re-spawns at a stronger class %s (%s per attempt), "
                     "unranked spawns %s"
                     % (_num(a["wall_seconds"], 1), _num(a["wall_seconds_per_attempt"], 1),
                        "unknown" if a["respawns_up"] is None else a["respawns_up"],
                        _num(a["respawns_up_per_attempt"], 2),
                        "unknown" if a["spawns_unranked"] is None else a["spawns_unranked"]))
        d = a["decisions"]
        if arm == BARE:
            lines.append("    decision calls: none, the bare arm has no decision layer")
            continue
        lines.append("    decision calls %d, priced %s USD, latency %s s (%s of wall time, already inside it)"
                     % (d["calls"], _num(d["priced_usd"], 4), _num(d["decision_seconds"], 1),
                        _num(d["share_of_wall"], 4)))
        for item in d["unpriced"]:
            lines.append("      unpriced: %s rep %s, point %s, model %s: %s"
                         % (item["task"], item["rep"], item["point"], item["model"] or "none", item["reason"]))
        for item in d["unmatched"]:
            lines.append("      unmatched session: %s rep %s, point %s" % (item["task"], item["rep"], item["point"]))
        for item in d["unknown"]:
            lines.append("      ledger unknown: %s rep %s: %s" % (item["task"], item["rep"], item["reason"]))
    for name, pair in sorted(result["comparisons"].items()):
        for basis in ("workers", "with_decisions"):
            c = pair[basis]
            if c.get("unavailable"):
                lines.append("  %s, %s: unavailable, %s" % (name.replace("_", " "), basis.replace("_", " "), c["unavailable"]))
                continue
            lines.append("  %s, %s: Cost-of-Pass ratio %s %s, pass-rate difference %s %s%s"
                         % (name.replace("_", " "), basis.replace("_", " "), _num(c["ratio"]), _span(c["ratio_interval"]),
                            _num(c["difference"]), _span(c["difference_interval"]),
                            "" if c["sm2_eligible"] else "; exploratory, fewer than five paired trials"))
    lines += ["", "| Arm | Mean USD per attempt | Pass rate | Pareto |", "| --- | --- | --- | --- |"]
    for arm, cost, rate, status in result["pareto"]:
        lines.append("| %s | %s | %s | %s |" % (arm, _num(cost, 4), _num(rate), status))
    parity_block = result["parity"]
    lines.append("")
    lines.append("post-run parity: %s" % ("the harness arms loaded the same surface in every trial"
                                          if parity_block["ok"] else "REFUSED"))
    lines += ["  " + reason for reason in parity_block["reasons"]]
    return "\n".join(lines) + "\n"
