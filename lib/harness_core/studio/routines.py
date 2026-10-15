# SPDX-License-Identifier: MIT
"""Routines: local checks the Studio schedules with a per-user launchd agent (AH-S383, #1335).

Two routines are defined, and nothing else can be named:

- **rule-adherence**: the rule detectors in `policy/hooks/rule-detectors.py`, run offline over the
  Claude Code transcripts under `~/.claude/projects` written since the last run (at most
  `AUDIT_DAYS` back, newest `AUDIT_SESSIONS`). No model call. Its findings are the detector hits;
  it fails only when the audit itself cannot run.
- **hook-liveness**: the Hooks page's own liveness (`surfaces.hooks`, through
  `periods.efficacy_rows`), so the routine and the page never disagree. It fails when a hook is
  erroring; a silent hook is named in the summary but is not a failure, since some hooks rarely
  fire.

**Where things live.** Run history is one JSON file per run under
`<home>/.local/state/agent-harness/studio/routines/<name>/runs/`. The scheduler writes one launchd
agent per installed routine to `<home>/Library/LaunchAgents/com.agent-harness.routine.<name>.plist`,
following `remote_control.py`, that runs `citizen studio routine run <name> --scheduled` every
`interval_seconds`. `launchctl` is a parameter everywhere it is called, so tests pass a fake.

**Why not `RunSupervisor`.** A supervised run builds its target, a clone of the checkout and a
synced profile, and its worker runs with `HOME` set to that profile (`run_worker.py`), so an audit
there would read an empty profile instead of the transcripts and decision log it exists to audit.
Routine runs therefore keep their own records, written atomically like the run store's.

**Runs and locks.** A run holds `routines/<name>/run.lock` for its whole length and writes its
record as `running` first, then finished. A `running` record whose lock nobody holds is read as
`failed` (interrupted), so a killed run never shows as running forever.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import importlib.util
import json
import os
import plistlib
import pwd
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import periods

LABEL_PREFIX = "com.agent-harness.routine."
LAUNCHCTL = "/bin/launchctl"
SYSTEM_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
HISTORY_KEEP = 100
HISTORY_SHOWN = 20
AUDIT_DAYS = 7
AUDIT_SESSIONS = 200
OUTCOMES = ("ok", "failed", "running")
TRIGGERS = ("citizen", "studio", "scheduled")
REPO = Path(__file__).resolve().parents[3]
CLI_COMMANDS = {
    "list": ("citizen", "studio", "routine", "list", "--json"),
    "run": ("citizen", "studio", "routine", "run", "{name}", "--json"),
    "install": ("citizen", "studio", "routine", "install", "{name}", "--json"),
    "remove": ("citizen", "studio", "routine", "remove", "{name}", "--json"),
}

Launchctl = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class RoutineError(ValueError):
    """A routine request refused; `code` is the error the route and the CLI print."""

    def __init__(self, code: str, reason: str = ""):
        super().__init__(reason or code)
        self.code = code
        self.reason = reason


# --------------------------------------------------------------------------- definitions

def _rule_adherence(home: Path, since: Optional[dt.datetime], now: dt.datetime) -> Dict[str, Any]:
    """The detectors over each transcript written since `since`, newest `AUDIT_SESSIONS` first."""
    detectors_path = REPO / "policy" / "hooks" / "rule-detectors.py"
    spec = importlib.util.spec_from_file_location("routine_rule_detectors", str(detectors_path))
    detectors = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(detectors)
    from ruleprobe.readers import claude_code  # importable once rule-detectors.py has loaded

    floor = now - dt.timedelta(days=AUDIT_DAYS)
    if since is None or since < floor:
        since = floor
    root = Path(home) / ".claude" / "projects"
    recent = []
    for path in (claude_code.transcripts(str(root)) if root.is_dir() else []):
        try:
            written = dt.datetime.fromtimestamp(Path(path).stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        if since <= written <= now:
            recent.append(path)
    recent = recent[-AUDIT_SESSIONS:]
    hits: Dict[str, int] = {}
    sessions = unreadable = 0
    for path in recent:
        try:
            session = claude_code.read(path)
        except (OSError, ValueError):
            unreadable += 1
            continue
        if session is None:
            continue
        sessions += 1
        for detector_id, found in detectors.run(session.events).items():
            hits[detector_id] = hits.get(detector_id, 0) + len(found)
    total = sum(hits.values())
    top = sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:3]
    summary = "%d session%s since %s, %d finding%s" % (
        sessions, "" if sessions == 1 else "s", periods.iso(since), total, "" if total == 1 else "s")
    if top:
        summary += ": " + ", ".join("%s %d" % pair for pair in top)
    if unreadable:
        summary += "; %d transcript%s unreadable" % (unreadable, "" if unreadable == 1 else "s")
    return {"outcome": "ok", "summary": summary,
            "details": {"sessions": sessions, "findings": total, "unreadable": unreadable,
                        "hits": dict(sorted(hits.items())), "since": periods.iso(since)}}


def _hook_liveness(home: Path, _since: Optional[dt.datetime], now: dt.datetime) -> Dict[str, Any]:
    """Each hook's liveness as the Hooks page computes it, over the last `SILENT_DAYS`."""
    from .surfaces import hooks
    found = periods.efficacy_rows("hooks", days=hooks.SILENT_DAYS, home=home, now=now)
    states: Dict[str, str] = {}
    for row in found["rows"]:
        states[row["item_id"]] = str(row["metrics"].get("liveness", "unknown"))
    erroring = sorted(unit for unit, state in states.items() if state == "erroring")
    silent = sorted(unit for unit, state in states.items() if state == "silent")
    live = sum(1 for state in states.values() if state in ("live", "built in"))
    summary = "%d live, %d silent, %d erroring" % (live, len(silent), len(erroring))
    named = [("erroring", erroring), ("silent", silent)]
    for label, units in named:
        if units:
            summary += "; %s: %s" % (label, ", ".join(units[:5]) + (" and more" if len(units) > 5 else ""))
    return {"outcome": "failed" if erroring else "ok", "summary": summary,
            "details": {"live": live, "silent": silent, "erroring": erroring,
                        "hooks": dict(sorted(states.items()))}}


DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "rule-adherence": {
        "title": "Rule adherence audit",
        "description": "Reads the transcripts written since the last run with the rule detectors. "
                       "No model calls.",
        "interval_seconds": 86400, "label": "Daily", "run": _rule_adherence},
    "hook-liveness": {
        "title": "Hook liveness",
        "description": "Checks every hook fired when it should and logged no errors.",
        "interval_seconds": 3600, "label": "Hourly", "run": _hook_liveness},
}


def names() -> List[str]:
    return sorted(DEFINITIONS)


def definition(name: Any) -> Dict[str, Any]:
    if not isinstance(name, str) or name not in DEFINITIONS:
        raise RoutineError("unknown_routine")
    return DEFINITIONS[name]


# --------------------------------------------------------------------------- paths

def state_root(home: Path) -> Path:
    return periods.state_dir(home) / "studio" / "routines"


def runs_dir(home: Path, name: str) -> Path:
    return state_root(home) / name / "runs"


def agents_dir(home: Path) -> Path:
    return Path(home) / "Library" / "LaunchAgents"


def label(name: str) -> str:
    return LABEL_PREFIX + name


def plist_path(home: Path, name: str) -> Path:
    return agents_dir(home) / (label(name) + ".plist")


def log_path(home: Path, name: str) -> Path:
    return state_root(home) / name / "launchd.log"


# --------------------------------------------------------------------------- history

def _now(now: Optional[dt.datetime]) -> dt.datetime:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _lock_path(home: Path, name: str) -> Path:
    return state_root(home) / name / "run.lock"


def _try_lock(home: Path, name: str) -> Optional[int]:
    """The routine's run lock, held, or None when a run holds it."""
    path = _lock_path(home, name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _running_now(home: Path, name: str) -> bool:
    if not _lock_path(home, name).exists():
        return False
    fd = _try_lock(home, name)
    if fd is None:
        return True
    _unlock(fd)
    return False


def history(home: Path, name: str, limit: int = HISTORY_SHOWN) -> List[Dict[str, Any]]:
    """The routine's runs, newest first. An unreadable record is skipped; a `running` one whose
    lock nobody holds is shown as failed, interrupted."""
    definition(name)
    directory = runs_dir(home, name)
    records = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        entries = []
    for path in entries:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("started_at"), str):
            records.append(record)
    records.sort(key=lambda r: (r["started_at"], r.get("run_id", "")), reverse=True)
    if any(r.get("outcome") == "running" for r in records) and not _running_now(home, name):
        for record in records:
            if record.get("outcome") == "running":
                record["outcome"] = "failed"
                record["summary"] = "interrupted before it finished"
    return records[:limit]


def _prune(home: Path, name: str) -> None:
    entries = sorted(runs_dir(home, name).glob("*.json"))
    for path in entries[:-HISTORY_KEEP]:
        try:
            path.unlink()
        except OSError:
            pass


def run(home: Path, name: str, *, trigger: str = "citizen",
        now: Optional[Callable[[], dt.datetime]] = None) -> Dict[str, Any]:
    """Run one routine now and record it; returns the finished run record. Raises
    `RoutineError("routine_running")` when a run of it already holds its lock."""
    spec = definition(name)
    if trigger not in TRIGGERS:
        raise RoutineError("invalid_request")
    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
    fd = _try_lock(home, name)
    if fd is None:
        raise RoutineError("routine_running")
    try:
        last = next((r for r in history(home, name, HISTORY_KEEP) if r.get("outcome") == "ok"), None)
        since = periods.parse_ts(last.get("started_at")) if last else None
        started = _now(clock())
        run_id = str(uuid.uuid4())
        path = runs_dir(home, name) / ("%s-%s.json" % (started.strftime("%Y%m%dT%H%M%S%fZ"), run_id))
        record: Dict[str, Any] = {"run_id": run_id, "routine": name, "trigger": trigger,
                                  "started_at": _iso(started), "finished_at": None,
                                  "outcome": "running", "summary": "", "details": {}}
        _write(path, record)
        try:
            result = spec["run"](Path(home), since, started)
            record.update(outcome=result["outcome"], summary=result["summary"],
                          details=result.get("details", {}))
        except Exception as exc:  # a routine that cannot run is a failed run, recorded
            record.update(outcome="failed", summary="could not run: %s" % type(exc).__name__,
                          details={"error": str(exc)[:400]})
        record["finished_at"] = _iso(_now(clock()))
        _write(path, record)
        _prune(home, name)
        return record
    finally:
        _unlock(fd)


# --------------------------------------------------------------------------- status

def _public_run(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {"run_id": str(record.get("run_id", "")), "started_at": record["started_at"],
            "finished_at": record.get("finished_at"),
            "outcome": record.get("outcome") if record.get("outcome") in OUTCOMES else "failed",
            "summary": str(record.get("summary", ""))}


def _installed_at(home: Path, name: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromtimestamp(plist_path(home, name).stat().st_mtime, dt.timezone.utc)
    except OSError:
        return None


def status_of(home: Path, name: str, *, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """One routine's status in the shape `status` lists (the Phase C routine status contract)."""
    spec = definition(name)
    now = _now(now)
    interval = dt.timedelta(seconds=spec["interval_seconds"])
    runs = history(home, name, 1)
    last = _public_run(runs[0]) if runs else None
    installed_at = _installed_at(home, name)
    next_run = None
    overdue = False
    if installed_at is not None:
        anchor = periods.parse_ts(last["started_at"]) if last else None
        base = max(anchor, installed_at) if anchor else installed_at
        due = base + interval
        next_run = _iso(due)
        overdue = now > due + interval
    return {"name": name, "title": spec["title"], "installed": installed_at is not None,
            "schedule": {"interval_seconds": spec["interval_seconds"], "label": spec["label"]},
            "last_run": last, "outcome": last["outcome"] if last else "never",
            "next_run": next_run, "overdue": overdue}


def status(home: Path, *, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Every defined routine's status, sorted by name."""
    return [status_of(home, name, now=now) for name in names()]


def listing(home: Path, *, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """`GET /api/routines` and `citizen studio routine list --json`: each routine's status with
    its description, and each routine's recent runs, newest first."""
    rows = []
    for row in status(home, now=now):
        rows.append(dict(row, description=DEFINITIONS[row["name"]]["description"],
                         command=" ".join(CLI_COMMANDS["run"]).replace("{name}", row["name"])))
    return {"routines": rows,
            "history": {name: history(home, name) for name in names()}}


def attention_items(home: Path, *, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Home's items for routines: a failed last run, and an installed routine that is overdue."""
    items = []
    for row in status(home, now=now):
        last = row["last_run"]
        if row["outcome"] == "failed" and last is not None:
            items.append({"id": "routines:%s:failed" % row["name"], "source": "routines",
                          "kind": "routine-failed", "severity": "high",
                          "title": "%s failed" % row["title"], "detail": last["summary"],
                          "at": last["finished_at"] or last["started_at"],
                          "action": {"label": "Open Routines", "href": "/routines"}})
        if row["overdue"]:
            items.append({"id": "routines:%s:overdue" % row["name"], "source": "routines",
                          "kind": "routine-overdue", "severity": "medium",
                          "title": "%s is overdue" % row["title"],
                          "detail": "It was due at %s and has not run since." % row["next_run"],
                          "at": row["next_run"],
                          "action": {"label": "Open Routines", "href": "/routines"}})
    return items


# --------------------------------------------------------------------------- scheduler

def run_launchctl(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=30)


def domain() -> str:
    return "gui/%d" % os.getuid()


def program(name: str, *, python: Optional[str] = None, repo: Optional[Path] = None) -> List[str]:
    """What launchd runs: this checkout's CLI, isolated (`-I`), as the Studio runs it."""
    return [python or sys.executable, "-I", str(Path(repo or REPO) / "bin" / "harness"),
            "studio", "routine", "run", name, "--scheduled", "--json"]


def plist(home: Path, name: str, *, python: Optional[str] = None,
          repo: Optional[Path] = None) -> Dict[str, Any]:
    spec = definition(name)
    log = str(log_path(home, name))
    return {
        "Label": label(name),
        "ProgramArguments": program(name, python=python, repo=repo),
        "WorkingDirectory": str(Path(repo or REPO)),
        "EnvironmentVariables": {"HOME": str(home), "PATH": ":".join(SYSTEM_PATH)},
        "StartInterval": spec["interval_seconds"],
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def render(home: Path, name: str, **kwargs: Any) -> bytes:
    return plistlib.dumps(plist(home, name, **kwargs), sort_keys=True)


def install(home: Path, name: str, *, launchctl: Optional[Launchctl] = None,
            now: Optional[dt.datetime] = None, **kwargs: Any) -> Dict[str, Any]:
    """Write the routine's launchd agent and load it; reloads one already installed. A refused
    load removes the file again, so `installed` never reports an agent launchd does not hold."""
    definition(name)
    if launchctl is None:
        _own_home(home)
    launchctl = launchctl or run_launchctl
    path = plist_path(home, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path(home, name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = "%s/%s" % (domain(), label(name))
    try:
        launchctl(["bootout", target])
        path.write_bytes(render(home, name, **kwargs))
        loaded = launchctl(["bootstrap", domain(), str(path)])
    except (OSError, subprocess.SubprocessError) as exc:
        _unlink(path)
        raise RoutineError("launchd_refused", "launchctl could not run: %s" % type(exc).__name__)
    if loaded.returncode != 0:
        _unlink(path)
        raise RoutineError("launchd_refused", (loaded.stderr or "").strip()[:300]
                           or "launchctl bootstrap exited %d" % loaded.returncode)
    return status_of(home, name, now=now)


def _own_home(home: Path) -> None:
    """The real `launchctl` loads agents into this user's GUI session whatever directory the file
    is in, so it is used only for the account's own home: a test or a scratch `HARNESS_HOME` that
    reached it would leave a live agent behind pointing at a directory that goes away."""
    own = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    if Path(home).resolve() != own:
        raise RoutineError("launchd_refused", "launchd agents are installed only for this "
                                              "account's own home, %s" % own)


def preflight(home: Path, name: Any) -> None:
    """What `install` and `remove` refuse before anyone is asked to confirm: an unknown routine,
    or a home that is not this account's own."""
    definition(name)
    _own_home(home)


def remove(home: Path, name: str, *, launchctl: Optional[Launchctl] = None,
           now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Unload the routine's agent and delete its file; removing one not installed is a no-op."""
    definition(name)
    if launchctl is None:
        _own_home(home)
    launchctl = launchctl or run_launchctl
    path = plist_path(home, name)
    try:
        launchctl(["bootout", "%s/%s" % (domain(), label(name))])
    except (OSError, subprocess.SubprocessError):
        pass  # nothing loaded, or no launchctl: the file is still removed
    _unlink(path)
    return status_of(home, name, now=now)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def presence_action(action: str, name: str) -> str:
    """The words the presence dialog uses for an install or a removal."""
    spec = definition(name)
    if action == "install":
        return "install a launchd agent that runs the %s routine %s" % (
            spec["title"].lower(), spec["label"].lower())
    return "remove the launchd agent that runs the %s routine" % spec["title"].lower()
