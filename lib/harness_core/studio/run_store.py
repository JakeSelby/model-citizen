"""Durable Studio run sidecars and their rebuildable SQLite index."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .state import StateError, Store

SIDECAR_SCHEMA_VERSION = 2
SIDECAR_META_SCHEMA_VERSION = 1
AUTHORITY_SCHEMA_VERSION = 1
APPEND_INTENT_SCHEMA_VERSION = 1
STORE_SCHEMA_VERSION = 2
SIDECAR_NAME = "record.jsonl"
SIDECAR_META_NAME = "record.meta.json"
AUTHORITY_NAME = "run-authority.jsonl"
AUTHORITY_HEAD_NAME = "run-authority.head.json"
APPEND_INTENT_NAME = "run-authority.intent.json"
AUTHORITY_LOCK_NAME = "run-authority.lock"
DATABASE_NAME = "run-index.sqlite3"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_RESULTS_FILES = 4096
COST_BASIS = "list_price_equivalent"
TERMINAL_STATUSES = frozenset(("succeeded", "failed", "cancelled", "timed_out", "orphaned"))
IMMUTABLE_FIELDS = (
    "schema_version", "run_id", "suite_id", "suite_version", "parameters", "target", "argv",
    "cost_class", "expected_duration_seconds", "timeout_seconds", "queue_sequence", "created_at",
    "case_identities", "spend_estimate", "spend_cap", "pricing_identity",
)
LEGAL_TRANSITIONS = {
    "queued": frozenset(("admitted", "cancelled")),
    "admitted": frozenset(("starting", "failed", "cancelled", "orphaned")),
    "starting": frozenset(("running", "failed", "cancelled", "orphaned", "cancel_requested")),
    "running": frozenset(("succeeded", "failed", "cancelled", "timed_out", "orphaned",
                           "cancel_requested")),
    "cancel_requested": frozenset(("cancelled", "failed", "orphaned")),
}


class RunStoreError(ValueError):
    """Run history is unsafe, corrupt, or uses an unsupported schema."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise RunStoreError("run record is not bounded JSON") from exc


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number: " + value)


def _loads(data: Any) -> Any:
    return json.loads(data, parse_constant=_reject_constant)


def _safe_regular(path: Path, maximum: int = MAX_SOURCE_BYTES) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RunStoreError("run source is unreadable: " + str(path)) from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise RunStoreError("run source is not a bounded regular file: " + str(path))
    return info


def _json_bytes(value: Mapping[str, Any]) -> str:
    encoded = _canonical(value)
    if len(encoded) > MAX_RECORD_BYTES:
        raise RunStoreError("run record exceeds the size limit")
    return encoded.decode("utf-8")


def _sync(descriptor: int) -> None:
    os.fsync(descriptor)


@contextlib.contextmanager
def _authority_lock(state_fd: int):
    descriptor = -1
    try:
        descriptor = os.open(AUTHORITY_LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                             0o600, dir_fd=state_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RunStoreError("run authority lock is not a regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run authority lock is owned by another user")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise RunStoreError("run authority lock is unavailable") from exc
    finally:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _atomic_json(directory_fd: int, name: str, value: Mapping[str, Any]) -> None:
    temporary = "." + name + "." + uuid.uuid4().hex
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        data = _canonical(value) + b"\n"
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise OSError("short metadata write")
            written += count
        _sync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        _sync(directory_fd)
    except OSError as exc:
        raise RunStoreError("run sidecar metadata could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory_fd)


def _canonical_run_digest(record: Mapping[str, Any]) -> str:
    if any(name not in record for name in IMMUTABLE_FIELDS):
        raise RunStoreError("run creation record is missing immutable fields")
    identity = {name: record[name] for name in IMMUTABLE_FIELDS}
    return _digest(identity)


def _immutable_digest(record: Mapping[str, Any]) -> str:
    if "canonical_run_digest" not in record:
        raise RunStoreError("run creation record is missing immutable fields")
    canonical_run_digest = _canonical_run_digest(record)
    if record["canonical_run_digest"] != canonical_run_digest:
        raise RunStoreError("run canonical digest is invalid")
    return _digest({name: record[name] for name in IMMUTABLE_FIELDS + ("canonical_run_digest",)})


def _terminal_digest(sequence: int, previous_hash: str, creation_digest: str,
                     record_digest: str) -> str:
    return _digest({"sequence": sequence, "previous_hash": previous_hash,
                    "creation_digest": creation_digest, "record_digest": record_digest})


def _is_final(record: Mapping[str, Any]) -> bool:
    return (record.get("status") in TERMINAL_STATUSES
            and record.get("capacity_reserved") is not True)


def _legal_transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    before, after = previous.get("status"), current.get("status")
    if after in LEGAL_TRANSITIONS.get(str(before), ()):
        return True
    return (before == "orphaned" and after == "orphaned"
            and previous.get("capacity_reserved") is True
            and "capacity_reserved" not in current)


def _read_metadata(directory_fd: int) -> Optional[Dict[str, Any]]:
    descriptor = -1
    try:
        descriptor = os.open(SIDECAR_META_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RunStoreError("run sidecar metadata must be a bounded mode-0600 regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run sidecar metadata is owned by another user")
        data = os.read(descriptor, MAX_RECORD_BYTES + 1)
        if len(data) != info.st_size or len(data) > MAX_RECORD_BYTES:
            raise RunStoreError("run sidecar metadata changed while it was read")
        value = _loads(data.decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, RunStoreError):
            raise
        raise RunStoreError("run sidecar metadata is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise RunStoreError("run sidecar metadata is invalid")
    return value


def _read_control_json(directory_fd: int, name: str) -> Optional[Dict[str, Any]]:
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RunStoreError("run authority control file is unsafe: " + name)
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run authority control file is owned by another user: " + name)
        data = os.read(descriptor, MAX_RECORD_BYTES + 1)
        if len(data) != info.st_size or len(data) > MAX_RECORD_BYTES:
            raise RunStoreError("run authority control file changed while read: " + name)
        value = _loads(data.decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, RunStoreError):
            raise
        raise RunStoreError("run authority control file is unreadable: " + name) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise RunStoreError("run authority control file is invalid: " + name)
    return value


def _history_metadata(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    first = rows[0]
    creation_digest = first["creation_digest"]
    last = rows[-1]
    return {
        "schema_version": SIDECAR_META_SCHEMA_VERSION,
        "run_id": first["record"]["run_id"],
        "creation_digest": creation_digest,
        "sequence": len(rows),
        "head_hash": last["hash"],
        "terminal_digest": last["terminal_digest"],
    }


def _verify_metadata(rows: Sequence[Mapping[str, Any]], metadata: Optional[Mapping[str, Any]]) -> None:
    if not rows:
        if metadata is not None:
            raise RunStoreError("run sidecar metadata exists without history")
        return
    if metadata is None:
        raise RunStoreError("run sidecar metadata is missing")
    expected = _history_metadata(rows)
    if metadata.get("schema_version") != SIDECAR_META_SCHEMA_VERSION:
        raise RunStoreError("unsupported run sidecar metadata schema version")
    for name, value in expected.items():
        if metadata.get(name) != value:
            raise RunStoreError("run sidecar metadata conflicts with its history")


def _authority_rows(state_fd: int) -> List[Dict[str, Any]]:
    descriptor = -1
    try:
        descriptor = os.open(AUTHORITY_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=state_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RunStoreError("run authority ledger must be a bounded mode-0600 regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run authority ledger is owned by another user")
        body = bytearray()
        while len(body) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
    except FileNotFoundError:
        if _read_control_json(state_fd, AUTHORITY_HEAD_NAME) is not None:
            raise RunStoreError("run authority head exists without its ledger")
        return []
    except OSError as exc:
        raise RunStoreError("run authority ledger is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    data = bytes(body)
    if len(data) != info.st_size or len(data) > MAX_SOURCE_BYTES:
        raise RunStoreError("run authority ledger changed while it was read")
    if data and not data.endswith(b"\n"):
        raise RunStoreError("run authority ledger ends with an incomplete record")
    rows: List[Dict[str, Any]] = []
    previous = "0" * 64
    latest: Dict[str, Mapping[str, Any]] = {}
    try:
        for sequence, line in enumerate(data.splitlines(), 1):
            if not line or len(line) > MAX_RECORD_BYTES:
                raise RunStoreError("run authority ledger contains an invalid line")
            envelope = _loads(line.decode("utf-8"))
            required = {"schema_version", "sequence", "previous_hash", "run_id",
                        "creation_digest", "sidecar_sequence", "previous_sidecar_hash",
                        "head_hash", "terminal_digest", "hash"}
            if (not isinstance(envelope, dict) or set(envelope) != required
                    or envelope["schema_version"] != AUTHORITY_SCHEMA_VERSION
                    or envelope["sequence"] != sequence
                    or envelope["previous_hash"] != previous):
                raise RunStoreError("run authority ledger contains an invalid envelope")
            unsigned = {name: value for name, value in envelope.items() if name != "hash"}
            if envelope["hash"] != _digest(unsigned):
                raise RunStoreError("run authority ledger hash chain is invalid")
            run_id = envelope["run_id"]
            sidecar_sequence = envelope["sidecar_sequence"]
            if (not isinstance(run_id, str) or not isinstance(sidecar_sequence, int)
                    or sidecar_sequence < 1):
                raise RunStoreError("run authority ledger contains an invalid identity")
            prior = latest.get(run_id)
            if prior is not None and (
                    envelope["creation_digest"] != prior["creation_digest"]
                    or sidecar_sequence != prior["sidecar_sequence"] + 1
                    or envelope["previous_sidecar_hash"] != prior["head_hash"]):
                raise RunStoreError("run authority ledger contains a rewritten history")
            latest[run_id] = envelope
            previous = envelope["hash"]
            rows.append(envelope)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, RunStoreError):
            raise
        raise RunStoreError("run authority ledger contains invalid JSON") from exc
    head = _read_control_json(state_fd, AUTHORITY_HEAD_NAME)
    if not rows:
        if head is not None:
            raise RunStoreError("run authority head exists without its ledger")
    elif (head is None or head.get("schema_version") != AUTHORITY_SCHEMA_VERSION
          or head.get("sequence") != len(rows) or head.get("head_hash") != rows[-1]["hash"]):
        raise RunStoreError("run authority head conflicts with its ledger")
    return rows


def _latest_authority(rows: Sequence[Mapping[str, Any]], run_id: str) -> Optional[Mapping[str, Any]]:
    return next((row for row in reversed(rows) if row["run_id"] == run_id), None)


def _verify_authority(state_fd: int, run_id: str,
                      history: Sequence[Mapping[str, Any]]) -> None:
    authority = _latest_authority(_authority_rows(state_fd), run_id)
    if not history:
        if authority is not None:
            raise RunStoreError("run authority ledger conflicts with missing sidecar history")
        return
    if authority is None:
        raise RunStoreError("run authority ledger is missing")
    metadata = _history_metadata(history)
    expected = {
        "creation_digest": metadata["creation_digest"],
        "sidecar_sequence": metadata["sequence"],
        "head_hash": metadata["head_hash"],
        "terminal_digest": metadata["terminal_digest"],
    }
    if any(authority.get(name) != value for name, value in expected.items()):
        raise RunStoreError("run authority ledger conflicts with sidecar history")


def _prepare_authority(state_fd: int, history: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    metadata = _history_metadata(history)
    run_id = metadata["run_id"]
    rows = _authority_rows(state_fd)
    prior = _latest_authority(rows, run_id)
    if prior is not None and all(prior.get(name) == value for name, value in {
            "creation_digest": metadata["creation_digest"],
            "sidecar_sequence": metadata["sequence"],
            "head_hash": metadata["head_hash"],
            "terminal_digest": metadata["terminal_digest"],
    }.items()):
        return dict(prior)
    previous_sidecar_hash = history[-2]["hash"] if len(history) > 1 else "0" * 64
    if prior is not None and (
            metadata["creation_digest"] != prior["creation_digest"]
            or metadata["sequence"] != prior["sidecar_sequence"] + 1
            or previous_sidecar_hash != prior["head_hash"]):
        raise RunStoreError("run sidecar does not extend its authority ledger")
    unsigned = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "sequence": len(rows) + 1,
        "previous_hash": rows[-1]["hash"] if rows else "0" * 64,
        "run_id": run_id,
        "creation_digest": metadata["creation_digest"],
        "sidecar_sequence": metadata["sequence"],
        "previous_sidecar_hash": previous_sidecar_hash,
        "head_hash": metadata["head_hash"],
        "terminal_digest": metadata["terminal_digest"],
    }
    return dict(unsigned, hash=_digest(unsigned))


def _ensure_append(directory_fd: int, name: str, old_size: int, line: bytes) -> None:
    if old_size < 0 or old_size + len(line) > MAX_SOURCE_BYTES:
        raise RunStoreError("run append target exceeds the size limit: " + name)
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RunStoreError("run append target is not a regular file: " + name)
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run append target is owned by another user: " + name)
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        if old_size < info.st_size < old_size + len(line):
            partial = os.pread(descriptor, info.st_size - old_size, old_size)
            if not line.startswith(partial):
                raise RunStoreError("run append target conflicts with durable intent: " + name)
            os.ftruncate(descriptor, old_size)
            info = os.fstat(descriptor)
        if info.st_size == old_size:
            os.lseek(descriptor, old_size, os.SEEK_SET)
            written = 0
            while written < len(line):
                count = os.write(descriptor, line[written:])
                if count <= 0:
                    raise OSError("short append")
                written += count
            _sync(descriptor)
            _sync(directory_fd)
        elif info.st_size == old_size + len(line):
            if os.pread(descriptor, len(line), old_size) != line:
                raise RunStoreError("run append target conflicts with durable intent: " + name)
        else:
            raise RunStoreError("run append target has unexpected size: " + name)
    except OSError as exc:
        raise RunStoreError("run durable append could not be completed: " + name) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _apply_append_intent(state_fd: int, intent: Mapping[str, Any]) -> None:
    required = {"schema_version", "run_id", "sidecar_old_size", "sidecar_line",
                "metadata", "authority_old_size", "authority_line", "authority_head"}
    if (set(intent) != required or intent.get("schema_version") != APPEND_INTENT_SCHEMA_VERSION
            or not isinstance(intent.get("run_id"), str)
            or not isinstance(intent.get("sidecar_old_size"), int)
            or not isinstance(intent.get("authority_old_size"), int)
            or not isinstance(intent.get("sidecar_line"), str)
            or not isinstance(intent.get("authority_line"), str)
            or not isinstance(intent.get("metadata"), dict)
            or not isinstance(intent.get("authority_head"), dict)):
        raise RunStoreError("run append intent is invalid")
    try:
        parsed_run_id = uuid.UUID(intent["run_id"])
    except (TypeError, ValueError) as exc:
        raise RunStoreError("run append intent has an invalid run id") from exc
    if str(parsed_run_id) != intent["run_id"]:
        raise RunStoreError("run append intent has a non-canonical run id")
    runs_fd = run_fd = -1
    try:
        runs_fd = os.open("runs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=state_fd)
        run_fd = os.open(intent["run_id"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=runs_fd)
        _ensure_append(run_fd, SIDECAR_NAME, intent["sidecar_old_size"],
                       intent["sidecar_line"].encode("utf-8"))
        _atomic_json(run_fd, SIDECAR_META_NAME, intent["metadata"])
        _ensure_append(state_fd, AUTHORITY_NAME, intent["authority_old_size"],
                       intent["authority_line"].encode("utf-8"))
        _atomic_json(state_fd, AUTHORITY_HEAD_NAME, intent["authority_head"])
        os.unlink(APPEND_INTENT_NAME, dir_fd=state_fd)
        _sync(state_fd)
    except FileNotFoundError as exc:
        raise RunStoreError("run append intent references missing history") from exc
    except OSError as exc:
        raise RunStoreError("run append intent recovery failed") from exc
    finally:
        if run_fd >= 0:
            os.close(run_fd)
        if runs_fd >= 0:
            os.close(runs_fd)


def _recover_append_intent(state_fd: int, locked: bool = False) -> None:
    if not locked:
        with _authority_lock(state_fd):
            _recover_append_intent(state_fd, locked=True)
        return
    intent = _read_control_json(state_fd, APPEND_INTENT_NAME)
    if intent is not None:
        _apply_append_intent(state_fd, intent)


def _read_source_bytes(path: Path) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise RunStoreError("run source is not a bounded regular file: " + str(path))
        body = bytearray()
        while len(body) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) != info.st_size or len(body) > MAX_SOURCE_BYTES:
            raise RunStoreError("run source changed while it was read: " + str(path))
        return bytes(body)
    except OSError as exc:
        raise RunStoreError("run source is unreadable: " + str(path)) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(path: Path) -> Any:
    try:
        data = _read_source_bytes(path)
        return _loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RunStoreError("run source is not valid JSON: " + str(path)) from exc


def _read_jsonl(path: Path) -> List[Tuple[int, Any]]:
    rows: List[Tuple[int, Any]] = []
    try:
        for number, line in enumerate(_read_source_bytes(path).splitlines(), 1):
            if len(line) > MAX_RECORD_BYTES:
                raise RunStoreError("run source line exceeds the size limit: " + str(path))
            if not line.strip():
                continue
            rows.append((number, _loads(line.decode("utf-8"))))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, RunStoreError):
            raise
        raise RunStoreError("run source is not valid JSONL: " + str(path)) from exc
    return rows


def _sidecar_rows(directory_fd: int, state_fd: Optional[int] = None,
                  expected_run_id: Optional[str] = None,
                  authority_locked: bool = False) -> List[Dict[str, Any]]:
    if state_fd is not None and not authority_locked:
        with _authority_lock(state_fd):
            return _sidecar_rows(directory_fd, state_fd, expected_run_id, authority_locked=True)
    if state_fd is not None:
        _recover_append_intent(state_fd, locked=True)
    descriptor = -1
    try:
        descriptor = os.open(SIDECAR_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RunStoreError("run sidecar must be a bounded mode-0600 regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RunStoreError("run sidecar is owned by another user")
        body = bytearray()
        while len(body) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
    except FileNotFoundError:
        _verify_metadata([], _read_metadata(directory_fd))
        if state_fd is not None and expected_run_id is not None:
            _verify_authority(state_fd, expected_run_id, [])
        return []
    except OSError as exc:
        raise RunStoreError("run sidecar is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    data = bytes(body)
    if len(data) != info.st_size or len(data) > MAX_SOURCE_BYTES:
        raise RunStoreError("run sidecar changed while it was read")
    if data and not data.endswith(b"\n"):
        raise RunStoreError("run sidecar ends with an incomplete record")
    rows = []
    previous = "0" * 64
    creation_digest = None
    previous_record = None
    try:
        for sequence, line in enumerate(data.splitlines(), 1):
            if len(line) > MAX_RECORD_BYTES or not line:
                raise RunStoreError("run sidecar contains an invalid line")
            envelope = _loads(line.decode("utf-8"))
            required = {"schema_version", "sequence", "event_type", "previous_hash",
                        "creation_digest", "record_digest", "terminal_digest", "record", "hash"}
            if not isinstance(envelope, dict) or required - set(envelope):
                raise RunStoreError("run sidecar contains an invalid envelope")
            if envelope["schema_version"] != SIDECAR_SCHEMA_VERSION:
                raise RunStoreError("unsupported run sidecar schema version: "
                                    + str(envelope["schema_version"]))
            unsigned = {name: value for name, value in envelope.items() if name != "hash"}
            record = envelope["record"]
            if (envelope["sequence"] != sequence
                    or envelope["previous_hash"] != previous
                    or not isinstance(record, dict)
                    or envelope["record_digest"] != _digest(record)
                    or envelope["hash"] != _digest(unsigned)):
                raise RunStoreError("run sidecar hash chain is invalid")
            current_creation = _immutable_digest(record)
            if sequence == 1:
                creation_digest = current_creation
                if envelope["event_type"] != "creation" or record.get("status") != "queued":
                    raise RunStoreError("run sidecar has an invalid creation event")
            else:
                if envelope["event_type"] != "transition":
                    raise RunStoreError("run sidecar has an invalid transition event")
                if current_creation != creation_digest:
                    raise RunStoreError("run sidecar immutable creation fields changed")
                if not _legal_transition(previous_record or {}, record):
                    raise RunStoreError("run sidecar contains an illegal lifecycle transition")
            if envelope["creation_digest"] != creation_digest:
                raise RunStoreError("run sidecar creation digest is invalid")
            expected_terminal = (_terminal_digest(sequence, previous, creation_digest,
                                                    envelope["record_digest"])
                                 if _is_final(record) else None)
            if envelope["terminal_digest"] != expected_terminal:
                raise RunStoreError("run sidecar terminal digest is missing or invalid")
            previous = envelope["hash"]
            previous_record = record
            rows.append(envelope)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, RunStoreError):
            raise
        raise RunStoreError("run sidecar contains invalid JSON") from exc
    _verify_metadata(rows, _read_metadata(directory_fd))
    if state_fd is not None:
        run_id = str(rows[0]["record"]["run_id"]) if rows else expected_run_id
        if run_id is None:
            raise RunStoreError("run authority verification needs a run id")
        _verify_authority(state_fd, run_id, rows)
    return rows


def read_sidecar(directory_fd: int, state_fd: Optional[int] = None,
                 expected_run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the newest verified record, or None for a pre-sidecar run."""
    rows = _sidecar_rows(directory_fd, state_fd, expected_run_id)
    return dict(rows[-1]["record"]) if rows else None


def read_sidecar_details(directory_fd: int, state_fd: Optional[int] = None,
                         expected_run_id: Optional[str] = None
                         ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return the latest record and verified chain identity."""
    rows = _sidecar_rows(directory_fd, state_fd, expected_run_id)
    if not rows:
        return None, None
    metadata = _history_metadata(rows)
    metadata["hashes"] = [row["hash"] for row in rows]
    return dict(rows[-1]["record"]), metadata


def append_sidecar(directory_fd: int, record: Mapping[str, Any],
                   state_fd: Optional[int] = None, authority_locked: bool = False) -> None:
    """Append and fsync one full snapshot linked to the previous snapshot."""
    if state_fd is not None and not authority_locked:
        with _authority_lock(state_fd):
            append_sidecar(directory_fd, record, state_fd, authority_locked=True)
        return
    rows = _sidecar_rows(directory_fd, state_fd, str(record.get("run_id")), authority_locked)
    clean = dict(record)
    _json_bytes(clean)
    if rows and rows[-1]["record"] == clean:
        return
    creation_digest = rows[0]["creation_digest"] if rows else _immutable_digest(clean)
    current_creation = _immutable_digest(clean)
    if current_creation != creation_digest:
        raise RunStoreError("run sidecar immutable creation fields changed")
    if not rows:
        if clean.get("status") != "queued":
            raise RunStoreError("run sidecar creation must start queued")
    elif not _legal_transition(rows[-1]["record"], clean):
        raise RunStoreError("run sidecar contains an illegal lifecycle transition")
    sequence = len(rows) + 1
    previous_hash = rows[-1]["hash"] if rows else "0" * 64
    record_digest = _digest(clean)
    terminal_digest = (_terminal_digest(sequence, previous_hash, creation_digest, record_digest)
                       if _is_final(clean) else None)
    unsigned = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "sequence": sequence,
        "event_type": "transition" if rows else "creation",
        "previous_hash": previous_hash,
        "creation_digest": creation_digest,
        "record_digest": record_digest,
        "terminal_digest": terminal_digest,
        "record": clean,
    }
    envelope = dict(unsigned, hash=_digest(unsigned))
    line = _canonical(envelope) + b"\n"
    if len(line) > MAX_RECORD_BYTES:
        raise RunStoreError("run sidecar record exceeds the size limit")
    metadata = {
        "schema_version": SIDECAR_META_SCHEMA_VERSION,
        "run_id": clean["run_id"],
        "creation_digest": creation_digest,
        "sequence": sequence,
        "head_hash": envelope["hash"],
        "terminal_digest": terminal_digest,
    }
    if state_fd is not None:
        history = list(rows) + [envelope]
        authority = _prepare_authority(state_fd, history)
        authority_line = _canonical(authority) + b"\n"
        try:
            sidecar_old_size = os.stat(SIDECAR_NAME, dir_fd=directory_fd,
                                       follow_symlinks=False).st_size
        except FileNotFoundError:
            sidecar_old_size = 0
        try:
            authority_old_size = os.stat(AUTHORITY_NAME, dir_fd=state_fd,
                                         follow_symlinks=False).st_size
        except FileNotFoundError:
            authority_old_size = 0
        intent = {
            "schema_version": APPEND_INTENT_SCHEMA_VERSION,
            "run_id": clean["run_id"],
            "sidecar_old_size": sidecar_old_size,
            "sidecar_line": line.decode("utf-8"),
            "metadata": metadata,
            "authority_old_size": authority_old_size,
            "authority_line": authority_line.decode("utf-8"),
            "authority_head": {
                "schema_version": AUTHORITY_SCHEMA_VERSION,
                "sequence": authority["sequence"],
                "head_hash": authority["hash"],
            },
        }
        _atomic_json(state_fd, APPEND_INTENT_NAME, intent)
        _apply_append_intent(state_fd, intent)
        return
    _ensure_append(directory_fd, SIDECAR_NAME, sum(len(_canonical(row)) + 1 for row in rows), line)
    _atomic_json(directory_fd, SIDECAR_META_NAME, metadata)


def _stable_id(kind: str, relative: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "model-citizen:" + kind + ":" + relative + ":" + key))


def _cost(amount: Any, normalized: Any = None) -> Optional[Dict[str, Any]]:
    if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
            or not math.isfinite(amount)):
        return None
    value: Dict[str, Any] = {"amount_usd": amount, "basis": COST_BASIS}
    if (isinstance(normalized, (int, float)) and not isinstance(normalized, bool)
            and math.isfinite(normalized)):
        value["cache_normalized_amount_usd"] = normalized
    return value


def _source_identity(record: Mapping[str, Any]) -> str:
    source = record.get("source") or {}
    return _digest({"kind": source.get("kind"), "path": source.get("path"),
                    "record_identity": source.get("record_identity", record.get("run_id"))})


def _seal_index_record(record: Mapping[str, Any], immutable: Optional[str] = None,
                       chain: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    sealed = dict(record)
    sealed["source_identity"] = _source_identity(sealed)
    raw = sealed.get("raw")
    sealed["immutable_digest"] = immutable or _digest({
        "source": sealed.get("source"), "raw": raw,
    })
    if chain is not None:
        sealed["chain"] = {
            "sequence": chain["sequence"], "head_hash": chain["head_hash"],
            "terminal_digest": chain["terminal_digest"],
            "hashes": list(chain.get("hashes") or []),
        }
    return sealed


def studio_record(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> Dict[str, Any]:
    target = dict(record.get("target") or {})
    raw = {name: value for name, value in record.items() if name != "admission_token"}
    normalized = {
        "schema_version": 1,
        "run_id": record["run_id"],
        "source": {"kind": "studio", "path": "runs/" + str(record["run_id"]) + "/" + SIDECAR_NAME},
        "suite": {"id": record.get("suite_id"), "version": record.get("suite_version")},
        "target": {
            "kind": target.get("kind"), "ref": target.get("ref"),
            "commit": target.get("revision"), "draft": target.get("draft"),
            "config_digest": target.get("config_digest"),
        },
        "runtime": record.get("runtime"),
        "model": record.get("model"),
        "arms": record.get("arms") or [],
        "trials": record.get("trials"),
        "parameters": record.get("parameters") or {},
        "argv": record.get("argv") or [],
        "case_identities": record.get("case_identities"),
        "spend": {
            "estimate": record.get("spend_estimate"),
            "cap": record.get("spend_cap"),
            "pricing_identity": record.get("pricing_identity"),
        },
        "canonical_run_digest": record.get("canonical_run_digest"),
        "status": record.get("status"),
        "times": {name: record.get(name) for name in ("created_at", "started_at", "completed_at")},
        "tokens": record.get("tokens") or {},
        "cost": _cost(record.get("cost_usd"), record.get("cost_normalised_usd")),
        "cases": record.get("cases") or {},
        "artifacts": ["runs/" + str(record["run_id"]) + "/" + name
                      for name in ("stdout.log", "stderr.log", "worker.log", SIDECAR_NAME,
                                   SIDECAR_META_NAME)] + [AUTHORITY_NAME],
        "raw": raw,
    }
    return _seal_index_record(normalized, str(metadata["creation_digest"]), metadata)


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _validate_benchmark_result(row: Mapping[str, Any]) -> None:
    if row.get("arm") not in ("bare", "harness") or not _nonnegative_int(row.get("rep")):
        raise RunStoreError("benchmark result row does not match schema 1")
    error = row.get("error")
    if not (error is None or isinstance(error, (bool, str))):
        raise RunStoreError("benchmark result row has invalid error")
    if not (isinstance(row.get("passed"), bool) or (row.get("passed") is None and bool(error))):
        raise RunStoreError("benchmark result row has invalid passed")
    if not _optional_string(row.get("error_kind")):
        raise RunStoreError("benchmark result row has invalid error kind")
    for name in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                 "cache_read_input_tokens", "turns", "spawns", "stop_hooks", "hook_blocks",
                 "first_call_cache_write"):
        if name in row and row[name] is not None and not _nonnegative_int(row[name]):
            raise RunStoreError("benchmark result row has invalid " + name)
    for name in ("cost_usd", "cost_normalised_usd", "wall_seconds", "cache_miss_ratio",
                 "predicted_ratio"):
        if (name in row and row[name] is not None
                and (not _finite_number(row[name]) or row[name] < 0)):
            raise RunStoreError("benchmark result row has invalid " + name)
    for name in ("tag", "cli_version", "date", "harness_version", "bucket", "change_note",
                 "preflight", "fingerprint_source", "arm_config_dir"):
        if name in row and not _optional_string(row[name]):
            raise RunStoreError("benchmark result row has invalid " + name)
    tool_counts = row.get("tool_counts")
    if (tool_counts is not None
            and (not isinstance(tool_counts, dict)
                 or any(not isinstance(name, str) or not name or not _nonnegative_int(count)
                        for name, count in tool_counts.items()))):
        raise RunStoreError("benchmark result row has invalid tool_counts")


def _benchmark_result(relative: str, number: int, row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise RunStoreError("benchmark result row is not an object")
    version = row.get("schema_version", 1)
    if version != 1:
        raise RunStoreError("unsupported benchmark result schema version: " + str(version))
    required = {"task", "arm", "rep", "harness_sha", "model"}
    if (required - set(row) or any(not isinstance(row.get(name), str) or not row[name]
                                   for name in ("task", "harness_sha", "model"))
            or (row.get("tag") is not None and not isinstance(row.get("tag"), str))):
        raise RunStoreError("benchmark result row does not match schema 1")
    _validate_benchmark_result(row)
    identity = _digest({name: row.get(name) for name in ("task", "arm", "rep", "harness_sha", "tag")})
    run_id = _stable_id("benchmark-result", relative, identity)
    status = "failed" if row.get("error") else "succeeded" if row.get("passed") is True else "failed"
    tokens = {name: row[name] for name in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens") if isinstance(row.get(name), int)}
    return {
        "schema_version": 1, "run_id": run_id,
        "source": {"kind": "benchmark-result", "path": relative, "line": number,
                   "record_identity": identity},
        "suite": {"id": "cost-benchmark", "version": 1},
        "target": {"kind": "commit", "ref": row.get("tag"), "commit": row.get("harness_sha"),
                   "draft": None, "config_digest": row.get("arm_fingerprint")},
        "runtime": row.get("cli_version"), "model": row.get("model"),
        "arms": [row.get("arm")], "trials": row.get("rep"), "parameters": {}, "argv": [],
        "status": status, "times": {"created_at": row.get("date")}, "tokens": tokens,
        "cost": _cost(row.get("cost_usd"), row.get("cost_normalised_usd")),
        "cases": {str(row.get("task")): {"passed": row.get("passed"),
                                            "error": row.get("error"),
                                            "error_kind": row.get("error_kind")}},
        "artifacts": [relative], "raw": row,
    }


def _history_number(value: Any) -> bool:
    return (value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)
                              and math.isfinite(value)))


def _validate_history_summary(row: Mapping[str, Any]) -> None:
    for name in ("tag", "cli_version", "bucket", "change_note"):
        if name in row and not isinstance(row[name], str):
            raise RunStoreError("benchmark history row has an invalid " + name)
    for name in ("predicted_ratio", "threshold"):
        if name in row and not _history_number(row[name]):
            raise RunStoreError("benchmark history row has an invalid " + name)
    if ("runs" in row and (not isinstance(row["runs"], int) or isinstance(row["runs"], bool)
                           or row["runs"] < 0)):
        raise RunStoreError("benchmark history row has invalid runs")
    for arm in ("bare", "harness"):
        summary = row.get(arm)
        if not isinstance(summary, dict):
            raise RunStoreError("benchmark history row has an invalid " + arm + " summary")
        summary_fields = {"runs", "errors", "passed", "cost_per_passed"}
        if set(summary) != summary_fields:
            raise RunStoreError("benchmark history row has an invalid " + arm + " summary")
        for name in ("runs", "errors"):
            value = summary[name]
            if not _nonnegative_int(value):
                raise RunStoreError("benchmark history row has an invalid " + arm + " summary")
        if not _history_number(summary["passed"]) or not _history_number(
                summary["cost_per_passed"]):
            raise RunStoreError("benchmark history row has an invalid " + arm + " summary")
    reps = row.get("reps")
    if not isinstance(reps, int) or isinstance(reps, bool) or reps < 1:
        raise RunStoreError("benchmark history row has invalid reps")
    for name in ("ratio", "ratio_cache_normalised"):
        if not _history_number(row.get(name)):
            raise RunStoreError("benchmark history row has an invalid " + name)
    cache_miss = row.get("cache_miss")
    if (cache_miss is not None
            and (not isinstance(cache_miss, dict) or set(cache_miss) - {"bare", "harness"}
                 or any(not _history_number(value) for value in cache_miss.values()))):
        raise RunStoreError("benchmark history row has invalid cache_miss")
    per_task = row.get("per_task", {})
    if not isinstance(per_task, dict):
        raise RunStoreError("benchmark history row has invalid per_task")
    allowed = {"bare", "harness", "ratio", "bare_spread", "harness_spread", "n"}
    for task, cell in per_task.items():
        if not isinstance(task, str) or not task or not isinstance(cell, dict) or set(cell) != allowed:
            raise RunStoreError("benchmark history row has invalid per_task")
        if any(not _history_number(cell.get(name)) for name in allowed - {"n"}):
            raise RunStoreError("benchmark history row has invalid per_task")
        count = cell["n"]
        if not _nonnegative_int(count):
            raise RunStoreError("benchmark history row has invalid per_task")


def _benchmark_history(relative: str, number: int, row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise RunStoreError("benchmark history row is not an object")
    version = row.get("schema_version", 1)
    if version != 1:
        raise RunStoreError("unsupported benchmark history schema version: " + str(version))
    required = {"date", "series", "harness_version", "harness_sha", "model", "status", "reps",
                "bare", "harness", "ratio", "ratio_cache_normalised", "per_task"}
    string_required = {"date", "series", "harness_version", "harness_sha", "model", "status"}
    if (required - set(row) or any(not isinstance(row.get(name), str) or not row[name]
                                   for name in string_required)):
        raise RunStoreError("benchmark history row does not match schema 1")
    _validate_history_summary(row)
    status = {"passed": "succeeded", "failed": "failed", "inconclusive": "unknown"}.get(row["status"])
    if status is None:
        raise RunStoreError("benchmark history row has an unsupported status")
    by_arm = {}
    for arm in ("bare", "harness"):
        summary = row.get(arm)
        amount = summary.get("cost_per_passed") if isinstance(summary, dict) else None
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount):
            by_arm[arm] = amount
    cost = {"basis": COST_BASIS, "by_arm_usd_per_pass": by_arm,
            "ratio": row.get("ratio"),
            "cache_normalized_ratio": row.get("ratio_cache_normalised")}
    identity = _digest({name: row.get(name) for name in (
        "date", "series", "harness_version", "harness_sha", "bucket")})
    return {
        "schema_version": 1, "run_id": _stable_id("benchmark-history", relative, identity),
        "source": {"kind": "benchmark-history", "path": relative, "line": number,
                   "record_identity": identity},
        "suite": {"id": "cost-benchmark-history", "version": 1},
        "target": {"kind": "commit", "ref": row.get("tag"), "commit": row.get("harness_sha"),
                   "draft": None, "config_digest": None},
        "runtime": row.get("cli_version"), "model": row.get("model"),
        "arms": [name for name in ("bare", "harness") if name in row],
        "trials": row.get("reps"), "parameters": {"series": row.get("series"),
                                                    "bucket": row.get("bucket")},
        "argv": [], "status": status, "times": {"created_at": row.get("date")}, "tokens": {},
        "cost": cost, "cases": row.get("per_task") or {}, "artifacts": [relative], "raw": row,
    }


def _benchmark_static(relative: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        version = value.get("schema_version") if isinstance(value, dict) else None
        raise RunStoreError("unsupported benchmark static schema version: " + str(version))
    required = {"harness_version", "total", "usd", "files"}
    if (required - set(value) or not isinstance(value.get("harness_version"), str)
            or not value["harness_version"] or not isinstance(value.get("total"), dict)
            or not isinstance(value.get("usd"), dict) or not isinstance(value.get("files"), dict)):
        raise RunStoreError("benchmark static record does not match schema 2")
    aggregate_fields = {"files", "lines", "chars", "est_tokens"}
    if (set(value["total"]) != aggregate_fields
            or any(not _nonnegative_int(value["total"][name]) for name in aggregate_fields)):
        raise RunStoreError("benchmark static total does not match schema 2")
    for model, prices in value["usd"].items():
        if (not isinstance(model, str) or not model or not isinstance(prices, dict)
                or set(prices) != {"session_start", "later_turn"}
                or any(not _finite_number(prices[name]) or prices[name] < 0
                       for name in ("session_start", "later_turn"))):
            raise RunStoreError("benchmark static usd does not match schema 2")
    for path, details in value["files"].items():
        parsed = PurePosixPath(path) if isinstance(path, str) else None
        if (parsed is None or not path or "\0" in path or "\\" in path or parsed.is_absolute()
                or ".." in parsed.parts or not isinstance(details, dict)
                or set(details) != {"group", "chars", "est_tokens", "usd"}
                or details.get("group") not in ("always_loaded", "listings")
                or not _nonnegative_int(details.get("chars"))
                or not _nonnegative_int(details.get("est_tokens"))
                or not isinstance(details.get("usd"), dict)):
            raise RunStoreError("benchmark static files do not match schema 2")
        for model, prices in details["usd"].items():
            if (not isinstance(model, str) or not model or not isinstance(prices, dict)
                    or set(prices) != {"session_start", "later_turn"}
                    or any(not _finite_number(prices[name]) or prices[name] < 0
                           for name in ("session_start", "later_turn"))):
                raise RunStoreError("benchmark static files do not match schema 2")
    identity = str(value.get("harness_version"))
    return {
        "schema_version": 1, "run_id": _stable_id("benchmark-static", relative, identity),
        "source": {"kind": "benchmark-static", "path": relative,
                   "record_identity": identity},
        "suite": {"id": "static-cost", "version": 2},
        "target": {"kind": "release", "ref": value.get("harness_version"), "commit": None,
                   "draft": None, "config_digest": None},
        "runtime": None, "model": None, "arms": [], "trials": None, "parameters": {},
        "argv": [], "status": "succeeded", "times": {},
        "tokens": {"estimated": (value.get("total") or {}).get("est_tokens")},
        "cost": {"by_model": value.get("usd"), "basis": COST_BASIS},
        "cases": {}, "artifacts": [relative], "raw": value,
    }


def _native_evidence(relative: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RunStoreError("native evidence is not an object")
    version = value.get("schema_version", 1)
    if version != 1:
        raise RunStoreError("unsupported native evidence schema version: " + str(version))
    required = {"kind", "client", "harness_version", "source_commit", "cases"}
    if (required - set(value) or value.get("kind") != "native"
            or any(not isinstance(value.get(name), str) or not value[name]
                   for name in ("client", "harness_version", "source_commit"))
            or any(not _optional_string(value.get(name))
                   for name in ("client_version", "model_run"))
            or not isinstance(value.get("cases"), dict)
            or any(not isinstance(name, str) or not name
                   or state not in ("passed", "failed", "skipped")
                   for name, state in value.get("cases", {}).items())):
        raise RunStoreError("native evidence does not match schema 1")
    states = set(value["cases"].values())
    status = "succeeded" if states and states == {"passed"} else "failed"
    identity = _digest({name: value.get(name) for name in (
        "client", "harness_version", "source_commit")})
    return {
        "schema_version": 1, "run_id": _stable_id("native-acceptance", relative, identity),
        "source": {"kind": "native-acceptance", "path": relative,
                   "record_identity": identity},
        "suite": {"id": "native-acceptance", "version": 1},
        "target": {"kind": "commit", "ref": value.get("harness_version"),
                   "commit": value.get("source_commit"), "draft": None, "config_digest": None},
        "runtime": value.get("client_version"), "model": value.get("model_run"),
        "arms": [], "trials": None, "parameters": {"client": value.get("client")},
        "argv": [], "status": status, "times": {}, "tokens": {}, "cost": None,
        "cases": value.get("cases"), "artifacts": [relative], "raw": value,
    }


class RunStore:
    """SQLite index whose complete contents can be reconstructed from tracked and sidecar files."""

    def __init__(self, state_root: Path, database_name: str = DATABASE_NAME):
        self.state_root = Path(state_root)
        self.database_name = database_name
        try:
            with Store(self.state_root) as store:
                self._state_fd = os.dup(store.fd)
        except (OSError, StateError) as exc:
            raise RunStoreError(str(exc)) from exc
        _recover_append_intent(self._state_fd)
        self.path = self.state_root / database_name
        self.connection = self._open(self.path)
        self._migrate()

    def close(self) -> None:
        connection = getattr(self, "connection", None)
        if connection is not None:
            connection.close()
            self.connection = None
        descriptor = getattr(self, "_state_fd", None)
        if descriptor is not None:
            os.close(descriptor)
            self._state_fd = None

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    def read_studio(self, directory_fd: int, run_id: str
                    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return read_sidecar_details(directory_fd, self._state_fd, run_id)

    def append_studio(self, directory_fd: int, record: Mapping[str, Any]) -> None:
        append_sidecar(directory_fd, record, self._state_fd)

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        if path.exists() or path.is_symlink():
            info = _safe_regular(path)
            if path.is_symlink():
                raise RunStoreError("run index must not be a symlink")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise RunStoreError("run index is owned by another user")
        try:
            connection = sqlite3.connect(str(path), timeout=10)
            os.chmod(path, 0o600)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise RunStoreError("run index could not be opened") from exc

    def _migrate(self) -> None:
        try:
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if version > STORE_SCHEMA_VERSION:
                raise RunStoreError("unsupported run index schema version: " + str(version))
            with self.connection:
                if version == 0:
                    self.connection.execute(
                        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, "
                        "source_path TEXT NOT NULL, suite_id TEXT NOT NULL, status TEXT NOT NULL, "
                        "record_json TEXT NOT NULL)")
                    self.connection.execute("PRAGMA user_version=1")
                    version = 1
                if version == 1:
                    self.connection.execute("ALTER TABLE runs ADD COLUMN source_digest TEXT")
                    self.connection.execute("ALTER TABLE runs ADD COLUMN indexed_at TEXT")
                    self.connection.execute("CREATE INDEX IF NOT EXISTS runs_source ON runs(source_kind, source_path)")
                    self.connection.execute("PRAGMA user_version=2")
        except sqlite3.Error as exc:
            raise RunStoreError("run index migration failed") from exc

    def upsert(self, record: Mapping[str, Any]) -> None:
        try:
            with self.connection:
                self._upsert(record)
        except sqlite3.Error as exc:
            raise RunStoreError("run index update failed") from exc

    def _upsert(self, record: Mapping[str, Any]) -> None:
        sealed = dict(record) if "source_identity" in record else _seal_index_record(record)
        source = sealed.get("source") or {}
        body = _json_bytes(sealed)
        suite = sealed.get("suite") or {}
        if (sealed.get("schema_version") != 1 or not isinstance(sealed.get("run_id"), str)
                or not isinstance(source.get("kind"), str) or not isinstance(source.get("path"), str)
                or not isinstance(suite.get("id"), str) or not isinstance(sealed.get("status"), str)
                or not isinstance(sealed.get("source_identity"), str)
                or not isinstance(sealed.get("immutable_digest"), str)):
            raise RunStoreError("indexed run record is incomplete")
        existing_row = self.connection.execute(
            "SELECT record_json FROM runs WHERE run_id=?", (sealed["run_id"],)).fetchone()
        if existing_row is not None:
            existing = _loads(existing_row[0])
            self._assert_compatible(existing, sealed)
            self.connection.execute(
                "UPDATE runs SET source_kind=?,source_path=?,suite_id=?,status=?,record_json=?,"
                "source_digest=?,indexed_at=? WHERE run_id=?",
                (source["kind"], source["path"], suite["id"], sealed["status"], body,
                 hashlib.sha256(body.encode("utf-8")).hexdigest(), _timestamp(), sealed["run_id"]))
        else:
            self.connection.execute(
                "INSERT INTO runs(run_id,source_kind,source_path,suite_id,status,record_json,"
                "source_digest,indexed_at) VALUES(?,?,?,?,?,?,?,?)",
                (sealed["run_id"], source["kind"], source["path"], suite["id"],
                 sealed["status"], body, hashlib.sha256(body.encode("utf-8")).hexdigest(),
                 _timestamp()))

    @staticmethod
    def _assert_compatible(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> None:
        if (existing.get("source_identity") != incoming.get("source_identity")
                or existing.get("immutable_digest") != incoming.get("immutable_digest")):
            raise RunStoreError("run source identity collision: " + str(incoming.get("run_id")))
        if (incoming.get("source") or {}).get("kind") != "studio":
            return
        before, after = existing.get("chain"), incoming.get("chain")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise RunStoreError("Studio run index is missing chain identity")
        old_sequence, new_sequence = before.get("sequence"), after.get("sequence")
        if not isinstance(old_sequence, int) or not isinstance(new_sequence, int):
            raise RunStoreError("Studio run index has invalid chain identity")
        if new_sequence < old_sequence:
            raise RunStoreError("Studio run history was truncated")
        if new_sequence == old_sequence:
            if after.get("head_hash") != before.get("head_hash"):
                raise RunStoreError("Studio run history was rewritten")
            return
        hashes = after.get("hashes")
        if (not isinstance(hashes, list) or len(hashes) != new_sequence
                or hashes[old_sequence - 1] != before.get("head_hash")):
            raise RunStoreError("Studio run history does not extend its indexed chain")

    def get(self, run_id: str) -> Dict[str, Any]:
        try:
            row = self.connection.execute("SELECT record_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        except sqlite3.Error as exc:
            raise RunStoreError("run index read failed") from exc
        if row is None:
            raise RunStoreError("unknown run: " + run_id)
        if len(row[0].encode("utf-8")) > MAX_RECORD_BYTES:
            raise RunStoreError("indexed run record exceeds the size limit")
        try:
            value = _loads(row[0])
        except (ValueError, RecursionError) as exc:
            raise RunStoreError("indexed run record is corrupt") from exc
        if not isinstance(value, dict):
            raise RunStoreError("indexed run record is corrupt")
        return value

    def list(self, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        if (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000
                or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0):
            raise RunStoreError("run index bounds are invalid")
        try:
            rows = self.connection.execute(
                "SELECT record_json FROM runs ORDER BY indexed_at DESC, run_id LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
        except sqlite3.Error as exc:
            raise RunStoreError("run index read failed") from exc
        values = []
        for row in rows:
            if len(row[0].encode("utf-8")) > MAX_RECORD_BYTES:
                raise RunStoreError("indexed run record exceeds the size limit")
            try:
                value = _loads(row[0])
            except (ValueError, RecursionError) as exc:
                raise RunStoreError("indexed run record is corrupt") from exc
            if not isinstance(value, dict):
                raise RunStoreError("indexed run record is corrupt")
            values.append(value)
        return values

    def _records_by_id(self) -> Dict[str, Dict[str, Any]]:
        try:
            rows = self.connection.execute("SELECT run_id,record_json FROM runs").fetchall()
            return {run_id: _loads(body) for run_id, body in rows}
        except (sqlite3.Error, ValueError, RecursionError) as exc:
            raise RunStoreError("run index identity scan failed") from exc

    def _import_repository(self, root: Path) -> Tuple[int, List[Dict[str, str]]]:
        imported = 0
        skipped: List[Dict[str, str]] = []
        candidates: List[Tuple[Path, str]] = []
        history = root / "benchmarks" / "history.jsonl"
        static = root / "benchmarks" / "static.json"
        if history.is_file() or history.is_symlink():
            candidates.append((history, "history"))
        if static.is_file() or static.is_symlink():
            candidates.append((static, "static"))
        results = sorted((root / "benchmarks").glob("*/results.jsonl")) if (root / "benchmarks").is_dir() else []
        if len(results) > MAX_RESULTS_FILES:
            raise RunStoreError("too many benchmark result files")
        candidates.extend((path, "results") for path in results)
        evidence = sorted((root / "compatibility" / "evidence").glob("*.json")) \
            if (root / "compatibility" / "evidence").is_dir() else []
        if len(candidates) + len(evidence) > MAX_RESULTS_FILES:
            raise RunStoreError("too many run source files")
        candidates.extend((path, "native") for path in evidence)
        for path, kind in candidates:
            relative = path.relative_to(root).as_posix()
            try:
                if kind == "results":
                    records = [_benchmark_result(relative, number, row)
                               for number, row in _read_jsonl(path)]
                elif kind == "history":
                    records = [_benchmark_history(relative, number, row)
                               for number, row in _read_jsonl(path)]
                elif kind == "static":
                    records = [_benchmark_static(relative, _read_json(path))]
                else:
                    value = _read_json(path)
                    if isinstance(value, dict) and value.get("kind") != "native":
                        continue
                    records = [_native_evidence(relative, value)]
                with self.connection:
                    for record in records:
                        self._upsert(record)
                imported += len(records)
            except (RunStoreError, sqlite3.Error, AttributeError, TypeError) as exc:
                skipped.append({"path": relative, "reason": str(exc)})
        return imported, skipped

    def _import_sidecars(self, runs_fd: int) -> int:
        imported = 0
        for run_id in sorted(os.listdir(runs_fd)):
            try:
                uuid.UUID(run_id)
            except (TypeError, ValueError) as exc:
                raise RunStoreError("run directory has an invalid id: " + run_id) from exc
            directory = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=runs_fd)
            try:
                record, authority = self.read_studio(directory, run_id)
            finally:
                os.close(directory)
            if record is None:
                raise RunStoreError("Studio run has no authoritative sidecar: " + run_id)
            if record.get("run_id") != run_id:
                raise RunStoreError("Studio sidecar run id does not match its directory")
            self.upsert(studio_record(record, authority or {}))
            imported += 1
        return imported

    def reindex(self, repository: Path, runs_fd: int) -> Dict[str, Any]:
        """Transactionally rebuild the live index from verified authoritative files."""
        temporary_name = ".run-index.reindex." + uuid.uuid4().hex + ".sqlite3"
        existing = self._records_by_id()
        rebuilt = None
        try:
            rebuilt = RunStore(self.state_root, temporary_name)
            studio_count = rebuilt._import_sidecars(runs_fd)
            imported_count, skipped = rebuilt._import_repository(Path(repository).resolve())
            incoming = rebuilt._records_by_id()
            for run_id in sorted(set(existing) & set(incoming)):
                self._assert_compatible(existing[run_id], incoming[run_id])
            try:
                self.connection.execute("BEGIN EXCLUSIVE")
                self.connection.execute("DELETE FROM runs")
                for run_id in sorted(incoming):
                    self._upsert(incoming[run_id])
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            return {"studio_runs": studio_count, "imported_runs": imported_count,
                    "total_runs": studio_count + imported_count, "skipped": skipped}
        finally:
            if rebuilt is not None:
                rebuilt.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=self._state_fd)
