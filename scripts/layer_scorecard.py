#!/usr/bin/env python3
"""The layer scorecard: one deterministic report, per layer and overall, built from saved rows.

It reads the result directories a definitive evaluation leaves behind, each passed by its role:
production rows (harness against bare on the outcome tasks), rule-task rows (harness against bare
on the tasks a layer should move), long-session rows, the removal sweep's rows and the diff judge's
results. Nothing is re-run and no model is called; every figure comes from a module that already
computes it:

- **Prefix** per layer and model: the resident tokens the rows' `context_attribution` records for
  the layer's entries (the median over harness rows), or `benchmarks/static.json` where no row
  records them, and the USD a run pays for them, written once and re-read on every later turn
  (the static figure's own split, priced from `policy/prices.json`) at the rows' mean turns.
- **Behaviour against bare** and **against the layer's own removal**: each of the layer's
  pre-registered scores, read as the removed arm minus the harness, `ablations._score` for both,
  so bare is "the whole harness removed" and the sweep arm "this layer removed".
- **Outcome**: the sweep's outcome-subset pass-rate difference for the removal.
- **Cost**, for a cost-control layer (one scored on the Cost-of-Pass ratio or run on long-session
  scenarios): the sweep's marginal cost and, from long-session rows, the removed arm's session
  cost over the harness's.
- **Verdict**: `ablations.justify`'s keep, trim or no evidence, with the scores it rests on, their
  margins and intervals, and the rows it came from.

The headline is harness against bare per stratum: the Cost-of-Pass ratio and pass-rate difference
(`replay_stats.analyse`), pass^k and the all-rules-at-once rate (`replay_reliability`), each as a
mean per-task difference with a task-clustered interval, and the judge's win rates on admitted
dimensions (`replay_judge.judge_section`), each with an `equivalence` verdict against its margin.

Rows must be pre-registered. An exploratory row is refused unless `--allow-exploratory` is given,
and then the whole scorecard is marked exploratory with the reason. Row fields that later tiers add
are optional: a row without `stratum` is grouped by its `model`, and a long-session directory whose
rows carry no `row_kind` is reported as not measured. Standard library only.
How to read it: docs/benchmarks.md, "Layer scorecard".
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ablations  # noqa: E402  the sweep manifest, its per-score reading and the justification rule
import equivalence  # noqa: E402  interval-inside-margin verdicts
import experiment_protocol  # noqa: E402  the pre-registered evidence label
import replay_judge  # noqa: E402  the judge's win-rate section and its clustered interval
import replay_reliability  # noqa: E402  pass^k and the all-rules-at-once rate
import replay_stats  # noqa: E402  SM-2's analysis, its seed and resamples

SCHEMA = 1
KIND = "layer-scorecard"
RESULTS = "results.jsonl"
ROLES = ("production", "rules", "long_session", "sweep")
ARMS = replay_stats.ARMS
BARE, HARNESS = ARMS
LAYERS = ("rule", "stance", "listing", "hook", "output style", "instructions")
PASS_K = "pass^k difference"
ALL_RULES = "All-rules rate difference"
JUDGE = "Judge win rate over one half"
LONG = "Session cost ratio"
# The headline's margins when no plan is given: the pre-registration template's defaults for the
# two SM-2 metrics, and the pass-rate margin for the other rates. A `--plan` field overrides each.
DEFAULT_MARGINS = {equivalence.RATIO: (0.85, 1.1765), equivalence.DIFFERENCE: (-0.125, 0.125),
                   PASS_K: (-0.125, 0.125), ALL_RULES: (-0.125, 0.125), JUDGE: (-0.125, 0.125)}
SOURCE_KEY = "_scorecard_source"
STATIC_PATHS = {"rules": "claude/rules/%s.md", "skills": "claude/skills/%s/SKILL.md",
                "roles": "claude/agents/%s.md", "workflows": "claude/commands/%s.md",
                "output-styles": "claude/output-styles/%s.md"}
NOT_MEASURED = "not measured"


class Refused(ValueError):
    """The rows cannot be scored as given: exploratory without the flag, or duplicated."""


def _r(value, places=6):
    return None if value is None else round(value, places)


def _load_pricing():
    spec = importlib.util.spec_from_file_location("scorecard_pricing", ROOT / "policy" / "hooks" / "pricing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRICING = _load_pricing()


# --- Reading --------------------------------------------------------------------------------------

def _jsonl(data):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def results_files(directory):
    """Every `results.jsonl` under `directory`, sorted, or the file itself when one is named."""
    path = Path(directory)
    if path.is_file():
        return [path]
    found = sorted(path.rglob(RESULTS))
    if not found:
        raise Refused("%s holds no %s" % (directory, RESULTS))
    return found


def load_role(role, directories):
    """`(sources, rows, detections)` for one role: each file's id, digest and row count, its rows,
    each carrying its source id, and `{id: detection rows or None}` from beside each file. The id is the role, the directory's index and the path inside it, so
    the report never holds a machine's absolute path."""
    sources, rows, found = [], [], {}
    for index, directory in enumerate(directories, 1):
        base = Path(directory)
        for path in results_files(directory):
            data = path.read_bytes()
            inside = path.name if base.is_file() else path.relative_to(base).as_posix()
            ident = "%s/%d/%s" % (role, index, inside)
            loaded = _jsonl(data)
            sources.append({"id": ident, "role": role, "sha256": hashlib.sha256(data).hexdigest(),
                            "rows": len(loaded), "registered": sum(1 for r in loaded if registered(r))})
            detections = replay_reliability.detections_beside(path)
            if detections is not None:
                sources[-1]["detections"] = len(detections)
            rows.extend(dict(row, **{SOURCE_KEY: ident}) for row in loaded)
            found[ident] = detections
    return sources, rows, found


def load_judge(directory, index):
    """One judge result: its verdicts, pair key and calibration, read from the names
    `replay_judge` writes (`verdicts.jsonl`, `pairs.key.json`, `calibration.json`)."""
    base = Path(directory)
    parts = {}
    for name in ("verdicts.jsonl", "pairs.key.json", "calibration.json"):
        path = base / name
        if not path.is_file():
            raise Refused("judge directory %s has no %s" % (directory, name))
        parts[name] = path.read_bytes()
    verdicts = _jsonl(parts["verdicts.jsonl"])
    ident = "judge/%d" % index
    return {"id": ident, "verdicts": verdicts,
            "key": json.loads(parts["pairs.key.json"].decode("utf-8"))["pairs"],
            "calibration": json.loads(parts["calibration.json"].decode("utf-8")),
            "source": {"id": ident, "role": "judge", "rows": len(verdicts),
                       "registered": sum(1 for v in verdicts if registered(v)),
                       "sha256": hashlib.sha256(b"".join(parts[n] for n in sorted(parts))).hexdigest()}}


def registered(row):
    return row.get("evidence") == experiment_protocol.PREREGISTERED and bool(row.get("pre_registration"))


def admission(sources, allow_exploratory):
    """The reasons the scorecard is exploratory, one per source holding an unregistered row.
    Refused, naming them, unless `allow_exploratory`."""
    reasons = ["%s: %d of %d row(s) are not pre-registered" % (s["id"], s["rows"] - s["registered"], s["rows"])
               for s in sources if s["registered"] < s["rows"]]
    if reasons and not allow_exploratory:
        raise Refused("refusing exploratory rows; pass --allow-exploratory to score them as a diagnostic:\n  "
                      + "\n  ".join(reasons))
    return reasons


def stratum_of(row):
    return row.get("stratum") if row.get("stratum") is not None else row.get("model")


def strata(rows):
    return sorted({str(stratum_of(r)) for r in rows}, key=str)


def _in(rows, stratum):
    return [r for r in rows if str(stratum_of(r)) == stratum]


def _runs(rows):
    """Rows of a task-tier run: a long-session row is keyed by `row_kind` and read elsewhere."""
    return [r for r in rows if not r.get("row_kind")]


def _check_unique(rows, role):
    seen = set()
    for row in rows:
        key = (str(stratum_of(row)), row.get("task"), row.get("arm"), row.get("rep", row.get("trial")),
               row.get("row_kind"), row.get("checkpoint_index"))
        if key in seen:
            raise Refused("%s rows hold %s/%s rep %s twice in stratum %s (%s); pass each set once"
                          % (role, key[1], key[2], key[3], key[0], row.get(SOURCE_KEY)))
        seen.add(key)


# --- Statistics shared by the sections ------------------------------------------------------------

def _mean(values):
    return sum(values) / len(values) if values else None


def _mean_difference(by_task, seed, resamples):
    """`(point, interval)`: the mean over tasks of a per-task difference, with the judge's
    task-clustered percentile interval."""
    if not by_task:
        return None, None
    return _r(_mean([v for vs in by_task.values() for v in vs])), \
        [_r(v) for v in replay_judge._cluster_interval(by_task, seed, resamples)]


def _ratio_interval(by_cluster, arms, seed, resamples):
    """`(point, interval, clusters)`: the second arm's pooled mean over the first's, resampling
    clusters that hold both arms; None where a mean is zero."""
    import random
    paired = sorted(c for c, cell in by_cluster.items() if cell.get(arms[0]) and cell.get(arms[1]))

    def ratio(picks):
        ref = [v for c in picks for v in by_cluster[c][arms[0]]]
        treat = [v for c in picks for v in by_cluster[c][arms[1]]]
        return None if not sum(ref) else _mean(treat) / _mean(ref)
    if not paired:
        return None, None, 0
    rng = random.Random(seed)
    alpha = (1 - replay_stats.CONFIDENCE) / 2
    samples = [ratio([paired[rng.randrange(len(paired))] for _ in paired]) for _ in range(resamples)]
    known = sorted(s for s in samples if s is not None)
    interval = None if len(known) < len(samples) else [_r(replay_stats._rank(known, alpha), 4),
                                                       _r(replay_stats._rank(known, 1 - alpha), 4)]
    return _r(ratio(paired), 4), interval, len(paired)


def _assessed(name, interval, margins, limitation):
    assessed = equivalence.assess({name: interval}, {name: margins[name]})
    return equivalence.label(assessed, limitation)[name]


# --- The headline ---------------------------------------------------------------------------------

def _pass_k(rows, seed, resamples):
    reading = replay_reliability.pass_k(rows)
    cells = {arm: reading["arms"].get(arm, {}).get("per_task", {}) for arm in ARMS}
    by_task = {t: [cells[HARNESS][t]["pass_hat_k"] - cells[BARE][t]["pass_hat_k"]]
               for t in sorted(set(cells[BARE]) & set(cells[HARNESS]))
               if cells[BARE][t]["pass_hat_k"] is not None and cells[HARNESS][t]["pass_hat_k"] is not None}
    point, interval = _mean_difference(by_task, seed, resamples)
    return {"k": reading["k"], "arms": {arm: {key: _r(reading["arms"][arm][key]) for key in
                                              ("pass_hat_k", "pass_1", "all_passed_rate")}
                                        for arm in ARMS if arm in reading["arms"]},
            "difference": point, "interval": interval, "tasks": len(by_task)}


def _all_rules(rows, found, seed, resamples):
    """The all-rules-at-once rate per arm, and the mean per-task difference of each task's clean
    share. None when no row's set has detections beside it."""
    detections = [d for ident in sorted({r[SOURCE_KEY] for r in rows}) for d in found.get(ident) or []]
    if not detections:
        return None
    joint = replay_reliability.joint_compliance(rows, detections)
    by_run, roster = {}, set()
    for det in detections:
        by_run.setdefault(replay_reliability._run_key(det), []).append(det)
        roster.add(det.get("detector"))
    shares = {}
    for row in rows:
        key = replay_reliability._run_key(row)
        reading = replay_reliability.classify_run(by_run.get(key, []), roster)
        if reading != replay_reliability.UNKNOWN:
            shares.setdefault(key[0], {}).setdefault(key[1], []).append(1.0 if reading == replay_reliability.CLEAN
                                                                        else 0.0)
    by_task = {t: [_mean(cell[HARNESS]) - _mean(cell[BARE])] for t, cell in shares.items()
               if cell.get(HARNESS) and cell.get(BARE)}
    point, interval = _mean_difference(by_task, seed, resamples)
    return {"arms": {arm: {k: (_r(v) if not isinstance(v, list) else [_r(x) for x in v])
                           for k, v in replay_reliability.joint_summary(joint)["arms"].get(arm, {}).items()}
                     for arm in ARMS if arm in joint["arms"]},
            "difference": point, "interval": interval, "tasks": len(by_task)}


def _judge_for(judges, stratum, only):
    """The judge results that belong to `stratum`: a verdict's own `stratum` when it records one,
    otherwise every judge result when the rows hold a single stratum."""
    out = []
    for judge in judges:
        named = {str(v["stratum"]) for v in judge["verdicts"] if v.get("stratum") is not None}
        if (named and stratum in named) or (not named and only):
            out.append(judge)
    return out


def _session_costs(rows, arms):
    by_cluster = {}
    for row in rows:
        if row.get("row_kind") == "session" and row.get("arm") in arms and row.get("cost_usd") is not None:
            scenario = row.get("scenario", row.get("task"))
            by_cluster.setdefault(scenario, {}).setdefault(row["arm"], []).append(float(row["cost_usd"]))
    return by_cluster


def long_session_reading(rows, arms, scenarios, seed, resamples):
    """The second arm's mean session cost over the first's, scenario-clustered, on `scenarios`
    (all when None); not measured when the rows carry no `row_kind`."""
    if not rows:
        return {"status": NOT_MEASURED, "reason": "no long-session rows were supplied"}
    if not any(r.get("row_kind") for r in rows):
        return {"status": NOT_MEASURED, "reason": "the long-session rows carry no row_kind"}
    sessions = _session_costs(rows, arms)
    if scenarios is not None:
        sessions = {s: c for s, c in sessions.items() if s in scenarios}
    point, interval, clusters = _ratio_interval(sessions, arms, seed, resamples)
    if not clusters:
        return {"status": NOT_MEASURED, "reason": "no scenario holds session rows of both %s and %s" % arms}
    return {"status": "measured", "metric": LONG, "ratio": point, "interval": interval, "scenarios": clusters,
            "arms": list(arms)}


def headline(production, found, long_rows, judges, margins, registration, seed, resamples):
    """Per stratum: harness against bare, every reading with its equivalence verdict."""
    out = {}
    names = strata(production + [r for r in long_rows if r.get("row_kind")])
    for stratum in names:
        rows = [r for r in _runs(_in(production, stratum)) if r.get("arm") in ARMS]
        entry = {"rows": _row_refs(rows)}
        try:
            sm2 = replay_stats.analyse(rows, seed, resamples) if rows else None
        except ValueError as exc:
            sm2, unavailable = None, "unavailable: %s" % exc
        else:
            unavailable = "no production rows of both arms in this stratum"
        if sm2:
            limitation = "; ".join(x for x in (registration, sm2["limitation"]) if x) or None
            entry["cost_of_pass"] = {"ratio": sm2["ratio"], "interval": sm2["ratio_interval"],
                                     "arms": {a: {"cost_of_pass": sm2["arms"][a]["cost_of_pass"],
                                                  "pass_rate": sm2["arms"][a]["pass_rate"],
                                                  "attempts": sm2["arms"][a]["attempts"]} for a in ARMS},
                                     "equivalence": _assessed(equivalence.RATIO, sm2["ratio_interval"], margins,
                                                              limitation)}
            entry["pass_rate"] = {"difference": sm2["difference"], "interval": sm2["difference_interval"],
                                  "equivalence": _assessed(equivalence.DIFFERENCE, sm2["difference_interval"],
                                                           margins, limitation)}
            reliability = _pass_k(rows, seed, resamples)
            reliability["equivalence"] = _assessed(PASS_K, reliability["interval"], margins, limitation)
            entry["pass_k"] = reliability
            joint = _all_rules(rows, found, seed, resamples)
            if joint is None:
                entry["all_rules"] = {"status": NOT_MEASURED, "reason": "no detections beside the production rows"}
            else:
                joint["equivalence"] = _assessed(ALL_RULES, joint["interval"], margins, limitation)
                entry["all_rules"] = joint
        else:
            entry["status"] = NOT_MEASURED
            entry["reason"] = unavailable
        entry["judge"] = _judge_entries(_judge_for(judges, stratum, len(names) == 1), margins, registration,
                                        seed, resamples)
        entry["long_session"] = long_session_reading(_in(long_rows, stratum), (BARE, HARNESS), None, seed,
                                                     resamples)
        out[stratum] = entry
    return out


def _judge_entries(judges, margins, registration, seed, resamples):
    if not judges:
        return {"status": NOT_MEASURED, "reason": "no judge results for this stratum"}
    results = []
    for judge in judges:
        section, _ = replay_judge.judge_section(judge["verdicts"], judge["key"], judge["calibration"], seed, resamples)
        dims = []
        for pair in section["pairs"]:
            for dim, cell in sorted(pair["dimensions"].items()):
                shifted = None if cell["interval"] is None else [_r(v - 0.5) for v in cell["interval"]]
                assessed = equivalence.label(equivalence.assess({JUDGE: shifted}, {JUDGE: margins[JUDGE]}),
                                             registration)[JUDGE]
                dims.append({"dimension": dim, "reference": pair["reference"], "treatment": pair["treatment"],
                             "win_rate": _r(cell["win_rate"]), "interval": [_r(v) for v in cell["interval"]]
                             if cell["interval"] else None, "pairs": cell["pairs"], "equivalence": assessed})
        results.append({"source": judge["id"], "admitted": section["admitted"],
                        "not_admitted": section["not_admitted"], "dimensions": dims})
    return {"status": "measured", "results": results}


def _row_refs(rows):
    return {"sources": sorted({r[SOURCE_KEY] for r in rows}), "arms": sorted({str(r.get("arm")) for r in rows}),
            "tasks": sorted({str(r.get("task")) for r in rows}), "count": len(rows)}


# --- The layers -----------------------------------------------------------------------------------

def _entry_for(item):
    """The `kind/unit` entry an unbuilt or excluded layer stands for."""
    layer, what = item.get("layer"), item.get("what") or ""
    if layer == "hook":
        return "hooks/%s" % what
    if layer == "instructions":
        return "instructions/CLAUDE.md"
    if layer == "stance" and what.startswith("the ") and what.endswith(" stance"):
        return "stances/%s" % what[4:-7]
    return what


def layer_list(manifest, static):
    """Every layer the scorecard reports, in a fixed order: each sweep arm, each unbuilt and
    excluded layer the manifest names, and each output style the static figure lists."""
    out = []
    for arm in manifest["arms"]:
        out.append({"layer": arm.get("layer"), "entry": ablations.entry_of(arm), "arm": arm["id"],
                    "what": arm.get("what"), "entries": ablations.entries_of(arm) if "removes" in arm
                    else [ablations.entry_of(arm)], "spec": arm})
    for key, why in (("unbuilt", "no removal arm can be built"), ("excluded", "excluded from the sweep")):
        for item in manifest.get(key) or []:
            entry = _entry_for(item)
            out.append({"layer": item.get("layer"), "entry": entry, "arm": None, "what": item.get("what"),
                        "entries": [entry], "spec": None, "absent": "%s: %s" % (why, item.get("reason"))})
    for path in sorted(p for p in (static.get("files") or {}) if p.startswith("claude/output-styles/")):
        entry = "output-styles/%s" % Path(path).stem
        out.append({"layer": "output style", "entry": entry, "arm": None, "what": "the %s output style" % Path(path).stem,
                    "entries": [entry], "spec": None, "absent": "the sweep manifest holds no removal arm for it"})
    order = dict((name, i) for i, name in enumerate(LAYERS))
    return sorted(out, key=lambda item: (order.get(item["layer"], len(LAYERS)), str(item["entry"])))


def _static_tokens(entry, static):
    files = static.get("files") or {}
    kind, _, unit = entry.partition("/")
    if kind == "hooks":
        return 0
    if kind == "instructions":
        paths = ["claude/CLAUDE.md"]
    elif kind == "stances":
        paths = [p for p in files if p.startswith("claude/stances/%s/" % unit)]
    elif kind in STATIC_PATHS:
        paths = [STATIC_PATHS[kind] % unit]
    else:
        return None
    found = [files[p]["est_tokens"] for p in paths if p in files]
    return sum(found) if found else None


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def prefix(item, harness_rows, static, table):
    """Per model: the layer's resident tokens, where they came from, the rows' mean turns and the
    USD a run pays for them; without rows, the static figure at one session start."""
    entries = [e for e in item["entries"] if e]
    models = sorted({str(r.get("model")) for r in harness_rows if r.get("model")})
    static_tokens = [_static_tokens(e, static) for e in entries]
    fallback = None if not entries or any(t is None for t in static_tokens) else sum(static_tokens)
    out = {}
    for model in models or [None]:
        rows = [r for r in harness_rows if r.get("model") == model] if model else []
        attributed = [sum(((r.get("context_attribution") or {}).get("modules") or {})[e] for e in entries)
                      for r in rows if all(e in (((r.get("context_attribution") or {}).get("modules")) or {})
                                           for e in entries)] if entries else []
        if attributed:
            tokens, source = _median(attributed), "rows' context_attribution, median of %d" % len(attributed)
        elif all(e.startswith("hooks/") for e in entries) and entries:
            tokens, source = 0, "a hook adds no resident text"
        else:
            tokens, source = fallback, "benchmarks/static.json" if fallback is not None else "unknown"
        turns = [r["turns"] for r in rows if isinstance(r.get("turns"), (int, float))]
        mean_turns = _mean(turns)
        rate = PRICING.price_for(table, model) if model else None
        cell = {"tokens": tokens, "source": source, "mean_turns": _r(mean_turns, 3)}
        if rate and tokens is not None:
            start = tokens * rate["cache_write"] / 1e6
            later = tokens * rate["cache_read"] / 1e6
            cell.update(usd_session_start=_r(start), usd_later_turn=_r(later),
                        usd_per_run=None if mean_turns is None else _r(start + later * max(mean_turns - 1, 0)))
        else:
            cell.update(usd_session_start=None, usd_later_turn=None, usd_per_run=None)
        out[model or "unpriced"] = cell
    return out


def _cost_control(spec):
    return bool(spec) and (bool(spec.get("long_session")) or any(
        s.get("metric") == equivalence.RATIO for s in spec.get("scores") or []))


def _score_tasks(spec, manifest):
    return list(spec.get("tasks") or []) or list(manifest["outcome"]["tasks"])


def _clean(score):
    keep = ("metric", "bounds", "tasks", "interval", "direction", "assessment", "reason", "harmful", "better",
            "limitation")
    return dict((k, score.get(k)) for k in keep if k in score)


def _vs_bare(spec, manifest, rows, seed, resamples):
    if not spec or not spec.get("scores"):
        return {"status": NOT_MEASURED, "reason": "the layer has no pre-registered score"}
    rows = [r for r in rows if r.get("arm") in ARMS]
    if not rows:
        return {"status": NOT_MEASURED, "reason": "no production or rule-task rows of both arms"}
    tasks = _score_tasks(spec, manifest)
    return {"status": "measured", "read_as": "bare minus harness",
            "scores": [_clean(ablations._score(rows, BARE, tasks, s["metric"], s["bounds"], seed, resamples))
                       for s in spec["scores"]]}


def _basis(verdict):
    judged = verdict["scores"] + [verdict["outcome"]]
    deciding = None
    if verdict["verdict"] == ablations.KEEP:
        deciding = next(s for s in judged if s["harmful"])
    elif verdict["verdict"] == ablations.NO_EVIDENCE:
        deciding = next((s for s in judged if not (s["assessment"] == equivalence.EQUIVALENT or s["better"])), None)
    return {"verdict": verdict["verdict"], "exploratory": verdict["exploratory"],
            "limitations": verdict["limitations"],
            "deciding": None if deciding is None else {k: deciding.get(k) for k in
                                                       ("metric", "bounds", "interval", "assessment", "reason")},
            "basis": [{k: s.get(k) for k in ("metric", "bounds", "interval", "assessment")} for s in judged]}


def layer_cards(manifest, static, table, run_rows, sweep_rows, long_rows, registration, seed, resamples):
    """One card per layer, its per-stratum readings and verdict."""
    items = layer_list(manifest, static)
    stratum_names = strata(run_rows + sweep_rows + [r for r in long_rows if r.get("row_kind")])
    verdicts = {}
    for stratum in stratum_names:
        rows = _runs(_in(sweep_rows, stratum))
        verdicts[stratum] = ablations.verdicts_by_entry(ablations.justify(rows, manifest, seed, resamples)) \
            if rows else {}
    harness_rows = [r for r in _runs(run_rows) if r.get("arm") == HARNESS]
    cards = []
    for item in items:
        spec = item["spec"]
        card = {"layer": item["layer"], "entry": item["entry"], "arm": item["arm"], "what": item["what"],
                "cost_control": _cost_control(spec),
                "prefix": prefix(item, harness_rows, static, table), "strata": {}}
        for stratum in stratum_names:
            cell = {"behaviour_vs_bare": _vs_bare(spec, manifest, _runs(_in(run_rows, stratum)), seed, resamples)}
            if item.get("absent"):
                reason = {"status": NOT_MEASURED, "reason": item["absent"]}
                cell.update(behaviour_vs_removal=reason, outcome=reason,
                            verdict={"verdict": ablations.NO_EVIDENCE, "reason": item["absent"]})
            elif item["entry"] not in verdicts[stratum]:
                reason = {"status": NOT_MEASURED, "reason": "no sweep rows in this stratum"}
                cell.update(behaviour_vs_removal=reason, outcome=reason,
                            verdict={"verdict": ablations.NO_EVIDENCE, "reason": reason["reason"]})
            else:
                found = verdicts[stratum][item["entry"]]
                sweep = [r for r in _runs(_in(sweep_rows, stratum)) if r.get("arm") in (ablations.CONTROL, item["arm"])]
                scored = set(_score_tasks(spec, manifest)) | set(manifest["outcome"]["tasks"])
                cell["behaviour_vs_removal"] = {"status": "measured", "read_as": "removal minus harness",
                                                "scores": [_clean(s) for s in found["scores"]]}
                cell["outcome"] = _clean(found["outcome"])
                verdict = _basis(found)
                verdict["exploratory"] = verdict["exploratory"] or bool(registration)
                verdict["rows"] = _row_refs([r for r in sweep if r.get("task") in scored])
                cell["verdict"] = verdict
                if card["cost_control"]:
                    cell["cost"] = {"marginal": found["marginal_cost"]}
            if card["cost_control"]:
                cost = cell.setdefault("cost", {"marginal": {"status": NOT_MEASURED,
                                                             "reason": "no sweep rows in this stratum"}})
                cost["long_session"] = long_session_reading(
                    _in(long_rows, stratum), (ablations.CONTROL, item["arm"]), set(spec.get("long_session") or []),
                    seed, resamples) if spec.get("long_session") else {
                    "status": NOT_MEASURED, "reason": "the arm names no long-session scenario"}
            card["strata"][stratum] = cell
        cards.append(card)
    return cards


# --- The whole scorecard --------------------------------------------------------------------------

def build(inputs, manifest, static, table, margins=None, margins_source="defaults", allow_exploratory=False,
          seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """The scorecard document from loaded inputs: `inputs` maps each role to `load_role`'s
    `(sources, rows, detections)` and `judge` to a list of `load_judge` results. Refused on exploratory rows without the flag."""
    sources = [s for role in ROLES for s in inputs.get(role, ([], [], {}))[0]]
    judges = inputs.get("judge") or []
    sources += [j["source"] for j in judges]
    reasons = admission(sources, allow_exploratory)
    registration = "; ".join(reasons) if reasons else None
    margins = dict(DEFAULT_MARGINS, **(margins or {}))
    rows = dict((role, inputs.get(role, ([], [], {}))[1]) for role in ROLES)
    found = inputs.get("production", ([], [], {}))[2]
    for role in ROLES:
        _check_unique(rows[role], role)
    run_rows = rows["production"] + rows["rules"]
    _check_unique(run_rows, "production and rule-task")
    document = {
        "schema": SCHEMA, "kind": KIND, "exploratory": bool(reasons), "exploratory_reasons": reasons,
        "manifest": {"name": manifest.get("name"), "sha256": manifest.get("sha256"),
                     "justification": manifest.get("justification")},
        "static": {"harness_version": static.get("harness_version"), "sha256": static.get("sha256")},
        "statistics": {"seed": seed, "resamples": resamples, "confidence": replay_stats.CONFIDENCE,
                       "method": replay_stats.METHOD},
        "margins": {"source": margins_source, "values": dict((k, list(v)) for k, v in sorted(margins.items()))},
        "strata_recorded": any(r.get("stratum") is not None for role in ROLES for r in rows[role]),
        "sources": sorted(sources, key=lambda s: s["id"]),
        "headline": headline(rows["production"], found, rows["long_session"], judges, margins, registration, seed,
                             resamples),
        "layers": layer_cards(manifest, static, table, run_rows, rows["sweep"], rows["long_session"],
                              registration, seed, resamples),
    }
    return json.loads(json.dumps(document, sort_keys=True))


def to_json(document):
    return json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"


# --- Markdown -------------------------------------------------------------------------------------

def _n(value, places=3):
    return "n/a" if value is None else ("%." + str(places) + "f") % value


def _iv(interval, places=3):
    if not interval:
        return "undefined"
    return "[%s, %s]" % tuple("undefined" if v is None else _n(v, places) for v in interval)


def _eq(item):
    return "%s%s" % (item["verdict"], " (exploratory)" if item.get("exploratory") else "")


def _score_text(section):
    if section.get("status") == NOT_MEASURED:
        return NOT_MEASURED
    return "; ".join("%s %s %s" % (s["metric"], _iv(s.get("interval")), s["assessment"]) if s.get("interval")
                     else "%s undefined (%s)" % (s["metric"], s.get("reason")) for s in section["scores"])


def headline_lines(document):
    lines = []
    for stratum, entry in sorted(document["headline"].items()):
        lines.append("### Stratum %s" % stratum)
        lines.append("")
        if entry.get("status") == NOT_MEASURED:
            lines.append("- Harness against bare: %s (%s)" % (NOT_MEASURED, entry["reason"]))
        else:
            cop, rate, pk = entry["cost_of_pass"], entry["pass_rate"], entry["pass_k"]
            lines.append("- Cost-of-Pass ratio, harness over bare: %s %s; %s" % (
                _n(cop["ratio"]), _iv(cop["interval"]), _eq(cop["equivalence"])))
            lines.append("- Pass rate: harness %s, bare %s; difference %s %s; %s" % (
                _n(cop["arms"][HARNESS]["pass_rate"]), _n(cop["arms"][BARE]["pass_rate"]),
                _n(rate["difference"]), _iv(rate["interval"]), _eq(rate["equivalence"])))
            lines.append("- pass^%d: harness %s, bare %s; mean per-task difference %s %s over %d task(s); %s" % (
                pk["k"], _n(pk["arms"].get(HARNESS, {}).get("pass_hat_k")), _n(pk["arms"].get(BARE, {}).get("pass_hat_k")),
                _n(pk["difference"]), _iv(pk["interval"]), pk["tasks"], _eq(pk["equivalence"])))
            joint = entry["all_rules"]
            if joint.get("status") == NOT_MEASURED:
                lines.append("- All-rules-at-once rate: %s (%s)" % (NOT_MEASURED, joint["reason"]))
            else:
                lines.append("- All-rules-at-once rate: harness %s, bare %s; mean per-task difference %s %s; %s" % (
                    _n(joint["arms"].get(HARNESS, {}).get("rate")), _n(joint["arms"].get(BARE, {}).get("rate")),
                    _n(joint["difference"]), _iv(joint["interval"]), _eq(joint["equivalence"])))
        judge = entry["judge"]
        if judge.get("status") == NOT_MEASURED:
            lines.append("- Judge win rates: %s (%s)" % (NOT_MEASURED, judge["reason"]))
        else:
            for result in judge["results"]:
                if not result["dimensions"]:
                    lines.append("- Judge %s: no dimension admitted" % result["source"])
                for dim in result["dimensions"]:
                    lines.append("- Judge %s, %s over %s: win rate %s %s over %d pair(s); %s" % (
                        dim["dimension"], dim["treatment"], dim["reference"], _n(dim["win_rate"]),
                        _iv(dim["interval"]), dim["pairs"], _eq(dim["equivalence"])))
        long = entry["long_session"]
        lines.append("- Long-session cost, harness over bare: %s" % (
            "%s (%s)" % (NOT_MEASURED, long["reason"]) if long["status"] == NOT_MEASURED
            else "%s %s over %d scenario(s)" % (_n(long["ratio"]), _iv(long["interval"]), long["scenarios"])))
        lines.append("")
    return lines


def _prefix_text(prefix, stratum):
    if stratum in prefix:
        prefix = {stratum: prefix[stratum]}
    return "; ".join("%s %s tok, %s USD/run" % (model, "n/a" if cell["tokens"] is None else int(round(cell["tokens"])),
                                                _n(cell["usd_per_run"], 4))
                     for model, cell in sorted(prefix.items()))


def _cost_text(cell):
    cost = cell.get("cost")
    if not cost:
        return ""
    marginal = cost["marginal"]
    parts = []
    if marginal.get("status") == NOT_MEASURED or marginal.get("usd_per_attempt") is None:
        parts.append("marginal %s" % NOT_MEASURED)
    else:
        parts.append("marginal %+.4f USD, ratio %s %s" % (marginal["usd_per_attempt"], _n(marginal["ratio"]),
                                                         _iv(marginal["ratio_interval"])))
    long = cost["long_session"]
    parts.append("sessions %s" % (NOT_MEASURED if long["status"] == NOT_MEASURED
                                  else "%s %s" % (_n(long["ratio"]), _iv(long["interval"]))))
    return "; ".join(parts)


def _verdict_text(verdict):
    if "deciding" not in verdict:
        return "%s (%s)" % (verdict["verdict"], verdict["reason"])
    text = verdict["verdict"] + (" (exploratory)" if verdict["exploratory"] else "")
    deciding = verdict["deciding"]
    if deciding:
        text += ": %s %s against [%g, %g]" % (deciding["metric"], _iv(deciding["interval"]), deciding["bounds"][0],
                                             deciding["bounds"][1])
    return text + "; %d row(s) from %s" % (verdict["rows"]["count"], ", ".join(verdict["rows"]["sources"]) or "none")


def render_markdown(document):
    lines = ["# Layer scorecard", ""]
    if document["exploratory"]:
        lines += ["**Exploratory: not evidence.** It scores rows that are not pre-registered:", ""]
        lines += ["- %s" % reason for reason in document["exploratory_reasons"]] + [""]
    lines.append("Manifest %s (%s); static figure %s; seed %s, %s resamples; margins from %s. Strata %s." % (
        document["manifest"]["name"], (document["manifest"]["sha256"] or "")[:12], document["static"]["harness_version"],
        document["statistics"]["seed"], document["statistics"]["resamples"], document["margins"]["source"],
        "recorded on rows" if document["strata_recorded"] else "not recorded on rows, so grouped by model"))
    lines += ["", "## Harness against bare", ""] + headline_lines(document)
    lines += ["## Layers", ""]
    names = sorted({s for card in document["layers"] for s in card["strata"]})
    for stratum in names:
        lines += ["### Stratum %s" % stratum, "",
                  "| Layer | Entry | Prefix | Bare minus harness | Removal minus harness | Outcome | Cost | Verdict |",
                  "|---|---|---|---|---|---|---|---|"]
        for card in document["layers"]:
            cell = card["strata"].get(stratum)
            if cell is None:
                continue
            outcome = cell["outcome"]
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                card["layer"], card["entry"], _prefix_text(card["prefix"], stratum), _score_text(cell["behaviour_vs_bare"]),
                _score_text(cell["behaviour_vs_removal"]),
                NOT_MEASURED if outcome.get("status") == NOT_MEASURED
                else "%s %s" % (_iv(outcome.get("interval")), outcome["assessment"]),
                _cost_text(cell), _verdict_text(cell["verdict"])))
        lines.append("")
    if not names:
        lines += ["No stratum holds rows, so no layer has a reading.", ""]
    lines += ["## Sources", ""] + ["- %s: %d row(s), %d pre-registered, sha256 %s" % (
        s["id"], s["rows"], s["registered"], s["sha256"][:16]) for s in document["sources"]]
    return "\n".join(lines).rstrip("\n") + "\n"


# --- CLI ------------------------------------------------------------------------------------------

def load_static(path):
    data = Path(path).read_bytes()
    return dict(json.loads(data.decode("utf-8")), sha256=hashlib.sha256(data).hexdigest())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="layer_scorecard.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--production", nargs="+", default=[], help="result directories of harness against bare")
    parser.add_argument("--rules", nargs="+", default=[], help="result directories of the rule-targeted tasks")
    parser.add_argument("--long-session", nargs="+", default=[], help="result directories of the long-session tier")
    parser.add_argument("--sweep", nargs="+", default=[], help="result directories of the removal sweep")
    parser.add_argument("--judge", nargs="+", default=[], help="judge directories: verdicts, key and calibration")
    parser.add_argument("--manifest", default=str(ROOT / "benchmarks" / "ablations.json"))
    parser.add_argument("--static", default=str(ROOT / "benchmarks" / "static.json"))
    parser.add_argument("--prices", default=str(ROOT / "policy" / "prices.json"))
    parser.add_argument("--plan", help="a pre-registration whose Equivalence margins override the defaults")
    parser.add_argument("--allow-exploratory", action="store_true",
                        help="score unregistered rows, and mark the whole scorecard exploratory")
    parser.add_argument("--seed", type=int, default=replay_stats.SEED)
    parser.add_argument("--resamples", type=int, default=replay_stats.RESAMPLES)
    parser.add_argument("--out", help="write scorecard.json and scorecard.md into this directory")
    parser.add_argument("--json", action="store_true", help="print the JSON instead of the Markdown")
    args = parser.parse_args(argv)
    try:
        manifest = ablations.load(args.manifest)
        if manifest.get("schema") != ablations.SCHEMA or not manifest.get("outcome"):
            raise Refused("%s is not a sweep manifest with an outcome subset" % args.manifest)
        margins, source = {}, "defaults"
        if args.plan:
            margins = equivalence.margins_from_plan(Path(args.plan).read_text(encoding="utf-8"))
            source = Path(args.plan).name
        inputs = {role: load_role(role, getattr(args, role)) for role in ROLES}
        inputs["judge"] = [load_judge(d, i) for i, d in enumerate(args.judge, 1)]
        document = build(inputs, manifest, load_static(args.static), PRICING.shipped_prices(args.prices), margins,
                         source, args.allow_exploratory, args.seed, args.resamples)
    except (Refused, ValueError, OSError) as exc:
        print("layer-scorecard: %s" % exc, file=sys.stderr)
        return 2
    text, markdown = to_json(document), render_markdown(document)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "scorecard.json").write_text(text, encoding="utf-8")
        (out / "scorecard.md").write_text(markdown, encoding="utf-8")
    sys.stdout.write(text if args.json else markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
