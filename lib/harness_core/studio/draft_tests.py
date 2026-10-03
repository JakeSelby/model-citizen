"""Test a draft: one live replay of the draft's base and the draft, and its verdict per checkpoint.

A draft test is a live replay (`replay.ReplayAdmission`) whose target 1 is the commit the draft was
based on and whose target 2 is the draft's checkpoint, so both run as one pair on the same tasks,
model, trials, evaluator pack and caps. Its verdict is #990's comparison of the two targets
(`compare.compare_runs`, the engine's `replay_stats.compare` with the base as control), read and
never recomputed.

**When the Studio may say "helped".** Only when the comparison is comparable, withholds no
direction (`compare.direction_withheld` is empty: both sides pre-registered, no arm the engine marks
exploratory, one run window), and the evidence standard's decision rule holds on the engine's
intervals (docs/evidence-standard.md, "The twelve items", item 1, "A pre-registered plan",
**Decision rule**): the paired, task-clustered 95% interval on the Cost-of-Pass ratio lies wholly
below 1.0, and the lower bound of the interval on the pass-rate difference (draft minus base) is
above -delta (`replay_stats.DELTA`, 0.125). "Worse" is the rule's exact mirror, the base winning by
it: the ratio's interval lies wholly above 1.0 and the difference's upper bound is below +delta.
Anything else is "inconclusive"; a withheld direction is "exploratory". Both are read from the
intervals the engine reports, never from its `reading` alone. A draft target always runs
`--exploratory` (`replay.target_evidence` registers only a release target), so a draft test is
exploratory today and its verdict never says helped; the readings are shown without direction.

**Power before the run.** `replay_stats.minimum_detectable_effect` gives the smallest relative
change in a per-attempt mean the pair can resolve at its attempts per side and a planning
coefficient of variation, either stated with the test or the repository's declared assumption in
`benchmarks/ablations.json`. It treats attempts as independent, while the verdict's intervals
resample tasks, so the power line says so. Fewer than the engine's `MIN_TRIALS` trials per task,
or a stated effect smaller than that figure, is flagged before the run with the fewest trials that
would do. A draft whose checkpoint is still its base commit is refused: both sides would be the
same commit.

Each test is recorded once under the supervisor's state root (`draft-tests/<run id>.json`, written
to a temporary name and linked into place through a directory descriptor) with the draft's identity
and checkpoint. Staleness is read from one `drafts.read_snapshot` of the draft per call, never under
the writer lock; only each checkpoint's latest test is compared, and that comparison is cached by
run id and a digest of the run's recorded results.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import stat
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

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
INDEPENDENCE_NOTE = ("The figure treats attempts as independent; the verdict's intervals resample "
                     "tasks, so few tasks can still read inconclusive.")
CACHE_SIZE = 128
_CACHE: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


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
    if (not isinstance(tasks, int) or isinstance(tasks, bool) or tasks < 1
            or not isinstance(trials, int) or isinstance(trials, bool)
            or not 1 <= trials <= MAX_TRIALS):
        raise DraftTestError("a draft test needs at least one task and one to %d trials per task"
                             % MAX_TRIALS)
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
        return ("Enough trials: %d attempt(s) per side resolve a %.1f%% change at %d%% power. %s"
                % (value["attempts_per_side"], 100 * value["minimum_detectable_effect"],
                   round(100 * value["power"]), INDEPENDENCE_NOTE))
    offer = ("run %d trials per task instead" % value["needed_trials"]
             if value["needed_trials"] is not None else
             "no trial count up to %d detects it on %d task(s); add tasks or state a larger effect"
             % (value["max_trials"], value["tasks"]))
    return "Too few trials for the effect you stated: %s; %s. %s" % (
        "; ".join(value["reasons"]), offer, INDEPENDENCE_NOTE)


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
    except targets.TargetError as exc:
        raise DraftTestError("the draft's configuration cannot be read",
                             "draft_unavailable") from exc


def _refuse_unchanged(base_revision: str, revision: str) -> None:
    if revision == base_revision:
        raise DraftTestError("the draft's checkpoint is still its base commit, so both sides "
                             "would run the same commit; checkpoint a change first",
                             "draft_test_unchanged")


def replay_form(draft: Mapping[str, Any], form: Any) -> Dict[str, Any]:
    """The replay form for a draft test: the base commit as target 1 and the draft as target 2.

    The base is named by its commit (a `branch` target accepts a full commit), so a moved
    installed checkout or tag can never stand in for the commit the draft started from."""
    if not isinstance(form, dict) or set(form) != FORM_KEYS:
        raise DraftTestError("a draft test names exactly its model, repetitions, tasks, "
                             "per-run budget, spend cap and pack")
    _refuse_unchanged(draft["base_revision"], draft["revision"])
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
    _refuse_unchanged(base.revision, candidate.revision)


def form_size(form: Any) -> Tuple[int, int]:
    """`(tasks, trials)` a replay form asks for, so power is checked before any target build."""
    tasks, trials = (form.get("tasks"), form.get("repetitions")) if isinstance(form, dict) \
        else (None, None)
    if not isinstance(tasks, list) or not isinstance(trials, int) or isinstance(trials, bool):
        raise DraftTestError("a draft test needs a task list and a whole number of trials")
    return len(tasks), trials


def _records_fd(root: Path, create: bool) -> Optional[int]:
    """A descriptor on `<root>/draft-tests`, opened without following a symlink; None when it does
    not exist and `create` is false."""
    try:
        parent = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        if create:
            raise
        return None
    try:
        if create:
            try:
                os.mkdir(RECORDS_DIR, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        try:
            descriptor = os.open(RECORDS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=parent)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DraftTestError("the draft test records are not a private directory",
                                 "draft_test_records_unsafe") from exc
    finally:
        os.close(parent)
    info = os.fstat(descriptor)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        os.close(descriptor)
        raise DraftTestError("the draft test records are not a private directory",
                             "draft_test_records_unsafe")
    return descriptor


def record(root: Path, run_id: str, draft: Mapping[str, Any], selected: replay.ReplayRequest,
           planned: Mapping[str, Any]) -> Dict[str, Any]:
    """Write a started test's record once, whole or not at all; an existing one is never replaced."""
    if str(uuid.UUID(run_id)) != run_id:
        raise DraftTestError("the run id is invalid")
    value = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "draft": draft["draft"],
             "draft_id": draft["draft_id"], "revision": selected.targets[1].revision,
             "config_digest": selected.targets[1].config_digest,
             "base_revision": selected.targets[0].revision, "power": dict(planned),
             "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    content = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    directory = _records_fd(Path(root), create=True)
    temporary = ".%s.%s.tmp" % (run_id, uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            try:
                if os.write(descriptor, content) != len(content):
                    raise OSError("the draft test record was written short")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            # A link fails when the name exists, so a record is never replaced or left half-written.
            os.link(temporary, run_id + ".json", src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)
    return value


RECORD_KEYS = frozenset(("schema_version", "run_id", "draft", "draft_id", "revision",
                         "config_digest", "base_revision", "power", "created_at"))


def _read_record(directory: int, name: str) -> Optional[Dict[str, Any]]:
    """One record, or None when it is unsafe or malformed (counted by the caller, never shown)."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
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
    if not isinstance(value, dict) or set(value) != RECORD_KEYS \
            or value["schema_version"] != SCHEMA_VERSION or name != "%s.json" % value["run_id"]:
        return None
    return value


def read_records(root: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Every readable record and the count of unreadable ones; a temporary name is not counted."""
    directory = _records_fd(Path(root), create=False)
    if directory is None:
        return [], 0
    try:
        names = sorted(name for name in os.listdir(directory)
                       if name.endswith(".json") and not name.startswith("."))
        values = [_read_record(directory, name) for name in names]
    finally:
        os.close(directory)
    return [value for value in values if value is not None], sum(1 for v in values if v is None)


def _interval(measure: Any) -> Optional[Tuple[float, float]]:
    interval = measure.get("interval") if isinstance(measure, dict) else None
    if (isinstance(interval, list) and len(interval) == 2
            and all(_number(bound) for bound in interval)):
        return float(interval[0]), float(interval[1])
    return None


def claim(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """The verdict for a finished comparison, by the decision rule in this module's docstring.

    The engine's Cost-of-Pass entry is a ratio against the base reported as an effect, the ratio
    less 1.0, so the ratio's interval lies wholly below 1.0 exactly when the effect's upper bound is
    below 0; its pass-rate entry is the difference itself."""
    if not comparison["comparable"]:
        return {"verdict": "not_compared", "reasons": list(comparison["refusals"])}
    if comparison["error"] is not None:
        return {"verdict": "engine_refused", "reasons": [comparison["error"]]}
    if comparison["direction_withheld"]:
        return {"verdict": EXPLORATORY, "reasons": list(comparison["direction_withheld"])}
    delta = compare._engine().DELTA
    measures = ((comparison["result"] or {}).get("arms") or [{}])[0].get("measures") or {}
    cost = _interval(measures.get("cost_per_passed"))
    passing = _interval(measures.get("pass_rate"))
    if cost is None or passing is None:
        missing = [name for name, value in (("Cost-of-Pass", cost), ("pass rate", passing))
                   if value is None]
        return {"verdict": INCONCLUSIVE,
                "reasons": ["the engine gives no interval for %s" % " or ".join(missing)]}
    reasons = ["Cost-of-Pass ratio interval [%s, %s]" % (_bound(1 + cost[0]), _bound(1 + cost[1])),
               "pass-rate difference interval [%s, %s] against the margin %s"
               % (_bound(passing[0]), _bound(passing[1]), _bound(delta))]
    if cost[1] < 0 and passing[0] > -delta:
        return {"verdict": HELPED, "reasons": reasons}
    if cost[0] > 0 and passing[1] < delta:
        return {"verdict": WORSE, "reasons": reasons}
    return {"verdict": INCONCLUSIVE, "reasons": reasons}


def _bound(value: float) -> str:
    return "%g" % round(value, 4)


HEADLINES = {
    "running": "Running: the base and the draft are being measured as a pair.",
    "unavailable": "No verdict: the run's result cannot be read.",
    "not_compared": "Not compared: the two targets did not finish as a matched pair.",
    "engine_refused": "No verdict: the engine refused the comparison.",
    EXPLORATORY: "Exploratory: no claim. " + EXPLORATORY_NOTE,
    INCONCLUSIVE: "Inconclusive: neither the evidence standard's decision rule nor its mirror holds.",
    HELPED: ("Helped: by the evidence standard's decision rule, the Cost-of-Pass ratio's interval "
             "lies wholly below 1.0 and the pass rate is non-inferior."),
    WORSE: ("Worse: the decision rule's mirror holds: the Cost-of-Pass ratio's interval lies wholly "
            "above 1.0 and the pass rate is not better by the margin."),
}


def _readings(comparison: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The engine's own entries for the judged measures, verbatim."""
    arms = ((comparison or {}).get("result") or {}).get("arms") or []
    measures = arms[0].get("measures", {}) if arms else {}
    return {key: measures[key] for key in compare.PREFERRED if key in measures}


def _recorded(supervisor: Any, run_id: str) -> Tuple[str, float]:
    """`(digest, spend)` of a finished run's recorded results: the summary, every native result
    file it lists and the engine analysis beside it. A rerun or rewrite changes the digest."""
    output = supervisor._run_path(run_id).parent / "replay"
    summary = replay.read_summary(output / replay.SUMMARY_NAME)
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode("utf-8"))
    for path in sorted(summary["result_files"]) + [str(output / replay.ANALYSIS_NAME)]:
        digest.update(path.encode("utf-8") + b"\0")
        try:
            digest.update(Path(path).read_bytes())
        except FileNotFoundError:
            digest.update(b"absent")
    return digest.hexdigest(), summary["spend_usd"]


def _cached(key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
    with _CACHE_LOCK:
        value = _CACHE.get(key)
        if value is not None:
            _CACHE.move_to_end(key)
        return value


def _remember(key: Tuple[str, str], value: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def _recorded_power_line(value: Any) -> str:
    try:
        return power_line(value)
    except (KeyError, TypeError, ValueError):
        return "The power check recorded with this test is unreadable."


def staleness(draft: Mapping[str, Any], revision: str,
              config_digest: Optional[str]) -> Tuple[bool, Optional[str]]:
    """`(stale, reason)` of a tested checkpoint against the draft's snapshot (`identity`)."""
    if revision != draft["revision"]:
        return True, "the draft has a newer checkpoint"
    if config_digest is not None and config_digest != draft["config_digest"]:
        return True, "the draft's configuration changed"
    return False, None


def verdict(supervisor: Any, repository: Path, item: Mapping[str, Any],
            draft: Mapping[str, Any]) -> Dict[str, Any]:
    """One test's state, verdict, staleness, spend and the comparison it links to."""
    run_id = item["run_id"]
    sides = {"base": {"run_id": run_id, "target": 1}, "candidate": {"run_id": run_id, "target": 2}}
    stale, stale_reason = staleness(draft, item["revision"], item["config_digest"])
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
            out.update(_scored(supervisor, repository, run_id, draft))
    out["headline"] = HEADLINES[out["verdict"]]
    return out


def _scored(supervisor: Any, repository: Path, run_id: str,
            draft: Mapping[str, Any]) -> Dict[str, Any]:
    """The verdict, reasons, readings and spend of a finished run, cached by its recorded results."""
    try:
        digest, spend = _recorded(supervisor, run_id)
    except (replay.ReplayError, runs.RunError, OSError):
        digest, spend = None, None
    key = (run_id, digest) if digest is not None else None
    found = _cached(key) if key is not None else None
    if found is not None:
        return dict(found)

    def snapshot_staleness(_repository: Path, target: replay.ReplayTarget):
        if target.kind != "draft":
            return False, None
        if target.draft != draft["draft"]:
            return True, "the run measured another draft"
        return staleness(draft, target.revision, target.config_digest)

    try:
        comparison = compare.compare_runs(supervisor, Path(repository), {
            "base": (run_id, 1), "candidate": (run_id, 2)}, staleness=snapshot_staleness)
    except compare.CompareError as exc:
        return {"verdict": "unavailable", "reasons": [str(exc)], "spend_usd": spend}
    value = dict(claim(comparison), readings=_readings(comparison), spend_usd=spend)
    if key is not None:
        _remember(key, value)
    return dict(value)


def verdicts(supervisor: Any, repository: Path, root: Path, name: str) -> Dict[str, Any]:
    """Each checkpoint's latest test, scored, and every test of this draft listed newest first.
    Only the latest test of a checkpoint is compared; older ones are listed by run id."""
    draft = identity(repository, name)
    found, skipped = read_records(root)
    items = sorted((value for value in found if value["draft_id"] == draft["draft_id"]),
                   key=lambda value: (value["created_at"], value["run_id"]), reverse=True)
    checkpoints: List[Dict[str, Any]] = []
    tests = []
    for item in items:
        entry = next((each for each in checkpoints if each["revision"] == item["revision"]), None)
        if entry is None:
            checkpoints.append({"revision": item["revision"],
                                "current": item["revision"] == draft["revision"],
                                "latest": verdict(supervisor, repository, item, draft), "tests": 1})
        else:
            entry["tests"] += 1
        tests.append({"run_id": item["run_id"], "revision": item["revision"],
                      "created_at": item["created_at"], "latest": entry is None})
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
