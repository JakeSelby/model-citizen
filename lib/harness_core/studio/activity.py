# SPDX-License-Identifier: MIT
"""Bounded, read-only Studio activity over the harness evidence ledgers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import urllib.parse
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

SCHEMA_VERSION = 1
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
READ_CHUNK = 64 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_SCAN_ROWS = 1_000
MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_OWNERSHIP_BYTES = 8 * 1024 * 1024
FILTERS = ("session", "repository", "hook", "outcome")
POLICY_HOOKS = Path(__file__).resolve().parents[3] / "policy" / "hooks"
HOOK_MODULE = re.compile(r"hooks/[a-z0-9][a-z0-9-]*\Z")
APPLY_ID = re.compile(r"[0-9a-f]{32}\Z")


class ActivityError(ValueError):
    """An activity query could not be honored as written."""


class ActivityReadError(OSError):
    """A ledger cannot be read within the public Activity bounds."""


def default_state_root(home: Optional[Path] = None) -> Path:
    root = Path(home) if home is not None else Path(
        os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())
    return root / ".local" / "state" / "agent-harness"


def _cursor(value: str, size: int) -> int:
    if not value:
        return size
    if not value.startswith("v1:"):
        raise ActivityError("activity cursor is invalid")
    try:
        offset = int(value[3:])
    except ValueError as exc:
        raise ActivityError("activity cursor is invalid") from exc
    if offset < 0 or offset > size:
        raise ActivityError("activity cursor is outside the decision ledger")
    return offset


def _reverse_lines(path: Path, end: int) -> Iterator[Tuple[int, bytes]]:
    """Yield `(line_start, line)` newest first without loading `path` whole."""
    with path.open("rb") as stream:
        position = end
        buffer = b""
        while position:
            amount = min(READ_CHUNK, position)
            position -= amount
            stream.seek(position)
            buffer = stream.read(amount) + buffer
            if len(buffer) > MAX_LINE_BYTES + READ_CHUNK:
                raise ActivityReadError("decision ledger row exceeds the safe read limit")
            while b"\n" in buffer:
                split = buffer.rfind(b"\n")
                line = buffer[split + 1:]
                line_start = position + split + 1
                buffer = buffer[:split]
                if line:
                    yield line_start, line
        if buffer:
            yield 0, buffer


def _detail(row: Dict[str, object]) -> Dict[str, object]:
    value = row.get("detail")
    return value if isinstance(value, dict) else {}


def _repository(row: Dict[str, object], detail: Dict[str, object]) -> str:
    for source in (detail, row):
        for name in ("repository", "repo", "counterparty"):
            value = source.get(name)
            if isinstance(value, str) and value:
                return value
    return ""


def _module(row: Dict[str, object]) -> str:
    value = row.get("module")
    if not isinstance(value, str) or not HOOK_MODULE.fullmatch(value):
        return ""
    source = POLICY_HOOKS / (value.removeprefix("hooks/") + ".py")
    return value if source.is_file() and source.resolve().parent == POLICY_HOOKS.resolve() else ""


def _library_href(module: str) -> str:
    if not module.startswith("hooks/"):
        return ""
    source = "policy/" + module + ".py"
    return "/library?path=" + urllib.parse.quote(source, safe="/")


def _decision_state(answer: str) -> str:
    if answer in ("deny", "blocked"):
        return "refused"
    if answer == "ask":
        return "confirmation-required"
    if answer in ("allow", "released", "passed"):
        return "allowed"
    return answer or "unknown"


def _decision_detail(row: Dict[str, object], point: str, command: str) -> Dict[str, object]:
    detail = _detail(row)
    if detail or point not in ("governance", "decision-provider"):
        return detail
    try:
        value = json.loads(command)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _decision_reason(point: str, answer: str, detail: Dict[str, object]) -> str:
    recorded = detail.get("reason")
    if isinstance(recorded, str) and recorded:
        return recorded
    if answer in ("deny", "blocked"):
        return "%s refused this command under the active guardrails." % (point or "Harness")
    if answer == "ask":
        return "%s required confirmation before this command could run." % (point or "Harness")
    if answer in ("allow", "released", "passed"):
        return "%s allowed this action under the active guardrails." % (point or "Harness")
    return "%s recorded %s." % (point or "Harness", answer or "an unknown decision")


def _decision_entry(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    identity = row.get("decision_id")
    answer = row.get("deterministic_answer")
    if not isinstance(identity, str) or not identity or not isinstance(answer, str):
        return None
    point = row.get("point") if isinstance(row.get("point"), str) else ""
    module = _module(row)
    state = _decision_state(answer)
    command = row.get("input") if isinstance(row.get("input"), str) else ""
    title = {
        "refused": "Command refused",
        "confirmation-required": "Command needed confirmation",
        "allowed": "Command allowed",
    }.get(state, "Harness decision")
    detail = _decision_detail(row, point, command)
    grade = row.get("grade", detail.get("grade"))
    return {
        "id": "decision:" + identity,
        "timestamp": row.get("ts") if isinstance(row.get("ts"), str) else "",
        "source": "decision-log",
        "kind": "decision",
        "title": title,
        "outcome": state,
        "reason": _decision_reason(point, answer, detail),
        "actor": row.get("runtime") if isinstance(row.get("runtime"), str) else "harness",
        "session": row.get("session_id") if isinstance(row.get("session_id"), str) else "",
        "repository": _repository(row, detail),
        "hook": module,
        "grade": str(grade) if isinstance(grade, int) and not isinstance(grade, bool) else "unknown",
        "command": command,
        "draft": "",
        "files": [],
        "evidence_href": _library_href(module),
        "evidence_label": "Open %s in Library" % module if module else "",
    }


def _strings(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _event_entry(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    event = row.get("event")
    if not isinstance(event, str) or not event.startswith("studio."):
        return None
    detail = _detail(row)
    action = event.split(".", 1)[1]
    draft = detail.get("draft") if isinstance(detail.get("draft"), str) else ""
    files = _strings(detail.get("files"))
    outcome = detail.get("outcome") if isinstance(detail.get("outcome"), str) else "completed"
    identity = row.get("id") if isinstance(row.get("id"), str) else ""
    if not identity:
        basis = json.dumps(row, sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
    titles = {"apply": "Draft applied", "rollback": "Apply rolled back"}
    # A rollback links to the apply (or rollback) it reversed, by that entry's Activity id.
    reverses = detail.get("reverses") if action == "rollback" else None
    linked = isinstance(reverses, str) and APPLY_ID.fullmatch(reverses) is not None
    return {
        "id": "studio:" + identity,
        "timestamp": row.get("ts") if isinstance(row.get("ts"), str) else "",
        "source": "studio-action",
        "kind": action,
        "title": titles.get(action) or "Studio " + action.replace("-", " "),
        "outcome": outcome,
        "reason": detail.get("reason") if isinstance(detail.get("reason"), str)
            else "Studio recorded the governed %s action." % action,
        "actor": detail.get("actor") if isinstance(detail.get("actor"), str) else "Studio",
        "session": detail.get("session") if isinstance(detail.get("session"), str) else "",
        "repository": _repository(row, detail),
        "hook": "",
        "grade": "unknown",
        "command": detail.get("command") if isinstance(detail.get("command"), str) else "",
        "draft": draft,
        "files": files,
        "evidence_href": "/activity?entry=" + urllib.parse.quote("studio:" + str(reverses), safe="")
        if linked else "",
        "evidence_label": "Open the change this rolled back" if linked else "",
    }


def _entry(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    if row.get("kind") == "decision":
        return _decision_entry(row)
    if row.get("kind") == "event":
        return _event_entry(row)
    return None


def _matches(entry: Dict[str, object], filters: Dict[str, str]) -> bool:
    fields = {"session": "session", "repository": "repository", "hook": "hook",
              "outcome": "outcome"}
    for name, field in fields.items():
        wanted = filters[name].strip().casefold()
        actual = str(entry[field]).casefold()
        if wanted and wanted not in actual:
            return False
    return True


def _ownership_source(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"id": "ownership-journal", "status": "empty",
                "message": "No ownership snapshot has been recorded."}
    try:
        if path.stat().st_size > MAX_OWNERSHIP_BYTES:
            raise ActivityError("ownership journal exceeds the safe read limit")
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        records = data.get("files") if isinstance(data, dict) else None
        if not isinstance(records, dict):
            raise ActivityError("ownership journal has an invalid shape")
    except (OSError, UnicodeError, ValueError, ActivityError) as exc:
        return {"id": "ownership-journal", "status": "failed", "message": str(exc)}
    return {"id": "ownership-journal", "status": "current",
            "message": "Current ownership snapshot records %d owned file%s." % (
                len(records), "" if len(records) == 1 else "s")}


def _request(value: Dict[str, object]) -> Tuple[int, str, Dict[str, str]]:
    allowed = set(FILTERS) | {"limit", "cursor"}
    if set(value) - allowed:
        raise ActivityError("activity request has unsupported fields")
    limit = value.get("limit", DEFAULT_LIMIT)
    cursor = value.get("cursor", "")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ActivityError("activity limit must be from 1 to %d" % MAX_LIMIT)
    if not isinstance(cursor, str):
        raise ActivityError("activity cursor must be a string")
    filters = {}
    for name in FILTERS:
        item = value.get(name, "")
        if not isinstance(item, str):
            raise ActivityError("activity filter %s must be a string" % name)
        filters[name] = item
    return limit, cursor, filters


def _command(limit: int, cursor: str, filters: Dict[str, str], json_output: bool = True) -> str:
    words = ["citizen", "activity"]
    if json_output:
        words.append("--json")
    words.extend(["--limit", str(limit)])
    if cursor:
        words.extend(["--cursor", cursor])
    for name in FILTERS:
        if filters[name]:
            words.extend(["--" + name, filters[name]])
    return " ".join(shlex.quote(word) for word in words)


def query(state_root: Path, request: Dict[str, object]) -> Dict[str, object]:
    """Return one newest-first page while keeping ledger reads bounded by a cursor."""
    limit, cursor_text, filters = _request(request)
    state_root = Path(state_root)
    decisions = state_root / "decisions.jsonl"
    ownership = state_root / "ownership.json"
    ownership_source = _ownership_source(ownership)
    entries = []
    decision_limit = limit
    malformed = 0
    next_offset = 0
    exhausted = True
    scan_limited = False
    if decisions.exists():
        try:
            size = decisions.stat().st_size
            end = _cursor(cursor_text, size)
            continuation = end
            scanned_rows = 0
            scanned_bytes = 0
            if decision_limit == 0:
                next_offset = end
                exhausted = end == 0
            for offset, line in _reverse_lines(decisions, end):
                if decision_limit == 0:
                    break
                row_bytes = len(line) + 1
                if scanned_rows and scanned_bytes + row_bytes > MAX_SCAN_BYTES:
                    next_offset = continuation
                    exhausted = False
                    scan_limited = True
                    break
                scanned_rows += 1
                scanned_bytes += row_bytes
                next_offset = offset
                continuation = offset
                if len(line) > MAX_LINE_BYTES:
                    malformed += 1
                else:
                    try:
                        row = json.loads(line.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        malformed += 1
                    else:
                        if not isinstance(row, dict):
                            malformed += 1
                        else:
                            item = _entry(row)
                            if item is not None:
                                item["id"] = "%s@%d" % (item["id"], offset)
                                if _matches(item, filters):
                                    entries.append(item)
                                    decision_limit -= 1
                if decision_limit == 0:
                    exhausted = offset == 0
                    break
                if scanned_rows >= MAX_SCAN_ROWS or scanned_bytes >= MAX_SCAN_BYTES:
                    exhausted = offset == 0
                    scan_limited = not exhausted
                    break
            else:
                exhausted = True
            decision_source = {"id": "decision-log", "status": "partial" if malformed else "current",
                               "message": "%d malformed row%s skipped." % (
                                   malformed, "" if malformed == 1 else "s") if malformed
                               else "Decision ledger page scan limit reached; continue with the returned cursor."
                               if scan_limited else "Decision ledger available."}
        except OSError as exc:
            decision_source = {"id": "decision-log", "status": "failed", "message": str(exc)}
            exhausted = True
    else:
        decision_source = {"id": "decision-log", "status": "empty",
                           "message": "No decisions have been recorded."}
    entries = [item for item in entries if item is not None]
    entries.sort(key=lambda item: (str(item["timestamp"]), str(item["id"])), reverse=True)
    next_cursor = "" if exhausted or next_offset <= 0 else "v1:%d" % next_offset
    return {
        "schema_version": SCHEMA_VERSION,
        "entries": entries,
        "next_cursor": next_cursor,
        "sources": [decision_source, ownership_source],
        "filters": filters,
        "command": _command(limit, cursor_text, filters),
        "next_command": _command(limit, next_cursor, filters, json_output=False)
            if next_cursor else "",
    }
