#!/usr/bin/env python3
"""Whether the delegation stance fired, per task, from saved replay rows alone (#429).

Each task gets one harness verdict:

- `not-offered`: some harness run's session was not offered a spawn tool, so it could not fire;
- `unknown`: the task's size or a harness run's spawn count is missing, or, short of a fire, some
  run's spawn-tool offer is; missing data is never read as a decline;
- `fired`: harness runs spawned in at least `fired_share` of the task's harness runs;
- `declined-below-break-even` / `missed-above-break-even`: it did not fire, and the task's size
  sits at or below, or above, the break-even.

Size is the median of the bare arm's gather calls, because the harness arm's own count falls when
it delegates. A gather call is a `GATHER_TOOLS` call, the only kind counted as absorbable: the
count is deterministic and undercounts, so a `missed` verdict is conservative. `Workflow` launches
are reported beside spawns, never counted as one, until Workflow agents are routed to a band.

The break-even and the fired share are pre-registered inputs, not measurements: FR-34 puts the
break-even at 4.8 to 7.6 absorbed calls and this uses the top of that range; the share is the one
#513 registers for its nudge, a spawn in three of four runs. The bare cell carries the same counts
with no verdict, as the control. Everything here is adherence, descriptive and not causal: the
cost split between spawning and non-spawning runs shows what they cost, not what a spawn saved.
Standard library only; it calls no model.
"""
import math
import statistics

# FR-34's upper figure, from the maintainer's unpublished break-even notes; hypothetical until a
# registered run measures one. Overridden with `--break-even` on `replay` and `summarise`.
BREAK_EVEN_CALLS = 7.6
BREAK_EVEN_SOURCE = "FR-34, hypothetical"
# #513's registered bar: a spawn in at least three of four runs.
FIRED_SHARE = 0.75
GATHER_TOOLS = ("Read", "Grep", "Glob")
WORKFLOW_TOOLS = ("Workflow",)
VERDICTS = ("fired", "declined-below-break-even", "missed-above-break-even", "not-offered", "unknown")
LABEL = "adherence, descriptive, not causal"
COUNT_FIELDS = ("spawns", "gather_calls", "absorbed_calls", "workflow_launches")


def _value(row, field):
    """A count field's value, or None when the row does not carry a number for it."""
    value = row.get(field)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _known(runs, field):
    return [_value(r, field) for r in runs if _value(r, field) is not None]


def _total(runs, field):
    known = _known(runs, field)
    return sum(known) if known else None


def needed(share, runs):
    """Spawning runs a cell of `runs` needs to read as fired; at least one."""
    return max(1, int(math.ceil(round(share * runs, 9))))


def counts(runs):
    """One arm's cell: its runs, how many were offered a spawn tool, and the summed counts.

    A summed count is None when no run in the cell reported it."""
    offered = [r.get("spawn_offered") for r in runs]
    return {"runs": len(runs), "offered": offered.count(True), "not_offered": offered.count(False),
            "spawning_runs": sum(1 for s in _known(runs, "spawns") if s >= 1),
            "spawns_unknown": sum(1 for r in runs if _value(r, "spawns") is None),
            "gather_median": statistics.median(_known(runs, "gather_calls")) if _known(runs, "gather_calls") else None,
            **{field: _total(runs, field) for field in COUNT_FIELDS if field != "gather_calls"}}


def cost_split(runs):
    """Mean `cost_usd` of spawning and of non-spawning runs; either is None when its group is empty."""
    def mean(group):
        costs = [r["cost_usd"] for r in group if isinstance(r.get("cost_usd"), (int, float))]
        return round(sum(costs) / len(costs), 6) if costs else None
    spawning = [r for r in runs if (_value(r, "spawns") or 0) >= 1]
    quiet = [r for r in runs if _value(r, "spawns") == 0]
    return {"spawning_usd": mean(spawning), "not_spawning_usd": mean(quiet), "label": LABEL}


def verdict(rows, break_even=BREAK_EVEN_CALLS, fired_share=FIRED_SHARE):
    """One task's cell from its rows, both arms: the harness verdict, the size and each arm's counts."""
    bare = [r for r in rows if r.get("arm") == "bare"]
    harness = [r for r in rows if r.get("arm") == "harness"]
    size = counts(bare)["gather_median"]
    mine = counts(harness)
    if mine["not_offered"]:
        said = "not-offered"
    elif size is None or not harness or mine["spawns_unknown"] == len(harness):
        said = "unknown"
    elif mine["spawning_runs"] >= needed(fired_share, len(harness)):
        said = "fired"  # a spawn proves the tool was offered, and unknown runs cannot undo a fire
    elif mine["spawns_unknown"] or mine["offered"] < len(harness):
        said = "unknown"  # a run that might have spawned, or might not have been offered the tool
    else:
        said = "declined-below-break-even" if size <= break_even else "missed-above-break-even"
    return {"verdict": said, "size": size,
            "above_break_even": None if size is None else size > break_even,
            "bare": counts(bare), "harness": dict(mine, **cost_split(harness))}


def report(rows, break_even=BREAK_EVEN_CALLS, fired_share=FIRED_SHARE):
    """The delegation block for a row set: one cell per task and a tally of the verdicts."""
    tasks = {}
    for task in sorted({r.get("task") or "" for r in rows}):
        tasks[task] = verdict([r for r in rows if (r.get("task") or "") == task], break_even, fired_share)
    tally = dict.fromkeys(VERDICTS, 0)
    for cell in tasks.values():
        tally[cell["verdict"]] += 1
    return {"label": LABEL, "break_even": break_even, "break_even_source": BREAK_EVEN_SOURCE,
            "fired_share": fired_share, "absorbable": list(GATHER_TOOLS), "tasks": tasks, "verdicts": tally}


def _n(value):
    if value is None:
        return "n/a"
    return ("%.3f" % value).rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def task_line(task, cell):
    """One task's verdict as a line of plain text."""
    harness, bare = cell["harness"], cell["bare"]
    return ("%s: %s, size %s gather calls (bare median), harness spawned in %d/%d runs "
            "(spawns %s, absorbed %s, workflow launches %s), bare %d/%d; mean cost spawning %s, "
            "not spawning %s" % (
                task or "n/a", cell["verdict"], _n(cell["size"]), harness["spawning_runs"], harness["runs"],
                _n(harness["spawns"]), _n(harness["absorbed_calls"]), _n(harness["workflow_launches"]),
                bare["spawning_runs"], bare["runs"], _n(harness["spawning_usd"]),
                _n(harness["not_spawning_usd"])))


def heading(block):
    return ("Delegation (%s): break-even %s absorbed calls (%s), fired at a spawn in %s of harness runs, "
            "absorbable %s" % (block["label"], _n(float(block["break_even"])), block["break_even_source"],
                               "%d%%" % round(block["fired_share"] * 100), ", ".join(block["absorbable"])))


def render(block):
    """The block as `summarise` prints it under SM-2's report."""
    lines = [heading(block)]
    lines.extend("  " + task_line(task, cell) for task, cell in sorted(block["tasks"].items()))
    if not block["tasks"]:
        lines.append("  no rows")
    return "\n".join(lines) + "\n"
