#!/usr/bin/env python3
"""The replay's micro tier: small tasks on a small model, each asking whether one mechanism fired.

Every task in `benchmarks/micro/tasks.json` is scored twice: by its held-back oracle, as any
synthetic task is, and by its `mechanism`, read from the run's own row or from the offline
detector rows `replay_detect` writes. So each run reports pass or fail, the mechanism fired or
not, and what it cost. A mechanism is one of two shapes:

- `{"id", "fired": "above-zero", "field": <row field>}`: fired when that stream field of the
  row, such as `spawns` or `hook_blocks`, is above zero.
- `{"id", "fired": "any-hit" | "none-hit", "detectors": [<detector id>, ...]}`: fired when any
  named detector hit the run, or when none did. `none-hit` suits a mechanism whose working shows
  as the absence of a miss, such as a reply shape the voice forbids.

A value that cannot be read, a timed-out run's stream or a detector row with no count, is
unknown (None), never "no". The tier's rows carry `tier: micro`, form their own series and go to
their own history file; `upsert_history` in `cost_bench.py` refuses to mix the two. What the tier
can and cannot claim is in docs/benchmarks.md.
"""
import json
from pathlib import Path

MICRO = "micro"
PRODUCTION = "production"
TIERS = (PRODUCTION, MICRO)
TASKS = Path("benchmarks") / "micro" / "tasks.json"
HISTORY_DIR = Path("benchmarks") / "micro"
# Distinct names, so a `--history-dir` shared with the production ledger still cannot mix them.
HISTORY_NAME = "micro-history.jsonl"
HISTORY_MD_NAME = "micro-history.md"
# The per-run and per-set caps. A full set is 3 tasks x 2 reps x 2 arms = 12 runs, at most
# 12 x 0.10 + 2 x 0.05 pre-flight = 1.30 USD if every run reached its cap; the spend cap stops
# the set before a launch that could pass 1.90, under the 2 USD the tier promises. The run cap is
# the CLI's `--max-budget-usd`, which is soft, so one run can overshoot it by its last turn.
RUN_CAP_USD = 0.10
PREFLIGHT_CAP_USD = 0.05
SPEND_CAP_USD = 1.90
REPS = 2
FIELDS = ("spawns", "stop_hooks", "hook_blocks")
FIRED = ("above-zero", "any-hit", "none-hit")


def tier_of(row):
    """A row's tier; a row that names none is production, as every row before this tier was."""
    return row.get("tier") or PRODUCTION


def manifest_tier(document):
    return document.get("tier") or PRODUCTION


def mechanism_errors(task, detector_ids):
    """Why a micro task's `mechanism` cannot be scored, or [] when it can."""
    spec = task.get("mechanism")
    where = "task %r" % task.get("id")
    if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not spec["id"]:
        return ["%s has no mechanism with an id" % where]
    if spec.get("fired") not in FIRED:
        return ["%s: mechanism fired must be one of %s" % (where, ", ".join(FIRED))]
    if spec["fired"] == "above-zero":
        return [] if spec.get("field") in FIELDS else [
            "%s: an above-zero mechanism reads one of %s" % (where, ", ".join(FIELDS))]
    detectors = spec.get("detectors")
    if not isinstance(detectors, list) or not detectors:
        return ["%s: a %s mechanism names its detectors" % (where, spec["fired"])]
    unknown = [d for d in detectors if d not in detector_ids]
    return ["%s: no such detector %s" % (where, ", ".join(map(str, unknown)))] if unknown else []


def check_manifest(document, detector_ids):
    """Errors for a micro manifest: its model, and every task's mechanism."""
    errors = [] if isinstance(document.get("model"), str) and document["model"] else [
        "a micro manifest names the one model it runs on"]
    for task in document.get("tasks", []):
        errors.extend(mechanism_errors(task, detector_ids))
    return errors


def fired(task, row, detections):
    """True, False or None (unknown) for one run of a micro task."""
    spec = task["mechanism"]
    if spec["fired"] == "above-zero":
        value = row.get(spec["field"])
        return None if not isinstance(value, (int, float)) else value > 0
    key = (row.get("task"), row.get("arm"), row.get("rep"))
    counts = dict((d["detector"], d.get("count")) for d in detections or []
                  if (d.get("task"), d.get("arm"), d.get("rep")) == key and d.get("detector") in spec["detectors"])
    if len(counts) != len(spec["detectors"]) or any(c is None for c in counts.values()):
        return None
    hit = any(counts.values())
    return hit if spec["fired"] == "any-hit" else not hit


def score_rows(rows, tasks, detections):
    """The rows, each with its task's `mechanism` id and `mechanism_fired`."""
    by_id = dict((t["id"], t) for t in tasks)
    out = []
    for row in rows:
        task = by_id.get(row.get("task"))
        if task is None:
            out.append(dict(row, mechanism=None, mechanism_fired=None))
            continue
        out.append(dict(row, mechanism=task["mechanism"]["id"], mechanism_fired=fired(task, row, detections)))
    return out


def _word(value):
    return "unknown" if value is None else ("yes" if value else "no")


def report_lines(rows):
    """One line per run: pass or fail, whether its mechanism fired, and its cost."""
    lines = []
    for r in rows:
        cost = "cost unknown" if r.get("cost_usd") is None else "%.4f USD" % r["cost_usd"]
        outcome = "error (%s)" % r.get("error_kind") if r.get("error") else ("pass" if r.get("passed") else "fail")
        lines.append("%s %s rep %s: %s, %s fired: %s, %s" % (r.get("task"), r.get("arm"), r.get("rep"), outcome,
                                                            r.get("mechanism"), _word(r.get("mechanism_fired")), cost))
    return lines


def _cell(rows):
    known = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    return {"runs": len(rows), "passed": sum(1 for r in rows if r.get("passed") and not r.get("error")),
            "errors": sum(1 for r in rows if r.get("error")),
            "fired": sum(1 for r in rows if r.get("mechanism_fired") is True),
            "fired_unknown": sum(1 for r in rows if r.get("mechanism_fired") is None),
            "cost_usd": round(sum(known), 6), "cost_unknown": len(rows) - len(known)}


def history_row(rows, series, arms, arm_records):
    """One line for the micro history: per task and arm, runs, passes, firings and cost.

    It carries no ratio and no verdict. The tier answers whether a mechanism fires on a small
    model, not what the harness costs on the production one, so it states no cost claim."""
    first = rows[0]
    per_task = {}
    for task in sorted(set(r["task"] for r in rows)):
        mine = [r for r in rows if r["task"] == task]
        per_task[task] = dict({"mechanism": mine[0].get("mechanism")},
                              **dict((arm, _cell([r for r in mine if r["arm"] == arm])) for arm in arms))
    known = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    walls = [r["wall_seconds"] for r in rows if isinstance(r.get("wall_seconds"), (int, float))]
    return {"date": first["date"], "tier": MICRO, "series": series, "bucket": first.get("bucket", ""),
            "harness_version": first["harness_version"], "harness_sha": first["harness_sha"],
            "tag": first["tag"], "model": first["model"], "cli_version": first["cli_version"],
            "reps": max(r["rep"] for r in rows), "runs": len(rows), "change_note": first.get("change_note", ""),
            "per_task": per_task, "cost_usd": round(sum(known), 6), "cost_unknown": len(rows) - len(known),
            "wall_seconds": round(sum(walls), 1), "arms": arm_records}


def render_history(rows, arms):
    """The micro ledger as text: one table line per set and task."""
    lines = ["# Micro tier: did each mechanism fire, on a small model", "",
             "A small model's behaviour is not the production model's. Read a line as whether the"
             " mechanism can fire and did, never as what the harness costs or saves.", "",
             "| Date | Series | Harness | Model | Task | Mechanism | %s |"
             % " | ".join("Passed, fired, USD (%s)" % arm for arm in arms),
             "| --- | --- | --- | --- | --- | --- | %s |" % " | ".join("---" for _ in arms)]
    for r in rows:
        for task, cell in sorted(r["per_task"].items()):
            arms_text = " | ".join("%d/%d, %d/%d, %.4f" % (cell[a]["passed"], cell[a]["runs"], cell[a]["fired"],
                                                         cell[a]["runs"], cell[a]["cost_usd"])
                                   for a in arms if a in cell)
            lines.append("| %s | %s | %s @ %s | %s | %s | %s | %s |" % (
                r["date"], r["series"], r["harness_version"], r["harness_sha"][:7], r["model"], task,
                cell.get("mechanism") or "n/a", arms_text))
        lines.append("")
        lines.append("Set %s: %d run(s), %.4f USD reported, %d without a readable cost, %.0f s of runs."
                     % (r["date"], r["runs"], r["cost_usd"], r["cost_unknown"], r["wall_seconds"]))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def load_document(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
