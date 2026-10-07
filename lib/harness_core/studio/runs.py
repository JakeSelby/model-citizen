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
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .state import StateError, Store, open_shared
from . import definitive, evaluation, free_suites, run_store, spend_guard, targets

SCHEMA_VERSION = 1
MAX_RUNNING = 3
MAX_OUTPUT_CHUNK = 64 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
PLUGIN_EVAL_SOURCE = "plugin-eval"
PLUGIN_EVAL_REPORT_ROUTE = "/api/runs/plugin-eval-report/"
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


def read_only_list(state_root: Path) -> List[Dict[str, Any]]:
    """Read public run sidecars without recovery, indexing, writes, or process admission."""
    root_fd = runs_fd = None
    try:
        root_fd = os.open(str(Path(state_root)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            runs_fd = _open_readonly_directory(root_fd, "runs")
        except FileNotFoundError:
            return []
        records = []
        for run_id in sorted(os.listdir(runs_fd)):
            descriptor = _open_readonly_directory(runs_fd, run_id)
            try:
                record = RunSupervisor._validate_record(_load_json(descriptor, "run.json"), run_id)
            finally:
                os.close(descriptor)
            records.append(record)
        records.sort(key=lambda item: (item.get("queue_sequence", 0), item["run_id"]))
        return [RunSupervisor._public(record) for record in records]
    except FileNotFoundError:
        return []
    finally:
        if runs_fd is not None:
            os.close(runs_fd)
        if root_fd is not None:
            os.close(root_fd)


def _open_readonly_directory(parent_fd: int, name: str) -> int:
    if not name or name in (".", "..") or os.sep in name:
        raise RunError("run state has an unsafe directory name")
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    info = os.fstat(descriptor)
    if (not stat.S_ISDIR(info.st_mode)
            or hasattr(os, "getuid") and info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        os.close(descriptor)
        raise RunError("run state directory is missing or unsafe")
    return descriptor


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


class _SharedCall:
    """Run one call at a time; callers arriving while it runs share its result or error."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._flight: Optional[Dict[str, Any]] = None

    def call(self, operation: Any) -> Any:
        with self._guard:
            flight = self._flight
            leader = flight is None
            if leader:
                flight = {"done": threading.Event(), "result": None, "error": None}
                self._flight = flight
        assert flight is not None
        if leader:
            try:
                flight["result"] = operation()
            except BaseException as exc:
                flight["error"] = exc
            finally:
                with self._guard:
                    self._flight = None
                flight["done"].set()
        else:
            flight["done"].wait()
        if flight["error"] is not None:
            raise flight["error"]
        return flight["result"]


class RunSupervisor:
    def __init__(self, state_root: Path, catalog_path: Path, max_running: int = MAX_RUNNING,
                 *, repository: Optional[Path] = None, target_service: Any = None):
        self.state_root = Path(state_root)
        self.catalog_path = Path(catalog_path).resolve()
        if repository is not None and target_service is not None:
            raise RunError("repository and target_service are mutually exclusive")
        self.target_service = (targets.TargetService(Path(repository))
                               if repository is not None else target_service)
        repository_root = (repository if repository is not None
                           else getattr(target_service, "repository", None))
        self.repository = Path(repository_root).resolve() if repository_root is not None else None
        if (not isinstance(max_running, int) or isinstance(max_running, bool)
                or not 1 <= max_running <= MAX_RUNNING):
            raise RunError("max_running must be between one and three")
        self.max_running = max_running
        self._catalog_call = _SharedCall()
        self.runs_dir = self.state_root / "runs"
        self.targets_dir = self.state_root / "targets"
        self.lock_path = self.state_root / "supervisor.lock"
        self.queue_path = self.state_root / "queue.json"
        try:
            with Store(self.state_root) as store:
                self._state_fd = os.dup(store.fd)
        except (OSError, StateError) as exc:
            raise RunError(str(exc)) from exc
        self._runs_fd = _open_directory(self._state_fd, "runs", create=True)
        self._targets_fd = _open_directory(self._state_fd, "targets", create=True)
        self._preparations_fd = _open_directory(self._state_fd, "preparations", create=True)
        self._confirmations_fd = _open_directory(self._state_fd, "confirmations", create=True)
        try:
            self.history = run_store.RunStore(self.state_root)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc

    def __del__(self):
        self.close()

    def close(self) -> None:
        """Release the supervisor's retained descriptors and index connection."""
        history = getattr(self, "history", None)
        if history is not None:
            with contextlib.suppress(Exception):
                history.close()
            self.history = None
        for name in ("_confirmations_fd", "_preparations_fd", "_targets_fd", "_runs_fd",
                     "_state_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
                setattr(self, name, None)

    @contextlib.contextmanager
    def lock(self):
        try:
            descriptor = open_shared("supervisor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                     0o600, self._state_fd)
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
                    "spend_actual", "spend_stop_reason", "case_results", "usage_ledger_state",
                    "rerun_of"}
        lifecycle_optional = optional - {"rerun_of"}
        if set(record) - required - optional or required - set(record):
            raise RunError("run state record has unsupported or missing fields")
        if record.get("schema_version") != SCHEMA_VERSION or record.get("run_id") != run_id:
            raise RunError("run state record is incomplete or inconsistent")
        try:
            uuid.UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise RunError("run state record has an invalid run id") from exc
        if "rerun_of" in record:
            try:
                uuid.UUID(record["rerun_of"])
            except (TypeError, ValueError) as exc:
                raise RunError("run state record has an invalid rerun source") from exc
            if record["rerun_of"] == run_id:
                raise RunError("run state record cannot rerun itself")
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
        target_fields = {"kind", "ref", "revision", "draft", "config_digest"}
        built_target_fields = target_fields | {"version", "source_path", "profile_path"}
        if (not isinstance(target, dict)
                or set(target) not in (target_fields, target_fields | {"version"},
                                       built_target_fields)
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
                or ("version" in target
                    and (not isinstance(target["version"], str) or not target["version"]
                         or len(target["version"]) > 4096 or "\0" in target["version"]))
                or any(not isinstance(target.get(name), str) or not target[name]
                       or len(target[name]) > 4096 or "\0" in target[name]
                       or not Path(target[name]).is_absolute()
                       for name in ("source_path", "profile_path") if name in target)
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
            paid_shape = (paid and all(isinstance(item, dict)
                          and set(item) == {"id", "status", "spend_usd"}
                          and item.get("status") in ("completed", "not_run")
                          and isinstance(item.get("spend_usd"), (int, float))
                          and not isinstance(item.get("spend_usd"), bool)
                          and math.isfinite(item["spend_usd"])
                          and item["spend_usd"] >= 0 for item in results)) if isinstance(results, list) else False
            free_shape = (not paid and all(isinstance(item, dict)
                          and set(item) == {"id", "status", "detail"}
                          and item.get("status") in ("passed", "failed", "skipped")
                          and isinstance(item.get("detail"), str) for item in results)) if isinstance(results, list) else False
            identities = [item.get("id") for item in results if isinstance(item, dict)] \
                if isinstance(results, list) else []
            invalid_paid = (paid and (not paid_shape or identities != (case_identities or [])))
            invalid_free = (not paid and (not free_shape
                            or len(results) > len(case_identities or [])
                            or any(identity not in (case_identities or []) for identity in identities)
                            or len(set(identities)) != len(identities)))
            if not isinstance(results, list) or invalid_paid or invalid_free:
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
        if record["status"] == "queued" and set(record) & lifecycle_optional:
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
        if record.get("suite_id") in free_suites.FREE_SUITE_IDS:
            public["exact_command"] = free_suites.start_command(
                str(record["suite_id"]), record.get("parameters", {}),
                str(record.get("target", {}).get("kind")),
                str(record.get("target", {}).get("ref")))
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

    def _check_confirmation_locked(self, token: Optional[str], request_digest: str) -> str:
        name = self._confirmation_name(token or "")
        try:
            pending = _load_json(self._confirmations_fd, name)
        except RunError as exc:
            raise RunError("paid run confirmation is missing, invalid, or already used") from exc
        if (set(pending) != {"schema_version", "request_digest", "created_at"}
                or not isinstance(pending.get("request_digest"), str)
                or not hmac.compare_digest(pending["request_digest"], request_digest)):
            raise RunError("paid run confirmation does not match the exact displayed request")
        return name

    def _consume_confirmation_locked(self, token: Optional[str], request_digest: str) -> None:
        name = self._check_confirmation_locked(token, request_digest)
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
            descriptor = open_shared(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                                     0o600, directory)
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

    def _prepared_path(self, run_id: str, name: str) -> Path:
        self._run_path(run_id)
        directory = _open_directory(self._targets_fd, run_id)
        try:
            prepared = _open_directory(directory, name)
            os.close(prepared)
        finally:
            os.close(directory)
        return self.targets_dir / run_id / name

    def prepare_profile(self, run_id: str) -> Path:
        return self._prepared_path(run_id, "profile")

    def prepare_source(self, run_id: str) -> Path:
        return self._prepared_path(run_id, "source")

    @staticmethod
    def _preparation_name(run_id: str) -> str:
        try:
            uuid.UUID(run_id)
        except (ValueError, TypeError) as exc:
            raise RunError("invalid run id") from exc
        return run_id + ".json"

    def _target_directory(self, run_id: str) -> Path:
        self._preparation_name(run_id)
        return self.targets_dir / run_id

    def _reserve_preparation_locked(self, run_id: str, sequence: int, created_at: str,
                                    confirmation_token: Optional[str],
                                    request_digest: Optional[str]) -> None:
        identity = process_identity(os.getpid())
        if identity is None:
            raise RunError("target preparation owner identity is unavailable")
        _atomic_json(self._preparations_fd, self._preparation_name(run_id), {
            "schema_version": 1,
            "run_id": run_id,
            "owner_pid": os.getpid(),
            "owner_identity": identity,
            "queue_sequence": sequence,
            "created_at": created_at,
            "confirmation_token": confirmation_token,
            "request_digest": request_digest,
        })

    def _read_preparation_locked(self, run_id: str) -> Dict[str, Any]:
        value = _load_json(self._preparations_fd, self._preparation_name(run_id))
        if (set(value) != {"schema_version", "run_id", "owner_pid", "owner_identity",
                           "queue_sequence", "created_at", "confirmation_token",
                           "request_digest"}
                or value.get("run_id") != run_id
                or ((value.get("confirmation_token") is None)
                    != (value.get("request_digest") is None))
                or (value.get("confirmation_token") is not None
                    and (not re.fullmatch(r"[0-9a-f]{64}", value["confirmation_token"])
                         or not isinstance(value.get("request_digest"), str)
                         or not re.fullmatch(r"[0-9a-f]{64}", value["request_digest"])))
                or not process_matches(value.get("owner_pid"), value.get("owner_identity"))):
            raise RunError("target preparation reservation is missing or stale")
        return value

    def _remove_preparation_locked(self, run_id: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._preparation_name(run_id), dir_fd=self._preparations_fd)
            os.fsync(self._preparations_fd)

    def _authoritative_preparation_record_locked(
            self, run_id: str) -> Optional[Dict[str, Any]]:
        try:
            info = os.stat(run_id, dir_fd=self._runs_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RunError("run state directory is missing or unsafe") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise RunError("run state path is not a directory")
        descriptor = self._run_directory(run_id)
        try:
            try:
                authoritative, _authority = self.history.read_studio(descriptor, run_id)
            except run_store.RunStoreError as exc:
                raise RunError(str(exc)) from exc
        finally:
            os.close(descriptor)
        if authoritative is None:
            return None
        return self._validate_record(authoritative, run_id)

    def _recover_preparations_locked(self) -> None:
        for name in sorted(os.listdir(self._preparations_fd)):
            if not name.endswith(".json"):
                raise RunError("target preparation state has an unsafe entry")
            run_id = name[:-5]
            self._preparation_name(run_id)
            value = _load_json(self._preparations_fd, name)
            if (set(value) != {"schema_version", "run_id", "owner_pid", "owner_identity",
                               "queue_sequence", "created_at", "confirmation_token",
                               "request_digest"}
                    or value.get("run_id") != run_id
                    or ((value.get("confirmation_token") is None)
                        != (value.get("request_digest") is None))
                    or (value.get("confirmation_token") is not None
                        and (not re.fullmatch(r"[0-9a-f]{64}", value["confirmation_token"])
                             or not isinstance(value.get("request_digest"), str)
                             or not re.fullmatch(r"[0-9a-f]{64}", value["request_digest"])))):
                raise RunError("target preparation state is invalid")
            authoritative = self._authoritative_preparation_record_locked(run_id)
            if authoritative is not None:
                if value.get("confirmation_token") is not None:
                    confirmation_name = self._confirmation_name(value["confirmation_token"])
                    try:
                        os.stat(confirmation_name, dir_fd=self._confirmations_fd,
                                follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        raise RunError("paid run confirmation could not be recovered") from exc
                    else:
                        self._consume_confirmation_locked(
                            value["confirmation_token"], value["request_digest"])
                self._remove_preparation_locked(run_id)
                continue
            if process_matches(value.get("owner_pid"), value.get("owner_identity")):
                continue
            shutil.rmtree(str(self._target_directory(run_id)), ignore_errors=True)
            self._remove_preparation_locked(run_id)

    def _recover_locked(self) -> None:
        self._recover_preparations_locked()
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
            pending_preparations = {
                name[:-5] for name in os.listdir(self._preparations_fd)
                if name.endswith(".json")
            }
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
            queued = sorted((record for record in records
                             if record.get("status") == "queued"
                             and record["run_id"] not in pending_preparations),
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
              pricing_source: Optional[str] = None,
              case_identities: Optional[Sequence[str]] = None,
              rerun_of: Optional[str] = None,
              expected_target: Optional[Mapping[str, Any]] = None,
              expected_argv: Optional[Sequence[str]] = None,
              expected_cases: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        catalog = SuiteCatalog.load(self.catalog_path)
        suite = catalog.get(suite_id)
        argv = suite.render(parameters, target_kind, target_ref)
        try:
            discovered = free_suites.resolve_case_identities(
                suite.suite_id, parameters, target_kind, target_ref)
        except free_suites.FreeSuiteError as exc:
            raise RunError(str(exc)) from exc
        cases = list(discovered or suite.cases or (suite.suite_id,))
        if expected_argv is not None and argv != list(expected_argv):
            raise RunError("rerun command changed since the original run")
        if expected_cases is not None and cases != list(expected_cases):
            raise RunError("rerun cases changed since the original run")
        if case_identities is not None:
            if (suite.cost_class != "spends_usage" or not case_identities
                    or len(set(case_identities)) != len(case_identities)
                    or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value)
                           for value in case_identities)):
                raise RunError("paid run case identities are invalid")
            cases = list(case_identities)
        if (suite.cost_class != "spends_usage"
                and (confirmed is not None or max_budget_usd is not None
                     or spend_cap_usd is not None or pricing_source is not None)):
            raise RunError("spend flags apply only to suites that spend usage")
        run_id = str(uuid.uuid4())
        if self.target_service is None:
            raise RunError("run target preparation requires an explicit repository or target service")
        prepared = None
        run_directory = self.runs_dir / run_id
        target_directory = self._target_directory(run_id)
        persisted = False
        reserved = False
        spend_plan = None
        request_digest = None
        try:
            with self.lock():
                self._recover_locked()
                if suite.cost_class == "spends_usage":
                    try:
                        spend_plan = spend_guard.plan(self._records(), suite.suite_id, cases,
                                                      max_budget_usd, spend_cap_usd, pricing_source)
                        request = spend_guard.confirmation_request(
                            suite.suite_id, suite.version, parameters, target_kind, target_ref,
                            cases, spend_plan)
                        request_digest = spend_guard.confirmation_digest(request)
                        self._check_confirmation_locked(confirmed, request_digest)
                        argv = spend_guard.guarded_argv(argv, spend_plan)
                    except spend_guard.SpendGuardError as exc:
                        raise RunError(str(exc)) from exc
                sequence = self._next_sequence()
                created_at = utc_now()
                self._reserve_preparation_locked(
                    run_id, sequence, created_at,
                    confirmed if request_digest is not None else None, request_digest)
                reserved = True
            try:
                prepared = self.target_service.build(target_kind, target_ref, target_directory)
            except targets.TargetError as exc:
                raise RunError(str(exc)) from exc
            with self.lock():
                self._recover_locked()
                reservation = self._read_preparation_locked(run_id)
                expected_source = str(target_directory / "source")
                expected_profile = str(target_directory / "profile")
                if (not isinstance(prepared, dict)
                        or prepared.get("source_path") != expected_source
                        or prepared.get("profile_path") != expected_profile):
                    raise RunError("target service returned paths outside the reserved run target")
                if request_digest is not None:
                    self._check_confirmation_locked(confirmed, request_digest)
                target = {
                    "kind": target_kind,
                    "ref": target_ref,
                    "revision": prepared["revision"] if prepared else None,
                    "draft": target_ref if target_kind == "draft" else None,
                    "config_digest": prepared["config_digest"] if prepared else None,
                }
                if prepared:
                    target["version"] = prepared["version"]
                    target["source_path"] = prepared["source_path"]
                    target["profile_path"] = prepared["profile_path"]
                if expected_target is not None:
                    identity_fields = ("kind", "ref", "revision", "draft", "config_digest")
                    if any(target.get(name) != expected_target.get(name)
                           for name in identity_fields):
                        raise RunError("rerun target changed since the original run")
                record: Dict[str, Any] = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "suite_id": suite.suite_id,
                    "suite_version": suite.version,
                    "parameters": dict(sorted(parameters.items())),
                    "target": target,
                    "argv": argv,
                    "cost_class": suite.cost_class,
                    "expected_duration_seconds": suite.expected_duration_seconds,
                    "timeout_seconds": suite.timeout_seconds,
                    "case_identities": cases,
                    "spend_estimate": spend_plan["estimate"] if spend_plan else None,
                    "spend_cap": spend_plan["caps"] if spend_plan else None,
                    "pricing_identity": spend_plan["pricing"] if spend_plan else None,
                    "status": "queued",
                    "queue_sequence": reservation["queue_sequence"],
                    "created_at": reservation["created_at"],
                }
                if rerun_of is not None:
                    record["rerun_of"] = rerun_of
                record["canonical_run_digest"] = run_store._canonical_run_digest(record)
                self._write(record)
                persisted = True
                if request_digest is not None:
                    self._consume_confirmation_locked(confirmed, request_digest)
                self._remove_preparation_locked(run_id)
                reserved = False
                self._admit_locked()
                return self._public(self._read(run_id))
        finally:
            if not persisted:
                if reserved:
                    with self.lock():
                        self._remove_preparation_locked(run_id)
                shutil.rmtree(str(target_directory), ignore_errors=True)
                shutil.rmtree(str(run_directory), ignore_errors=True)

    def spend_preview(self, suite_id: str, parameters: Mapping[str, str], target_kind: str,
                      target_ref: str, max_budget_usd: Any, spend_cap_usd: Any,
                      pricing_source: Optional[str], *,
                      case_identities: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        suite = SuiteCatalog.load(self.catalog_path).get(suite_id)
        suite.render(parameters, target_kind, target_ref)
        if suite.cost_class != "spends_usage":
            return {"cost_class": "free", "confirmation_required": False}
        cases = list(suite.cases or (suite.suite_id,))
        if case_identities is not None:
            if (not case_identities or len(set(case_identities)) != len(case_identities)
                    or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value)
                           for value in case_identities)):
                raise RunError("paid run case identities are invalid")
            cases = list(case_identities)
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

    def catalog(self, repository: Path) -> Dict[str, Any]:
        """Describe the free catalog from the same definitions used for launch.

        Test discovery takes seconds in a subprocess and runs on request threads, so concurrent
        callers share one in-flight discovery instead of each starting another.
        """
        def load() -> Dict[str, Any]:
            catalog = SuiteCatalog.load(self.catalog_path)
            try:
                return free_suites.catalog_payload(catalog, repository)
            except free_suites.FreeSuiteError as exc:
                raise RunError(str(exc)) from exc
        return self._catalog_call.call(load)

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

    def history_page(self, **filters: Any) -> Dict[str, Any]:
        try:
            return self.history.history(**filters)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc

    def _rerun_request(self, record: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        if record.get("status") not in TERMINAL:
            return None, "Only terminal runs can be rerun."
        if record.get("cost_class") == "spends_usage":
            return None, "Usage-spending runs require a new estimate and confirmation."
        try:
            suite = SuiteCatalog.load(self.catalog_path).get(str(record.get("suite_id")))
            if suite.version != record.get("suite_version"):
                return None, "The suite changed since the original run."
            target = record.get("target") if isinstance(record.get("target"), dict) else {}
            parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
            argv = suite.render(parameters, str(target.get("kind")), str(target.get("ref")))
            discovered = free_suites.resolve_case_identities(
                suite.suite_id, parameters, str(target.get("kind")), str(target.get("ref")))
            cases = list(discovered or suite.cases or (suite.suite_id,))
        except (RunError, free_suites.FreeSuiteError):
            return None, "The canonical request can no longer be reproduced."
        if argv != record.get("argv") or cases != record.get("case_identities"):
            return None, "The command or discovered cases changed since the original run."
        return ({"suite_id": suite.suite_id, "parameters": dict(parameters),
                 "target_kind": target["kind"], "target_ref": target["ref"],
                 "expected_target": dict(target), "expected_argv": list(argv),
                 "expected_cases": list(cases)}, "")

    def _declared_artifact(self, indexed: Mapping[str, Any], artifact: str,
                           limit: int = MAX_OUTPUT_CHUNK, read: bool = True) -> bytes:
        """A declared artifact's bytes; with `read` false, only its bounded availability checks."""
        declared = indexed.get("artifacts")
        if (self.repository is None or not isinstance(declared, list)
                or not artifact.startswith("imported-")):
            raise RunError("run evidence is unavailable")
        try:
            position = int(artifact.removeprefix("imported-"))
            relative = declared[position]
        except (ValueError, IndexError, TypeError):
            raise RunError("run evidence is unavailable") from None
        if not isinstance(relative, str):
            raise RunError("run evidence is unavailable")
        parsed = Path(relative)
        if parsed.is_absolute() or ".." in parsed.parts or "\0" in relative:
            raise RunError("run evidence is unavailable")
        path = (self.repository / parsed).resolve()
        try:
            candidate_info = (self.repository / parsed).lstat()
            if not stat.S_ISREG(candidate_info.st_mode):
                raise RunError("run evidence is unavailable")
            path.relative_to(self.repository)
            descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                        or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
                    raise RunError("run evidence is unavailable")
                if not read:
                    return b""
                body = os.pread(descriptor, info.st_size, 0)
            finally:
                os.close(descriptor)
            body.decode("utf-8")
            return body
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, RunError):
                raise
            raise RunError("run evidence is unavailable") from exc

    def run_detail(self, run_id: str, **lineage: Any) -> Dict[str, Any]:
        try:
            with self.lock():
                detail = self.history.detail(run_id, **lineage)
                indexed = self.history.get(run_id)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc
        source_kind = (indexed.get("source") or {}).get("kind")
        raw = indexed.get("raw") if isinstance(indexed.get("raw"), dict) else {}
        detail["exact_command"] = None
        detail["rerun"] = {"available": False, "reason": "Imported runs have no canonical request."}
        if source_kind == "studio":
            target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
            if raw.get("suite_id") in free_suites.FREE_SUITE_IDS:
                detail["exact_command"] = free_suites.start_command(
                    str(raw["suite_id"]), raw.get("parameters") or {},
                    str(target.get("kind")), str(target.get("ref")))
            elif isinstance(raw.get("argv"), list) and raw["argv"]:
                detail["exact_command"] = free_suites.command_text(raw["argv"])
            request, reason = self._rerun_request(raw)
            detail["rerun"] = {"available": request is not None,
                               "reason": None if request is not None else reason}
            detail["artifacts"] = [
                {"id": name, "label": name.capitalize() + " log", "available": True}
                for name in ("stdout", "stderr", "worker")]
        else:
            declared = indexed.get("artifacts") if isinstance(indexed.get("artifacts"), list) else []
            report = indexed.get("report") if source_kind == PLUGIN_EVAL_SOURCE else None
            detail["artifacts"] = []
            for position, relative in enumerate(declared):
                artifact_id = "imported-" + str(position)
                is_report = report is not None and relative == report
                try:
                    if is_report:
                        self._declared_artifact(indexed, artifact_id, MAX_REPORT_BYTES, read=False)
                    else:
                        self._declared_artifact(indexed, artifact_id)
                    available = True
                except RunError:
                    available = False
                item = {"id": artifact_id,
                        "label": "Declared artifact " + str(position + 1),
                        "available": available}
                if is_report:
                    item.update({"label": "HTML report", "kind": "html-report",
                                 "href": PLUGIN_EVAL_REPORT_ROUTE + run_id})
                elif source_kind == PLUGIN_EVAL_SOURCE and position == 0:
                    item["label"] = "Result JSON"
                detail["artifacts"].append(item)
        # The landed contract the source declared, as indexed; nothing is derived here.
        detail["evaluation"] = evaluation.detail_contract(indexed)
        # A benchmark row's engine fields and the definitive evaluation's reports, verbatim.
        imported_row = source_kind == "benchmark-result"
        detail["engine_row"] = evaluation.engine_row(raw) if imported_row else None
        detail["engine_reports"] = (definitive.engine_reports(
            self.repository, (indexed.get("source") or {}).get("path")) if imported_row else None)
        return detail

    def run_evaluation(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The landed evaluation contract an indexed source declared, carried as indexed; no
        figure, verdict or proof status is derived here. None for a source with no contract."""
        try:
            with self.lock():
                indexed = self.history.get(run_id)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc
        return evaluation.detail_contract(indexed)

    def plugin_eval_report(self, run_id: str) -> bytes:
        """The original `claude plugin eval` HTML report an imported run declares."""
        try:
            indexed = self.history.get(run_id)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc
        report = indexed.get("report")
        declared = indexed.get("artifacts")
        if ((indexed.get("source") or {}).get("kind") != PLUGIN_EVAL_SOURCE
                or not isinstance(report, str) or not isinstance(declared, list)
                or report not in declared):
            raise RunError("run evidence is unavailable")
        return self._declared_artifact(indexed, "imported-" + str(declared.index(report)),
                                       MAX_REPORT_BYTES)

    def case_history(self, case_id: str, **bounds: Any) -> Dict[str, Any]:
        try:
            return self.history.case_history(case_id, **bounds)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc

    def evidence(self, run_id: str, artifact: str) -> Dict[str, Any]:
        try:
            indexed = self.history.get(run_id)
        except run_store.RunStoreError as exc:
            raise RunError(str(exc)) from exc
        if (indexed.get("source") or {}).get("kind") != "studio":
            content = self._declared_artifact(indexed, artifact).decode("utf-8")
            return {"run_id": run_id, "artifact": artifact, "content": content}
        if artifact not in ("stdout", "stderr", "worker"):
            raise RunError("run evidence is unavailable")
        if artifact in ("stdout", "stderr"):
            payload = self.read_output(run_id, artifact, 0, MAX_OUTPUT_CHUNK)
            if not payload["eof"] or payload["next_cursor"] > MAX_OUTPUT_CHUNK:
                raise RunError("run evidence is unavailable")
            content = payload["chunk"]
        else:
            with self.lock():
                record = self._read(run_id)
                directory = self._run_directory(run_id)
                descriptor = -1
                try:
                    descriptor = os.open("worker.log", os.O_RDONLY | os.O_NOFOLLOW,
                                         dir_fd=directory)
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT_CHUNK
                            or stat.S_IMODE(info.st_mode) != 0o600
                            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                            or record.get("status") not in TERMINAL):
                        raise RunError("run evidence is unavailable")
                    content = os.pread(descriptor, info.st_size, 0).decode("utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise RunError("run evidence is unavailable") from exc
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                    os.close(directory)
        return {"run_id": run_id, "artifact": artifact, "content": content}

    def rerun(self, run_id: str) -> Dict[str, Any]:
        with self.lock():
            original = self._read(run_id)
            request, reason = self._rerun_request(original)
            if request is None:
                raise RunError(reason)
        return self.start(request["suite_id"], request["parameters"],
                          request["target_kind"], request["target_ref"],
                          case_identities=None, rerun_of=run_id,
                          expected_target=request["expected_target"],
                          expected_argv=request["expected_argv"],
                          expected_cases=request["expected_cases"])

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
                if stopped and record.get("cost_class") == "spends_usage":
                    self._record_cancelled_spend_locked(record)
                self._write(record)
                if record.get("usage_ledger_state") == "pending":
                    self._settle_usage_locked(record)
            with contextlib.suppress(RunError):
                self._admit_locked()
            return self._public(self._read(run_id))

    def _record_cancelled_spend_locked(self, record: Dict[str, Any]) -> None:
        """Keep the spend a cancelled paid runner reported before it was stopped, if it did."""
        descriptor = self._run_directory(record["run_id"])
        try:
            reported = spend_guard.read_result(descriptor, record["run_id"],
                                               record["case_identities"])
        except spend_guard.SpendGuardError:
            return
        finally:
            os.close(descriptor)
        record["spend_actual"] = reported["spend_usd"]
        record["case_results"] = reported["cases"]
        if reported["stop_reason"] is not None:
            record["spend_stop_reason"] = reported["stop_reason"]
        record["usage_ledger_state"] = "pending"

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
