"""Pre-register a draft test, so a run that matches its registration may say helped or worse.

A draft test is exploratory by default (`draft_tests`). Opting in, the developer registers the test
before running it: the draft's checkpoint and configuration digest, its base commit, the task set
and evaluator pack, the trials per task, the model and the change worth detecting. `register` is
the one path `citizen draft test --register` and `POST /api/configure/test/register` share.

**The engine's format and checks.** The plan is written from `docs/pre-registration-template.md`,
every section filled, as `benchmarks/preregistrations/<date>-draft-<id>.md` and committed to a
private Git repository under the supervisor's state root (`draft-tests/registry`), and it must pass
`experiment_protocol.check` there, unchanged: dated name, every required field filled, committed,
clean, and the first filled commit's text intact. The registry stands in for the repository the
engine's own runs commit to, because a draft test must never write into the installed checkout or
the draft (an edit to the draft would make the registration stale at once). A structured record
beside it (`draft-tests/registrations/<id>.json`) is linked into place once and never replaced; it
names the plan, its commit and its SHA-256, so an edit to either is found on every read.

**Refused before it is written.** SM-2 states the minimum detectable effect before the run and caps
it at 15% (docs/evidence-standard.md, item 1), so a larger effect is refused. A trial count that
cannot detect the effect is refused with the engine's minimum detectable effect and the fewest trials
that would (`draft_tests.power`, from `replay_stats.minimum_detectable_effect`).

**When it counts.** A registration is stale once the draft has another checkpoint or configuration;
a stale one stays listed and a new one is needed. A run counts as pre-registered only when its
registration is intact, was written before the run started, and the run measured exactly what it
registers (`deviations`); any difference makes the run exploratory, and its verdict carries no
direction. The native benchmark still runs the draft `--exploratory`, so a draft test never writes a
benchmark history row: the registration lets the Studio apply the decision rule, nothing more.
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from . import compare, draft_tests, packs, replay

SCHEMA_VERSION = 1
REGISTRATIONS_DIR = "registrations"
REGISTRY_DIR = "registry"
LOCK_NAME = "registry.lock"
PLAN_DIRECTORY = Path("benchmarks") / "preregistrations"
MAX_EFFECT = 0.15  # SM-2: the minimum detectable effect is at most 15%
MAX_MODEL = 256
SPEC_KEYS = frozenset(("model", "repetitions", "tasks", "pack"))
RECORD_KEYS = frozenset((
    "schema_version", "registration_id", "draft", "draft_id", "revision", "config_digest",
    "base_revision", "model", "tasks", "repetitions", "pack", "effect", "cv", "power", "plan",
    "plan_commit", "plan_sha256", "created_at"))
IDENTITY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REPOSITORY_VARIABLES = frozenset(("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                  "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                                  "GIT_NAMESPACE", "GIT_PREFIX"))
GIT_IDENTITY = ("-c", "user.name=Model Citizen Studio", "-c", "user.email=studio@localhost",
                "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false")


def _protocol() -> Any:
    return replay._engine_module("experiment_protocol")


def _git_env() -> Dict[str, str]:
    """The environment for registry Git calls, without a caller's repository variables."""
    return {key: value for key, value in os.environ.items() if key not in REPOSITORY_VARIABLES}


def _git(registry: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(registry)] + list(GIT_IDENTITY) + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=_git_env())
    if done.returncode:
        raise draft_tests.DraftTestError("the registration could not be committed",
                                         "draft_test_registration_failed")
    return done.stdout.strip()


def _private_dir(directory: int, name: str) -> int:
    """A private subdirectory of `directory`, created if absent and opened without a symlink."""
    try:
        os.mkdir(name, 0o700, dir_fd=directory)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError as exc:
        raise draft_tests.DraftTestError("the draft test records are not a private directory",
                                         "draft_test_records_unsafe") from exc
    if hasattr(os, "getuid") and os.fstat(descriptor).st_uid != os.getuid():
        os.close(descriptor)
        raise draft_tests.DraftTestError("the draft test records are not a private directory",
                                         "draft_test_records_unsafe")
    return descriptor


@contextmanager
def _locked(root: Path) -> Iterator[int]:
    """The records directory, held under the one registry lock across processes."""
    directory = draft_tests._records_fd(Path(root), create=True)
    try:
        descriptor = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                             dir_fd=directory)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _parse_spec(repository: Path, spec: Any) -> Dict[str, Any]:
    """The registered sample: model, trials per task, a sorted task set and a resolved pack."""
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        raise draft_tests.DraftTestError("a registration names exactly its model, repetitions, "
                                         "tasks and pack")
    model, repetitions, tasks, chosen = (spec["model"], spec["repetitions"], spec["tasks"],
                                         spec["pack"])
    if (not isinstance(model, str) or not model or len(model) > MAX_MODEL or "\0" in model
            or model.startswith("-")):
        raise draft_tests.DraftTestError("a registration names its model")
    if (not isinstance(repetitions, int) or isinstance(repetitions, bool)
            or not 1 <= repetitions <= draft_tests.MAX_TRIALS):
        raise draft_tests.DraftTestError("trials per task must be from 1 to %d"
                                         % draft_tests.MAX_TRIALS)
    if (not isinstance(tasks, list) or not tasks
            or any(not isinstance(task, str) or not replay.IDENTIFIER.fullmatch(task)
                   for task in tasks) or len(set(tasks)) != len(tasks)):
        raise draft_tests.DraftTestError("a registration names unique task ids")
    pack = None
    if chosen is not None:
        if not isinstance(chosen, dict) or set(chosen) != {"name", "digest"}:
            raise draft_tests.DraftTestError("pack selection must contain only name and digest")
        try:
            found = packs.select(Path(repository), chosen["name"], chosen["digest"])
        except ValueError as exc:
            raise draft_tests.DraftTestError(str(exc)) from exc
        pack = {"name": found["name"], "version": found.get("version"),
                "commit": found["commit"], "digest": found["digest"]}
    catalog = _catalog(repository, chosen)
    unknown = sorted(set(tasks) - set(catalog))
    if unknown:
        raise draft_tests.DraftTestError("unknown benchmark tasks: " + ", ".join(unknown))
    return {"model": model, "repetitions": repetitions, "tasks": sorted(tasks), "pack": pack,
            "long": sum(1 for task in tasks if catalog[task])}


def _catalog(repository: Path, chosen: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    """`{task id: long}` for the pack's tasks, or the repository's own task list."""
    try:
        if chosen is not None:
            items = packs.select(Path(repository), chosen["name"], chosen["digest"])["tasks"]
        else:
            items = replay._repository_tasks(Path(repository))
    except (ValueError, KeyError, TypeError) as exc:
        raise draft_tests.DraftTestError("the task catalog cannot be read") from exc
    return {item["id"]: item.get("long") is True for item in items}


def _percent(value: Optional[float]) -> str:
    return "undefined" if value is None else "%.1f%%" % (100 * value)


def plan_text(record: Mapping[str, Any], sample: Mapping[str, Any], today: str) -> str:
    """The plan, every section of `docs/pre-registration-template.md` filled for this draft test."""
    stats = compare._engine()
    power, pack = record["power"], record["pack"]
    manifest = ("evaluator pack `%s` version `%s` at commit `%s`, digest `%s`"
                % (pack["name"], pack["version"], pack["commit"], pack["digest"]) if pack
                else "`benchmarks/tasks.json` at the base commit `%s`" % record["base_revision"])
    k, m = len(record["tasks"]), record["repetitions"]
    return "\n".join([
        "# Draft test pre-registration: %s at %s" % (record["draft"], record["revision"][:12]),
        "",
        "Written by the Studio (`citizen draft test --register`) from",
        "`docs/pre-registration-template.md` before the run's first trial. The sections above the",
        "deviation log are frozen; the registration is stale once the draft changes.",
        "",
        "## Run",
        "",
        "- **Question:** does draft %s at checkpoint %s lower Cost-of-Pass against its base commit"
        % (record["draft"], record["revision"]),
        "  %s without lowering the pass rate by more than the margin?" % record["base_revision"],
        "- **Author role:** maintainer",
        "- **Date registered:** %s" % today,
        "- **Task manifest:** %s; tasks %s" % (manifest, ", ".join(record["tasks"])),
        "- **Arms:** base commit `%s` (control) and draft checkpoint `%s`, configuration digest `%s`"
        % (record["base_revision"], record["revision"], record["config_digest"]),
        "- **Model, CLI and effort:** `%s`, the CLI version each row stamps, the default effort"
        % record["model"],
        "- **Draft:** `%s` (%s), registration %s" % (record["draft"], record["draft_id"],
                                                     record["registration_id"]),
        "",
        "## Hypotheses",
        "",
        "- **Primary:** the draft lowers Cost-of-Pass against its base by at least %s, and does not"
        % _percent(record["effect"]),
        "  lower the pass rate by more than the non-inferiority margin δ = %g." % stats.DELTA,
        "- **Secondary:** none; the draft test judges the decision rule only.",
        "- **Exploratory:** every other measure the engine reports; none supports a claim.",
        "",
        "## Primary metric",
        "",
        "- **Metric:** Cost-of-Pass ratio, draft over base, pooled across the set: the total cost",
        "  of every attempt divided by the total number of passes, per side.",
        "- **Interval:** paired, task-clustered %g%% interval, by the engine's %s"
        % (100 * stats.CONFIDENCE, stats.METHOD),
        "  (`replay_stats.compare`) with %d resamples and seed %d." % (stats.RESAMPLES, stats.SEED),
        "- **Undefined case:** if either side passes nothing, the result is reported as a pass-rate",
        "  result only and the verdict is inconclusive.",
        "",
        "## Guardrails",
        "",
        "- **Contamination control:** the native benchmark's own controls, unchanged.",
        "- **Pass rate:** non-inferiority margin δ = %g on the paired, task-clustered pass-rate"
        % stats.DELTA,
        "  difference (draft minus base).",
        "- **Fallback rate:** none; the native benchmark pins the model it is given.",
        "- **Spend:** the per-run budget and whole-test cap confirmed when the test starts.",
        "- **Other:** none.",
        "",
        "## Sample size",
        "",
        "- **Tasks:** %d, of which %d are long multi-turn tasks." % (k, sample["long"]),
        "- **Trials per task and arm:** %d" % m,
        "- **α and power:** α %g two-sided, power %g." % (round(1 - stats.CONFIDENCE, 4),
                                                         stats.POWER),
        "- **Minimum detectable effect:** %s at %d attempts per side; the change to detect is %s."
        % (_percent(power["minimum_detectable_effect"]), power["attempts_per_side"],
           _percent(record["effect"])),
        "- **Variance source:** coefficient of variation %g (%s)." % (power["cv"],
                                                                     power["cv_source"]),
        "- **Power calculation:** `replay_stats.minimum_detectable_effect(%d, %g)` gives %s, at or"
        % (power["attempts_per_side"], power["cv"], _percent(power["minimum_detectable_effect"])),
        "  below the %s registered; it treats attempts as independent." % _percent(record["effect"]),
        "",
        "## Stopping rule",
        "",
        "- **Fixed sample:** the run stops when every task has %d trials per side, and no result is" % m,
        "  read before then.",
        "- **Early stop for harm or cost:** the spend cap confirmed at start.",
        "- **Stop condition for the claim:** the decision rule below, on the whole set.",
        "",
        "## Multiplicity",
        "",
        "- **Decision rule:** both conditions must hold, so the two tests form one joint test.",
        "- **Further confirmatory tests:** none.",
        "- **Everything else:** exploratory, labelled so, and supports no claim.",
        "",
        "## Decision rule",
        "",
        "The hypothesis is supported only when both hold:",
        "",
        "- the paired, task-clustered 95% interval on the Cost-of-Pass ratio lies wholly below 1.0;",
        "- the lower bound of the paired, task-clustered 95% interval on the pass-rate difference",
        "  (draft minus base) is above −δ.",
        "",
        "Its exact mirror reads worse; anything else is inconclusive.",
        "",
        "## Exclusions",
        "",
        "- **Analysis population:** every assigned trial, crashes, timeouts and fallbacks included.",
        "- **Pre-stated exclusions:** none.",
        "",
        "## Deviation log",
        "",
        "- %s: none yet" % today,
        "",
    ])


def _write_once(directory: int, name: str, content: bytes) -> None:
    """Write `name` whole or not at all; an existing file is never replaced."""
    temporary = ".%s.%s.tmp" % (name, uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400,
                         dir_fd=directory)
    try:
        try:
            if os.write(descriptor, content) != len(content):
                raise OSError("the registration was written short")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        os.unlink(temporary, dir_fd=directory)
    os.fsync(directory)


def _registry(root: Path) -> Path:
    return Path(root) / draft_tests.RECORDS_DIR / REGISTRY_DIR


def register(root: Path, repository: Path, name: str, spec: Any, effect: Any,
             cv: Any) -> Dict[str, Any]:
    """Register a test of the draft's current checkpoint, or refuse it, before any run starts."""
    plan = draft_tests.parse_plan(effect, cv)
    if plan["effect"] > MAX_EFFECT:
        raise draft_tests.DraftTestError(
            "SM-2 caps the minimum detectable effect at %s; state a change of at most that"
            % _percent(MAX_EFFECT), "draft_test_effect_too_large")
    draft = draft_tests.identity(repository, name)
    draft_tests._refuse_unchanged(draft["base_revision"], draft["revision"])
    sample = _parse_spec(repository, spec)
    power = draft_tests.power(repository, len(sample["tasks"]), sample["repetitions"], plan)
    if not power["enough"]:
        raise draft_tests.DraftTestError(
            "registration refused: " + draft_tests.power_line(power), "draft_test_underpowered")
    registration_id = str(uuid.uuid4())
    today = datetime.date.today().isoformat()
    record = {"schema_version": SCHEMA_VERSION, "registration_id": registration_id,
              "draft": draft["draft"], "draft_id": draft["draft_id"],
              "revision": draft["revision"], "config_digest": draft["config_digest"],
              "base_revision": draft["base_revision"], "model": sample["model"],
              "tasks": sample["tasks"], "repetitions": sample["repetitions"],
              "pack": sample["pack"], "effect": plan["effect"], "cv": plan["cv"], "power": power}
    relative = PLAN_DIRECTORY / ("%s-draft-%s.md" % (today, registration_id))
    text = plan_text(record, sample, today)
    with _locked(root) as directory:
        registry = _registry(root)
        holder = _private_dir(directory, REGISTRY_DIR)
        os.close(holder)
        if not (registry / ".git").is_dir():
            _git(registry, "init", "-q", "--template=")
        (registry / PLAN_DIRECTORY).mkdir(mode=0o700, parents=True, exist_ok=True)
        plans = os.open(str(registry / PLAN_DIRECTORY), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write_once(plans, relative.name, text.encode("utf-8"))
        finally:
            os.close(plans)
        _git(registry, "add", "--", relative.as_posix())
        _git(registry, "commit", "-q", "-m", "Register draft test %s" % registration_id,
             "--", relative.as_posix())
        errors, checked = _protocol().check(str(relative), str(registry),
                                            today=datetime.date.fromisoformat(today),
                                            cwd=str(registry))
        if errors or not checked:
            raise draft_tests.DraftTestError("the registration failed the pre-registration check: "
                                             + "; ".join(errors), "draft_test_registration_failed")
        record.update(plan=relative.as_posix(), plan_commit=checked["pre_registration_commit"],
                      plan_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        records = _private_dir(directory, REGISTRATIONS_DIR)
        try:
            _write_once(records, registration_id + ".json",
                        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(records)
    return dict(record, stale=False, stale_reason=None, problems=[])


def _read_json(directory: int, name: str) -> Optional[Dict[str, Any]]:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > draft_tests.MAX_RECORD_BYTES:
            return None
        value = json.loads(os.read(descriptor, draft_tests.MAX_RECORD_BYTES + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        os.close(descriptor)
    if (not isinstance(value, dict) or set(value) != RECORD_KEYS
            or value["schema_version"] != SCHEMA_VERSION
            or name != "%s.json" % value["registration_id"]):
        return None
    return value


def problems(root: Path, record: Mapping[str, Any]) -> List[str]:
    """Why a registration no longer stands as written; empty when its plan is intact."""
    registry = _registry(root)
    errors, checked = _protocol().check(str(record["plan"]), str(registry), cwd=str(registry))
    out = list(errors)
    if checked and checked["pre_registration_commit"] != record["plan_commit"]:
        out.append("the plan's registered commit changed")
    path = registry / str(record["plan"])
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if not path.is_symlink() else None
    except OSError:
        digest = None
    if digest != record["plan_sha256"]:
        out.append("the plan differs from the text registered")
    return out


def load(root: Path, registration_id: Any) -> Dict[str, Any]:
    """One registration and its problems; DraftTestError when it does not exist."""
    if not isinstance(registration_id, str) or not IDENTITY.fullmatch(registration_id):
        raise draft_tests.DraftTestError("the registration id is invalid")
    directory = draft_tests._records_fd(Path(root), create=False)
    value = None
    if directory is not None:
        try:
            try:
                records = os.open(REGISTRATIONS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=directory)
            except OSError:
                records = None
            if records is not None:
                try:
                    value = _read_json(records, registration_id + ".json")
                finally:
                    os.close(records)
        finally:
            os.close(directory)
    if value is None:
        raise draft_tests.DraftTestError("no such registration", "draft_test_registration_not_found")
    return dict(value, problems=problems(root, value))


def listed(root: Path, draft: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Every registration of this draft, newest first, each marked stale or not; none removed."""
    directory = draft_tests._records_fd(Path(root), create=False)
    if directory is None:
        return []
    try:
        try:
            records = os.open(REGISTRATIONS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=directory)
        except OSError:
            return []
        try:
            values = [_read_json(records, name) for name in sorted(os.listdir(records))
                      if name.endswith(".json") and not name.startswith(".")]
        finally:
            os.close(records)
    finally:
        os.close(directory)
    out = []
    for value in values:
        if value is None or value["draft_id"] != draft["draft_id"]:
            continue
        stale, reason = draft_tests.staleness(draft, value["revision"], value["config_digest"])
        out.append(dict(value, stale=stale, stale_reason=reason, problems=problems(root, value)))
    return sorted(out, key=lambda item: (item["created_at"], item["registration_id"]), reverse=True)


def measured(base_revision: str, revision: str, config_digest: Optional[str], tasks: Any,
             trials: int, model: str, pack_digest: Optional[str]) -> Dict[str, Any]:
    """What a run measured, in the shape `deviations` compares against a registration."""
    return {"base_revision": base_revision, "revision": revision, "config_digest": config_digest,
            "tasks": sorted(tasks), "trials": trials, "model": model, "pack_digest": pack_digest}


def deviations(record: Mapping[str, Any], run: Mapping[str, Any]) -> List[str]:
    """Every way a run departs from its registration; empty only on an exact match."""
    registered = measured(record["base_revision"], record["revision"], record["config_digest"],
                          record["tasks"], record["repetitions"], record["model"],
                          record["pack"]["digest"] if record["pack"] else None)
    labels = (("base_revision", "base commit"), ("revision", "draft commit"),
              ("config_digest", "draft configuration"), ("tasks", "task set"),
              ("trials", "trials per task"), ("model", "model"), ("pack_digest", "evaluator pack"))
    return ["the run's %s differs from the registration's" % label
            for key, label in labels if registered[key] != run[key]]


def from_request(selected: replay.ReplayRequest) -> Dict[str, Any]:
    base, candidate = selected.targets
    return measured(base.revision, candidate.revision, candidate.config_digest, selected.tasks,
                    selected.repetitions, selected.model,
                    selected.pack["digest"] if selected.pack else None)


def from_comparison(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    base, candidate = comparison["base"], comparison["candidate"]
    return measured(base["ref"].get("revision"), candidate["ref"].get("revision"),
                    candidate["ref"].get("config_digest"), candidate["tasks"],
                    candidate["trials"], candidate["model"], candidate["pack_digest"])


def _instant(value: Any) -> Optional[datetime.datetime]:
    """An aware timestamp from an ISO string, with "Z" read as UTC; None when unreadable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z")
                                                 else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def evidence(root: Path, registration_id: Optional[str], recorded: List[str],
             comparison: Mapping[str, Any], started_at: Optional[str]) -> Tuple[str, List[str]]:
    """`(label, reasons)` for a finished run: pre-registered only on an intact registration written
    before the run started that the run matches exactly; otherwise exploratory, with every reason."""
    if registration_id is None:
        return replay.EXPLORATORY, []
    try:
        record = load(root, registration_id)
    except draft_tests.DraftTestError as exc:
        return replay.EXPLORATORY, ["the registration cannot be read: %s" % exc]
    reasons = list(recorded) + ["the registration is not intact: %s" % item
                                for item in record["problems"]]
    registered, started = _instant(record["created_at"]), _instant(started_at)
    if started_at is not None and (registered is None or started is None or registered > started):
        reasons.append("the registration was written after the run started")
    if comparison.get("base") and comparison.get("candidate"):
        found = deviations(record, from_comparison(comparison))
        reasons.extend(item for item in found if item not in reasons)
    return (replay.EXPLORATORY if reasons else replay.PREREGISTERED), reasons


def render(registration: Mapping[str, Any]) -> List[str]:
    """`citizen draft test --register` text."""
    pack = registration["pack"]
    return [
        "registered %s for draft %s at %s, based on %s" % (
            registration["registration_id"], registration["draft"],
            registration["revision"][:12], registration["base_revision"][:12]),
        "  %d task(s), %d trial(s) per task, model %s, %s" % (
            len(registration["tasks"]), registration["repetitions"], registration["model"],
            "pack %s@%s" % (pack["name"], pack["digest"][:12]) if pack else "repository tasks"),
        "  " + draft_tests.power_line(registration["power"]),
        "  plan: %s at %s" % (registration["plan"], registration["plan_commit"][:12]),
    ]
