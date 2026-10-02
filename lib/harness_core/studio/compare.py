"""Two finished replay targets compared side by side, with the engine's paired intervals.

A side is one target of one finished live replay: its harness arm's native rows are the run being
compared, and its own bare arm stays with its own recorded analysis. Two sides are compared only
when each comes from a succeeded run that did not stop at its spend cap and finished every task and
trial it requested, and both ran the same tasks, model, trials per task, evaluator pack, per-trial
budget cap and pinned environment stamps (`STAMP_FIELDS`); otherwise the comparison is refused and
every reason is named. A comparable pair goes to `replay_stats.compare` with the base as control,
and its output is returned verbatim: Studio computes no interval, ratio or reading of its own. A
preferred direction is attached to a reading only when the result could be cited as evidence.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import replay, runs

SCHEMA_VERSION = 1
BASE, CANDIDATE = "base", "candidate"
SIDES = (BASE, CANDIDATE)
HARNESS_ARM = "harness"
ENGINE = "replay_stats.compare"
REPLAY_SUITE = "live-replay"
COMPLETE_STATUS = "succeeded"
# Which way each measure is preferable. The engine states no direction in its output, so this is
# the one table: a lower Cost-of-Pass and a higher pass rate are preferable, the two measures the
# evidence standard's decision rule judges. Every other measure, per-attempt cost included, gets
# the engine's reading alone: a run that gives up early is cheaper per attempt without being better.
PREFERRED = {"cost_per_passed": "lower", "pass_rate": "higher"}
# Row stamps the evidence standard's protocol item 2 (pinned inputs) and item 4 (held constant)
# require equal across a comparison; a difference refuses it. `(field, what it names)`.
STAMP_FIELDS = (("cli_version", "CLI versions"), ("os", "container platforms"),
                ("prices_sha256", "price tables"), ("tier", "replay tiers"))
# The run date is item 4's run window. Two runs are never in one window, so a different date is
# named and withholds a direction (the result is not citable) rather than refusing the comparison.
DATE_FIELD = "date"
PREREGISTERED = "pre-registered"
_ENGINE_MODULE = None


class CompareError(ValueError):
    """A comparison that cannot be read; `code` is the route's error name."""

    def __init__(self, message: str, code: str = "compare_invalid"):
        super().__init__(message)
        self.code = code


def parse_side(value: Any, name: str) -> Tuple[str, int]:
    """`(run_id, target index)` from `{"run_id": uuid, "target": 1 or 2}`."""
    if not isinstance(value, dict) or set(value) != {"run_id", "target"}:
        raise CompareError("%s must name exactly a run_id and a target" % name, "invalid_request")
    run_id, target = value["run_id"], value["target"]
    try:
        if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
            raise ValueError(run_id)
    except ValueError:
        raise CompareError("%s has an invalid run id" % name, "invalid_request") from None
    if type(target) is not int or target not in (1, 2):
        raise CompareError("%s target must be 1 or 2" % name, "invalid_request")
    return run_id, target


def parse_request(value: Any) -> Dict[str, Tuple[str, int]]:
    if not isinstance(value, dict) or set(value) != set(SIDES):
        raise CompareError("a comparison names exactly a base and a candidate", "invalid_request")
    return {name: parse_side(value[name], name) for name in SIDES}


def _engine():
    global _ENGINE_MODULE
    if _ENGINE_MODULE is None:
        _ENGINE_MODULE = replay._engine_module("replay_stats")
    return _ENGINE_MODULE


def _finished(rows: List[Mapping[str, Any]]) -> Dict[str, int]:
    """Finished harness trials per task: the matrix the two sides must share to be paired."""
    out: Dict[str, int] = {}
    for row in rows:
        out[row["task"]] = out.get(row["task"], 0) + 1
    return out


def load_side(supervisor: runs.RunSupervisor, repository: Path, run_id: str,
              index: int) -> Dict[str, Any]:
    """One finished replay target: its identity, recorded analysis, staleness and harness rows."""
    try:
        run = supervisor.show(run_id)
    except (runs.RunError, OSError) as exc:
        raise CompareError("run %s is not known" % run_id, "compare_not_found") from exc
    if run.get("suite_id") != REPLAY_SUITE:
        raise CompareError("run %s is not a live replay" % run_id, "compare_not_replay")
    if run.get("status") not in runs.TERMINAL:
        raise CompareError("run %s has not finished" % run_id, "compare_not_finished")
    try:
        with supervisor.lock():
            private = supervisor._read(run_id)
        request = replay.ReplayRequest.parse(json.loads(private["parameters"]["request_json"]))
        run_root = supervisor._run_path(run_id).parent
        output = run_root / "replay"
        summary = replay.read_summary(output / replay.SUMMARY_NAME)
    except (runs.RunError, replay.ReplayError, KeyError, TypeError, ValueError) as exc:
        raise CompareError("run %s has no readable replay result" % run_id,
                           "compare_unavailable") from exc
    target = request.targets[index - 1]
    path = (output / ("target-%d" % index) / target.execution_ref / replay.RESULTS_NAME).resolve()
    listed = [str(Path(item).resolve()) for item in summary["result_files"]]
    if str(path) not in listed:
        raise CompareError("run %s recorded no native rows for target %d" % (run_id, index),
                           "compare_unavailable")
    try:
        rows = [row for row in replay._read_rows(path) if row["arm"] == HARNESS_ARM]
        analysis = replay.read_analysis(output)
    except replay.ReplayError as exc:
        raise CompareError(str(exc), "compare_unavailable") from exc
    position = listed.index(str(path)) + 1
    recorded = next((dict(item) for item in analysis or [] if item.get("target") == position), None)
    if recorded is not None:
        recorded["target"] = index
    stale, reason = replay.draft_staleness(repository, target)
    checked = target.kind == "draft" and bool(target.draft)
    freshness = ("stale" if stale else "current") if checked else "not checked"
    return {"run_id": run_id, "target": index, "ref": target.as_dict(),
            "tasks": sorted(request.tasks), "model": request.model,
            "trials": request.repetitions, "max_budget_usd": request.max_budget_usd,
            "run_status": run.get("status"), "stopped_at_cap": summary["stopped_at_cap"],
            "freshness": freshness,
            "pack_digest": request.pack["digest"] if request.pack else None,
            "key": replay.comparison_key(request),
            "evidence": replay.target_evidence(request, target),
            "finished": _finished(rows), "stamps": _stamps(rows), "stale": stale, "stale_reason": reason,
            "analysis": recorded, "_rows": rows}


def _stamps(rows: List[Mapping[str, Any]]) -> Dict[str, List[Any]]:
    """Each stamp field's distinct values across the side's rows; absent reads as null."""
    out = {}
    for field in [name for name, _ in STAMP_FIELDS] + [DATE_FIELD]:
        values = {json.dumps(row.get(field), sort_keys=True) for row in rows}
        out[field] = [json.loads(value) for value in sorted(values)]
    return out


def _shown(values: List[Any]) -> str:
    return ", ".join("unrecorded" if value is None else str(value) for value in values) or "none"


def _listed(values: List[str]) -> str:
    return ", ".join(values) if values else "none"


def _incomplete(name: str, side: Mapping[str, Any]) -> List[str]:
    """Why one side is not a whole run of what it requested; empty when it is."""
    out = []
    if side["run_status"] != COMPLETE_STATUS:
        out.append("the %s run ended %s, not %s" % (name, side["run_status"], COMPLETE_STATUS))
    if side["stopped_at_cap"]:
        out.append("the %s run stopped at its spend cap" % name)
    short = ["%s (%d of %d)" % (task, side["finished"].get(task, 0), side["trials"])
             for task in side["tasks"] if side["finished"].get(task, 0) != side["trials"]]
    extra = sorted(set(side["finished"]) - set(side["tasks"]))
    if short or extra:
        out.append("the %s did not finish the trials it requested: %s"
                   % (name, "; ".join(short + ["%s (not requested)" % task for task in extra])))
    return out


def refusals(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    """Every reason the two sides cannot be paired, all of them; empty when they can."""
    out = _incomplete(BASE, base) + _incomplete(CANDIDATE, candidate)
    if (base["run_id"], base["target"]) == (candidate["run_id"], candidate["target"]):
        out.append("both sides are target %d of the same run" % base["target"])
    if base["tasks"] != candidate["tasks"]:
        only_base = sorted(set(base["tasks"]) - set(candidate["tasks"]))
        only_candidate = sorted(set(candidate["tasks"]) - set(base["tasks"]))
        out.append("different task sets: only the base runs %s; only the candidate runs %s"
                   % (_listed(only_base), _listed(only_candidate)))
    if base["model"] != candidate["model"]:
        out.append("different models: the base ran %s, the candidate %s"
                   % (base["model"], candidate["model"]))
    if base["trials"] != candidate["trials"]:
        out.append("different trials per task: the base ran %d, the candidate %d"
                   % (base["trials"], candidate["trials"]))
    if base["pack_digest"] != candidate["pack_digest"]:
        out.append("different evaluator packs: the base ran %s, the candidate %s"
                   % (base["pack_digest"] or "no pack", candidate["pack_digest"] or "no pack"))
    if base["max_budget_usd"] != candidate["max_budget_usd"]:
        out.append("different per-trial budget caps: the base ran $%s, the candidate $%s"
                   % (base["max_budget_usd"], candidate["max_budget_usd"]))
    for field, label in STAMP_FIELDS:
        if base["stamps"][field] != candidate["stamps"][field]:
            out.append("different %s: the base ran %s, the candidate %s"
                       % (label, _shown(base["stamps"][field]), _shown(candidate["stamps"][field])))
    return out


def notes(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    """Differences named without refusing: the run dates."""
    if base["stamps"][DATE_FIELD] == candidate["stamps"][DATE_FIELD]:
        return []
    return ["different run dates: the base ran %s, the candidate %s"
            % (_shown(base["stamps"][DATE_FIELD]), _shown(candidate["stamps"][DATE_FIELD]))]


def direction_withheld(base: Mapping[str, Any], candidate: Mapping[str, Any],
                       result: Optional[Mapping[str, Any]]) -> List[str]:
    """Why no reading may be labelled better or worse: a result the evidence standard says
    supports no claim (an exploratory arm or side, item 1 and item 7) or a different run window
    (item 4). Empty when a direction may be shown."""
    out = []
    for name, side in ((BASE, base), (CANDIDATE, candidate)):
        if side["evidence"] != PREREGISTERED:
            out.append("the %s is %s" % (name, side["evidence"]))
    for arm in (result or {}).get("arms") or []:
        if arm.get("exploratory"):
            out.append("the engine marks %s exploratory" % arm.get("arm"))
    if notes(base, candidate):
        out.append("the two runs are from different dates")
    return out


def compare(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """The payload for two loaded sides: identities, refusals, and the engine's output verbatim."""
    reasons = refusals(base, candidate)
    result, error = None, None
    if not reasons:
        rows = ([dict(row, arm=BASE) for row in base["_rows"]]
                + [dict(row, arm=CANDIDATE) for row in candidate["_rows"]])
        try:
            result = _engine().compare(rows, control=BASE)
        except ValueError as exc:
            error = str(exc)
    public = {name: {key: value for key, value in side.items() if not key.startswith("_")}
              for name, side in ((BASE, base), (CANDIDATE, candidate))}
    return {"schema_version": SCHEMA_VERSION, "engine": ENGINE, "control": BASE,
            "base": public[BASE], "candidate": public[CANDIDATE],
            "key_match": base["key"] == candidate["key"],
            "comparable": not reasons, "refusals": reasons,
            "stale": ["%s: %s" % (name, side["stale_reason"] or "stale")
                      for name, side in ((BASE, base), (CANDIDATE, candidate)) if side["stale"]],
            "notes": notes(base, candidate),
            "direction_withheld": direction_withheld(base, candidate, result),
            "preferred": dict(PREFERRED), "result": result, "error": error}


def compare_runs(supervisor: runs.RunSupervisor, repository: Path,
                 sides: Mapping[str, Tuple[str, int]]) -> Dict[str, Any]:
    """The route's and `citizen runs compare`'s one answer for two `(run_id, target)` sides."""
    loaded = {name: load_side(supervisor, repository, *sides[name]) for name in SIDES}
    return compare(loaded[BASE], loaded[CANDIDATE])


def render(result: Mapping[str, Any]) -> str:
    """The engine's own text report for an engine result."""
    return _engine().render_compare(result)


def side_argument(value: str) -> Optional[Tuple[str, int]]:
    """`RUN_ID:TARGET` from the command line, or None when it does not parse."""
    run_id, _, target = value.rpartition(":")
    if not run_id or target not in ("1", "2"):
        return None
    return run_id, int(target)
