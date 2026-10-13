# SPDX-License-Identifier: MIT
"""Workflows: the Workflow tool's runs, read from the subagent rows its agents left.

The ledger knows a Workflow-tool agent by its `workflow` field, the `wf_<id>` directory of the run
it belonged to, so a run is one distinct id and its agents are the rows that carry it. A
slash-command workflow such as `/build` leaves no mark on a usage row, so it is not an Efficacy
item here; the Settings tab lists those. The tool has no setting the harness writes, so the item
answers one period with the periods note.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from .. import periods
from . import roles

UNIT = "workflow-tool"
LABEL = "Workflow tool runs"
SETTING = "Runtime default · unconfined"


def is_workflow_row(row: Mapping[str, Any]) -> bool:
    return row.get("kind") == "subagent" and bool(row.get("workflow"))


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    roles.annotate(ctx, is_workflow_row, lambda row: UNIT)
    observations = [(row["_ts"], SETTING, periods.stream_of(row))
                    for row in ctx.usage if is_workflow_row(row)]
    if not observations:
        return {}
    return {UNIT: {"label": LABEL, "observations": observations, "current": SETTING}}


def metrics(rows: Sequence[Mapping[str, Any]], _unit: str, _value: str) -> Dict[str, Any]:
    agents = [row for row in rows if is_workflow_row(row)]
    by_run: Dict[str, list] = {}
    for row in agents:
        by_run.setdefault(str(row["workflow"]), []).append(row)
    out: Dict[str, Any] = {"runs": len(by_run), "agents": len(agents),
                           "agents_per_run": round(len(agents) / len(by_run), 1) if by_run else 0}
    run_costs = []
    unpriced_runs = 0
    for run in by_run.values():
        costs = [row.get("_cost") for row in run]
        if any(cost is None for cost in costs):
            unpriced_runs += 1
        else:
            run_costs.append(sum(costs))
    run_costs.sort()
    if run_costs:
        out["cost"] = roles.usd(sum(run_costs))
        out["cost_per_run"] = "%s · %s" % (roles.usd(roles.percentile(run_costs, 0.5)),
                                            roles.usd(roles.percentile(run_costs, 0.75)))
    out["unpriced_runs"] = unpriced_runs
    return out


periods.register_surface("workflows", items, metrics)
