# SPDX-License-Identifier: MIT
"""One operational overview assembled from the CLI's authoritative queries."""
from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
COMMANDS = {
    "doctor": "citizen doctor",
    "diff": "citizen diff",
    "catalog": "citizen catalog",
    "sync": "citizen sync",
}
_SOURCE: Optional[Callable[[], Dict[str, Any]]] = None
_REPAIR_COMMAND = re.compile(
    r"`((?:citizen|bin/harness)(?: [^`]+)?|/plugin install(?: [^`]*)?)`"
)
# Doctor lines that name a command as a pointer, not a repair: always informational, so a
# correctly configured home can read green. Matched by line, never by its backticks.
_INFORMATIONAL = re.compile(r"^constrained roles: ")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_ATTENTION = re.compile(
    r"\b(?:error|failed|missing|problem|stale|unavailable|unreadable|warning)\b|not on PATH",
    re.IGNORECASE,
)


def install(source: Callable[[], Dict[str, Any]]) -> None:
    """Install the process-local adapter over the CLI functions that launched Studio."""
    global _SOURCE
    _SOURCE = source


def current() -> Dict[str, Any]:
    if _SOURCE is None:
        return unavailable("overview source is unavailable")
    return _SOURCE()


def unavailable(message: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "installed": {"status": "failed", "version": None},
        "release": {"status": "unavailable", "version": None, "changelog_url": None},
        "mode": {"status": "failed", "value": None, "source": None},
        "doctor": {"status": "failed", "message": message, "checks": []},
        "drift": {"status": "failed", "message": message, "items": [],
                  "command": COMMANDS["sync"]},
        "runs": {"status": "failed", "message": message, "items": []},
        "commands": dict(COMMANDS),
    }


def snapshot(root: Path, *, catalog: Callable[[], Mapping[str, Any]],
             doctor: Callable[[], Sequence[str]], drift: Callable[[], Sequence[str]],
             selection: Callable[[], Mapping[str, Any]],
             runs: Callable[[], Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    """Run every source independently so one unavailable source never looks empty or healthy."""
    payload = unavailable("source unavailable")
    payload["generated_at"] = _now()

    try:
        catalog_value = catalog()
        version = catalog_value.get("version")
        if not isinstance(version, str) or _version(version) is None:
            raise ValueError("catalog version is invalid")
        payload["installed"] = {"status": "current", "version": version}
        payload["release"] = stable_release(Path(root), version)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, SystemExit):
        pass

    try:
        payload["doctor"] = {
            "status": "current", "message": None, "checks": doctor_checks(doctor()),
        }
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, SystemExit):
        payload["doctor"] = {"status": "failed", "message": "Doctor checks are unavailable.",
                             "checks": []}

    try:
        items = list(drift())
        if any(not isinstance(item, str) or not item for item in items):
            raise ValueError("drift item is invalid")
        payload["drift"] = {
            "status": "drift" if items else "current", "message": None,
            "items": items, "command": COMMANDS["sync"],
        }
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, SystemExit):
        payload["drift"] = {"status": "failed", "message": "Projection drift is unavailable.",
                            "items": [], "command": COMMANDS["sync"]}

    try:
        selected = selection()
        sources = selected.get("sources") if isinstance(selected.get("sources"), Mapping) else {}
        value = selected.get("mode") or "none"
        source = sources.get("mode") if isinstance(sources, Mapping) else None
        if not isinstance(value, str) or source is not None and not isinstance(source, str):
            raise ValueError("selection mode is invalid")
        payload["mode"] = {"status": "current", "value": value, "source": source}
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, SystemExit):
        payload["mode"] = {"status": "failed", "value": None, "source": None}

    try:
        payload["runs"] = {"status": "current", "message": None,
                           "items": recent_runs(runs())}
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, SystemExit):
        payload["runs"] = {"status": "failed", "message": "Recent runs are unavailable.",
                           "items": []}
    return payload


def doctor_checks(lines: Iterable[str]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, str):
            raise ValueError("doctor output is invalid")
        message = line.strip()
        if not message or message.startswith("model-citizen "):
            continue
        informational = _INFORMATIONAL.match(message) is not None
        fixes = [] if informational else [found.group(1) for found in _REPAIR_COMMAND.finditer(message)]
        command = fixes[0] if fixes else None
        # A pointer line is never a repair, but a warning word in it still needs attention.
        tone = "attention" if fixes or _ATTENTION.search(message) else "informational"
        checks.append({"id": "doctor-%d" % (len(checks) + 1), "status": tone,
                       "message": message, "fix": command, "fixes": fixes})
    return checks


def recent_runs(records: Sequence[Mapping[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    ordered = sorted(records, key=lambda row: str(row.get("created_at") or ""), reverse=True)
    result = []
    for row in ordered[:limit]:
        required = (row.get("run_id"), row.get("suite_id"), row.get("status"),
                    row.get("created_at"))
        if any(not isinstance(value, str) or not value for value in required):
            raise ValueError("run summary is invalid")
        target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
        result.append({"run_id": row["run_id"], "suite_id": row["suite_id"],
                       "status": row["status"], "created_at": row["created_at"],
                       "target_kind": target.get("kind") if isinstance(target.get("kind"), str)
                       else "unknown"})
    return result


def stable_release(root: Path, installed: str) -> Dict[str, Any]:
    stable = None
    for ref in ("refs/remotes/origin/stable", "refs/heads/stable"):
        done = _git(root, "show", ref + ":VERSION")
        if done is not None:
            stable = done.strip()
            break
    installed_version = _version(installed)
    stable_version = _version(stable) if stable else None
    if installed_version is None or stable_version is None:
        return {"status": "unavailable", "version": None, "changelog_url": None}
    newer = stable_version > installed_version
    return {"status": "update_available" if newer else "current", "version": stable,
            "changelog_url": _changelog_url(root) if newer else None}


def _git(root: Path, *args: str) -> Optional[str]:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          timeout=5, check=False)
    return done.stdout if done.returncode == 0 else None


def _changelog_url(root: Path) -> Optional[str]:
    remote = _git(root, "remote", "get-url", "origin")
    if remote is None:
        return None
    value = remote.strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?", value)
    return "https://github.com/%s/blob/stable/CHANGELOG.md" % match.group(1) if match else None


def _version(value: Optional[str]) -> Optional[Tuple[int, int, int]]:
    match = _SEMVER.fullmatch(value or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
