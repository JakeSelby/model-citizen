"""Validated two-target adapter for the live cost replay."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import run_store, runs, spend_guard, targets

TARGET_KINDS = frozenset(("installed", "release", "branch", "worktree", "draft"))
ARM_NAMES = ("bare", "harness")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
RELEASE_REF = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9._-]+)?$")
SUMMARY_NAME = "replay-summary.json"
RESULTS_NAME = "results.jsonl"
SPEND_NAME = "spend.json"
HISTORY_NAMES = ("history.jsonl", "history.md")
HISTORY_TRANSACTION = ".studio-replay-history-transaction"
HISTORY_STAGE_PREFIX = ".studio-replay-history-stage-"
MAX_SPEND_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 4 * 1024 * 1024


class ReplayError(ValueError):
    """A replay request or native result is unsafe or incomplete."""


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

    @classmethod
    def parse(cls, value: Any) -> "ReplayRequest":
        if not isinstance(value, dict):
            raise ReplayError("replay request must be an object")
        _strict_keys(value, {"targets", "model", "repetitions", "tasks", "max_budget_usd",
                             "spend_cap_usd", "pre_registration"}, "replay request")
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
        return cls(targets, model, repetitions, tuple(raw_tasks), maximum, cap, registration)

    def as_dict(self) -> Dict[str, Any]:
        return {"targets": [target.as_dict() for target in self.targets], "model": self.model,
                "repetitions": self.repetitions, "tasks": list(self.tasks),
                "max_budget_usd": self.max_budget_usd, "spend_cap_usd": self.spend_cap_usd,
                "pre_registration": self.pre_registration}


def resolve_request(value: Any,
                    resolver: Callable[[str, str], Mapping[str, Any]]) -> ReplayRequest:
    """Resolve the two UI references through AH-S301 before spend preview or launch."""
    if not isinstance(value, dict):
        raise ReplayError("replay request must be an object")
    _strict_keys(value, {"targets", "model", "repetitions", "tasks", "max_budget_usd",
                         "spend_cap_usd", "pre_registration"}, "replay request")
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
    if normalized.get("pre_registration") == "":
        normalized["pre_registration"] = None
    return ReplayRequest.parse(normalized)


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
                command="citizen runs replay")


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
                raise ReplayError(str(exc)) from exc
            resolved = {
                "kind": kind, "ref": ref, "revision": built.get("revision"),
                "version": built.get("version"),
                "draft": built.get("draft"),
                "config_digest": built.get("config_digest"),
            }
            return ReplayTarget.parse(resolved).as_dict()
        finally:
            shutil.rmtree(str(temporary), ignore_errors=True)

    def resolve(self, value: Any) -> ReplayRequest:
        request = resolve_request(value, self._resolve)
        validate_task_selection(self.repository, request)
        return request

    def _confirm_resolved(self, request: ReplayRequest) -> None:
        for expected in request.targets:
            actual = ReplayTarget.parse(self._resolve(expected.kind, expected.ref))
            if actual.identity != expected.identity or actual.version != expected.version:
                raise ReplayError("replay target identity changed after spend preview")

    def preview(self, value: Any) -> Dict[str, Any]:
        request = self.resolve(value)
        launch = launch_payload(request, "preview", self.repository)
        try:
            value = self.supervisor.spend_preview(
                self.SUITE_ID, launch["parameters"], launch["target_kind"],
                launch["target_ref"], request.max_budget_usd, request.spend_cap_usd,
                "api_credit", case_identities=launch["case_identities"])
        except runs.RunError as exc:
            raise ReplayError(str(exc)) from exc
        return dict(value, valid=True, errors=[], request=request.as_dict(),
                    command="citizen runs replay")

    def start(self, value: Any, confirmation_token: Any) -> Dict[str, Any]:
        request = ReplayRequest.parse(value)
        self._confirm_resolved(request)
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


def command_for_target(request: ReplayRequest, target: ReplayTarget, repository: Path,
                       output: Path, remaining_cap: Optional[str] = None,
                       history_dir: Optional[Path] = None) -> List[str]:
    repository = Path(repository).resolve()
    cap = remaining_cap or request.spend_cap_usd
    command = [sys.executable, str(repository / "scripts" / "cost_bench.py"), "replay",
               "--tag", target.execution_ref, "--model", request.model,
               "--reps", str(request.repetitions), "--run-cap", request.max_budget_usd,
               "--spend-cap", cap, "--out", str(output)]
    for task in request.tasks:
        command.extend(("--task", task))
    if target.kind == "release":
        registration = _safe_file(repository, request.pre_registration or "")
        command.extend(("--pre-registration", str(registration),
                        "--history-dir", str(history_dir or repository / "benchmarks")))
    else:
        command.append("--exploratory")
    return command


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


def release_history_transaction(repository: Path, action: Callable[[Path], Any]) -> Any:
    """Serialize release-only history writes and publish the JSONL/Markdown pair together."""
    repository = Path(repository).resolve()
    home = repository / "benchmarks"
    with history_lock(home):
        _recover_history_pair(home)
        original = _history_pair(home)
        stage = Path(tempfile.mkdtemp(prefix=HISTORY_STAGE_PREFIX, dir=home))
        try:
            for name, content in original.items():
                if content is not None:
                    (stage / name).write_bytes(content)
            result = action(stage)
            if getattr(result, "returncode", 1) == 0:
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
    row_charge = Decimal(str(round(float(charged_spend(rows, run_cap)), 6)))
    if scored != row_charge:
        raise ReplayError("replay spend sidecar does not match its native rows")
    if abs(charged - preflight - scored) > Decimal("0.000001"):
        raise ReplayError("replay spend sidecar components do not equal charged spend")
    if charged > Decimal(spend_cap) and not value["stopped_at_cap"]:
        raise ReplayError("replay spend sidecar exceeds its cap without stopping")
    return dict(value)


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


def _verify_target_rows(target: ReplayTarget, rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
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
        launch_error: Optional[BaseException] = None
        try:
            if target.kind == "release":
                def launch_release(history_dir: Path) -> Any:
                    release_command = command_for_target(
                        request, target, repository, target_out, format(remaining, "f"), history_dir)
                    return launch(release_command, cwd=str(repository), check=False)

                done = release_history_transaction(repository, launch_release)
            else:
                done = launch(command, cwd=str(repository), check=False)
            returncode = getattr(done, "returncode", 1)
        except Exception as exc:
            launch_error = exc
            returncode = 2
        result_path = target_out / target.execution_ref / RESULTS_NAME
        spend_path = target_out / target.execution_ref / SPEND_NAME
        rows: List[Dict[str, Any]] = []
        try:
            rows = _read_rows(result_path) if result_path.is_file() else []
            _verify_target_rows(target, rows)
            _reconcile_target_rows(request, rows, complete=returncode == 0)
            spend = _read_spend(spend_path, target, request.max_budget_usd,
                                format(remaining, "f"), rows)
            if spend["stopped_at_cap"] != (returncode == 1):
                raise ReplayError("replay spend sidecar does not match the native exit status")
            if returncode in (0, 1) and not rows and not spend["stopped_at_cap"]:
                raise ReplayError("replay target finished without producing a native result")
        except ReplayError as exc:
            charge = round(float(remaining), 6)
            spend_records[index] = {
                "preflight_spend_usd": charge, "scored_spend_usd": 0.0,
                "charged_spend_usd": charge, "stopped_at_cap": True,
            }
            spent += remaining
            stopped = True
            failure = (str(exc), exc)
            break
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
        "model": request.model, "repetitions": request.repetitions,
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
    if (not isinstance(value, dict) or set(value) != required
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


def task_catalog(repository: Path) -> Dict[str, Any]:
    """Expose only stable benchmark task identities needed by the replay form."""
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
    tasks = [{"id": item["id"], "label": item["id"].replace("-", " ").title()}
             for item in raw]
    return {"schema_version": 1, "tasks": tasks, "target_kinds": sorted(TARGET_KINDS),
            "default_model": "claude-haiku-4-5",
            "commands": {"run": "citizen runs replay"}}


def validate_task_selection(repository: Path, request: ReplayRequest) -> None:
    available = {item["id"] for item in task_catalog(repository)["tasks"]}
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
    _verify_target_rows(target, rows)
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
                        "status": "completed" if row is not None else "pending",
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
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReplayError("replay result is outside its authoritative run root") from exc
        for number, row in enumerate(_read_rows(path), 1):
            store.upsert(run_store._benchmark_result(relative, number, row))
            count += 1
    return count
