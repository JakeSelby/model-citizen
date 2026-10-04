"""Pre-register a draft test, so the one run that matches its registration may say helped or worse.

A draft test is exploratory by default (`draft_tests`). Opting in, the developer registers the test
before running it: the draft's checkpoint and configuration digest, its base commit, the whole task
set of its evaluator pack or of the repository, the trials per task, the model and the change worth
detecting. `register` is the one path `citizen draft test --register` and
`POST /api/configure/test/register` share.

**The engine's format and checks.** The plan is `docs/pre-registration-template.md` itself, read
section by section with `experiment_protocol`'s readers: every field this module states replaces the
template's, every other field keeps the template's text, and a field still holding a `<...>`
placeholder fails the registration, so a template change is never silently skipped. The plan is
written as `benchmarks/preregistrations/<date>-draft-<id>.md`, committed to a private Git repository
under the supervisor's state root (`draft-tests/registry`), and must pass `experiment_protocol.check`
there, unchanged: dated name, every required field filled, committed, clean, the first filled
commit's text intact. The registry stands in for the repository the engine's own runs commit to,
because a draft test must never write into the installed checkout or the draft (an edit to the draft
would make the registration stale at once). Every value a run is compared against is read back from
the committed plan (`committed`), never from the record JSON beside it, which only indexes it.

**Refused before it is written.** SM-2 states the minimum detectable effect before the run and caps
it at 15% (docs/evidence-standard.md, item 1), so a larger effect is refused. Power is planned from
the repository's declared variance (`benchmarks/ablations.json`), never a coefficient of variation
the caller states; with none declared, registration is refused with the way to declare one. A task
subset is refused: as for a release (`replay.label_evidence`), only the
whole set may be registered. A trial count that cannot detect the effect is refused with the
engine's minimum detectable effect and the fewest trials that would (`draft_tests.power`).

**When it counts.** A registration is single-use: a start claims it before the run is admitted
with a file created exclusively (`<id>.used`); a claim that exists or cannot be written refuses the
start, a run that is then not admitted releases it, and an admitted run is recorded beside it
(`<id>.run`). At verdict time only that recorded run may count. A registration is stale once the draft has another checkpoint or
configuration; a stale one stays listed and a new one is needed. A run counts as pre-registered only
when its registration is intact, was written before the run started, was claimed by that run, and
the run measured exactly what the committed plan registers (`deviations`); any difference makes it
exploratory, and its verdict carries no direction. The native benchmark still runs the draft
`--exploratory`, so a draft test never writes a benchmark history row: the registration lets the
Studio apply the decision rule, nothing more.
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
USED_SUFFIX = ".used"
RUN_SUFFIX = ".run"
PENDING = "pending"
PLAN_DIRECTORY = Path("benchmarks") / "preregistrations"
TEMPLATE = Path(__file__).resolve().parents[3] / "docs" / "pre-registration-template.md"
TASKS_FILE = Path("benchmarks") / "tasks.json"
MAX_EFFECT = 0.15  # SM-2: the minimum detectable effect is at most 15%
MAX_MODEL = 256
SPEC_KEYS = frozenset(("model", "repetitions", "tasks", "pack"))
RECORD_KEYS = frozenset((
    "schema_version", "registration_id", "draft", "draft_id", "revision", "config_digest",
    "base_revision", "model", "tasks", "repetitions", "pack", "manifest_digest", "effect", "cv",
    "power", "plan", "plan_commit", "plan_sha256", "created_at"))
# What a run is compared against: the key, the field in the plan's Run section, and its label.
COMPARED = (("base_revision", "Base commit", "base commit"),
            ("revision", "Draft commit", "draft commit"),
            ("config_digest", "Configuration digest", "draft configuration"),
            ("tasks", "Task set", "task set"),
            ("model", "Model", "model"),
            ("pack_digest", "Evaluator pack digest", "evaluator pack"))
TRIALS_FIELD = ("Sample size", "Trials per task and arm")
NONE = "none"
IDENTITY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REPOSITORY_VARIABLES = frozenset(("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                  "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                                  "GIT_NAMESPACE", "GIT_PREFIX"))
GIT_IDENTITY = ("-c", "user.name=Model Citizen Studio", "-c", "user.email=studio@localhost",
                "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false")


def _git_env() -> Dict[str, str]:
    """The environment for registry Git calls, without a caller's repository variables."""
    return {key: value for key, value in os.environ.items() if key not in REPOSITORY_VARIABLES}


_PROTOCOL: Optional[Any] = None


def _protocol() -> Any:
    """`experiment_protocol`, loaded privately once, its Git calls run without a caller's repository
    variables so a `GIT_DIR` in the environment cannot point the check at another repository."""
    global _PROTOCOL
    if _PROTOCOL is not None:
        return _PROTOCOL
    module = replay._engine_module("experiment_protocol")

    def scrubbed(root: Any, *args: str) -> Tuple[int, str]:
        done = subprocess.run(["git", "-C", str(root)] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, env=_git_env())
        return done.returncode, done.stdout.strip()

    module._git = scrubbed
    _PROTOCOL = module
    return module


def _git(registry: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(registry)] + list(GIT_IDENTITY) + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=_git_env())
    if done.returncode:
        raise draft_tests.DraftTestError("the registration could not be committed",
                                         "draft_test_registration_failed")
    return done.stdout


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


def manifest(repository: Path, pack: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Where the task catalog is read from and the digest that pins it: the pack's own digest, or
    the SHA-256 of the installed checkout's task list as it reads now."""
    if pack is not None:
        return {"source": "evaluator pack `%s` version `%s` at commit `%s`, digest `%s`"
                          % (pack["name"], pack["version"], pack["commit"], pack["digest"]),
                "digest": pack["digest"]}
    try:
        content = (Path(repository) / TASKS_FILE).read_bytes()
    except OSError as exc:
        raise draft_tests.DraftTestError("the task catalog cannot be read") from exc
    digest = hashlib.sha256(content).hexdigest()
    return {"source": "the installed checkout's `%s` as read at registration, not the base "
                      "commit's, SHA-256 `%s`" % (TASKS_FILE.as_posix(), digest),
            "digest": digest}


def _parse_spec(repository: Path, spec: Any) -> Dict[str, Any]:
    """The registered sample: model, trials per task, the whole task set and a resolved pack."""
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        raise draft_tests.DraftTestError("a registration names exactly its model, repetitions, "
                                         "tasks and pack")
    model, repetitions, tasks, chosen = (spec["model"], spec["repetitions"], spec["tasks"],
                                         spec["pack"])
    model = model.strip() if isinstance(model, str) else model
    if (not isinstance(model, str) or not model or len(model) > MAX_MODEL or "\0" in model
            or model.startswith("-") or "\n" in model or "`" in model):
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
    if set(tasks) != set(catalog):
        raise draft_tests.DraftTestError(
            "only the whole task set can be registered, as for a release; this leaves out "
            + ", ".join(sorted(set(catalog) - set(tasks))), "draft_test_registration_subset")
    return {"model": model, "repetitions": repetitions, "tasks": sorted(tasks), "pack": pack,
            "long": sum(1 for task in tasks if catalog[task]),
            "manifest": manifest(repository, pack)}


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


def _fill(template: str, fields: Mapping[str, Mapping[str, str]],
          rewrites: Mapping[str, Mapping[str, str]],
          extra: Mapping[str, List[Tuple[str, str]]]) -> str:
    """The template's sections, in order, below its `---` rule, its own text kept: each field in
    `fields` replaces the template's (continuation lines included), each line in `rewrites` (by
    section, the template's exact line) is replaced by its rewrite, and `extra` fields follow a
    section's own. A placeholder left anywhere fails, as does an override or rewrite naming a
    section, field or line the template lacks."""
    protocol = _protocol()
    lines = template.split("\n")
    try:
        lines = lines[lines.index("---") + 1:]
    except ValueError as exc:
        raise draft_tests.DraftTestError("the pre-registration template has no rule above its "
                                         "sections", "draft_test_registration_failed") from exc
    known = protocol.sections("\n".join(lines))
    for section, values in fields.items():
        present = protocol.fields(known.get(section, ""))
        missing = [name for name in values if name not in present]
        if section not in known or missing:
            raise draft_tests.DraftTestError(
                "the pre-registration template has no %s field %s" % (section, missing),
                "draft_test_registration_failed")
    out: List[str] = []
    section, replacing = None, False
    unused = {(name, line) for name, values in rewrites.items() for line in values}

    def close() -> None:
        if section in extra:
            while out and not out[-1].strip():
                out.pop()
            out.extend("- **%s:** %s" % pair for pair in extra[section])
            out.append("")

    for line in lines:
        if line.startswith("## "):
            close()
            section, replacing = line[3:].strip(), False
            out.append(line)
            continue
        if line in rewrites.get(section, {}):
            unused.discard((section, line))
            out.append(rewrites[section][line])
            replacing = False
            continue
        match = protocol.FIELD.match(line)
        if match:
            name = match.group(1).strip()
            replacing = name in fields.get(section, {})
            if replacing:
                out.append("- **%s:** %s" % (name, fields[section][name]))
                continue
        elif replacing and line.startswith("  ") and line.strip():
            continue
        else:
            replacing = False
        if protocol.PLACEHOLDER.search(line):
            raise draft_tests.DraftTestError(
                "the pre-registration template's %s section has an unfilled line: %s"
                % (section, line.strip()), "draft_test_registration_failed")
        out.append(line)
    close()
    if unused:
        raise draft_tests.DraftTestError(
            "the pre-registration template no longer has the lines %s" % sorted(unused),
            "draft_test_registration_failed")
    return "\n".join(out).strip("\n") + "\n"


def plan_text(record: Mapping[str, Any], sample: Mapping[str, Any], today: str,
              template: Optional[str] = None) -> str:
    """The plan: `docs/pre-registration-template.md` with this draft test's values filled in."""
    stats = compare._engine()
    power, pack = record["power"], record["pack"]
    k, m = len(record["tasks"]), record["repetitions"]
    effect, delta = _percent(record["effect"]), "%g" % stats.DELTA
    fields = {
        "Run": {
            "Question": "does draft %s at checkpoint %s lower Cost-of-Pass against its base "
                        "commit %s without lowering the pass rate by more than the margin?"
                        % (record["draft"], record["revision"], record["base_revision"]),
            "Author role": "maintainer",
            "Date registered": today,
            "Task manifest": "%s; set: the whole set" % sample["manifest"]["source"],
            "Arms": "base commit `%s` (control) and draft checkpoint `%s`"
                    % (record["base_revision"], record["revision"]),
            "Model, CLI and effort": "`%s`, the CLI version each row stamps, the default effort"
                                     % record["model"],
        },
        "Hypotheses": {
            "Primary": "the draft lowers Cost-of-Pass against its base by at least %s, and does "
                       "not lower the pass rate by more than the non-inferiority margin δ = %s."
                       % (effect, delta),
            "Secondary": "none; the draft test judges the decision rule only.",
            "Exploratory": "every other measure the engine reports; none supports a claim.",
        },
        "Primary metric": {
            "Metric": "Cost-of-Pass ratio, draft over base, pooled across the set: the total cost "
                      "of every attempt divided by the total number of passes, per side.",
            "Interval": "paired, task-clustered %g%% interval, by the engine's %s "
                        "(`replay_stats.compare`) with %d resamples and seed %d."
                        % (100 * stats.CONFIDENCE, stats.METHOD, stats.RESAMPLES, stats.SEED),
        },
        "Guardrails": {
            "Pass rate": "non-inferiority margin δ = %s on the paired, task-clustered pass-rate "
                         "difference (draft minus base)." % delta,
            "Fallback rate": "none; the native benchmark pins the model it is given.",
            "Spend": "the per-run budget and whole-test cap confirmed when the test starts.",
            "Other": "none.",
        },
        "Sample size": {
            "Tasks": "%d, of which %d are long multi-turn tasks." % (k, sample["long"]),
            "Trials per task and arm": "%d" % m,
            "Claim power": "not computed: a draft test makes no long-task saving claim, only the "
                           "decision rule below.",
            "Minimum detectable effect": "%s at %d attempts per side; the change to detect is %s."
                                         % (_percent(power["minimum_detectable_effect"]),
                                            power["attempts_per_side"], effect),
            "Variance source": "coefficient of variation %g (%s)." % (power["cv"],
                                                                     power["cv_source"]),
            "Power calculation": "`replay_stats.minimum_detectable_effect(%d, %g)` gives %s, at "
                                 "or below the %s registered; it treats attempts as independent."
                                 % (power["attempts_per_side"], power["cv"],
                                    _percent(power["minimum_detectable_effect"]), effect),
        },
        "Stopping rule": {
            "Fixed sample": "the run stops when every task has %d trials per side, and no result "
                            "is read before then. The registration backs one run only." % m,
            "Early stop for harm or cost": "the spend cap confirmed at start.",
            "Stop condition for the claim": "the decision rule below, on the whole set.",
        },
        "Multiplicity": {"Further confirmatory tests": "none."},
        "Exclusions": {"Pre-stated exclusions": "none."},
    }
    # The template's own lines, rewritten only where its arms are harness and bare.
    rewrites = {
        "Decision rule": {
            "- the lower bound of the paired, task-clustered 95% interval on the pass-rate "
            "difference (harness": "- the lower bound of the paired, task-clustered 95% interval "
                                   "on the pass-rate difference (draft",
            "  minus bare) is above −δ.": "  minus base) is above −δ.",
            "The result is published with its intervals, whatever it shows.":
                "The result is published with its intervals, whatever it shows. Its exact mirror "
                "reads worse; anything else is inconclusive.",
        },
        "Deviation log": {"- <YYYY-MM-DD: none yet>": "- %s: none yet" % today},
    }
    extra = {"Run": [
        ("Registration", record["registration_id"]),
        ("Draft", "`%s` (%s)" % (record["draft"], record["draft_id"])),
        ("Draft commit", record["revision"]),
        ("Base commit", record["base_revision"]),
        ("Configuration digest", record["config_digest"] or NONE),
        ("Task set", ", ".join(record["tasks"])),
        ("Task manifest digest", record["manifest_digest"]),
        ("Evaluator pack digest", pack["digest"] if pack else NONE),
        ("Model", record["model"]),
    ]}
    header = "\n".join([
        "# Draft test pre-registration: %s at %s" % (record["draft"], record["revision"][:12]),
        "",
        "Written by the Studio (`citizen draft test --register`) from",
        "`docs/pre-registration-template.md` before the run's first trial. The sections above the",
        "deviation log are frozen; the registration backs one run and is stale once the draft",
        "changes.", "", ""])
    text = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    return header + _fill(text, fields, rewrites, extra)


def _write_once(directory: int, name: str, content: bytes) -> None:
    """Write `name` whole or not at all, read-only; an existing file is never replaced
    (FileExistsError)."""
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
    if cv is not None:
        raise draft_tests.DraftTestError(
            "a registration plans from the repository's declared variance (%s); leave the "
            "coefficient of variation empty" % draft_tests.PLANNING_MANIFEST.as_posix(),
            "draft_test_cv_not_declared")
    plan = draft_tests.parse_plan(effect, None)
    if plan["effect"] > MAX_EFFECT:
        raise draft_tests.DraftTestError(
            "SM-2 caps the minimum detectable effect at %s; state a change of at most that"
            % _percent(MAX_EFFECT), "draft_test_effect_too_large")
    draft = draft_tests.identity(repository, name)
    draft_tests._refuse_unchanged(draft["base_revision"], draft["revision"])
    sample = _parse_spec(repository, spec)
    try:
        power = draft_tests.power(repository, len(sample["tasks"]), sample["repetitions"], plan)
    except draft_tests.DraftTestError as exc:
        if exc.code != "draft_test_planning_unavailable":
            raise
        raise draft_tests.DraftTestError(
            "no planning variance is declared, so a registration cannot be sized. Run an "
            "exploratory pilot (`python3 scripts/cost_bench.py replay --exploratory ...`), size it "
            "with `python3 scripts/replay_power.py --pilot <results dir>`, and declare its "
            "coefficient of variation as `planning` (`cv`, `source`) in %s"
            % draft_tests.PLANNING_MANIFEST.as_posix(), "draft_test_variance_undeclared") from exc
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
              "pack": sample["pack"], "manifest_digest": sample["manifest"]["digest"],
              "effect": plan["effect"], "cv": power["cv"], "power": power}
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
    return dict(record, stale=False, stale_reason=None, problems=[], used_by=None,
                committed=committed(root, record))


def _open_records(root: Path) -> Optional[int]:
    directory = draft_tests._records_fd(Path(root), create=False)
    if directory is None:
        return None
    try:
        return os.open(REGISTRATIONS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=directory)
    except OSError:
        return None
    finally:
        os.close(directory)


def _read_small(directory: int, name: str) -> Optional[bytes]:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > draft_tests.MAX_RECORD_BYTES:
            return None
        return os.read(descriptor, draft_tests.MAX_RECORD_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _read_json(directory: int, name: str) -> Optional[Dict[str, Any]]:
    content = _read_small(directory, name)
    try:
        value = json.loads(content.decode("utf-8")) if content is not None else None
    except (UnicodeError, ValueError):
        return None
    if (not isinstance(value, dict) or set(value) != RECORD_KEYS
            or value["schema_version"] != SCHEMA_VERSION
            or name != "%s.json" % value["registration_id"]):
        return None
    return value


def _read_marker(root: Path, name: str) -> Optional[str]:
    records = _open_records(root)
    if records is None:
        return None
    try:
        content = _read_small(records, name)
    finally:
        os.close(records)
    try:
        return content.decode("utf-8").strip() if content is not None else None
    except UnicodeError:
        return "unreadable"


def used_by(root: Path, registration_id: str) -> Optional[str]:
    """The run the registration backs; `PENDING` while it is claimed and that run is not yet
    recorded (or never was); None while it is unclaimed."""
    run = _read_marker(root, registration_id + RUN_SUFFIX)
    if run is not None:
        return run
    return PENDING if _read_marker(root, registration_id + USED_SUFFIX) is not None else None


def claim(root: Path, registration_id: str) -> None:
    """Claim the registration before its run starts, atomically: an exclusively created
    `<id>.used`. A registration already claimed is refused (`draft_test_registration_used`), and
    a claim that cannot be written refuses the start (`draft_test_registration_failed`)."""
    if not isinstance(registration_id, str) or not IDENTITY.fullmatch(registration_id):
        raise draft_tests.DraftTestError("the registration id is invalid")
    try:
        with _locked(root) as directory:
            records = _private_dir(directory, REGISTRATIONS_DIR)
            try:
                _write_once(records, registration_id + USED_SUFFIX, (
                    datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n").encode("utf-8"))
            finally:
                os.close(records)
    except FileExistsError as exc:
        raise draft_tests.DraftTestError("the registration already backs a run; register the "
                                         "test again", "draft_test_registration_used") from exc
    except OSError as exc:
        raise draft_tests.DraftTestError("the registration's use could not be recorded, so the "
                                         "test was not started",
                                         "draft_test_registration_failed") from exc


def release(root: Path, registration_id: str) -> None:
    """Undo a claim whose run was never admitted; a claim with a recorded run is never undone."""
    with _locked(root) as directory:
        records = _private_dir(directory, REGISTRATIONS_DIR)
        try:
            try:
                os.stat(registration_id + RUN_SUFFIX, dir_fd=records)
            except FileNotFoundError:
                try:
                    os.unlink(registration_id + USED_SUFFIX, dir_fd=records)
                except FileNotFoundError:
                    pass
        finally:
            os.close(records)


def bind(root: Path, registration_id: str, run_id: str) -> None:
    """Record the admitted run a claimed registration backs, once; OSError when it cannot be
    written, and the run then never counts as pre-registered."""
    if str(uuid.UUID(run_id)) != run_id:
        raise draft_tests.DraftTestError("the run id is invalid")
    with _locked(root) as directory:
        records = _private_dir(directory, REGISTRATIONS_DIR)
        try:
            _write_once(records, registration_id + RUN_SUFFIX, (run_id + "\n").encode("utf-8"))
        finally:
            os.close(records)


def _value(text: str) -> str:
    return text.strip().strip("`").strip()


def committed(root: Path, record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The values the committed plan registers, read back from its registry commit; None when the
    commit or a field cannot be read."""
    protocol = _protocol()
    done = subprocess.run(["git", "-C", str(_registry(root)), "show",
                           "%s:%s" % (record["plan_commit"], record["plan"])],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=_git_env())
    if done.returncode:
        return None
    parts = protocol.sections(done.stdout)
    run = protocol.fields(parts.get("Run", ""))
    trials = re.match(r"^\s*(\d+)\b", protocol.fields(parts.get(TRIALS_FIELD[0], "")).get(
        TRIALS_FIELD[1], ""))
    if trials is None or run.get("Registration") != record["registration_id"] \
            or any(field not in run for _key, field, _label in COMPARED):
        return None
    out: Dict[str, Any] = {"trials": int(trials.group(1))}
    for key, field, _label in COMPARED:
        value = _value(run[field])
        out[key] = (None if value == NONE else value) if key in ("config_digest", "pack_digest") \
            else value
    out["tasks"] = sorted(item.strip() for item in out["tasks"].split(",") if item.strip())
    digest = _value(run.get("Task manifest digest", ""))
    out["manifest_digest"] = digest or None
    return out


def problems(root: Path, record: Mapping[str, Any],
             registered: Optional[Mapping[str, Any]]) -> List[str]:
    """Why a registration no longer stands as written; empty when its plan is intact and the record
    agrees with it."""
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
    if registered is None:
        out.append("the committed plan's registered values cannot be read")
    elif registered != _from_record(record):
        out.append("the record differs from the committed plan")
    return out


def _from_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = measured(record["base_revision"], record["revision"], record["config_digest"],
                     record["tasks"], record["repetitions"], record["model"],
                     record["pack"]["digest"] if record["pack"] else None)
    value["manifest_digest"] = record["manifest_digest"]
    return value


def _annotated(root: Path, value: Mapping[str, Any]) -> Dict[str, Any]:
    registered = committed(root, value)
    return dict(value, committed=registered, problems=problems(root, value, registered),
                used_by=used_by(root, value["registration_id"]))


def load(root: Path, registration_id: Any,
         cache: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One registration, the values its committed plan registers, its problems and the run that
    claimed it; DraftTestError when it does not exist. `cache` holds what one request has already
    loaded, so a page reads each registration's plan and Git history once."""
    if not isinstance(registration_id, str) or not IDENTITY.fullmatch(registration_id):
        raise draft_tests.DraftTestError("the registration id is invalid")
    if cache is not None and registration_id in cache:
        return cache[registration_id]
    records = _open_records(root)
    value = None
    if records is not None:
        try:
            value = _read_json(records, registration_id + ".json")
        finally:
            os.close(records)
    if value is None:
        raise draft_tests.DraftTestError("no such registration", "draft_test_registration_not_found")
    loaded = _annotated(root, value)
    if cache is not None:
        cache[registration_id] = loaded
    return loaded


def listed(root: Path, draft: Mapping[str, Any],
           cache: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every registration of this draft, newest first, each marked stale, used or not; none
    removed."""
    records = _open_records(root)
    if records is None:
        return []
    try:
        values = [_read_json(records, name) for name in sorted(os.listdir(records))
                  if name.endswith(".json") and not name.startswith(".")]
    finally:
        os.close(records)
    out = []
    for value in values:
        if value is None or value["draft_id"] != draft["draft_id"]:
            continue
        stale, reason = draft_tests.staleness(draft, value["revision"], value["config_digest"])
        identity = value["registration_id"]
        if cache is not None and identity in cache:
            annotated = cache[identity]
        else:
            annotated = _annotated(root, value)
            if cache is not None:
                cache[identity] = annotated
        out.append(dict(annotated, stale=stale, stale_reason=reason))
    return sorted(out, key=lambda item: (item["created_at"], item["registration_id"]), reverse=True)


def measured(base_revision: str, revision: str, config_digest: Optional[str], tasks: Any,
             trials: int, model: str, pack_digest: Optional[str]) -> Dict[str, Any]:
    """What a run measured, in the shape `deviations` compares against a registration."""
    return {"base_revision": base_revision, "revision": revision, "config_digest": config_digest,
            "tasks": sorted(tasks), "trials": trials, "model": model, "pack_digest": pack_digest}


def deviations(registered: Optional[Mapping[str, Any]], run: Mapping[str, Any]) -> List[str]:
    """Every way a run departs from what the committed plan registers; empty only on an exact
    match. `registered` is `committed`'s answer; None is itself a deviation."""
    if registered is None:
        return ["the committed plan's registered values cannot be read"]
    labels = [(key, label) for key, _field, label in COMPARED] + [("trials", "trials per task")]
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


def start_check(root: Path, repository: Path, draft: Mapping[str, Any],
                selected: replay.ReplayRequest, registration_id: Any) -> Tuple[str, List[str]]:
    """`(registration id, deviations)` for a test about to start under a registration. A stale, used
    or foreign registration is refused; any other departure is returned, and the test runs
    exploratory."""
    found = load(root, registration_id)
    if found["draft_id"] != draft["draft_id"]:
        raise draft_tests.DraftTestError("the registration is for another draft",
                                         "draft_test_registration_mismatch")
    stale, reason = draft_tests.staleness(draft, found["revision"], found["config_digest"])
    if stale:
        raise draft_tests.DraftTestError(
            "the registration is stale (%s); register the test again" % reason,
            "draft_test_registration_stale")
    if found["used_by"] is not None:
        raise draft_tests.DraftTestError(
            "the registration already backs run %s; register the test again" % found["used_by"],
            "draft_test_registration_used")
    out = ["the registration is not intact: %s" % item for item in found["problems"]]
    out += deviations(found["committed"], from_request(selected))
    if found["committed"] is not None:
        current = manifest(repository, selected.pack)["digest"]
        if current != found["committed"]["manifest_digest"]:
            out.append("the run's task manifest differs from the registration's")
    return found["registration_id"], out


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


def evidence(root: Path, registration_id: Optional[str], recorded: List[str], run_id: str,
             comparison: Mapping[str, Any], started_at: Optional[str],
             cache: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[str, List[str]]:
    """`(label, reasons)` for a finished run: pre-registered only on an intact registration written
    before the run started, claimed by this run, that the run matches exactly; otherwise
    exploratory, with every reason."""
    if registration_id is None:
        return replay.EXPLORATORY, []
    try:
        record = load(root, registration_id, cache)
    except draft_tests.DraftTestError as exc:
        return replay.EXPLORATORY, ["the registration cannot be read: %s" % exc]
    reasons = list(recorded) + ["the registration is not intact: %s" % item
                                for item in record["problems"]]
    if record["used_by"] != run_id:
        reasons.append("the registration backs %s, not this run" % (
            "run %s" % record["used_by"] if record["used_by"] not in (None, PENDING)
            else "no recorded run"))
    registered, started = _instant(record["created_at"]), _instant(started_at)
    if started_at is not None and (registered is None or started is None or registered > started):
        reasons.append("the registration was written after the run started")
    if comparison.get("base") and comparison.get("candidate"):
        found = deviations(record["committed"], from_comparison(comparison))
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
        "  plan: %s at %s; it backs one run" % (registration["plan"],
                                                registration["plan_commit"][:12]),
    ]
