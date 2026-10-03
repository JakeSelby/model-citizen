"""Test a draft: one live replay of the draft's base and the draft, and its verdict per checkpoint.

A draft test is a live replay (`replay.ReplayAdmission`) whose target 1 is the commit the draft was
based on and whose target 2 is the draft's checkpoint, so both run as one pair on the same tasks,
model, trials, evaluator pack and caps. Its verdict is #990's comparison of the two targets
(`compare.compare_runs`, the engine's `replay_stats.compare` with the base as control), read and
never recomputed.

**When the Studio may say "helped".** Only when all three hold: the comparison is comparable; it
withholds no direction (`compare.direction_withheld` is empty: both sides pre-registered, no arm
the engine marks exploratory, one run window); and every measure in `compare.PREFERRED` reads its
preferred way (Cost-of-Pass lower and pass rate higher, each interval clear of no effect). "Worse"
is the mirror: a direction may be shown and a preferred measure reads the other way. Otherwise the
verdict is "inconclusive" or, when a direction is withheld, "exploratory". A draft target always
runs `--exploratory` (`replay.target_evidence` registers only a release target), so a draft test is
exploratory today and its verdict never says helped; the readings are shown without direction.

**Power before the run.** `replay_stats.minimum_detectable_effect` gives the smallest relative
change in a per-attempt mean the pair can resolve at its attempts per side and a planning
coefficient of variation, either stated with the test or the repository's declared assumption in
`benchmarks/ablations.json`. Fewer than the engine's `MIN_TRIALS` trials per task, or a stated
effect smaller than that figure, is flagged before the run with the fewest trials that would do.

Each test is recorded once under the supervisor's state root (`draft-tests/<run id>.json`) with
the draft's identity and checkpoint; the run, its comparison and staleness are read live.
"""
from __future__ import annotations

import datetime
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import compare, drafts, replay, runs, targets

SCHEMA_VERSION = 1
RECORDS_DIR = "draft-tests"
MAX_RECORD_BYTES = 64 * 1024
MAX_TRIALS = 20  # `ReplayRequest.parse` accepts one to twenty repetitions
PLANNING_MANIFEST = Path("benchmarks") / "ablations.json"
FORM_KEYS = frozenset(("model", "repetitions", "tasks", "max_budget_usd", "spend_cap_usd", "pack"))
STALE_COPY = "Stale: the draft changed after this comparison."
EXPLORATORY_NOTE = ("A draft test is exploratory: the draft runs unregistered, so its result is "
                    "never cited as evidence and the Studio does not say whether the draft helped.")
HELPED, WORSE, INCONCLUSIVE, EXPLORATORY = "helped", "worse", "inconclusive", "exploratory"
OPPOSITE = {"lower": "higher", "higher": "lower"}


class DraftTestError(ValueError):
    """A draft test that cannot be planned, started or read; `code` is the route's error name."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_plan(effect: Any, cv: Any) -> Dict[str, Any]:
    """The stated effect, a fraction above 0 and below 1, and an optional planning cv above 0."""
    if not _number(effect) or not 0 < effect < 1:
        raise DraftTestError("the effect to detect must be a fraction above 0 and below 1")
    if cv is not None and (not _number(cv) or not 0 < cv <= 10):
        raise DraftTestError("the coefficient of variation must be above 0 and at most 10")
    return {"effect": float(effect), "cv": None if cv is None else float(cv)}


def declared_planning(repository: Path) -> Dict[str, Any]:
    """The repository's declared planning assumption: `{cv, source}` from the ablation manifest."""
    try:
        planning = json.loads((Path(repository) / PLANNING_MANIFEST).read_text(
            encoding="utf-8"))["planning"]
        cv, source = planning["cv"], planning["source"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise DraftTestError("no planning assumption is declared; state a coefficient of "
                             "variation", "draft_test_planning_unavailable") from exc
    if not _number(cv) or cv <= 0 or not isinstance(source, str) or not source.strip():
        raise DraftTestError("the declared planning assumption is malformed; state a "
                             "coefficient of variation", "draft_test_planning_unavailable")
    return {"cv": float(cv), "source": "%s: %s" % (PLANNING_MANIFEST.as_posix(), source)}


def power(repository: Path, tasks: int, trials: int, plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Whether `trials` per task over `tasks` tasks can detect the stated effect, and if not, the
    fewest trials per task that can. Every figure is the engine's; nothing is estimated here."""
    stats = compare._engine()
    planning = ({"cv": plan["cv"], "source": "stated with this test"} if plan["cv"] is not None
                else declared_planning(repository))
    effect = plan["effect"]

    def detectable(count: int) -> Optional[float]:
        value = stats.minimum_detectable_effect(tasks * count, planning["cv"])
        return None if value is None else round(value, 4)

    smallest = detectable(trials)
    reasons = []
    if trials < stats.MIN_TRIALS:
        reasons.append("fewer than %d trials per task: the engine marks the result exploratory"
                       % stats.MIN_TRIALS)
    if smallest is None or smallest > effect:
        reasons.append("%d attempt(s) per side resolve a change of %s at the least, more than the "
                       "%.1f%% you want to detect"
                       % (tasks * trials, "undefined" if smallest is None
                          else "%.1f%%" % (100 * smallest), 100 * effect))
    needed = None
    if reasons:
        for count in range(max(trials, stats.MIN_TRIALS), MAX_TRIALS + 1):
            figure = detectable(count)
            if figure is not None and figure <= effect:
                needed = count
                break
    return {"effect": effect, "cv": planning["cv"], "cv_source": planning["source"],
            "tasks": tasks, "trials": trials, "attempts_per_side": tasks * trials,
            "minimum_detectable_effect": smallest, "min_trials": stats.MIN_TRIALS,
            "power": stats.POWER, "confidence": stats.CONFIDENCE,
            "enough": not reasons, "reasons": reasons, "needed_trials": needed,
            "max_trials": MAX_TRIALS}


def power_line(value: Mapping[str, Any]) -> str:
    """One sentence for the CLI and the page: enough, or too few and the number needed."""
    if value["enough"]:
        return ("Enough trials: %d attempt(s) per side resolve a %.1f%% change at %d%% power."
                % (value["attempts_per_side"], 100 * value["minimum_detectable_effect"],
                   round(100 * value["power"])))
    offer = ("run %d trials per task instead" % value["needed_trials"]
             if value["needed_trials"] is not None else
             "no trial count up to %d detects it on %d task(s); add tasks or state a larger effect"
             % (value["max_trials"], value["tasks"]))
    return "Too few trials for the effect you stated: %s; %s." % ("; ".join(value["reasons"]), offer)


def identity(repository: Path, name: str) -> Dict[str, Any]:
    """The draft's id, base commit, checkpoint and configuration digest, read without blocking a
    writer (`drafts.read_snapshot`)."""
    if not isinstance(name, str) or not drafts.NAME.fullmatch(name):
        raise DraftTestError("the draft name is invalid")
    try:
        return drafts.read_snapshot(Path(repository), name, lambda _worktree, state, config: {
            "draft": state["name"], "draft_id": state["draft_id"],
            "base_ref": state["base_ref"], "base_revision": state["base_revision"],
            "revision": state["revision"], "config_digest": targets._config_digest(dict(config))})
    except drafts.DraftError as exc:
        raise DraftTestError(str(exc), "draft_not_found" if exc.code == "not-found"
                             else "draft_unavailable") from exc


def replay_form(draft: Mapping[str, Any], form: Any) -> Dict[str, Any]:
    """The replay form for a draft test: the base commit as target 1 and the draft as target 2.

    The base is named by its commit (a `branch` target accepts a full commit), so a moved
    installed checkout or tag can never stand in for the commit the draft started from."""
    if not isinstance(form, dict) or set(form) != FORM_KEYS:
        raise DraftTestError("a draft test names exactly its model, repetitions, tasks, "
                             "per-run budget, spend cap and pack")
    return dict(form, pre_registration=None, targets=[
        {"kind": "branch", "ref": draft["base_revision"]},
        {"kind": "draft", "ref": draft["draft"]}])


def check_request(draft: Mapping[str, Any], selected: replay.ReplayRequest) -> None:
    """Refuse a resolved request that is not a test of this draft's checkpoint against its base."""
    base, candidate = selected.targets
    if (base.kind != "branch" or base.revision != draft["base_revision"]
            or candidate.kind != "draft" or candidate.draft != draft["draft"]
            or candidate.revision != draft["revision"]):
        raise DraftTestError("the request is not a test of this draft's checkpoint against its "
                             "base; plan it again", "draft_test_mismatch")


def _records_dir(root: Path) -> Path:
    return Path(root) / RECORDS_DIR


def record(root: Path, run_id: str, draft: Mapping[str, Any], selected: replay.ReplayRequest,
           planned: Mapping[str, Any]) -> Dict[str, Any]:
    """Write a started test's record once; an existing record is never replaced."""
    if str(uuid.UUID(run_id)) != run_id:
        raise DraftTestError("the run id is invalid")
    value = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "draft": draft["draft"],
             "draft_id": draft["draft_id"], "revision": selected.targets[1].revision,
             "config_digest": selected.targets[1].config_digest,
             "base_revision": selected.targets[0].revision, "power": dict(planned),
             "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    directory = _records_dir(root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory / (run_id + ".json"),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
    return value


def _read_record(path: Path) -> Optional[Dict[str, Any]]:
    """One record, or None when it is unsafe or malformed (counted by the caller, never shown)."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            return None
        value = json.loads(os.read(descriptor, MAX_RECORD_BYTES + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        os.close(descriptor)
    keys = {"schema_version", "run_id", "draft", "draft_id", "revision", "config_digest",
            "base_revision", "power", "created_at"}
    if not isinstance(value, dict) or set(value) != keys or value["schema_version"] != SCHEMA_VERSION \
            or path.name != "%s.json" % value["run_id"]:
        return None
    return value


def claim(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """The verdict for a finished comparison, by the rule in this module's docstring."""
    if not comparison["comparable"]:
        return {"verdict": "not_compared", "reasons": list(comparison["refusals"])}
    if comparison["error"] is not None:
        return {"verdict": "engine_refused", "reasons": [comparison["error"]]}
    if comparison["direction_withheld"]:
        return {"verdict": EXPLORATORY, "reasons": list(comparison["direction_withheld"])}
    measures = ((comparison["result"] or {}).get("arms") or [{}])[0].get("measures") or {}
    readings = {key: (measures.get(key) or {}).get("reading") for key in comparison["preferred"]}
    if any(readings[key] == OPPOSITE[way] for key, way in comparison["preferred"].items()):
        return {"verdict": WORSE, "reasons": ["%s reads %s" % (key, readings[key])
                                               for key in sorted(readings)]}
    if all(readings[key] == way for key, way in comparison["preferred"].items()):
        return {"verdict": HELPED, "reasons": ["%s reads %s" % (key, readings[key])
                                                for key in sorted(readings)]}
    return {"verdict": INCONCLUSIVE, "reasons": ["%s reads %s" % (key, readings[key])
                                                  for key in sorted(readings)]}


HEADLINES = {
    "running": "Running: the base and the draft are being measured as a pair.",
    "unavailable": "No verdict: the run's result cannot be read.",
    "not_compared": "Not compared: the two targets did not finish as a matched pair.",
    "engine_refused": "No verdict: the engine refused the comparison.",
    EXPLORATORY: "Exploratory: no claim. " + EXPLORATORY_NOTE,
    INCONCLUSIVE: "Inconclusive: no interval on the judged measures clears no effect.",
    HELPED: "Helped: Cost-of-Pass is lower and the pass rate higher, each interval clear of no effect.",
    WORSE: "Worse: a judged measure moved the wrong way with its interval clear of no effect.",
}


def _readings(comparison: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The engine's own entries for the judged measures, verbatim."""
    arms = ((comparison or {}).get("result") or {}).get("arms") or []
    measures = arms[0].get("measures", {}) if arms else {}
    return {key: measures[key] for key in compare.PREFERRED if key in measures}


def _spend(supervisor: Any, run_id: str) -> Optional[float]:
    try:
        summary = replay.read_summary(supervisor._run_path(run_id).parent / "replay"
                                      / replay.SUMMARY_NAME)
    except (replay.ReplayError, runs.RunError, OSError):
        return None
    return summary["spend_usd"]


def _recorded_power_line(value: Any) -> str:
    try:
        return power_line(value)
    except (KeyError, TypeError, ValueError):
        return "The power check recorded with this test is unreadable."


def verdict(supervisor: Any, repository: Path, item: Mapping[str, Any]) -> Dict[str, Any]:
    """One test's state, verdict, staleness, spend and the comparison it links to."""
    run_id = item["run_id"]
    sides = {"base": {"run_id": run_id, "target": 1}, "candidate": {"run_id": run_id, "target": 2}}
    target = replay.ReplayTarget("draft", item["draft"], item["revision"], draft=item["draft"],
                                 config_digest=item["config_digest"])
    stale, stale_reason = replay.draft_staleness(Path(repository), target)
    out = {"run_id": run_id, "revision": item["revision"], "base_revision": item["base_revision"],
           "created_at": item["created_at"], "power": item["power"],
           "power_line": _recorded_power_line(item["power"]), "stale": stale,
           "stale_reason": stale_reason, "stale_copy": STALE_COPY if stale else None,
           "comparison": dict(sides, command="citizen runs compare %s:1 %s:2" % (run_id, run_id)),
           "status": None, "verdict": "unavailable", "reasons": [], "readings": {},
           "spend_usd": None}
    try:
        out["status"] = supervisor.show(run_id).get("status")
    except (runs.RunError, OSError):
        out["reasons"] = ["the run is no longer known"]
    else:
        if out["status"] not in runs.TERMINAL:
            out["verdict"] = "running"
        else:
            out["spend_usd"] = _spend(supervisor, run_id)
            try:
                comparison = compare.compare_runs(supervisor, Path(repository), {
                    name: (run_id, side["target"]) for name, side in sides.items()})
            except compare.CompareError as exc:
                out["reasons"] = [str(exc)]
            else:
                out.update(claim(comparison))
                out["readings"] = _readings(comparison)
    out["headline"] = HEADLINES[out["verdict"]]
    return out


def verdicts(supervisor: Any, repository: Path, root: Path, name: str) -> Dict[str, Any]:
    """Every test of this draft, newest first, and the latest verdict for each checkpoint."""
    draft = identity(repository, name)
    items, skipped = [], 0
    directory = _records_dir(root)
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        value = _read_record(path)
        if value is None:
            skipped += 1
        elif value["draft_id"] == draft["draft_id"]:
            items.append(value)
    items.sort(key=lambda value: (value["created_at"], value["run_id"]), reverse=True)
    tests = [verdict(supervisor, repository, value) for value in items]
    checkpoints: List[Dict[str, Any]] = []
    for test in tests:
        found = next((entry for entry in checkpoints if entry["revision"] == test["revision"]), None)
        if found is None:
            checkpoints.append({"revision": test["revision"], "current":
                                test["revision"] == draft["revision"], "latest": test, "tests": 1})
        else:
            found["tests"] += 1
    return {"schema_version": SCHEMA_VERSION, "draft": draft["draft"],
            "revision": draft["revision"], "base_revision": draft["base_revision"],
            "evidence_note": EXPLORATORY_NOTE, "tests": tests, "checkpoints": checkpoints,
            "unreadable_records": skipped}


def render(payload: Mapping[str, Any]) -> List[str]:
    """`citizen draft test` text: one block per checkpoint, newest first."""
    lines = ["draft %s at %s, based on %s" % (payload["draft"], payload["revision"][:12],
                                             payload["base_revision"][:12])]
    if not payload["checkpoints"]:
        lines.append("  no tests yet")
    for entry in payload["checkpoints"]:
        test = entry["latest"]
        lines.append("  checkpoint %s%s: %s" % (entry["revision"][:12],
                                                " (current)" if entry["current"] else "",
                                                test["headline"]))
        if test["stale"]:
            lines.append("    " + STALE_COPY)
        for reason in test["reasons"]:
            lines.append("    " + reason)
        lines.append("    " + test["power_line"])
        lines.append("    comparison: " + test["comparison"]["command"])
    if payload["unreadable_records"]:
        lines.append("  %d unreadable test record(s) skipped" % payload["unreadable_records"])
    return lines
