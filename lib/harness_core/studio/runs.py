"""Allowlisted run catalog and crash-recoverable process supervision."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .state import StateError, Store
from . import run_store, spend_guard

SCHEMA_VERSION = 1
MAX_RUNNING = 3
MAX_OUTPUT_CHUNK = 64 * 1024
ACTIVE = {"admitted", "starting", "running", "cancel_requested"}
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "orphaned", "capped", "limited"}
STATUSES = ACTIVE | TERMINAL | {"queued"}
TARGET_KINDS = {"installed", "release", "branch", "worktree", "draft"}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PARAMETER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PLACEHOLDER = re.compile(r"^\{(?:param:([a-z][a-z0-9_]{0,63})|target_kind|target_ref)\}$")
_DETACHED_WORKERS: Dict[int, subprocess.Popen] = {}


class RunError(ValueError):
    """A catalog or run request is invalid and no process should start."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_keys(value: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    extra = set(value) - set(allowed)
    if extra:
        raise RunError(where + " has unsupported fields: " + ", ".join(sorted(extra)))


@dataclass(frozen=True)
class ParameterSpec:
    kind: str
    values: Tuple[str, ...] = ()
    pattern: Optional[str] = None
    max_length: int = 256
    default: Optional[str] = None

    @classmethod
    def parse(cls, name: str, value: Any) -> "ParameterSpec":
        if not PARAMETER.fullmatch(name) or not isinstance(value, dict):
            raise RunError("invalid parameter declaration: " + name)
        _strict_keys(value, {"kind", "values", "pattern", "max_length", "default"},
                     "parameter " + name)
        kind = value.get("kind")
        maximum = value.get("max_length", 256)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 4096:
            raise RunError("parameter " + name + " has invalid max_length")
        values = value.get("values", [])
        pattern = value.get("pattern")
        if kind == "choice":
            if (not isinstance(values, list) or not values
                    or any(not isinstance(item, str) or not item or len(item) > maximum
                           or "\0" in item for item in values)
                    or len(set(values)) != len(values) or pattern is not None):
                raise RunError("parameter " + name + " needs unique string choices")
        elif kind == "pattern":
            if not isinstance(pattern, str) or not pattern or len(pattern) > 256 or values:
                raise RunError("parameter " + name + " needs one bounded pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RunError("parameter " + name + " has invalid pattern") from exc
        else:
            raise RunError("parameter " + name + " has invalid kind")
        default = value.get("default")
        spec = cls(kind=kind, values=tuple(values), pattern=pattern,
                   max_length=maximum, default=default)
        if default is not None:
            spec.validate(name, default)
        return spec

    def validate(self, name: str, value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > self.max_length or "\0" in value:
            raise RunError("invalid value for parameter " + name)
        if self.kind == "choice" and value not in self.values:
            raise RunError("parameter " + name + " is outside its allowed values")
        if self.kind == "pattern" and not re.fullmatch(self.pattern or "", value):
            raise RunError("parameter " + name + " does not match its allowed pattern")
        return value


@dataclass(frozen=True)
class SuiteSpec:
    suite_id: str
    version: int
    argv: Tuple[str, ...]
    parameters: Mapping[str, ParameterSpec]
    cost_class: str
    expected_duration_seconds: int
    timeout_seconds: int
    targets: Tuple[str, ...]
    cases: Tuple[str, ...]

    @classmethod
    def parse(cls, value: Any) -> "SuiteSpec":
        if not isinstance(value, dict):
            raise RunError("suite entries must be objects")
        _strict_keys(value, {"id", "version", "argv", "parameters", "cost_class",
                             "expected_duration_seconds", "timeout_seconds", "targets", "cases"},
                     "suite")
        suite_id = value.get("id")
        version = value.get("version")
        argv = value.get("argv")
        parameters = value.get("parameters", {})
        cost_class = value.get("cost_class")
        expected = value.get("expected_duration_seconds")
        timeout = value.get("timeout_seconds")
        targets = value.get("targets")
        cases = value.get("cases", [])
        if not isinstance(suite_id, str) or not IDENTIFIER.fullmatch(suite_id):
            raise RunError("suite id must be a lowercase identifier")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise RunError("suite " + suite_id + " has invalid version")
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(token, str) or not token or "\0" in token for token in argv)):
            raise RunError("suite " + suite_id + " needs an argv string list")
        if PLACEHOLDER.fullmatch(argv[0]) or "{" in argv[0] or "}" in argv[0]:
            raise RunError("suite " + suite_id + " executable must be literal")
        if not isinstance(parameters, dict):
            raise RunError("suite " + suite_id + " parameters must be an object")
        parsed = {name: ParameterSpec.parse(name, item) for name, item in parameters.items()}
        for token in argv[1:]:
            if "{" in token or "}" in token:
                match = PLACEHOLDER.fullmatch(token)
                if not match:
                    raise RunError("suite " + suite_id + " placeholders must fill one argv element")
                if match.group(1) and match.group(1) not in parsed:
                    raise RunError("suite " + suite_id + " references an undeclared parameter")
        used = {match.group(1) for token in argv[1:] for match in [PLACEHOLDER.fullmatch(token)]
                if match and match.group(1)}
        if set(parsed) - used:
            raise RunError("suite " + suite_id + " declares unused parameters")
        if cost_class not in ("free", "spends_usage"):
            raise RunError("suite " + suite_id + " has invalid cost_class")
        if (not isinstance(expected, int) or isinstance(expected, bool)
                or not 1 <= expected <= 604800):
            raise RunError("suite " + suite_id + " has invalid expected duration")
        if (not isinstance(timeout, int) or isinstance(timeout, bool)
                or not expected <= timeout <= 604800):
            raise RunError("suite " + suite_id + " has invalid timeout")
        if (not isinstance(targets, list) or not targets or len(set(targets)) != len(targets)
                or any(target not in TARGET_KINDS for target in targets)):
            raise RunError("suite " + suite_id + " has invalid targets")
        if (not isinstance(cases, list) or len(set(cases)) != len(cases)
                or any(not isinstance(case, str) or not IDENTIFIER.fullmatch(case)
                       for case in cases)):
            raise RunError("suite " + suite_id + " has invalid cases")
        return cls(suite_id, version, tuple(argv), parsed, cost_class, expected, timeout,
                   tuple(targets), tuple(cases))

    def render(self, parameters: Mapping[str, str], target_kind: str,
               target_ref: str) -> List[str]:
        if target_kind not in self.targets:
            raise RunError("suite " + self.suite_id + " does not support target " + target_kind)
        if (not isinstance(target_ref, str) or not target_ref or len(target_ref) > 4096
                or "\0" in target_ref):
            raise RunError("invalid target reference")
        if set(parameters) - set(self.parameters):
            raise RunError("unsupported parameters: " + ", ".join(sorted(set(parameters) - set(self.parameters))))
        resolved: Dict[str, str] = {}
        for name, spec in self.parameters.items():
            supplied = parameters.get(name, spec.default)
            if supplied is None:
                raise RunError("missing parameter " + name)
            resolved[name] = spec.validate(name, supplied)
        rendered = []
        for token in self.argv:
            match = PLACEHOLDER.fullmatch(token)
            if not match:
                rendered.append(token)
            elif match.group(1):
                rendered.append(resolved[match.group(1)])
            elif token == "{target_kind}":
                rendered.append(target_kind)
            else:
                rendered.append(target_ref)
        return rendered


class SuiteCatalog:
    def __init__(self, suites: Sequence[SuiteSpec]):
        self.suites = {suite.suite_id: suite for suite in suites}
        if len(self.suites) != len(suites):
            raise RunError("suite ids must be unique")

    @classmethod
    def load(cls, path: Path) -> "SuiteCatalog":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunError("suite catalog is unreadable") from exc
        if not isinstance(value, dict):
            raise RunError("suite catalog must be an object")
        _strict_keys(value, {"schema_version", "suites"}, "suite catalog")
        if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("suites"), list):
            raise RunError("unsupported suite catalog schema")
        return cls([SuiteSpec.parse(item) for item in value["suites"]])

    def get(self, suite_id: str) -> SuiteSpec:
        try:
            return self.suites[suite_id]
        except KeyError as exc:
            raise RunError("unknown suite: " + str(suite_id)) from exc


def default_catalog_path(root: Path) -> Path:
    return Path(root) / "policy" / "studio" / "suites.json"


def _atomic_json(directory_fd: int, name: str, value: Mapping[str, Any]) -> None:
    temporary = "." + name + "." + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory_fd)


def _load_json(directory_fd: int, name: str) -> Dict[str, Any]:
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise RunError("run state must be a mode-0600 regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunError("run state is owned by another user")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            value = json.load(stream)
    except (OSError, ValueError) as exc:
        raise RunError("run state is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise RunError("unsupported run state schema")
    return value


def _open_directory(parent_fd: int, name: str, create: bool = False) -> int:
    if not name or name in (".", "..") or os.sep in name:
        raise RunError("run state has an unsafe directory name")
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=parent_fd)
    except OSError as exc:
        raise RunError("run state directory is missing or unsafe") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise RunError("run state path is not a directory")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        os.close(descriptor)
        raise RunError("run state directory is owned by another user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.fchmod(descriptor, 0o700)
    return descriptor


def process_identity(pid: int) -> Optional[str]:
    """Return a start identity for a live process, not merely its reusable PID."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    proc = Path("/proc") / str(pid) / "stat"
    try:
        value = proc.read_text(encoding="utf-8")
        closing = value.rfind(")")
        if closing < 0:
            raise ValueError("missing process command boundary")
        fields = value[closing + 1:].split()
        return "proc:" + fields[19]
    except (OSError, IndexError, ValueError):
        pass
    try:
        environment = dict(os.environ)
        environment.update({"LC_ALL": "C", "LANG": "C"})
        found = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                               text=True, timeout=2, check=False, env=environment)
    except (OSError, subprocess.SubprocessError):
        return None
    value = found.stdout.strip()
    return "ps:" + value if found.returncode == 0 and value else None


def process_matches(pid: Any, identity: Any) -> bool:
    return isinstance(pid, int) and isinstance(identity, str) and process_identity(pid) == identity


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_group(pgid: int, identity: str, timeout: float = 5.0) -> bool:
    """Stop the exact process group leader within the bounded cancellation window."""
    if not process_matches(pgid, identity):
        return not _group_exists(pgid)
    return terminate_owned_group(pgid, timeout)


def terminate_owned_group(pgid: int, timeout: float = 5.0,
                          process: Optional[subprocess.Popen] = None) -> bool:
    """Stop a process group whose creator still owns its lifecycle."""
    started = time.monotonic()
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGTERM)
    soft_deadline = started + min(4.0, max(0.0, timeout - 1.0))
    while time.monotonic() < soft_deadline:
        if process is not None:
            process.poll()
        if not _group_exists(pgid):
            return True
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
    deadline = started + timeout
    while time.monotonic() < deadline:
        if process is not None:
            process.poll()
        if not _group_exists(pgid):
            return True
        time.sleep(0.02)
    return not _group_exists(pgid)


class RunSupervisor:
    def __init__(self, state_root: Path, catalog_path: Path, max_running: int = MAX_RUNNING):
        self.state_root = Path(state_root)
        self.catalog_path = Path(catalog_path).resolve()
        if (not isinstance(max_running, int) or isinstance(max_running, bool)
                or not 1 <= max_running <= MAX_RUNNING):
            raise RunError("max_running must be between one and three")
        self.max_running = max_running
        self.runs_dir = self.state_root / "runs"
        self.lock_path = self.state_root / "supervisor.lock"
        self.queue_path = self.state_root / "queue.json"
        try:
            with Store(self.state_root) as store:
                self._state_fd = os.dup(store.fd)
        except (OSError, StateError) as exc:
            raise RunError(str(exc)) from exc
        self._runs_fd = _open_directory(self._state_fd, "runs", create=True)
        self._confirmations_fd = _open_directory(self._state_fd, "confirmations", create=True)
        try:
            self.history = run_store.RunStore(self.state_root)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc

    def __del__(self):
        history = getattr(self, "history", None)
        if history is not None:
            with contextlib.suppress(Exception):
                history.close()
        for name in ("_confirmations_fd", "_runs_fd", "_state_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)

    @contextlib.contextmanager
    def lock(self):
        try:
            descriptor = os.open("supervisor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._state_fd)
        except OSError as exc:
            raise RunError("run lock is missing or unsafe") from exc
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise RunError("run lock is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            yield

    def _run_path(self, run_id: str) -> Path:
        try:
            uuid.UUID(run_id)
        except (ValueError, TypeError) as exc:
            raise RunError("invalid run id") from exc
        return self.runs_dir / run_id / "run.json"

    def _run_directory(self, run_id: str, create: bool = False) -> int:
        self._run_path(run_id)
        return _open_directory(self._runs_fd, run_id, create=create)

    def _read(self, run_id: str) -> Dict[str, Any]:
        descriptor = self._run_directory(run_id)
        try:
            try:
                authoritative, authority = self.history.read_studio(descriptor, run_id)
            except run_store.RunStoreError as exc:
                raise RunError(str(exc)) from exc
            if authoritative is None:
                record = self._validate_record(_load_json(descriptor, "run.json"), run_id)
                try:
                    self.history.append_studio(descriptor, record)
                    record, authority = self.history.read_studio(descriptor, run_id)
                    self.history.upsert(run_store.studio_record(record, authority or {}))
                except run_store.RunStoreError as exc:
                    raise RunError(str(exc)) from exc
                return record
            record = self._validate_record(authoritative, run_id)
            try:
                self.history.upsert(run_store.studio_record(record, authority or {}))
            except run_store.RunStoreError as exc:
                raise RunError(str(exc)) from exc
            try:
                cached = self._validate_record(_load_json(descriptor, "run.json"), run_id)
            except RunError:
                cached = None
            if cached != record:
                _atomic_json(descriptor, "run.json", record)
            return record
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_record(record: Dict[str, Any], run_id: str) -> Dict[str, Any]:
        required = {"schema_version", "run_id", "suite_id", "suite_version", "parameters",
                    "target", "argv", "cost_class", "expected_duration_seconds",
                    "timeout_seconds", "status", "queue_sequence", "created_at",
                    "case_identities", "spend_estimate", "spend_cap", "pricing_identity",
                    "canonical_run_digest"}
        optional = {"admission_token", "runner_pid", "runner_identity", "command_pid", "command_identity",
                    "started_at", "completed_at", "reason", "returncode", "capacity_reserved",
                    "spend_actual", "spend_stop_reason", "case_results", "usage_ledger_state"}
        if set(record) - required - optional or required - set(record):
            raise RunError("run state record has unsupported or missing fields")
        if record.get("schema_version") != SCHEMA_VERSION or record.get("run_id") != run_id:
            raise RunError("run state record is incomplete or inconsistent")
        try:
            uuid.UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise RunError("run state record has an invalid run id") from exc
        if (not isinstance(record.get("suite_id"), str)
                or not IDENTIFIER.fullmatch(record["suite_id"])):
            raise RunError("run state record has an invalid suite id")
        for name in ("suite_version", "queue_sequence"):
            value = record.get(name)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or not 1 <= value <= (2 ** 63 - 1)):
                raise RunError("run state record has an invalid " + name)
        parameters = record.get("parameters")
        if (not isinstance(parameters, dict)
                or any(not isinstance(name, str) or not PARAMETER.fullmatch(name)
                       or not isinstance(value, str) or not value or "\0" in value
                       for name, value in parameters.items())):
            raise RunError("run state record has invalid parameters")
        target = record.get("target")
        if (not isinstance(target, dict)
                or set(target) != {"kind", "ref", "revision", "draft", "config_digest"}
                or target.get("kind") not in TARGET_KINDS
                or not isinstance(target.get("ref"), str) or not target["ref"]
                or len(target["ref"]) > 4096 or "\0" in target["ref"]
                or (target.get("revision") is not None
                    and (not isinstance(target["revision"], str) or not target["revision"]
                         or len(target["revision"]) > 4096 or "\0" in target["revision"]))
                or (target.get("config_digest") is not None
                    and (not isinstance(target["config_digest"], str)
                         or not target["config_digest"] or len(target["config_digest"]) > 4096
                         or "\0" in target["config_digest"]))
                or target.get("draft") != (target["ref"] if target["kind"] == "draft" else None)):
            raise RunError("run state record has an invalid target")
        argv = record.get("argv")
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(item, str) or not item or "\0" in item for item in argv)):
            raise RunError("run state record has invalid argv")
        if record.get("cost_class") not in ("free", "spends_usage"):
            raise RunError("run state record has an invalid cost class")
        case_identities = record.get("case_identities")
        if (case_identities is not None
                and (not isinstance(case_identities, list)
                     or any(not isinstance(value, str) or not value for value in case_identities)
                     or len(set(case_identities)) != len(case_identities))):
            raise RunError("run state record has invalid case identities")
        paid = record.get("cost_class") == "spends_usage"
        if paid:
            estimate = record.get("spend_estimate")
            caps = record.get("spend_cap")
            pricing = record.get("pricing_identity")
            if (not isinstance(estimate, dict)
                    or set(estimate) != {"amount_usd", "basis", "sample_count", "suite_id",
                                        "case_count"}
                    or estimate.get("suite_id") != record.get("suite_id")
                    or estimate.get("basis") not in ("no_history", "median_same_suite_scale")
                    or not isinstance(estimate.get("sample_count"), int)
                    or isinstance(estimate.get("sample_count"), bool)
                    or estimate["sample_count"] < 0
                    or not isinstance(estimate.get("case_count"), int)
                    or estimate["case_count"] != len(case_identities or [])
                    or (estimate.get("amount_usd") is not None
                        and (not isinstance(estimate["amount_usd"], (int, float))
                             or isinstance(estimate["amount_usd"], bool)
                             or not math.isfinite(estimate["amount_usd"])
                             or estimate["amount_usd"] < 0))):
                raise RunError("run state record has invalid spend estimate")
            if (not isinstance(caps, dict)
                    or set(caps) != {"max_budget_usd", "spend_cap_usd"}
                    or any(not spend_guard.valid_money_text(caps.get(name))
                           for name in caps)
                    or Decimal(caps["max_budget_usd"]) > Decimal(caps["spend_cap_usd"])):
                raise RunError("run state record has invalid spend cap")
            if (not isinstance(pricing, dict) or set(pricing) != {"source", "basis"}
                    or pricing.get("source") not in spend_guard.PRICING_SOURCES
                    or not isinstance(pricing.get("basis"), str) or not pricing["basis"]):
                raise RunError("run state record has invalid pricing identity")
        elif any(record.get(name) is not None
                 for name in ("spend_estimate", "spend_cap", "pricing_identity")):
            raise RunError("free run state record carries a spend contract")
        try:
            run_store._immutable_digest(record)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc
        expected = record.get("expected_duration_seconds")
        timeout = record.get("timeout_seconds")
        if (not isinstance(expected, int) or isinstance(expected, bool)
                or not 1 <= expected <= 604800
                or not isinstance(timeout, int) or isinstance(timeout, bool)
                or not expected <= timeout <= 604800):
            raise RunError("run state record has invalid duration limits")
        if record.get("status") not in STATUSES:
            raise RunError("run state record has an invalid status")
        for name in ("created_at", "started_at", "completed_at", "reason",
                     "runner_identity", "command_identity"):
            if name in record and (not isinstance(record[name], str) or not record[name]):
                raise RunError("run state record has an invalid " + name)
        for name in ("created_at", "started_at", "completed_at"):
            if name in record:
                try:
                    parsed = dt.datetime.fromisoformat(record[name].replace("Z", "+00:00"))
                except ValueError as exc:
                    raise RunError("run state record has an invalid " + name) from exc
                if parsed.tzinfo is None:
                    raise RunError("run state record has an invalid " + name)
        for name in ("runner_pid", "command_pid"):
            if name in record and (not isinstance(record[name], int)
                                   or isinstance(record[name], bool)
                                   or not 1 <= record[name] <= (2 ** 31 - 1)):
                raise RunError("run state record has an invalid " + name)
        if "returncode" in record and (not isinstance(record["returncode"], int)
                                        or isinstance(record["returncode"], bool)
                                        or not -255 <= record["returncode"] <= 255):
            raise RunError("run state record has an invalid returncode")
        if "spend_actual" in record and (not isinstance(record["spend_actual"], (int, float))
                                          or isinstance(record["spend_actual"], bool)
                                          or not math.isfinite(record["spend_actual"])
                                          or record["spend_actual"] < 0):
            raise RunError("run state record has invalid actual spend")
        if ("spend_stop_reason" in record
                and record["spend_stop_reason"] not in spend_guard.STOP_REASONS):
            raise RunError("run state record has invalid spend stop reason")
        if "case_results" in record:
            results = record["case_results"]
            if (not isinstance(results, list) or len(results) != len(case_identities or [])
                    or [item.get("id") for item in results if isinstance(item, dict)]
                    != list(case_identities or [])
                    or any(not isinstance(item, dict)
                           or set(item) != {"id", "status", "spend_usd"}
                           or item.get("status") not in ("completed", "not_run")
                           or not isinstance(item.get("spend_usd"), (int, float))
                           or isinstance(item.get("spend_usd"), bool)
                           or not math.isfinite(item["spend_usd"])
                           or item["spend_usd"] < 0 for item in results)):
                raise RunError("run state record has invalid case results")
        if ("usage_ledger_state" in record
                and record["usage_ledger_state"] not in ("pending", "recorded")):
            raise RunError("run state record has invalid usage ledger state")
        if ("usage_ledger_state" in record
                and (not paid or "spend_actual" not in record or record["status"] not in TERMINAL)):
            raise RunError("only a terminal paid run may carry usage ledger state")
        if "capacity_reserved" in record and record["capacity_reserved"] is not True:
            raise RunError("run state record has an invalid capacity reservation")
        if ("admission_token" in record
                and (not isinstance(record["admission_token"], str)
                     or not re.fullmatch(r"[0-9a-f]{32}", record["admission_token"]))):
            raise RunError("run state record has an invalid admission token")
        if ("runner_pid" in record) != ("runner_identity" in record):
            raise RunError("run state record has an incomplete runner identity")
        if ("command_pid" in record) != ("command_identity" in record):
            raise RunError("run state record has an incomplete command identity")
        if record["status"] in ("starting", "running") and "runner_pid" not in record:
            raise RunError("run state record is missing its runner identity")
        if record["status"] == "admitted" and ("admission_token" not in record
                                                or "runner_pid" in record):
            raise RunError("admitted run state has an invalid handoff")
        if record["status"] == "starting" and "admission_token" not in record:
            raise RunError("starting run state has no admission token")
        if record["status"] == "running" and "command_pid" not in record:
            raise RunError("run state record is missing its command identity")
        if record["status"] in TERMINAL and "completed_at" not in record:
            raise RunError("terminal run state has no completion time")
        if record["status"] == "queued" and set(record) & optional:
            raise RunError("queued run state contains lifecycle fields")
        if record["status"] == "starting" and ("command_pid" in record
                                                or "completed_at" in record):
            raise RunError("starting run state has invalid lifecycle fields")
        if record["status"] == "running" and ("started_at" not in record
                                               or "completed_at" in record):
            raise RunError("running run state has invalid lifecycle fields")
        if record["status"] == "cancel_requested" and "runner_pid" not in record:
            raise RunError("cancel-requested run state has no runner identity")
        if "capacity_reserved" in record and record["status"] != "orphaned":
            raise RunError("only an orphaned run may reserve capacity")
        return record

    @staticmethod
    def _public(record: Mapping[str, Any]) -> Dict[str, Any]:
        private = {"admission_token", "argv", "parameters", "stdout_path", "stderr_path",
                   "worker_log_path", "profile_path"}
        public = {name: value for name, value in record.items() if name not in private}
        target = record.get("target", {})
        public["target"] = {"kind": target.get("kind"), "reference_set": bool(target.get("ref"))}
        public["command"] = {"argument_count": len(record.get("argv", []))}
        parameters = record.get("parameters", {})
        public["parameters"] = {"count": len(parameters), "names": sorted(parameters)}
        return public

    def _write(self, record: Mapping[str, Any]) -> None:
        run_id = str(record["run_id"])
        validated = self._validate_record(dict(record), run_id)
        descriptor = self._run_directory(run_id, create=True)
        try:
            try:
                self.history.append_studio(descriptor, validated)
                _, authority = self.history.read_studio(descriptor, run_id)
                self.history.upsert(run_store.studio_record(validated, authority or {}))
            except run_store.RunStoreError as exc:
                raise RunError(str(exc)) from exc
            _atomic_json(descriptor, "run.json", validated)
        finally:
            os.close(descriptor)

    def _records(self) -> List[Dict[str, Any]]:
        records = []
        for run_id in sorted(os.listdir(self._runs_fd)):
            records.append(self._read(run_id))
        return records

    def reindex(self, repository: Path) -> Dict[str, Any]:
        """Rebuild the disposable index from sidecars and recognized repository evidence."""
        with self.lock():
            self._records()  # bootstrap sidecars for runs created before the index existed
            try:
                return self.history.reindex(Path(repository), self._runs_fd)
            except run_store.RunStoreError as exc:
                raise RunError(str(exc)) from exc

    def _next_sequence(self) -> int:
        try:
            queue = _load_json(self._state_fd, "queue.json")
            sequence = queue.get("next_sequence")
            if not isinstance(sequence, int) or sequence < 1:
                raise RunError("invalid queue sequence")
        except RunError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
            sequence = 1
        _atomic_json(self._state_fd, "queue.json", {"schema_version": SCHEMA_VERSION,
                                                    "next_sequence": sequence + 1})
        return sequence

    @staticmethod
    def _confirmation_name(token: str) -> str:
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise RunError("paid run requires a valid confirmation token")
        return hashlib.sha256(token.encode("ascii")).hexdigest() + ".json"

    def _issue_confirmation_locked(self, request_digest: str) -> str:
        token = secrets.token_hex(32)
        _atomic_json(self._confirmations_fd, self._confirmation_name(token), {
            "schema_version": 1,
            "request_digest": request_digest,
            "created_at": utc_now(),
        })
        return token

    def _consume_confirmation_locked(self, token: Optional[str], request_digest: str) -> None:
        name = self._confirmation_name(token or "")
        try:
            pending = _load_json(self._confirmations_fd, name)
        except RunError as exc:
            raise RunError("paid run confirmation is missing, invalid, or already used") from exc
        if (set(pending) != {"schema_version", "request_digest", "created_at"}
                or not isinstance(pending.get("request_digest"), str)
                or not hmac.compare_digest(pending["request_digest"], request_digest)):
            raise RunError("paid run confirmation does not match the exact displayed request")
        try:
            os.unlink(name, dir_fd=self._confirmations_fd)
            os.fsync(self._confirmations_fd)
        except OSError as exc:
            raise RunError("paid run confirmation could not be consumed") from exc

    def open_run_output(self, run_id: str, name: str):
        if name not in ("stdout.log", "stderr.log", "worker.log"):
            raise RunError("unsupported run output")
        directory = self._run_directory(run_id)
        descriptor = -1
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RunError("run output is not a regular file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise RunError("run output is owned by another user")
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            stream = os.fdopen(descriptor, "ab", buffering=0)
            descriptor = -1
            return stream
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    def read_output(self, run_id: str, stream: str, cursor: int = 0,
                    limit: int = MAX_OUTPUT_CHUNK) -> Dict[str, Any]:
        """Read one bounded UTF-8 chunk; cursors are byte offsets at code-point boundaries."""
        if stream not in ("stdout", "stderr"):
            raise RunError("output stream must be stdout or stderr")
        if (not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                or cursor > (2 ** 63 - 1)):
            raise RunError("output cursor must be a non-negative integer")
        if (not isinstance(limit, int) or isinstance(limit, bool)
                or not 1 <= limit <= MAX_OUTPUT_CHUNK):
            raise RunError("output limit must be between one and 65536 bytes")
        with self.lock():
            self._recover_locked()
            record = self._read(run_id)
            directory = self._run_directory(run_id)
            descriptor = -1
            try:
                try:
                    descriptor = os.open(stream + ".log", os.O_RDONLY | os.O_NOFOLLOW,
                                         dir_fd=directory)
                except FileNotFoundError:
                    data = b""
                    size = 0
                except OSError as exc:
                    raise RunError("run output is missing or unsafe") from exc
                else:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode)
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise RunError("run output must be a mode-0600 regular file")
                    if hasattr(os, "getuid") and info.st_uid != os.getuid():
                        raise RunError("run output is owned by another user")
                    size = os.fstat(descriptor).st_size
                    if cursor < size:
                        leading = os.pread(descriptor, 1, cursor)
                        if leading and leading[0] & 0xC0 == 0x80:
                            raise RunError("output cursor is not at a UTF-8 boundary")
                    data = os.pread(descriptor, limit, cursor)
                    while data:
                        try:
                            data.decode("utf-8")
                            break
                        except UnicodeDecodeError as exc:
                            if exc.reason != "unexpected end of data" or exc.end != len(data):
                                break
                            size = os.fstat(descriptor).st_size
                            if cursor + len(data) < size and len(data) < limit + 3:
                                data += os.pread(descriptor, 1, cursor + len(data))
                                continue
                            if record["status"] not in TERMINAL:
                                data = data[:exc.start]
                            break
                    size = os.fstat(descriptor).st_size
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                os.close(directory)
            next_cursor = cursor + len(data)
            return {
                "run_id": run_id,
                "stream": stream,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "chunk": data.decode("utf-8", errors="replace"),
                "eof": record["status"] in TERMINAL and next_cursor >= size,
            }

    def prepare_profile(self, run_id: str) -> Path:
        directory = self._run_directory(run_id)
        try:
            profile = _open_directory(directory, "profile", create=True)
            os.close(profile)
        finally:
            os.close(directory)
        return self.runs_dir / run_id / "profile"

    def _recover_locked(self) -> None:
        for pid, process in list(_DETACHED_WORKERS.items()):
            if process.poll() is not None:
                _DETACHED_WORKERS.pop(pid, None)
        for record in self._records():
            if record.get("capacity_reserved") is True:
                command_pid = record.get("command_pid")
                if not isinstance(command_pid, int) or not _group_exists(command_pid):
                    record.pop("capacity_reserved")
                    self._write(record)
                continue
            if record.get("status") not in ACTIVE:
                continue
            runner_ok = process_matches(record.get("runner_pid"), record.get("runner_identity"))
            command_pid = record.get("command_pid")
            command_identity = record.get("command_identity")
            command_ok = process_matches(command_pid, command_identity)
            if runner_ok:
                continue
            if command_ok:
                stopped = terminate_group(command_pid, command_identity)
            else:
                stopped = not isinstance(command_pid, int) or not _group_exists(command_pid)
            record["status"] = "orphaned"
            record.pop("admission_token", None)
            record["completed_at"] = utc_now()
            record["reason"] = "supervisor or command identity was not recoverable"
            if not stopped:
                record["capacity_reserved"] = True
            self._write(record)
        for record in self._records():
            self._settle_usage_locked(record)

    def _settle_usage_locked(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        current = dict(record)
        if current.get("usage_ledger_state") != "pending":
            return current
        if spend_guard.upsert_usage(self.state_root.parent / "usage.jsonl", current):
            current["usage_ledger_state"] = "recorded"
            self._write(current)
        return current

    def _spawn_locked(self, record: Dict[str, Any]) -> None:
        admission_token = uuid.uuid4().hex
        record["status"] = "admitted"
        record["admission_token"] = admission_token
        self._write(record)
        command = [sys.executable, "-m", "harness_core.studio.run_worker",
                   "--state-root", str(self.state_root), "--catalog", str(self.catalog_path),
                   "--run-id", record["run_id"], "--max-running", str(self.max_running),
                   "--admission-token", admission_token]
        env = dict(os.environ)
        library = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = library + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            with self.open_run_output(record["run_id"], "worker.log") as errors:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=errors,
                                           close_fds=True, start_new_session=True, env=env)
        except (OSError, RunError) as exc:
            record.pop("admission_token", None)
            record["status"] = "failed"
            record["completed_at"] = utc_now()
            record["reason"] = "run worker could not start"
            self._write(record)
            return
        record["status"] = "starting"
        record["runner_pid"] = process.pid
        record["runner_identity"] = process_identity(process.pid)
        if record["runner_identity"] is None:
            record.pop("runner_pid", None)
            record.pop("runner_identity", None)
            record.pop("admission_token", None)
            record["status"] = "orphaned"
            record["completed_at"] = utc_now()
            record["reason"] = "run worker identity could not be established"
            record["capacity_reserved"] = True
            _DETACHED_WORKERS[process.pid] = process
            self._write(record)
            return
        _DETACHED_WORKERS[process.pid] = process
        self._write(record)

    def _admit_locked(self) -> None:
        while True:
            records = self._records()
            released = False
            for record in records:
                if record.get("capacity_reserved") is not True:
                    continue
                command_pid = record.get("command_pid")
                if not isinstance(command_pid, int) or not _group_exists(command_pid):
                    record.pop("capacity_reserved")
                    self._write(record)
                    released = True
            if released:
                continue
            occupied = sum(record.get("status") in ACTIVE
                           or record.get("capacity_reserved") is True for record in records)
            if occupied >= self.max_running:
                return
            queued = sorted((record for record in records if record.get("status") == "queued"),
                            key=lambda item: (item.get("queue_sequence", 0), item["run_id"]))
            if not queued:
                return
            self._spawn_locked(queued[0])

    def reconcile_and_drain(self) -> None:
        with self.lock():
            self._recover_locked()
            self._admit_locked()

    def start(self, suite_id: str, parameters: Mapping[str, str], target_kind: str,
              target_ref: str, *, confirmed: Optional[str] = None,
              max_budget_usd: Any = None, spend_cap_usd: Any = None,
              pricing_source: Optional[str] = None) -> Dict[str, Any]:
        catalog = SuiteCatalog.load(self.catalog_path)
        suite = catalog.get(suite_id)
        argv = suite.render(parameters, target_kind, target_ref)
        cases = list(suite.cases or (suite.suite_id,))
        if (suite.cost_class != "spends_usage"
                and (confirmed is not None or max_budget_usd is not None
                     or spend_cap_usd is not None or pricing_source is not None)):
            raise RunError("spend flags apply only to suites that spend usage")
        run_id = str(uuid.uuid4())
        with self.lock():
            self._recover_locked()
            spend_plan = None
            if suite.cost_class == "spends_usage":
                try:
                    spend_plan = spend_guard.plan(self._records(), suite.suite_id, cases,
                                                  max_budget_usd, spend_cap_usd, pricing_source)
                    request = spend_guard.confirmation_request(
                        suite.suite_id, suite.version, parameters, target_kind, target_ref,
                        cases, spend_plan)
                    self._consume_confirmation_locked(
                        confirmed, spend_guard.confirmation_digest(request))
                    argv = spend_guard.guarded_argv(argv, spend_plan)
                except spend_guard.SpendGuardError as exc:
                    raise RunError(str(exc)) from exc
            record: Dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "suite_id": suite.suite_id,
                "suite_version": suite.version,
                "parameters": dict(sorted(parameters.items())),
                "target": {
                    "kind": target_kind,
                    "ref": target_ref,
                    "revision": None,
                    "draft": target_ref if target_kind == "draft" else None,
                    "config_digest": None,
                },
                "argv": argv,
                "cost_class": suite.cost_class,
                "expected_duration_seconds": suite.expected_duration_seconds,
                "timeout_seconds": suite.timeout_seconds,
                "case_identities": cases if spend_plan else None,
                "spend_estimate": spend_plan["estimate"] if spend_plan else None,
                "spend_cap": spend_plan["caps"] if spend_plan else None,
                "pricing_identity": spend_plan["pricing"] if spend_plan else None,
                "status": "queued",
                "queue_sequence": self._next_sequence(),
                "created_at": utc_now(),
            }
            record["canonical_run_digest"] = run_store._canonical_run_digest(record)
            self._write(record)
            self._admit_locked()
            return self._public(self._read(run_id))

    def spend_preview(self, suite_id: str, parameters: Mapping[str, str], target_kind: str,
                      target_ref: str, max_budget_usd: Any, spend_cap_usd: Any,
                      pricing_source: Optional[str]) -> Dict[str, Any]:
        suite = SuiteCatalog.load(self.catalog_path).get(suite_id)
        suite.render(parameters, target_kind, target_ref)
        if suite.cost_class != "spends_usage":
            return {"cost_class": "free", "confirmation_required": False}
        cases = list(suite.cases or (suite.suite_id,))
        with self.lock():
            self._recover_locked()
            try:
                preview = spend_guard.plan(self._records(), suite.suite_id, cases,
                                           max_budget_usd, spend_cap_usd, pricing_source)
            except spend_guard.SpendGuardError as exc:
                raise RunError(str(exc)) from exc
            request = spend_guard.confirmation_request(
                suite.suite_id, suite.version, parameters, target_kind, target_ref, cases, preview)
            token = self._issue_confirmation_locked(spend_guard.confirmation_digest(request))
            return dict(preview, confirmation_token=token, cost_class=suite.cost_class,
                        case_identities=cases)

    def list(self) -> List[Dict[str, Any]]:
        self.reconcile_and_drain()
        with self.lock():
            records = sorted(self._records(), key=lambda item: (item.get("queue_sequence", 0),
                                                                  item["run_id"]))
            return [self._public(record) for record in records]

    def show(self, run_id: str) -> Dict[str, Any]:
        self.reconcile_and_drain()
        with self.lock():
            return self._public(self._read(run_id))

    def cancel(self, run_id: str) -> Dict[str, Any]:
        pid = None
        identity = None
        with self.lock():
            record = self._read(run_id)
            if record["status"] in TERMINAL:
                return self._public(record)
            if record["status"] in ("queued", "admitted"):
                record.pop("admission_token", None)
                record["status"] = "cancelled"
                record["completed_at"] = utc_now()
                self._write(record)
                with contextlib.suppress(RunError):
                    self._admit_locked()
                return self._public(record)
            runner_ok = process_matches(record.get("runner_pid"), record.get("runner_identity"))
            command_ok = process_matches(record.get("command_pid"),
                                         record.get("command_identity"))
            if not runner_ok and not command_ok:
                command_pid = record.get("command_pid")
                record.pop("admission_token", None)
                record["status"] = "orphaned"
                record["completed_at"] = utc_now()
                record["reason"] = "run ownership could not be recovered for cancellation"
                if isinstance(command_pid, int) and _group_exists(command_pid):
                    record["capacity_reserved"] = True
                self._write(record)
                with contextlib.suppress(RunError):
                    self._admit_locked()
                return self._public(record)
            record["status"] = "cancel_requested"
            self._write(record)
            if command_ok:
                pid, identity = record["command_pid"], record["command_identity"]
        stopped = None if pid is None else bool(identity and terminate_group(pid, identity))
        with self.lock():
            record = self._read(run_id)
            if record["status"] not in TERMINAL:
                if stopped is None:
                    return self._public(record)
                record["status"] = "cancelled" if stopped else "orphaned"
                record["completed_at"] = utc_now()
                if stopped is False:
                    record["reason"] = "process identity could not be stopped safely"
                    record["capacity_reserved"] = True
                self._write(record)
            with contextlib.suppress(RunError):
                self._admit_locked()
            return self._public(self._read(run_id))

    def fail(self, run_id: str, reason: str) -> None:
        """Record a pre-launch worker failure and release its slot to the FIFO queue."""
        with self.lock():
            record = self._read(run_id)
            if record["status"] not in TERMINAL:
                record.pop("admission_token", None)
                record["status"] = "failed"
                record["completed_at"] = utc_now()
                record["reason"] = reason
                self._write(record)
            self._admit_locked()


def parse_parameters(items: Sequence[str]) -> Dict[str, str]:
    parameters: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise RunError("parameters use NAME=VALUE")
        name, value = item.split("=", 1)
        if not PARAMETER.fullmatch(name) or name in parameters:
            raise RunError("invalid or duplicate parameter: " + name)
        parameters[name] = value
    return parameters
