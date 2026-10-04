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
exploratory by default and its verdict never says helped; the readings are shown without direction.
A test pre-registered before it ran (`draft_registration`) that matches its registration exactly
counts both sides as pre-registered for the direction check; the engine's own exploratory marks and
the run-window note still withhold a direction, and any deviation leaves the test exploratory.

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
and checkpoint. Staleness is read from one `drafts.read_snapshot` of the draft per call. That read is
lock-free while no writer is committing, and waits at most `SNAPSHOT_LOCK_TIMEOUT` seconds for the
writer lock otherwise (a save holds it through its check, up to 600 s), so a read during a save
answers `draft_unavailable` at once instead of hanging. Only each checkpoint's latest test is
compared, and that verdict is cached on disk (`draft-tests/cache/`) by run id and a digest of the
run's recorded results, so the route and `citizen draft test` share it.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import stat
import uuid
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
EXPLORATORY_NOTE = ("A draft test is exploratory unless it is pre-registered before it runs: an "
                    "unregistered result is never cited as evidence and the Studio does not say "
                    "whether the draft helped. A run that matches its registration exactly may.")
HELPED, WORSE, INCONCLUSIVE, EXPLORATORY = "helped", "worse", "inconclusive", "exploratory"
INDEPENDENCE_NOTE = ("The figure treats attempts as independent; the verdict's intervals resample "
                     "tasks, so few tasks can still read inconclusive.")
CACHE_DIR = "cache"
CACHE_KEYS = frozenset(("verdict", "reasons", "readings", "spend_usd", "evidence", "deviations"))
SNAPSHOT_LOCK_TIMEOUT = 2.0  # seconds a read waits for a save's writer lock before giving up
MAX_RESULT_BYTES = 256 * 1024 * 1024  # a native results file hashed for the cache key


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
            "revision": state["revision"], "config_digest": targets._config_digest(dict(config))},
            lock_timeout=SNAPSHOT_LOCK_TIMEOUT)
    except drafts.DraftError as exc:
        if exc.code == "busy":
            raise DraftTestError("the draft is being saved; try again in a moment",
                                 "draft_unavailable") from exc
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
           planned: Mapping[str, Any], registration: Optional[str] = None,
           deviations: Optional[List[str]] = None) -> Dict[str, Any]:
    """Write a started test's record once, whole or not at all; an existing one is never replaced.
    `registration` names the pre-registration the test was started under and `deviations` every
    way the started request already departs from it."""
    if str(uuid.UUID(run_id)) != run_id:
        raise DraftTestError("the run id is invalid")
    value = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "draft": draft["draft"],
             "draft_id": draft["draft_id"], "revision": selected.targets[1].revision,
             "config_digest": selected.targets[1].config_digest,
             "base_revision": selected.targets[0].revision, "power": dict(planned),
             "registration": registration, "deviations": list(deviations or []),
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
# A record from before pre-registration lacks these two and reads as unregistered.
REGISTRATION_KEYS = frozenset(("registration", "deviations"))


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
    if not isinstance(value, dict) or set(value) not in (RECORD_KEYS, RECORD_KEYS | REGISTRATION_KEYS) \
            or value["schema_version"] != SCHEMA_VERSION or name != "%s.json" % value["run_id"]:
        return None
    value.setdefault("registration", None)
    value.setdefault("deviations", [])
    if (value["registration"] is not None and not isinstance(value["registration"], str)
            or not isinstance(value["deviations"], list)
            or not all(isinstance(item, str) for item in value["deviations"])):
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


def _hash_file(digest: Any, path: str, run_root: str) -> None:
    """Hash one recorded file confined to the run directory, opened without following a symlink
    and refused above `MAX_RESULT_BYTES`; an absent file hashes as absent."""
    resolved = os.path.realpath(os.path.dirname(path))
    if not os.path.isabs(path) or not (resolved + os.sep).startswith(run_root + os.sep):
        raise replay.ReplayError("a recorded result lies outside its run")
    digest.update(path.encode("utf-8") + b"\0")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        digest.update(b"absent")
        return
    except OSError as exc:
        raise replay.ReplayError("a recorded result is unsafe to read") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
            raise replay.ReplayError("a recorded result is unsafe to read")
        remaining = info.st_size
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)


def _recorded(supervisor: Any, run_id: str) -> Tuple[str, float]:
    """`(digest, spend)` of a finished run's recorded results: the summary, every native result
    file it lists and the engine analysis beside it. A rerun or rewrite changes the digest."""
    run_root = supervisor._run_path(run_id).parent
    output = run_root / "replay"
    summary = replay.read_summary(output / replay.SUMMARY_NAME)
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode("utf-8"))
    confined = os.path.realpath(str(run_root))
    for path in sorted(summary["result_files"]) + [str(output / replay.ANALYSIS_NAME)]:
        _hash_file(digest, path, confined)
    return digest.hexdigest(), summary["spend_usd"]


def _cache_fd(root: Path, create: bool) -> Optional[int]:
    directory = _records_fd(Path(root), create=create)
    if directory is None:
        return None
    try:
        if create:
            try:
                os.mkdir(CACHE_DIR, 0o700, dir_fd=directory)
            except FileExistsError:
                pass
        try:
            return os.open(CACHE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DraftTestError("the draft test cache is not a private directory",
                                 "draft_test_records_unsafe") from exc
    finally:
        os.close(directory)


def _cache_name(run_id: str, digest: str) -> str:
    return "%s.%s.json" % (run_id, digest)


def _cached(root: Path, run_id: str, digest: str) -> Optional[Dict[str, Any]]:
    """A cached verdict for exactly these recorded results, or None (a bad entry is ignored)."""
    directory = _cache_fd(root, create=False)
    if directory is None:
        return None
    try:
        descriptor = os.open(_cache_name(run_id, digest), os.O_RDONLY | os.O_NOFOLLOW,
                             dir_fd=directory)
    except OSError:
        return None
    finally:
        os.close(directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            return None
        value = json.loads(os.read(descriptor, MAX_RECORD_BYTES + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        os.close(descriptor)
    if (not isinstance(value, dict) or set(value) != CACHE_KEYS
            or value.get("verdict") not in HEADLINES or not isinstance(value.get("reasons"), list)
            or not isinstance(value.get("readings"), dict)):
        return None
    return value


def _remember(root: Path, run_id: str, digest: str, value: Mapping[str, Any]) -> None:
    """Cache a verdict, replacing older entries for the run; a failure only costs a recompute."""
    try:
        directory = _cache_fd(root, create=True)
    except (OSError, DraftTestError):
        return
    if directory is None:
        return
    name = _cache_name(run_id, digest)
    temporary = ".%s.%s.tmp" % (run_id, uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            os.write(descriptor, json.dumps(dict(value), sort_keys=True).encode("utf-8"))
        finally:
            os.close(descriptor)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        for other in os.listdir(directory):
            if other.startswith(run_id + ".") and other != name:
                os.unlink(other, dir_fd=directory)
    except OSError:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
    finally:
        os.close(directory)


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
            draft: Mapping[str, Any], root: Path,
            registrations: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One test's state, verdict, staleness, spend and the comparison it links to.
    `registrations` caches the registrations this request has loaded."""
    run_id = item["run_id"]
    sides = {"base": {"run_id": run_id, "target": 1}, "candidate": {"run_id": run_id, "target": 2}}
    stale, stale_reason = staleness(draft, item["revision"], item["config_digest"])
    out = {"run_id": run_id, "revision": item["revision"], "base_revision": item["base_revision"],
           "created_at": item["created_at"], "power": item["power"],
           "power_line": _recorded_power_line(item["power"]), "stale": stale,
           "stale_reason": stale_reason, "stale_copy": STALE_COPY if stale else None,
           "comparison": dict(sides, command="citizen runs compare %s:1 %s:2" % (run_id, run_id)),
           "status": None, "verdict": "unavailable", "reasons": [], "readings": {},
           "spend_usd": None, "registration": item.get("registration"),
           "evidence": replay.EXPLORATORY, "deviations": list(item.get("deviations") or [])}
    try:
        shown = supervisor.show(run_id)
        out["status"] = shown.get("status")
    except (runs.RunError, OSError):
        out["reasons"] = ["the run is no longer known"]
    else:
        if out["status"] not in runs.TERMINAL:
            out["verdict"] = "running"
        else:
            started = shown.get("created_at") or item["created_at"]
            out.update(_scored(supervisor, repository, item, draft, root, started,
                               registrations))
    out["headline"] = HEADLINES[out["verdict"]]
    return out


def _registration_key(root: Path, item: Mapping[str, Any], started_at: Optional[str],
                      registrations: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """What the cached verdict depends on beyond the results: the registration as it reads now."""
    if item.get("registration") is None:
        return "unregistered"
    from . import draft_registration
    try:
        found = draft_registration.load(root, item["registration"], registrations)
    except DraftTestError as exc:
        return "unreadable:" + str(exc)
    return json.dumps([found["plan_sha256"], found["created_at"], found["problems"],
                       found["used_by"], found["committed"], item.get("deviations") or [],
                       started_at], sort_keys=True)


def registered_claim(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """`claim` for a run that matches its pre-registration exactly: both sides count as
    pre-registered, while the engine's exploratory marks and the run window still withhold."""
    if not comparison["comparable"] or comparison["error"] is not None:
        return claim(comparison)
    sides = [dict(comparison[name], evidence=replay.PREREGISTERED) for name in compare.SIDES]
    return claim(dict(comparison, direction_withheld=compare.direction_withheld(
        sides[0], sides[1], comparison["result"])))


def _scored(supervisor: Any, repository: Path, item: Mapping[str, Any],
            draft: Mapping[str, Any], root: Path, started_at: Optional[str],
            registrations: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The verdict, reasons, readings and spend of a finished run, cached by its recorded results
    and the state of its registration."""
    run_id = item["run_id"]
    try:
        digest, spend = _recorded(supervisor, run_id)
    except (replay.ReplayError, runs.RunError, OSError):
        digest, spend = None, None
    if digest is not None:
        digest = hashlib.sha256((digest + "\0" + _registration_key(root, item, started_at, registrations)).encode(
            "utf-8")).hexdigest()
    found = _cached(root, run_id, digest) if digest is not None else None
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
    from . import draft_registration
    label, deviations = draft_registration.evidence(
        root, item.get("registration"), list(item.get("deviations") or []), item["run_id"],
        comparison, started_at, registrations)
    judged = registered_claim(comparison) if label == replay.PREREGISTERED else claim(comparison)
    if label != replay.PREREGISTERED and judged["verdict"] in (HELPED, WORSE, INCONCLUSIVE,
                                                               EXPLORATORY):
        # Unregistered or deviating: no direction, whatever the intervals show.
        judged = {"verdict": EXPLORATORY, "reasons": deviations + [
            reason for reason in judged["reasons"] if reason not in deviations]}
    value = dict(judged, readings=_readings(comparison), spend_usd=spend, evidence=label,
                 deviations=deviations)
    if digest is not None:
        _remember(root, run_id, digest, value)
    return dict(value)


def verdicts(supervisor: Any, repository: Path, root: Path, name: str) -> Dict[str, Any]:
    """Each checkpoint's latest test, scored, and every test of this draft listed newest first.
    Only the latest test of a checkpoint is compared; older ones are listed by run id."""
    draft = identity(repository, name)
    found, skipped = read_records(root)
    registrations: Dict[str, Dict[str, Any]] = {}  # each registration loaded once per request
    items = sorted((value for value in found if value["draft_id"] == draft["draft_id"]),
                   key=lambda value: (value["created_at"], value["run_id"]), reverse=True)
    checkpoints: List[Dict[str, Any]] = []
    tests = []
    for item in items:
        entry = next((each for each in checkpoints if each["revision"] == item["revision"]), None)
        if entry is None:
            checkpoints.append({"revision": item["revision"],
                                "current": item["revision"] == draft["revision"],
                                "latest": verdict(supervisor, repository, item, draft, root,
                                                  registrations),
                                "tests": 1})
        else:
            entry["tests"] += 1
        tests.append({"run_id": item["run_id"], "revision": item["revision"],
                      "created_at": item["created_at"], "latest": entry is None})
    from . import draft_registration
    return {"schema_version": SCHEMA_VERSION, "draft": draft["draft"],
            "revision": draft["revision"], "base_revision": draft["base_revision"],
            "evidence_note": EXPLORATORY_NOTE, "tests": tests, "checkpoints": checkpoints,
            "registrations": draft_registration.listed(root, draft, registrations),
            "unreadable_records": skipped}


def start_registration(root: Path, repository: Path, draft: Mapping[str, Any],
                       selected: replay.ReplayRequest,
                       registration: Any) -> Tuple[Optional[str], List[str]]:
    """`(registration id, deviations)` for a test about to start: None and nothing when it is not
    registered. A stale or already used registration is refused (a new one is required); any other
    departure from it starts the test exploratory, with every deviation recorded
    (`draft_registration.start_check`). The caller claims the registration once the run exists."""
    if registration is None:
        return None, []
    from . import draft_registration
    return draft_registration.start_check(root, repository, draft, selected, registration)


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
        lines.append("    evidence: %s%s" % (test["evidence"], " under registration %s"
                                             % test["registration"] if test["registration"] else ""))
        if test["stale"]:
            lines.append("    " + STALE_COPY)
        for reason in test["reasons"]:
            lines.append("    " + reason)
        lines.append("    " + test["power_line"])
        lines.append("    comparison: " + test["comparison"]["command"])
    for entry in payload.get("registrations", []):
        lines.append("  registration %s at %s, %s: %d task(s), %d trial(s), %s" % (
            entry["registration_id"], entry["revision"][:12],
            "stale" if entry["stale"] else "current", len(entry["tasks"]), entry["repetitions"],
            entry["model"]))
        if entry.get("used_by"):
            lines.append("    backs run %s" % entry["used_by"])
        for problem in entry["problems"]:
            lines.append("    not intact: " + problem)
    if payload["unreadable_records"]:
        lines.append("  %d unreadable test record(s) skipped" % payload["unreadable_records"])
    return lines
