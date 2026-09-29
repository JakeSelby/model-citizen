"""Import `claude plugin eval` aggregate results into the Studio run store.

`claude plugin eval` writes `<plugin>/<eval dir>/results/<timestamp>/aggregate-result.json` and a
self-contained `report.html` beside it. The JSON is versioned by `schemaVersion`; only version 1
is understood here, and any other version is refused rather than interpreted, so a format change
upstream surfaces as a skipped source instead of a silently wrong run. Unknown fields inside a
version 1 document are ignored, as the format's own documentation asks of readers.
"""
from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from .run_store import (RunStoreError, _cost, _digest, _finite_number, _indexed_timestamp,
                        _nonnegative_int, _optional_string, _read_json, _stable_id)

SOURCE_KIND = "plugin-eval"
SUITE_ID = "claude-plugin-eval"
SUPPORTED_SCHEMA_VERSION = 1
RESULT_NAME = "aggregate-result.json"
REPORT_NAME = "report.html"
DEFAULT_EVAL_DIRECTORY = "evals"
ARMS = ("with", "without")
MAX_DURATION_SECONDS = 366 * 24 * 60 * 60
PARTIAL_STATUS = {"cost_ceiling": "capped", "interrupted": "cancelled", "auth_failed": "failed"}


def _safe_relative(value: Any) -> Optional[PurePosixPath]:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        return None
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        return None
    return parsed


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _eval_directories(root: Path) -> List[Path]:
    """The repository-root eval directory plus each plugin's, as its manifest names it."""
    directories = [root / DEFAULT_EVAL_DIRECTORY]
    manifests = [root / ".claude-plugin" / "plugin.json"]
    manifests.extend(sorted(root.glob("*/.claude-plugin/plugin.json")))
    manifests.extend(sorted(root.glob("*/*/.claude-plugin/plugin.json")))
    for manifest in manifests:
        if manifest.is_symlink() or not manifest.is_file() or not _inside(root, manifest):
            continue
        try:
            value = _read_json(manifest)
        except RunStoreError:
            value = None
        experimental = value.get("experimental") if isinstance(value, dict) else None
        configured = experimental.get("evals") if isinstance(experimental, dict) else None
        name = _safe_relative(configured) or PurePosixPath(DEFAULT_EVAL_DIRECTORY)
        directory = manifest.parent.parent / Path(*name.parts)
        if directory not in directories:
            directories.append(directory)
    return directories


def discover(root: Path, limit: int) -> List[Path]:
    """Every aggregate result under a discovered eval directory, inside the repository.

    Raises before any result is read once more than `limit` are found.
    """
    found: List[Path] = []
    for directory in _eval_directories(root):
        results = directory / "results"
        if not results.is_dir() or not _inside(root, results):
            continue
        for path in results.glob("*/" + RESULT_NAME):
            if path not in found and _inside(root, path):
                found.append(path)
                if len(found) > limit:
                    raise RunStoreError("too many plugin eval result files")
    return sorted(found)


def report_for(root: Path, result: Path) -> Optional[str]:
    """The repository-relative path of the HTML report written beside a result, if present."""
    report = result.parent / REPORT_NAME
    if report.is_symlink() or not report.is_file() or not _inside(root, report):
        return None
    return report.relative_to(root).as_posix()


def _score(value: Any) -> bool:
    return _finite_number(value) and 0 <= value <= 1


def _optional_score(value: Any) -> bool:
    return value is None or _score(value)


def _optional_amount(value: Any) -> bool:
    return value is None or (_finite_number(value) and value >= 0)


def _run(value: Any, case: str, arm: str) -> Dict[str, Any]:
    if (not isinstance(value, dict) or not _score(value.get("score"))
            or not isinstance(value.get("passed"), bool)
            or not _optional_string(value.get("error"))
            or not _optional_string(value.get("startedAt"))
            or any(not _optional_amount(value.get(name))
                   for name in ("costUsd", "judgeCostUsd", "durationSeconds"))
            or not (value.get("turns") is None or _nonnegative_int(value.get("turns")))
            or not isinstance(value.get("skippedPaidGraders", False), bool)
            or not isinstance(value.get("graders", []), list)):
        raise RunStoreError("plugin eval run does not match schema 1: %s [%s]" % (case, arm))
    graders = []
    for grader in value.get("graders", []):
        if (not isinstance(grader, dict) or not isinstance(grader.get("name"), str)
                or not isinstance(grader.get("passed"), bool)
                or not isinstance(grader.get("scored", True), bool)
                or not isinstance(grader.get("withOnly", False), bool)
                or not _optional_amount(grader.get("weight"))):
            raise RunStoreError("plugin eval grader does not match schema 1: %s [%s]" % (case, arm))
        graders.append({"name": grader["name"], "passed": grader["passed"],
                        "weight": grader.get("weight"), "scored": grader.get("scored", True),
                        "with_only": grader.get("withOnly", False)})
    return {"score": value["score"], "passed": value["passed"], "error": value.get("error"),
            "aborted": value.get("aborted") is not None, "turns": value.get("turns"),
            "cost_usd": value.get("costUsd"), "duration_seconds": value.get("durationSeconds"),
            "skipped_paid_graders": value.get("skippedPaidGraders", False),
            "graders": graders}


def _case(value: Any, threshold: float) -> Dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"]:
        raise RunStoreError("plugin eval case does not match schema 1")
    name = value["name"]
    arms = value.get("arms")
    aggregates = value.get("aggregates")
    if (not isinstance(arms, dict) or not arms or set(arms) - set(ARMS) or "with" not in arms
            or any(not isinstance(runs, list) for runs in arms.values())):
        raise RunStoreError("plugin eval case has unrecognized arms: " + name)
    if (not isinstance(aggregates, dict) or not _optional_score(aggregates.get("score"))
            or any(not _optional_score(aggregates.get(field))
                   for field in ("passRate", "scoreWithout", "passRateWithout"))
            or not (aggregates.get("delta") is None
                    or (_finite_number(aggregates["delta"]) and -1 <= aggregates["delta"] <= 1))):
        raise RunStoreError("plugin eval case aggregates do not match schema 1: " + name)
    runs = {arm: [_run(item, name, arm) for item in arms[arm]] for arm in ARMS if arm in arms}
    score = aggregates.get("score")
    if not runs["with"] or score is None:
        outcome: Dict[str, Any] = {"status": "not_run"}
    else:
        outcome = {"passed": score >= threshold}
    outcome.update({
        "name": name, "score": score, "score_without": aggregates.get("scoreWithout"),
        "delta": aggregates.get("delta"), "pass_rate": aggregates.get("passRate"),
        "pass_rate_without": aggregates.get("passRateWithout"), "arms": runs,
    })
    return outcome


def _completed_at(started_at: str, duration: Any) -> Optional[str]:
    if not _finite_number(duration) or duration < 0:
        return None
    normalized = _indexed_timestamp(started_at)
    start = dt.datetime.fromisoformat(str(normalized).replace("Z", "+00:00"))
    try:
        return (start + dt.timedelta(seconds=duration)).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise RunStoreError("plugin eval result has an out-of-range duration") from exc


def _without_local_paths(value: Mapping[str, Any]) -> Dict[str, Any]:
    """The raw document minus the absolute paths of the machine that ran it."""
    raw = copy.deepcopy(dict(value))
    suite = raw.get("suite")
    if isinstance(suite, dict):
        suite.pop("root", None)
        for plugin in suite.get("plugins") or []:
            if isinstance(plugin, dict):
                plugin.pop("path", None)
    for case in raw.get("cases") or []:
        arms = case.get("arms") if isinstance(case, dict) else None
        for runs in (arms.values() if isinstance(arms, dict) else ()):
            for run in (runs if isinstance(runs, list) else ()):
                if isinstance(run, dict):
                    run.pop("tracePath", None)
    return raw


def plugin_eval_record(relative: str, value: Any, report: Optional[str] = None) -> Dict[str, Any]:
    """Normalize one aggregate result; refuse, never guess, when its shape is not version 1."""
    if not isinstance(value, dict):
        raise RunStoreError("plugin eval result is not an object")
    version = value.get("schemaVersion")
    if (not isinstance(version, int) or isinstance(version, bool)
            or version != SUPPORTED_SCHEMA_VERSION):
        raise RunStoreError("unsupported plugin eval schema version: " + str(version))
    suite = value.get("suite")
    cases = value.get("cases")
    aggregates = value.get("aggregates")
    started_at = value.get("startedAt")
    if (not isinstance(suite, dict) or not isinstance(cases, list) or not cases
            or not isinstance(aggregates, dict) or not isinstance(started_at, str)
            or not isinstance(value.get("partial"), bool)
            or not _optional_string(value.get("partialReason"))
            or not _optional_string(value.get("claudeVersion"))
            or not _optional_amount(value.get("costUsd"))
            or not _optional_amount(value.get("durationSeconds"))
            or (value.get("durationSeconds") or 0) > MAX_DURATION_SECONDS):
        raise RunStoreError("plugin eval result does not match schema 1")
    _indexed_timestamp(started_at)
    threshold = suite.get("threshold")
    plugins = suite.get("plugins", [])
    if (not _score(threshold) or not isinstance(plugins, list)
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                   or not item["name"] or not _optional_string(item.get("version"))
                   for item in plugins)
            or not _optional_string(suite.get("ablation"))
            or not _optional_string(suite.get("modelOverride"))):
        raise RunStoreError("plugin eval suite does not match schema 1")
    if (not _nonnegative_int(aggregates.get("casesTotal"))
            or not _nonnegative_int(aggregates.get("casesPassed"))
            or aggregates["casesPassed"] > aggregates["casesTotal"]
            or not _optional_score(aggregates.get("overallScore"))
            or not (aggregates.get("meanDelta") is None
                    or (_finite_number(aggregates["meanDelta"])
                        and -1 <= aggregates["meanDelta"] <= 1))):
        raise RunStoreError("plugin eval aggregates do not match schema 1")
    label = "+".join(item["name"] for item in plugins) or "no-plugin"
    normalized_cases: Dict[str, Dict[str, Any]] = {}
    for item in cases:
        case = _case(item, threshold)
        identity = "plugin:" + label + "/" + case["name"]
        if identity in normalized_cases:
            raise RunStoreError("plugin eval result repeats a case: " + case["name"])
        normalized_cases[identity] = case
    if value["partial"]:
        status = PARTIAL_STATUS.get(str(value.get("partialReason")), "failed")
    else:
        status = ("succeeded" if aggregates["casesPassed"] == aggregates["casesTotal"]
                  else "failed")
    arms = [arm for arm in ARMS if any(arm in case["arms"] for case in normalized_cases.values())]
    trials = {case["name"]: len(case["arms"]["with"]) for case in normalized_cases.values()}
    identity = _digest({"started_at": started_at, "claude_version": value.get("claudeVersion"),
                        "plugins": [[item["name"], item.get("version")] for item in plugins]})
    artifacts = [relative] + ([report] if report else [])
    return {
        "schema_version": 1, "run_id": _stable_id(SOURCE_KIND, relative, identity),
        "source": {"kind": SOURCE_KIND, "path": relative, "record_identity": identity},
        "suite": {"id": SUITE_ID, "version": SUPPORTED_SCHEMA_VERSION},
        "target": {"kind": "plugin", "ref": label,
                   "version": ",".join(str(item.get("version")) for item in plugins) or None,
                   "commit": None, "draft": None, "config_digest": None},
        "runtime": value.get("claudeVersion"), "model": suite.get("modelOverride"),
        "arms": arms,
        "trials": max(trials.values()) if len(set(trials.values())) == 1 else None,
        "parameters": {"ablation": suite.get("ablation"), "threshold": threshold},
        "argv": [], "status": status,
        "times": {"created_at": started_at, "started_at": started_at,
                  "completed_at": _completed_at(started_at, value.get("durationSeconds"))},
        "tokens": {}, "cost": _cost(value.get("costUsd")),
        "scores": {"overall": aggregates.get("overallScore"),
                   "mean_delta": aggregates.get("meanDelta"),
                   "cases_passed": aggregates["casesPassed"],
                   "cases_total": aggregates["casesTotal"],
                   "partial": value["partial"], "partial_reason": value.get("partialReason")},
        "cases": normalized_cases, "artifacts": artifacts,
        "report": report, "raw": _without_local_paths(value),
    }


def import_records(root: Path, limit: int) -> List[Any]:
    """(relative path, record or error) for each discovered result, for the run store to index."""
    entries: List[Any] = []
    for path in discover(root, limit):
        relative = path.relative_to(root).as_posix()
        try:
            record: Any = plugin_eval_record(relative, _read_json(path), report_for(root, path))
        except (RunStoreError, OverflowError, ValueError, TypeError, RecursionError) as exc:
            record = exc if isinstance(exc, RunStoreError) else RunStoreError(
                "plugin eval result could not be read: " + type(exc).__name__)
        entries.append((relative, record))
    return entries
