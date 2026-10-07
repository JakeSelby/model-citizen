"""Validated two-target adapter for the live cost replay."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace as _replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import drafts, packs, run_store, runs, spend_guard, targets

TARGET_KINDS = frozenset(("installed", "release", "branch", "worktree", "draft"))
ARM_NAMES = ("bare", "harness")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PACK_KEYS = frozenset(("name", "version", "commit", "digest", "source"))
# A pack's set (`--pack-set`); a request saved before sets were offered names none and runs the
# production set, as `cost_bench.py replay --pack` does by default.
PACK_SET = "set"
PREREGISTERED, EXPLORATORY = "pre-registered", "exploratory"
ANALYSIS_NAME = "analysis.json"
MAX_ANALYSIS_BYTES = 4 * 1024 * 1024
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
RELEASE_REF = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9._-]+)?$")
SUMMARY_NAME = "replay-summary.json"
RESULTS_NAME = "results.jsonl"
SPEND_NAME = "spend.json"
HISTORY_NAMES = ("history.jsonl", "history.md")
HISTORY_TRANSACTION = ".studio-replay-history-transaction"
HISTORY_STAGE_PREFIX = ".studio-replay-history-stage-"
MAX_SPEND_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 4 * 1024 * 1024
# The digest AH-S301 records for a target with no configuration of its own. cost_bench builds the
# harness arm from the commit's defaults, so a replay measures source only; see `_refuse_edited_config`.
DEFAULT_CONFIG_DIGEST = targets._config_digest({})
MEASURES = "source"
NATIVE_COMMAND = ("python3", "scripts/cost_bench.py", "replay")
FALLBACK_MODEL = "claude-haiku-4-5-20251001"
# Exit codes cost_bench gives a settled run: done, stopped at its cap, refused or failed.
SETTLED_EXITS = (0, 1, 2)


def _pinned_model() -> str:
    """The dated model the micro manifest pins, so the Studio's default never floats."""
    try:
        value = json.loads((Path(__file__).resolve().parents[3] / "benchmarks" / "micro"
                            / "tasks.json").read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        return FALLBACK_MODEL
    return value if isinstance(value, str) and value else FALLBACK_MODEL


DEFAULT_MODEL = _pinned_model()


class ReplayError(ValueError):
    """A replay request or native result is unsafe or incomplete."""


class ReplayRefusal(ReplayError):
    """A request the replay refuses for a reason the Studio names by a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ReplayExecutionError(ReplayError):
    """A native replay failed after leaving authoritative paid-spend evidence."""

    def __init__(self, message: str, summary: Mapping[str, Any]):
        super().__init__(message)
        self.summary = dict(summary)


def _strict_keys(value: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    extra = set(value) - set(allowed)
    if extra:
        raise ReplayError(where + " has unsupported fields: " + ", ".join(sorted(extra)))


def _money(value: Any, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ReplayError(name + " must be a finite positive dollar amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReplayError(name + " must be a finite positive dollar amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ReplayError(name + " must be a finite positive dollar amount")
    native = float(amount)
    if not math.isfinite(native) or native <= 0:
        raise ReplayError(name + " must be a finite positive dollar amount")
    round_trip = Decimal(str(native))
    if round_trip != amount:
        raise ReplayError(name + " has more precision than the native replay can enforce")
    return format(round_trip.normalize(), "f")


@dataclass(frozen=True)
class ReplayTarget:
    kind: str
    ref: str
    revision: str
    version: Optional[str] = None
    draft: Optional[str] = None
    config_digest: Optional[str] = None

    @classmethod
    def parse(cls, value: Any) -> "ReplayTarget":
        if not isinstance(value, dict):
            raise ReplayError("each replay target must be an object")
        _strict_keys(value, {"kind", "ref", "revision", "version", "draft", "config_digest"},
                     "replay target")
        kind, ref, revision = value.get("kind"), value.get("ref"), value.get("revision")
        version, draft = value.get("version"), value.get("draft")
        digest = value.get("config_digest")
        if kind not in TARGET_KINDS:
            raise ReplayError("replay target has an unsupported kind")
        if (not isinstance(ref, str) or not ref or len(ref) > 4096 or "\0" in ref
                or not isinstance(revision, str) or not FULL_COMMIT.fullmatch(revision)):
            raise ReplayError("replay target needs an explicit ref and full resolved revision")
        if kind == "release" and not RELEASE_REF.fullmatch(ref):
            raise ReplayError("release replay target must name an explicit version tag")
        if version is not None and (not isinstance(version, str) or not version or len(version) > 128):
            raise ReplayError("replay target has an invalid version")
        if draft != (ref if kind == "draft" else None):
            raise ReplayError("replay target has inconsistent draft identity")
        if digest is not None and (not isinstance(digest, str) or not digest or len(digest) > 256):
            raise ReplayError("replay target has an invalid config digest")
        return cls(kind, ref, revision, version, draft, digest)

    @property
    def execution_ref(self) -> str:
        return self.revision

    @property
    def identity(self) -> str:
        return self.kind + ":" + self.ref + ":" + self.revision + ":" + (self.config_digest or "")

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "revision": self.revision,
                "version": self.version, "draft": self.draft,
                "config_digest": self.config_digest}


@dataclass(frozen=True)
class ReplayRequest:
    targets: Tuple[ReplayTarget, ReplayTarget]
    model: str
    repetitions: int
    tasks: Tuple[str, ...]
    max_budget_usd: str
    spend_cap_usd: str
    pre_registration: Optional[str]
    pack: Optional[Dict[str, str]] = None
    evidence: str = PREREGISTERED
    # Set only by the Studio's definitive-evaluation action; such a request is admitted through
    # `definitive_launch.DefinitiveLaunch` and its registered budget, never the plain path.
    definitive: bool = False

    @classmethod
    def parse(cls, value: Any) -> "ReplayRequest":
        if not isinstance(value, dict):
            raise ReplayError("replay request must be an object")
        _strict_keys(value, {"targets", "model", "repetitions", "tasks", "max_budget_usd",
                             "spend_cap_usd", "pre_registration", "pack", "evidence",
                             "definitive"}, "replay request")
        raw_targets = value.get("targets")
        if not isinstance(raw_targets, list) or len(raw_targets) != 2:
            raise ReplayError("replay requires exactly two explicit targets")
        targets = tuple(ReplayTarget.parse(item) for item in raw_targets)
        if targets[0].identity == targets[1].identity:
            raise ReplayError("replay targets must be different")
        model = value.get("model")
        if (not isinstance(model, str) or not model or len(model) > 256 or "\0" in model
                or model.startswith("-")):
            raise ReplayError("replay requires an explicit model")
        repetitions = value.get("repetitions")
        if (not isinstance(repetitions, int) or isinstance(repetitions, bool)
                or not 1 <= repetitions <= 20):
            raise ReplayError("repetitions must be between one and twenty")
        raw_tasks = value.get("tasks")
        if (not isinstance(raw_tasks, list) or not raw_tasks
                or any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item)
                       for item in raw_tasks)
                or len(set(raw_tasks)) != len(raw_tasks)):
            raise ReplayError("replay requires unique explicit task ids")
        maximum = _money(value.get("max_budget_usd"), "--max-budget-usd")
        cap = _money(value.get("spend_cap_usd"), "--spend-cap")
        if Decimal(maximum) > Decimal(cap):
            raise ReplayError("--max-budget-usd cannot exceed --spend-cap")
        registration = value.get("pre_registration")
        if registration is not None and (not isinstance(registration, str) or not registration
                                          or "\0" in registration):
            raise ReplayError("pre-registration must name a file")
        if any(target.kind == "release" for target in targets) and not registration:
            raise ReplayError("a release replay needs a pre-registration before it can write history")
        pack = value.get("pack")
        if pack is not None and (
                not isinstance(pack, dict) or set(pack) - {PACK_SET} != PACK_KEYS
                or any(not isinstance(pack[key], str) or not pack[key] or "\0" in pack[key]
                       for key in PACK_KEYS)
                or (PACK_SET in pack and (not isinstance(pack[PACK_SET], str)
                                          or not IDENTIFIER.fullmatch(pack[PACK_SET])))
                or not HEX40.fullmatch(pack["commit"]) or not HEX64.fullmatch(pack["digest"])
                or not Path(pack["source"]).is_absolute()):
            raise ReplayError("replay pack must be a resolved evaluator pack")
        # A saved request from before the label existed reads exploratory, never pre-registered.
        evidence = value.get("evidence", EXPLORATORY)
        if evidence not in (PREREGISTERED, EXPLORATORY) or (evidence == PREREGISTERED
                                                            and not registration):
            raise ReplayError("a pre-registered replay names its pre-registration")
        definitive = value.get("definitive", False)
        if definitive is not True and definitive is not False:
            raise ReplayError("definitive must be true or false")
        return cls(targets, model, repetitions, tuple(raw_tasks), maximum, cap, registration,
                   dict(pack) if pack is not None else None, evidence, definitive)

    def as_dict(self) -> Dict[str, Any]:
        return {"targets": [target.as_dict() for target in self.targets], "model": self.model,
                "repetitions": self.repetitions, "tasks": list(self.tasks),
                "max_budget_usd": self.max_budget_usd, "spend_cap_usd": self.spend_cap_usd,
                "pre_registration": self.pre_registration, "pack": self.pack,
                "evidence": self.evidence,
                # Only a definitive request names the flag, so every other request's JSON, and the
                # confirmation digest bound to it, is what it was.
                **({"definitive": True} if self.definitive else {})}


def resolve_request(value: Any,
                    resolver: Callable[[str, str], Mapping[str, Any]],
                    pack_resolver: Optional[Callable[..., Mapping[str, Any]]] = None
                    ) -> ReplayRequest:
    """Resolve the two UI references through AH-S301, and the chosen pack by name and digest,
    before spend preview or launch."""
    if not isinstance(value, dict):
        raise ReplayError("replay request must be an object")
    _strict_keys(value, {"targets", "model", "repetitions", "tasks", "max_budget_usd",
                         "spend_cap_usd", "pre_registration", "pack", "definitive"},
                 "replay request")
    # The evidence label is the server's to decide (`ReplayAdmission.resolve`), never the form's.
    supplied = value.get("targets")
    if not isinstance(supplied, list) or len(supplied) != 2:
        raise ReplayError("replay requires exactly two explicit targets")
    resolved = []
    for item in supplied:
        if (not isinstance(item, dict) or set(item) != {"kind", "ref"}
                or item.get("kind") not in TARGET_KINDS
                or not isinstance(item.get("ref"), str) or not item["ref"]):
            raise ReplayError("target selection must contain only kind and ref")
        result = resolver(item["kind"], item["ref"])
        if not isinstance(result, Mapping):
            raise ReplayError("target resolver returned an invalid result")
        resolved.append(dict(result))
    normalized = dict(value, targets=resolved)
    chosen = value.get("pack")
    if chosen is not None:
        if (not isinstance(chosen, dict) or set(chosen) - {PACK_SET} != {"name", "digest"}
                or pack_resolver is None):
            raise ReplayError("pack selection must contain only name and digest, and optionally a set")
        found = (pack_resolver(chosen["name"], chosen["digest"], chosen[PACK_SET])
                 if PACK_SET in chosen else pack_resolver(chosen["name"], chosen["digest"]))
        normalized["pack"] = pinned_pack(found, PACK_SET in chosen)
    if normalized.get("pre_registration") == "":
        normalized["pre_registration"] = None
    return ReplayRequest.parse(normalized)


def pinned_pack(found: Mapping[str, Any], with_set: bool) -> Dict[str, str]:
    """The pack identity a request pins: name, version, commit, digest and source, and the set
    when the selection named one. A selection naming none keeps the identity it always had."""
    pinned = {key: found[key] for key in PACK_KEYS}
    if with_set:
        pinned[PACK_SET] = found[PACK_SET]
    return pinned


def pack_set(pack: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The set a pinned pack names, or None for a request that named none (the production set)."""
    return None if pack is None else pack.get(PACK_SET)


def case_identities(request: ReplayRequest) -> List[str]:
    return [identity for index in (1, 2)
            for identity in ([preflight_case_id(index)] + [
                case_id(index, task, repetition, arm) for task in request.tasks
                for repetition in range(1, request.repetitions + 1) for arm in ARM_NAMES
            ])]


def preview_payload(records: Iterable[Mapping[str, Any]], request: ReplayRequest) -> Dict[str, Any]:
    """Build the server handler's immutable preview after target resolution."""
    cases = case_identities(request)
    try:
        plan = spend_guard.plan(records, "live-replay", cases, request.max_budget_usd,
                                request.spend_cap_usd, "api_credit")
    except spend_guard.SpendGuardError as exc:
        raise ReplayError(str(exc)) from exc
    confirmation = spend_guard.confirmation_digest({
        "suite_id": "live-replay", "request": request.as_dict(), "plan": plan,
    })
    return dict(plan, valid=True, request=request.as_dict(), confirmation_digest=confirmation,
                command=native_commands(request))


def launch_payload(request: ReplayRequest, confirmation_token: str,
                   repository: Optional[Path] = None) -> Dict[str, Any]:
    """Adapt a resolved preview to the shared supervisor's replay start handler."""
    if (not isinstance(confirmation_token, str) or not confirmation_token
            or len(confirmation_token) > 512 or "\0" in confirmation_token):
        raise ReplayError("replay start needs its one-use confirmation token")
    request_json = json.dumps(request.as_dict(), sort_keys=True, separators=(",", ":"))
    if len(request_json) > 4096:
        raise ReplayError("resolved replay request is too large for the catalog boundary")
    primary = request.targets[0]
    parameters = {"request_json": request_json}
    if repository is not None:
        parameters["repository"] = str(Path(repository).resolve())
    return {
        "suite_id": "live-replay", "parameters": parameters,
        "target_kind": primary.kind, "target_ref": primary.ref,
        "case_identities": case_identities(request),
        "confirmed": confirmation_token, "max_budget_usd": request.max_budget_usd,
        "spend_cap_usd": request.spend_cap_usd, "pricing_source": "api_credit",
        "targets": [target.as_dict() for target in request.targets],
    }


def catalog_entry() -> Dict[str, Any]:
    """The standard catalog entry merged after the shared catalog claim is released."""
    return {
        "id": "live-replay", "version": 1,
        "argv": ["python3", "-m", "harness_core.studio.replay_runner",
                 "--request-json", "{param:request_json}",
                 "--repository", "{param:repository}"],
        "parameters": {
            "request_json": {"kind": "pattern", "pattern": r"^\{.+\}$",
                             "max_length": 4096},
            "repository": {"kind": "pattern", "pattern": r"^/.+$", "max_length": 4096},
        },
        "cost_class": "spends_usage", "expected_duration_seconds": 3600,
        "timeout_seconds": 21600, "targets": sorted(TARGET_KINDS), "cases": [],
    }


class ReplayAdmission:
    """Resolve both targets before paid preview and bind them to supervisor admission."""

    SUITE_ID = "live-replay"

    def __init__(self, repository: Path, state_directory: Path, supervisor: Any,
                 target_service: Any):
        self.repository = Path(repository).resolve()
        self.state_directory = Path(state_directory).resolve()
        self.supervisor = supervisor
        self.target_service = target_service

    def _resolve(self, kind: str, ref: str) -> Mapping[str, Any]:
        parent = self.state_directory / "replay-resolutions"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="target-", dir=str(parent)))
        try:
            try:
                built = self.target_service.build(kind, ref, temporary / "target")
            except targets.TargetError as exc:
                if isinstance(exc.__cause__, drafts.DraftError) and exc.__cause__.code == "busy":
                    raise ReplayRefusal(
                        "replay_target_busy",
                        "draft %s is being saved; preview again in a moment" % ref) from exc
                raise ReplayError(str(exc)) from exc
            if kind == "draft":
                self._refuse_edited_config(ref, built.get("config_digest"))
            if built.get("snapshot"):
                # A dirty worktree resolves to a commit that exists only in this disposable
                # clone, and each snapshot is a new commit, so the benchmark could never run it.
                raise ReplayRefusal(
                    "replay_worktree_dirty",
                    "worktree target %s has uncommitted changes; commit them or checkpoint "
                    "them as a draft before a replay" % ref)
            resolved = {
                "kind": kind, "ref": ref, "revision": built.get("revision"),
                "version": built.get("version"),
                "draft": built.get("draft"),
                "config_digest": built.get("config_digest"),
            }
            return ReplayTarget.parse(resolved).as_dict()
        finally:
            shutil.rmtree(str(temporary), ignore_errors=True)

    def _refuse_edited_config(self, name: str, digest: Any) -> None:
        """Refuse a draft whose configuration was edited after it was created.

        Every draft inherits the creator's live configuration, and the benchmark runs the harness
        arm from the commit's defaults, so an inherited configuration is simply not measured. An
        edited one is the change the user wants measured, and the arm cannot apply it. A draft
        whose configuration is empty runs exactly as the arm does."""
        if digest in (None, DEFAULT_CONFIG_DIGEST):
            return
        try:
            worktree, _state = drafts.find(self.repository, name)
            base_path = drafts._paths(worktree)["base_config"]  # written once, at create
            base = (json.loads(base_path.read_text(encoding="utf-8"))
                    if base_path.is_file() else {})
            inherited = targets._config_digest(base) if isinstance(base, dict) else None
        except (drafts.DraftError, targets.TargetError, OSError, ValueError) as exc:
            raise ReplayError("draft configuration base is unreadable") from exc
        if digest != inherited:
            raise ReplayRefusal(
                "replay_target_config_unsupported",
                "draft %s changed its configuration, but the benchmark builds the harness arm "
                "from the commit's defaults and cannot apply it; checkpoint the change as "
                "source or restore the inherited configuration" % name)

    def _resolve_pack(self, name: Any, digest: Any, set_name: Any = None) -> Mapping[str, Any]:
        try:
            return packs.select(self.repository, name, digest, set_name)
        except ValueError as exc:
            raise ReplayError(str(exc)) from exc

    def resolve(self, value: Any) -> ReplayRequest:
        request = resolve_request(value, self._resolve, self._resolve_pack)
        validate_task_selection(self.repository, request)
        return label_evidence(self.repository, request)

    def _confirm_resolved(self, request: ReplayRequest) -> None:
        if request.pack is not None:
            found = self._resolve_pack(request.pack["name"], request.pack["digest"],
                                       pack_set(request.pack))
            if pinned_pack(found, PACK_SET in request.pack) != request.pack:
                raise ReplayError("replay pack identity changed after spend preview")
        for expected in request.targets:
            actual = ReplayTarget.parse(self._resolve(expected.kind, expected.ref))
            if actual.identity != expected.identity or actual.version != expected.version:
                raise ReplayError("replay target identity changed after spend preview")

    def preview(self, value: Any) -> Dict[str, Any]:
        return self.preview_resolved(self.resolve(value))

    @staticmethod
    def _plain(request: ReplayRequest, definitive_admitted: bool) -> None:
        if request.definitive and not definitive_admitted:
            raise ReplayRefusal(
                "definitive_launch_required",
                "a definitive request is launched only by the definitive evaluation action")

    def preview_resolved(self, request: ReplayRequest,
                         definitive_admitted: bool = False) -> Dict[str, Any]:
        """The supervisor half of preview; the target builds in `resolve` stay outside it. A
        definitive request is refused unless `definitive_launch` has admitted it."""
        self._plain(request, definitive_admitted)
        launch = launch_payload(request, "preview", self.repository)
        try:
            value = self.supervisor.spend_preview(
                self.SUITE_ID, launch["parameters"], launch["target_kind"],
                launch["target_ref"], request.max_budget_usd, request.spend_cap_usd,
                "api_credit", case_identities=launch["case_identities"])
        except runs.RunError as exc:
            raise ReplayError(str(exc)) from exc
        return dict(value, valid=True, errors=[], request=request.as_dict(),
                    command=native_commands(request),
                    sampling=sampling_payload(self.repository, request))

    def confirm(self, value: Any) -> ReplayRequest:
        """Re-resolve a previewed request; this builds both targets, so it runs unserialized."""
        request = ReplayRequest.parse(value)
        self._confirm_resolved(request)
        if label_evidence(self.repository, _replace(request, evidence=(
                PREREGISTERED if request.pre_registration else EXPLORATORY))).evidence != request.evidence:
            raise ReplayError("replay evidence label changed after spend preview")
        return request

    def start(self, value: Any, confirmation_token: Any) -> Dict[str, Any]:
        return self.start_confirmed(self.confirm(value), confirmation_token)

    def start_confirmed(self, request: ReplayRequest, confirmation_token: Any) -> Dict[str, Any]:
        self._plain(request, False)  # a definitive start goes through `definitive_launch`
        launch = launch_payload(request, confirmation_token, self.repository)
        try:
            started = self.supervisor.start(
                launch["suite_id"], launch["parameters"], launch["target_kind"],
                launch["target_ref"], confirmed=launch["confirmed"],
                max_budget_usd=launch["max_budget_usd"],
                spend_cap_usd=launch["spend_cap_usd"],
                pricing_source=launch["pricing_source"],
                case_identities=launch["case_identities"])
        except runs.RunError as exc:
            raise ReplayError(str(exc)) from exc
        return {"run_id": started["run_id"], "status": started["status"],
                "targets": [target.as_dict() for target in request.targets]}


def _safe_file(root: Path, supplied: str) -> Path:
    root = Path(root).resolve()
    path = Path(supplied)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReplayError("pre-registration must stay inside the repository") from exc
    if path.is_symlink() or not path.is_file():
        raise ReplayError("pre-registration must be a regular repository file")
    return path


def _native_arguments(request: ReplayRequest, target: ReplayTarget, cap: str) -> List[str]:
    arguments = ["--tag", target.execution_ref, "--model", request.model,
                 "--reps", str(request.repetitions), "--run-cap", request.max_budget_usd,
                 "--spend-cap", cap]
    if request.pack is not None:
        # Pinned: the run reads this commit and refuses any other content.
        arguments.extend(("--pack", request.pack["source"], "--pack-ref", request.pack["commit"],
                          "--pack-digest", request.pack["digest"]))
        if pack_set(request.pack) is not None:
            arguments.extend(("--pack-set", request.pack[PACK_SET]))
    for task in request.tasks:
        arguments.extend(("--task", task))
    return arguments


def _registered(request: ReplayRequest, target: ReplayTarget) -> bool:
    """A release target of a pre-registered, whole-set replay runs registered; all else runs
    `--exploratory`, which writes no history row."""
    return request.evidence == PREREGISTERED and target.kind == "release"


def target_evidence(request: ReplayRequest, target: ReplayTarget) -> str:
    """The label of the command one target actually runs."""
    return PREREGISTERED if _registered(request, target) else EXPLORATORY


def replay_evidence(request: ReplayRequest) -> str:
    """Pre-registered only when every command the replay runs is registered."""
    return (PREREGISTERED if all(_registered(request, target) for target in request.targets)
            else EXPLORATORY)


def command_for_target(request: ReplayRequest, target: ReplayTarget, repository: Path,
                       output: Path, remaining_cap: Optional[str] = None,
                       history_dir: Optional[Path] = None) -> List[str]:
    repository = Path(repository).resolve()
    cap = remaining_cap or request.spend_cap_usd
    command = ([sys.executable, str(repository / "scripts" / "cost_bench.py"), "replay"]
               + _native_arguments(request, target, cap) + ["--out", str(output)])
    if _registered(request, target):
        registration = _safe_file(repository, request.pre_registration or "")
        command.extend(("--pre-registration", str(registration),
                        "--history-dir", str(history_dir or repository / "benchmarks")))
    else:
        command.append("--exploratory")
    return command


def native_commands(request: ReplayRequest) -> str:
    """The native commands a replay runs, one per target, as a person would type them.

    The Studio gives target two only what target one left of the whole-set cap; run by hand,
    each command carries the whole cap."""
    lines = []
    for target in request.targets:
        command = list(NATIVE_COMMAND) + _native_arguments(request, target, request.spend_cap_usd)
        if _registered(request, target):
            command.extend(("--pre-registration", request.pre_registration or ""))
        else:
            command.append("--exploratory")
        lines.append(" ".join(shlex.quote(part) for part in command))
    return "\n".join(lines)


def _history_lock_path(history_dir: Path) -> Path:
    digest = hashlib.sha256(str(Path(history_dir).resolve()).encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / ("citizen-studio-replay-history-" + digest + ".lock")


@contextmanager
def history_lock(history_dir: Path):
    """Hold the one narrow cross-process lock shared by Studio and direct history writers."""
    history_dir = Path(history_dir).resolve()
    history_dir.mkdir(parents=True, exist_ok=True)
    with open(_history_lock_path(history_dir), "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _atomic_history_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name("." + path.name + ".studio-tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _history_pair(home: Path) -> Dict[str, Optional[bytes]]:
    pair: Dict[str, Optional[bytes]] = {}
    for name in HISTORY_NAMES:
        path = home / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ReplayError("replay history contains an unsafe file")
        pair[name] = path.read_bytes() if path.is_file() else None
    if sum(value is not None for value in pair.values()) == 1:
        raise ReplayError("replay history JSONL and Markdown are not a complete pair")
    return pair


def _restore_history_pair(home: Path, transaction: Path,
                          manifest: Mapping[str, Any]) -> None:
    for name in HISTORY_NAMES:
        destination = home / name
        if manifest.get(name) is True:
            snapshot = transaction / name
            if not snapshot.is_file() or snapshot.is_symlink():
                raise ReplayError("replay history recovery snapshot is incomplete")
            _atomic_history_bytes(destination, snapshot.read_bytes())
        elif manifest.get(name) is False:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        else:
            raise ReplayError("replay history recovery manifest is invalid")


def _recover_history_pair(home: Path) -> None:
    transaction = home / HISTORY_TRANSACTION
    if not transaction.exists():
        return
    if not transaction.is_dir() or transaction.is_symlink():
        raise ReplayError("replay history recovery state is unsafe")
    if (transaction / "committed").is_file():
        shutil.rmtree(transaction)
        return
    manifest_path = transaction / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReplayError("replay history recovery manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise ReplayError("replay history recovery manifest is invalid")
        _restore_history_pair(home, transaction, manifest)
    shutil.rmtree(transaction)


def _replace_history_file(source: Path, destination: Path) -> None:
    """Publish one staged history file; split out so the paired rollback is testable."""
    os.replace(source, destination)


def _publish_history_pair(home: Path, stage: Path,
                          original: Mapping[str, Optional[bytes]]) -> None:
    staged = _history_pair(stage)
    if staged == original:
        return
    if any(value is None for value in staged.values()):
        raise ReplayError("release replay did not stage a complete history pair")
    transaction = home / HISTORY_TRANSACTION
    transaction.mkdir(mode=0o700)
    manifest = {name: original[name] is not None for name in HISTORY_NAMES}
    try:
        for name, content in original.items():
            if content is not None:
                _atomic_history_bytes(transaction / name, content)
        _atomic_history_bytes(transaction / "manifest.json",
                              (json.dumps(manifest, sort_keys=True) + "\n").encode())
        for name in HISTORY_NAMES:
            _replace_history_file(stage / name, home / name)
        _atomic_history_bytes(transaction / "committed", b"\n")
    except BaseException as exc:
        try:
            _restore_history_pair(home, transaction, manifest)
            shutil.rmtree(transaction)
        except BaseException as recovery_exc:
            raise ReplayError("replay history publish failed; recovery is pending") from recovery_exc
        raise ReplayError("replay history publish failed and was rolled back") from exc
    shutil.rmtree(transaction)


def _sweep_stale_stages(home: Path) -> None:
    """Remove stages a killed run left behind; under the lock none of them is live."""
    for stale in home.glob(HISTORY_STAGE_PREFIX + "*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(str(stale), ignore_errors=True)


def release_history_transaction(repository: Path, action: Callable[[Path], Any],
                                verify: Optional[Callable[[Any], bool]] = None) -> Any:
    """Serialize release-only history writes and publish the JSONL/Markdown pair together.

    The pair is published only when the action exits 0 and `verify` accepts its result, so a
    run whose native rows or spend fail verification leaves project history untouched."""
    repository = Path(repository).resolve()
    home = repository / "benchmarks"
    with history_lock(home):
        _recover_history_pair(home)
        _sweep_stale_stages(home)
        original = _history_pair(home)
        stage = Path(tempfile.mkdtemp(prefix=HISTORY_STAGE_PREFIX, dir=home))
        try:
            for name, content in original.items():
                if content is not None:
                    (stage / name).write_bytes(content)
            result = action(stage)
            if (getattr(result, "returncode", 1) == 0
                    and (verify is None or verify(result))):
                _publish_history_pair(home, stage, original)
            return result
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def _validate_native_row(row: Any, number: int) -> Dict[str, Any]:
    if (not isinstance(row, dict) or row.get("arm") not in ARM_NAMES
                or not isinstance(row.get("task"), str) or not row["task"]
                or not isinstance(row.get("rep"), int) or isinstance(row.get("rep"), bool)
                or row["rep"] < 1 or not isinstance(row.get("error"), (bool, str))
                or not (isinstance(row.get("passed"), bool)
                        or (row.get("passed") is None and bool(row.get("error"))))
                or (row.get("cost_usd") is not None
                    and (not isinstance(row["cost_usd"], (int, float))
                         or isinstance(row["cost_usd"], bool)
                         or not math.isfinite(row["cost_usd"]) or row["cost_usd"] < 0))):
        raise ReplayError("replay result line %d does not match the native schema" % number)
    return row


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReplayError("replay native results are missing") from exc
    rows: List[Dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise ReplayError("replay result line %d is not JSON" % number) from exc
        rows.append(_validate_native_row(row, number))
    return rows


def _sidecar_money(value: Any, name: str) -> Decimal:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise ReplayError("replay spend sidecar has invalid " + name)
    return Decimal(str(value))


def _read_spend(path: Path, target: ReplayTarget, run_cap: str, spend_cap: str,
                rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Read and reconcile cost_bench's bounded authoritative cap accounting."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SPEND_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ReplayError("replay spend sidecar must be a bounded mode-0600 regular file")
        data = os.read(descriptor, MAX_SPEND_BYTES + 1)
        if len(data) != info.st_size:
            raise ReplayError("replay spend sidecar changed while it was read")
        value = json.loads(data.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError("non-finite number")))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ReplayError):
            raise
        raise ReplayError("replay spend sidecar is missing or unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    expected = {"schema_version", "tag", "run_cap_usd", "spend_cap_usd",
                "preflight_spend_usd", "scored_spend_usd", "charged_spend_usd",
                "stopped_at_cap"}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get("schema_version") != 1 or value.get("tag") != target.execution_ref
            or not isinstance(value.get("stopped_at_cap"), bool)):
        raise ReplayError("replay spend sidecar has an invalid identity or schema")
    if (_sidecar_money(value.get("run_cap_usd"), "run cap") != Decimal(run_cap)
            or _sidecar_money(value.get("spend_cap_usd"), "spend cap") != Decimal(spend_cap)):
        raise ReplayError("replay spend sidecar does not match its enforced caps")
    preflight = _sidecar_money(value.get("preflight_spend_usd"), "preflight spend")
    scored = _sidecar_money(value.get("scored_spend_usd"), "scored spend")
    charged = _sidecar_money(value.get("charged_spend_usd"), "charged spend")
    # Both sides are rounded to six places independently, one from a Decimal sum and one from a
    # float difference, so a half-way seventh place may legitimately differ by one unit.
    tolerance = Decimal("0.000001")
    row_charge = Decimal(str(round(float(charged_spend(rows, run_cap)), 6)))
    if abs(scored - row_charge) > tolerance:
        raise ReplayError("replay spend sidecar does not match its native rows")
    if abs(charged - preflight - scored) > tolerance:
        raise ReplayError("replay spend sidecar components do not equal charged spend")
    if charged > Decimal(spend_cap) and not value["stopped_at_cap"]:
        raise ReplayError("replay spend sidecar exceeds its cap without stopping")
    return dict(value)


class _NothingSpent(ReplayError):
    """The native replay stopped before it created its output, so before any paid call."""


def _verify_target_output(request: ReplayRequest, target: ReplayTarget, native_out: Path,
                          returncode: int, remaining: str
                          ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read and verify one target's native rows and spend sidecar, or raise ReplayError."""
    if returncode not in SETTLED_EXITS:
        # Killed by a signal, or any exit cost_bench never gives: its sidecar may predate the run
        # in flight, so the spend is unknown and the remaining cap is charged.
        raise ReplayError("replay exited %s, so its spend is unknown" % returncode)
    if not native_out.exists() and returncode != 0:
        raise _NothingSpent("replay target was refused before any spend (exit %s)" % returncode)
    result_path, spend_path = native_out / RESULTS_NAME, native_out / SPEND_NAME
    rows = _read_rows(result_path) if result_path.is_file() else []
    _verify_target_rows(target, rows, request.pack)
    _reconcile_target_rows(request, rows, complete=returncode == 0)
    spend = _read_spend(spend_path, target, request.max_budget_usd, remaining, rows)
    if (returncode != 0 and not rows and spend["charged_spend_usd"] == 0
            and not spend["stopped_at_cap"]):
        # cost_bench writes a zero sidecar when arm admission or the workdir probe refuses.
        raise _NothingSpent("replay target was refused before any spend (exit %s)" % returncode)
    if spend["stopped_at_cap"] != (returncode == 1):
        raise ReplayError("replay spend sidecar does not match the native exit status")
    if returncode in (0, 1) and not rows and not spend["stopped_at_cap"]:
        raise ReplayError("replay target finished without producing a native result")
    return rows, spend


def case_id(target_index: int, task: str, repetition: int, arm: str) -> str:
    """The stable spend-result identity shared with the catalog integration."""
    if (target_index not in (1, 2) or not IDENTIFIER.fullmatch(task)
            or not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1
            or arm not in ARM_NAMES):
        raise ReplayError("invalid replay case identity")
    return "target-%d-%s-%d-%s" % (target_index, task.lower(), repetition, arm)


def preflight_case_id(target_index: int) -> str:
    if target_index not in (1, 2):
        raise ReplayError("invalid replay preflight identity")
    return "target-%d-preflight" % target_index


def _verify_target_rows(target: ReplayTarget, rows: Sequence[Mapping[str, Any]],
                        pack: Optional[Mapping[str, str]] = None) -> None:
    for row in rows:
        if pack is not None and (row.get("pack_digest") != pack["digest"]
                                 or row.get("pack_commit") != pack["commit"]):
            raise ReplayError("replay result does not carry the pinned evaluator pack")
        if row.get("harness_sha") != target.revision:
            raise ReplayError("replay result does not match its resolved target revision")
        if row.get("tag") != target.execution_ref:
            raise ReplayError("replay result does not match its explicit target reference")


def _reconcile_target_rows(request: ReplayRequest, rows: Sequence[Mapping[str, Any]],
                           complete: bool) -> None:
    """Validate the exact requested matrix before rows enter summaries or the run store."""
    expected = {(task, repetition, arm) for task in request.tasks
                for repetition in range(1, request.repetitions + 1) for arm in ARM_NAMES}
    actual = set()
    for row in rows:
        identity = (row.get("task"), row.get("rep"), row.get("arm"))
        if identity not in expected:
            raise ReplayError("replay produced an unknown or out-of-range task, repetition or arm")
        if identity in actual:
            raise ReplayError("replay produced a duplicate task, repetition and arm row")
        actual.add(identity)
    if complete and actual != expected:
        raise ReplayError("replay completed without every requested task, repetition and arm row")


def table_rows(target_rows: Sequence[Tuple[ReplayTarget, Sequence[Mapping[str, Any]]]]) -> List[Dict[str, Any]]:
    table = []
    for target, rows in target_rows:
        for task in sorted({str(row["task"]) for row in rows}):
            for arm in ARM_NAMES:
                cell = [row for row in rows if row.get("task") == task and row.get("arm") == arm]
                passed = sum(row.get("passed") is True for row in cell)
                costs = [row.get("cost_usd") for row in cell]
                known_cost = bool(cell) and None not in costs
                table.append({
                    "target": target.as_dict(), "task": task, "arm": arm,
                    "runs": len(cell), "passed": passed,
                    "pass_rate": round(passed / len(cell), 6) if cell else None,
                    "cost_per_passed": (round(sum(float(cost) for cost in costs) / passed, 6)
                                        if passed and known_cost else None),
                })
    return table


def total_spend(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        cost = row.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost):
            total += Decimal(str(cost))
    return total


def charged_spend(rows: Iterable[Mapping[str, Any]], run_cap: str) -> Decimal:
    """Charge unreadable cost at the exact per-run cap, matching cost_bench.replay."""
    cap = Decimal(run_cap)
    return sum(((cap if row.get("cost_usd") is None else Decimal(str(row["cost_usd"])))
                for row in rows), Decimal("0"))


def execute(request: ReplayRequest, repository: Path, output: Path,
            launch: Callable[..., Any] = subprocess.run) -> Dict[str, Any]:
    """Run each explicit target with one whole-set spend cap and preserve native rows."""
    repository, output = Path(repository).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    spent = Decimal("0")
    reported_spent = Decimal("0")
    collected: List[Tuple[ReplayTarget, Sequence[Mapping[str, Any]]]] = []
    spend_records: Dict[int, Mapping[str, Any]] = {}
    result_files = []
    spend_files = []
    stopped = False
    failure: Optional[Tuple[str, Optional[BaseException]]] = None
    for index, target in enumerate(request.targets, 1):
        remaining = Decimal(request.spend_cap_usd) - spent
        if remaining <= 0:
            stopped = True
            break
        target_out = output / ("target-%d" % index)
        target_out.mkdir(mode=0o700)
        command = command_for_target(request, target, repository, target_out,
                                     format(remaining, "f"))
        native_out = target_out / target.execution_ref
        result_path = native_out / RESULTS_NAME
        spend_path = native_out / SPEND_NAME
        settled: Dict[str, Any] = {}

        def settle(done: Any) -> bool:
            """Verify the native output once; the release path does so before publishing."""
            if not settled:
                code = getattr(done, "returncode", 1)
                try:
                    settled["value"] = _verify_target_output(
                        request, target, native_out, code, format(remaining, "f"))
                except ReplayError as exc:
                    settled["error"] = exc
            return "value" in settled

        launch_error: Optional[BaseException] = None
        try:
            if target.kind == "release":
                def launch_release(history_dir: Path) -> Any:
                    release_command = command_for_target(
                        request, target, repository, target_out, format(remaining, "f"), history_dir)
                    return launch(release_command, cwd=str(repository), check=False)

                done = release_history_transaction(repository, launch_release, settle)
            else:
                done = launch(command, cwd=str(repository), check=False)
            returncode = getattr(done, "returncode", 1)
        except Exception as exc:
            launch_error = exc
            returncode = 2
            settled.clear()
        settle(SimpleNamespace(returncode=returncode))
        if "error" in settled:
            exc = settled["error"]
            if isinstance(exc, _NothingSpent):
                # No target folder: cost_bench refused before creating it (credential,
                # pre-registration, tag, arm build), so nothing was charged. A refusal after it
                # creates the folder (observation folder, admission, contamination, probe)
                # writes a $0 spend.json there instead, which `_read_spend` reads.
                charge = Decimal("0")
            else:
                charge = remaining
                spend_records[index] = {
                    "preflight_spend_usd": round(float(remaining), 6), "scored_spend_usd": 0.0,
                    "charged_spend_usd": round(float(remaining), 6), "stopped_at_cap": True,
                }
                stopped = True
            spent += charge
            failure = (str(exc), launch_error or exc)
            break
        rows, spend = settled["value"]
        spent += Decimal(str(spend["charged_spend_usd"]))
        reported_spent += total_spend(rows)
        collected.append((target, rows))
        spend_records[index] = spend
        if result_path.is_file():
            result_files.append(str(result_path))
        spend_files.append(str(spend_path))
        if returncode not in (0, 1):
            failure = ("replay target failed after producing a partial native result", launch_error)
            break
        if returncode == 1:
            stopped = True
            break
    cases = []
    for index, target in enumerate(request.targets, 1):
        target_rows = next((rows for known, rows in collected if known == target), [])
        spend = spend_records.get(index)
        cases.append({"id": preflight_case_id(index), "target": target.as_dict(),
                      "status": "completed" if spend is not None else "not_run",
                      "spend_usd": (spend["preflight_spend_usd"] if spend is not None else 0.0)})
        for task in request.tasks:
            for repetition in range(1, request.repetitions + 1):
                for arm in ARM_NAMES:
                    mine = [row for row in target_rows if row.get("task") == task
                            and row.get("rep") == repetition and row.get("arm") == arm]
                    cases.append({"id": case_id(index, task, repetition, arm),
                                  "target": target.as_dict(), "task": task,
                                  "repetition": repetition, "arm": arm,
                                  "status": "completed" if len(mine) == 1 else "not_run",
                                  "spend_usd": (round(float(charged_spend(
                                      mine, request.max_budget_usd)), 6)
                                                if len(mine) == 1 else 0.0)})
    summary = {
        "schema_version": 1, "targets": [target.as_dict() for target in request.targets],
        "measures": MEASURES, "model": request.model, "repetitions": request.repetitions,
        "tasks": list(request.tasks), "spend_usd": round(float(spent), 6),
        "reported_spend_usd": round(float(reported_spent), 6),
        "spend_cap_usd": request.spend_cap_usd, "stopped_at_cap": stopped,
        "result_files": result_files, "spend_files": spend_files,
        "cases": cases, "table": table_rows(collected),
    }
    path = output / SUMMARY_NAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        write_analysis(repository, output, summary)
    except (OSError, ReplayError):
        pass  # the run stands; `result_payload` reports the missing analysis as `analysis_error`
    if failure is not None:
        message, cause = failure
        if cause is not None:
            raise ReplayExecutionError(message, summary) from cause
        raise ReplayExecutionError(message, summary)
    return summary


def read_summary(path: Path) -> Dict[str, Any]:
    """Read one private replay summary without following or accepting mutable file shapes."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or hasattr(os, "getuid") and info.st_uid != os.getuid()
                or info.st_size > MAX_SUMMARY_BYTES):
            raise ReplayError("replay summary is unsafe")
        data = os.read(descriptor, MAX_SUMMARY_BYTES + 1)
        if len(data) != info.st_size:
            raise ReplayError("replay summary changed while it was read")
        value = json.loads(data.decode("utf-8"),
                           parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ReplayError):
            raise
        raise ReplayError("replay summary is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    required = {"schema_version", "targets", "model", "repetitions", "tasks",
                "spend_usd", "reported_spend_usd", "spend_cap_usd", "stopped_at_cap",
                "result_files", "spend_files", "cases", "table"}
    if (not isinstance(value, dict) or set(value) - {"measures"} != required
            or value.get("measures", MEASURES) != MEASURES
            or value.get("schema_version") != 1
            or not isinstance(value.get("targets"), list) or len(value["targets"]) != 2
            or not isinstance(value.get("table"), list)
            or not isinstance(value.get("stopped_at_cap"), bool)
            or not isinstance(value.get("spend_usd"), (int, float))
            or isinstance(value.get("spend_usd"), bool)
            or not math.isfinite(value["spend_usd"]) or value["spend_usd"] < 0
            or any(not isinstance(item, str) for item in value.get("result_files", []))):
        raise ReplayError("replay summary has an invalid schema")
    identities = {ReplayTarget.parse(item).identity for item in value["targets"]}
    for item in value["table"]:
        if (not isinstance(item, dict)
                or set(item) != {"target", "task", "arm", "runs", "passed", "pass_rate",
                                    "cost_per_passed"}
                or ReplayTarget.parse(item.get("target")).identity not in identities
                or item.get("arm") not in ARM_NAMES
                or not isinstance(item.get("task"), str)
                or not isinstance(item.get("runs"), int) or isinstance(item.get("runs"), bool)
                or not isinstance(item.get("passed"), int) or isinstance(item.get("passed"), bool)
                or item["runs"] < 0 or item["passed"] < 0 or item["passed"] > item["runs"]
                or (item.get("pass_rate") is not None and item["pass_rate"] > 1)
                or any(number is not None and (not isinstance(number, (int, float))
                                               or isinstance(number, bool)
                                               or not math.isfinite(number) or number < 0)
                       for number in (item.get("pass_rate"), item.get("cost_per_passed")))):
            raise ReplayError("replay summary has an invalid result table")
    return value


def _engine_module(name: str):
    """A landed engine module from this checkout's `scripts/`, loaded as the CLI loads it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("studio_" + name, str(_SCRIPTS / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LEADING_INT = re.compile(r"^\s*(\d+)\b")
# `replay_power.py --have K N M`, as the pre-registration template asks the field to quote it.
_HAVE = re.compile(r"--have\s+(\d+)\s+(\d+)\s+(\d+)")
_LONG_COUNT = re.compile(r"of which\s+(\d+)\s+(?:are|is)\s+long", re.IGNORECASE)


def registered_sample(repository: Path, registration: str) -> Dict[str, Any]:
    """The sample a pre-registration commits to, read through `experiment_protocol`'s own
    section and field readers: Tasks (k, and n long), Trials per task and arm (m), and the
    Power calculation text `replay_power.py` produced. Nothing is recomputed here."""
    path = _safe_file(repository, registration)
    protocol = _engine_module("experiment_protocol")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReplayError("pre-registration is unreadable") from exc
    sample = protocol.fields(protocol.sections(text).get("Sample size", ""))
    tasks = _LEADING_INT.match(sample.get("Tasks", ""))
    trials = _LEADING_INT.match(sample.get("Trials per task and arm", ""))
    if tasks is None or trials is None:
        raise ReplayRefusal("replay_registration_unreadable",
                            "the pre-registration states no task count or trials per task and arm")
    long = _LONG_COUNT.search(sample.get("Tasks", ""))
    power = sample.get("Power calculation")
    have = _HAVE.search(power or "")
    return {"tasks": int(tasks.group(1)), "long": int(long.group(1)) if long else None,
            "trials": int(trials.group(1)), "power_calculation": power,
            "have": tuple(int(have.group(i)) for i in (1, 2, 3)) if have else None,
            "min_trials": _engine_module("replay_power").MIN_REPS}


def _long_count(repository: Path, request: ReplayRequest) -> int:
    """How many of the chosen tasks the pack or task list marks long."""
    if request.pack is not None:
        try:
            chosen = packs.select(Path(repository), request.pack["name"], request.pack["digest"],
                                   pack_set(request.pack))
        except ValueError as exc:
            raise ReplayError(str(exc)) from exc
        flags = {item["id"]: item.get("long") is True for item in chosen["tasks"]}
    else:
        flags = {item["id"]: item.get("long") is True for item in _repository_tasks(repository)}
    return sum(1 for task in request.tasks if flags.get(task))


def whole_set(repository: Path, request: ReplayRequest) -> bool:
    """True when the replay runs every task of its set: the pack's production set, or the
    repository's own tasks. `cost_bench` writes history only for a whole set."""
    if request.pack is not None:
        try:
            chosen = packs.select(Path(repository), request.pack["name"], request.pack["digest"],
                                   pack_set(request.pack))
        except ValueError as exc:
            raise ReplayError(str(exc)) from exc
        every = {item["id"] for item in chosen["tasks"]}
    else:
        every = {item["id"] for item in _repository_tasks(repository)}
    return set(request.tasks) == every


def label_evidence(repository: Path, request: ReplayRequest) -> ReplayRequest:
    """Decide the evidence label: a whole set with a pre-registration whose registered sample it
    matches is pre-registered; a task subset, or no pre-registration, is exploratory and writes no
    history. A whole registered set that departs from its registered sample is refused."""
    if (not request.pre_registration or not whole_set(repository, request)
            or not any(target.kind == "release" for target in request.targets)):
        return _replace(request, evidence=EXPLORATORY)
    registered = registered_sample(repository, request.pre_registration)
    long_tasks = _long_count(repository, request)
    if (registered["tasks"] != len(request.tasks) or registered["trials"] != request.repetitions
            or registered["trials"] < registered["min_trials"]
            or registered["long"] is not None and registered["long"] != long_tasks
            or registered["have"] is not None
            and registered["have"] != (len(request.tasks), long_tasks, request.repetitions)):
        raise ReplayRefusal(
            "replay_sample_unregistered",
            "the pre-registration registers %d task(s) (%s long) and %d trial(s) per task and arm "
            "(at least %d)%s; this replay runs %d task(s) (%d long) and %d"
            % (registered["tasks"], "unstated" if registered["long"] is None else registered["long"],
               registered["trials"], registered["min_trials"],
               "" if registered["have"] is None else
               ", and its power calculation sized k, n, m = %d, %d, %d" % registered["have"],
               len(request.tasks), long_tasks, request.repetitions))
    return _replace(request, evidence=PREREGISTERED)


def sampling_payload(repository: Path, request: ReplayRequest) -> Dict[str, Any]:
    """What the form shows about the sample: the label, the registered requirement as written,
    and what this replay asks for."""
    registered = None
    if request.evidence == PREREGISTERED and request.pre_registration:
        registered = dict(registered_sample(repository, request.pre_registration))
        registered["have"] = list(registered["have"]) if registered["have"] else None
    labels = [{"target": target.as_dict(), "evidence": target_evidence(request, target)}
              for target in request.targets]
    evidence = replay_evidence(request)
    if evidence == PREREGISTERED:
        note = "Pre-registered: every target is a release that runs the registered sample."
    elif registered is not None:
        note = ("Mixed: each release target runs the registered sample and may write history; "
                "every other target runs exploratory, so the replay as a whole is exploratory.")
    else:
        note = ("Exploratory: a task subset, a draft or a replay with no pre-registration writes "
                "no history row and is never cited as evidence.")
    return {"evidence": evidence, "targets": labels, "registered": registered,
            "requested": {"tasks": len(request.tasks), "trials": request.repetitions},
            "note": note}


def comparison_key(request: ReplayRequest) -> str:
    """The identity two draft comparisons must share to be compared: tasks, model, trials, pack."""
    return hashlib.sha256(json.dumps({
        "tasks": sorted(request.tasks), "model": request.model, "trials": request.repetitions,
        "pack_digest": request.pack["digest"] if request.pack else None,
        # Only a set other than production joins the key, so earlier keys still match.
        **({"pack_set": pack_set(request.pack)}
           if pack_set(request.pack) not in (None, packs.TIER) else {}),
    }, sort_keys=True).encode("utf-8")).hexdigest()


def draft_staleness(repository: Path, target: ReplayTarget) -> Tuple[bool, Optional[str]]:
    """`(stale, reason)` for a measured target: a draft is stale once it has a newer checkpoint,
    a changed configuration or is gone; any other target never goes stale."""
    if target.kind != "draft" or not target.draft:
        return False, None
    try:
        current = drafts.read_config(Path(repository), target.draft)
        revision = str(current["draft"]["revision"])
        digest = targets._config_digest(dict(current["config"]))
    except (drafts.DraftError, targets.TargetError, KeyError, TypeError):
        return True, "the draft is no longer available"
    if revision != target.revision:
        return True, "the draft has a newer checkpoint"
    if target.config_digest is not None and digest != target.config_digest:
        return True, "the draft's configuration changed"
    return False, None


def draft_comparisons(repository: Path, request: ReplayRequest) -> List[Dict[str, Any]]:
    """One record per draft target: the draft's checkpoint and configuration the replay measured,
    the other target it was matched against (each by its 1-based `target` index, as the compare
    view takes them), the shared task/model/trial identity, and whether the draft has changed
    since (`stale`)."""
    out = []
    for index, target in enumerate(request.targets):
        if target.kind != "draft" or not target.draft:
            continue
        base = request.targets[1 - index]
        stale, reason = draft_staleness(repository, target)
        out.append({"draft": target.draft, "revision": target.revision,
                    "target": index + 1, "base_target": 2 - index,
                    "config_digest": target.config_digest, "base": base.as_dict(),
                    "tasks": list(request.tasks), "model": request.model,
                    "trials": request.repetitions,
                    "pack_digest": request.pack["digest"] if request.pack else None,
                    "evidence": target_evidence(request, target), "key": comparison_key(request),
                    "stale": stale, "stale_reason": reason})
    return out


def engine_analysis(repository: Path, results: Path) -> Dict[str, Any]:
    """`cost_bench.py summarise --json` over one target's native rows, as the CLI prints it:
    the parsed engine output under `result`, or the engine's refusal under `error`."""
    command = [sys.executable, str(Path(repository) / "scripts" / "cost_bench.py"), "summarise",
               "--results", str(results), "--json"]
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": "the engine analysis did not run: " + type(exc).__name__}
    try:
        return {"result": json.loads(done.stdout)}
    except ValueError:
        lines = (done.stderr or "").strip().splitlines()
        return {"error": lines[-1] if lines else "the engine printed no analysis"}


def result_payload(repository: Path, run_root: Path, request: ReplayRequest,
                   summary: Mapping[str, Any]) -> Dict[str, Any]:
    """The result route's `result`: the summary's figures, the engine's analysis as recorded, an
    `analysis_error` when none could be read, and each draft's matched comparison."""
    result = {name: summary[name] for name in (
        "targets", "table", "spend_usd", "reported_spend_usd", "spend_cap_usd", "stopped_at_cap")}
    result["measures"] = summary.get("measures", MEASURES)
    result["evidence"] = replay_evidence(request)
    try:
        result["analysis"] = read_analysis(Path(run_root) / "replay")
        result["analysis_error"] = (None if result["analysis"] is not None else
                                    "no engine analysis was recorded for this run")
    except ReplayError as exc:
        result["analysis"], result["analysis_error"] = None, str(exc)
    result["comparisons"] = draft_comparisons(repository, request)
    return result


def write_analysis(repository: Path, output: Path, summary: Mapping[str, Any]) -> None:
    """Record each target's engine analysis once, beside the summary, for the result route."""
    entries = []
    for index, path in enumerate(summary.get("result_files") or [], 1):
        entries.append(dict(engine_analysis(repository, Path(path)), target=index))
    target = Path(output) / ANALYSIS_NAME
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(entries, stream, sort_keys=True)


def read_analysis(output: Path) -> Optional[List[Dict[str, Any]]]:
    """The recorded engine analysis, or None when the run recorded none."""
    path = Path(output) / ANALYSIS_NAME
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReplayError("replay analysis is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ANALYSIS_BYTES:
            raise ReplayError("replay analysis is unsafe")
        value = json.loads(os.read(descriptor, MAX_ANALYSIS_BYTES + 1).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ReplayError):
            raise
        raise ReplayError("replay analysis is unavailable") from exc
    finally:
        os.close(descriptor)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ReplayError("replay analysis has an invalid shape")
    return value


def _repository_tasks(repository: Path) -> List[Dict[str, str]]:
    try:
        value = json.loads((Path(repository) / "benchmarks" / "tasks.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ReplayError("replay task catalog is unavailable") from exc
    raw = value.get("tasks") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(raw, list)
            or any(not isinstance(item, dict) or not IDENTIFIER.fullmatch(str(item.get("id", "")))
                   for item in raw)):
        raise ReplayError("replay task catalog is invalid")
    return [{"id": item["id"], "label": item["id"].replace("-", " ").title(),
             "long": item.get("long") is True} for item in raw]


def task_catalog(repository: Path) -> Dict[str, Any]:
    """Expose only stable task identities the replay form needs: the repository's own tasks, and
    one entry per production-tier set of each evaluator pack found beside the checkout, with its
    tasks (`packs.discover`). The path of a pack stays on the server; the form chooses one by
    name, digest and set."""
    tasks = _repository_tasks(repository)
    found = packs.discover(Path(repository))
    public = [{key: item[key] for key in ("name", "version", "commit", "digest", "short_digest",
                                           "set", "tasks")} for item in found["packs"]]
    return {"schema_version": 1, "tasks": tasks, "packs": public,
            "default_pack": found["default_digest"], "target_kinds": sorted(TARGET_KINDS),
            "default_model": DEFAULT_MODEL,
            "commands": {"run": " ".join(NATIVE_COMMAND)}}


def validate_task_selection(repository: Path, request: ReplayRequest) -> None:
    if request.pack is not None:
        try:
            chosen = packs.select(Path(repository), request.pack["name"], request.pack["digest"],
                                   pack_set(request.pack))
        except ValueError as exc:
            raise ReplayError(str(exc)) from exc
        available = {item["id"] for item in chosen["tasks"]}
    else:
        available = {item["id"] for item in _repository_tasks(repository)}
    unknown = sorted(set(request.tasks) - available)
    if unknown:
        raise ReplayError("replay requested unknown benchmark tasks: " + ", ".join(unknown))


def read_progress_rows(path: Path, target: ReplayTarget,
                       request: ReplayRequest) -> List[Dict[str, Any]]:
    """Read complete native JSONL rows while the benchmark may still be appending."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SUMMARY_BYTES
                or hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise ReplayError("replay progress is unsafe")
        data = os.read(descriptor, MAX_SUMMARY_BYTES + 1)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ReplayError("replay progress is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data.endswith(b"\n"):
        data = data.rsplit(b"\n", 1)[0] + (b"\n" if b"\n" in data else b"")
    rows = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise ReplayError("replay progress line %d is not JSON" % number) from exc
        rows.append(_validate_native_row(row, number))
    _verify_target_rows(target, rows, request.pack)
    _reconcile_target_rows(request, rows, complete=False)
    return rows


def progress_payload(request: ReplayRequest, rows_by_target: Mapping[int, Sequence[Mapping[str, Any]]]
                     ) -> List[Dict[str, Any]]:
    progress = []
    for index, target in enumerate(request.targets, 1):
        found = {(row.get("task"), row.get("rep"), row.get("arm")): row
                 for row in rows_by_target.get(index, ())}
        for task in request.tasks:
            for repetition in range(1, request.repetitions + 1):
                for arm in ARM_NAMES:
                    row = found.get((task, repetition, arm))
                    progress.append({
                        "target": target.as_dict(), "task": task, "arm": arm,
                        "repetition": repetition,
                        "status": ("pending" if row is None
                                   else "errored" if row.get("error") else "completed"),
                        "passed": row.get("passed") if row is not None else None,
                        "cost_usd": row.get("cost_usd") if row is not None else None,
                    })
    return progress


def index_native_rows(store: run_store.RunStore, source_root: Path,
                      summary: Mapping[str, Any]) -> int:
    """Index preserved native rows without making SQLite their authority."""
    count = 0
    root = Path(source_root).resolve()
    for supplied in summary.get("result_files") or []:
        path = Path(supplied).resolve()
        try:
            # Keyed under the run folder's name, so a second replay of the same revision is a
            # second set of rows rather than an overwrite of the first.
            relative = "runs/" + root.name + "/" + path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReplayError("replay result is outside its authoritative run root") from exc
        for number, row in enumerate(_read_rows(path), 1):
            store.upsert(run_store._benchmark_result(relative, number, row))
            count += 1
    return count


def index_run_directory(store: run_store.RunStore, run_root: Path) -> int:
    """Re-index one Studio run's preserved replay rows, if it was a replay; for rebuilds."""
    summary_path = Path(run_root) / "replay" / SUMMARY_NAME
    if not summary_path.is_file():
        return 0
    return index_native_rows(store, run_root, read_summary(summary_path))
