# SPDX-License-Identifier: MIT
"""Roll back an applied draft from its record in the apply journal.

Every apply journals, before its first write, the configuration bytes it replaced and the prior
bytes and mode of each file it wrote (`apply.journal_path`). Rollback puts exactly those keys and
files back, then runs `citizen sync` and the doctor, under the same sync and configuration locks
apply takes. When nothing else changed since, the configuration and the personal root return byte
for byte; when other keys changed, only the keys the apply wrote are restored.

Rollback restores only what still holds the value the apply wrote. A later apply or rollback still
in effect that touched one of the same keys or files refuses it, named; so does a later hand edit,
named by key or file. It never overwrites either.

A rollback journals itself in the apply's own shape (an intent row, then a terminal row), with
`kind: rollback` and the apply it `reverses`, so an interrupted rollback is restored by `citizen
draft recover` and a completed one can itself be rolled back.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shlex
import stat
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import apply as draft_apply

SCHEMA_VERSION = 1
CLI_COMMANDS = {
    "preview": ("citizen", "draft", "rollback", "{apply_id}", "--preview", "--json"),
    "rollback": ("citizen", "draft", "rollback", "{apply_id}", "--draft", "{draft}", "--json"),
}
APPLY_ID = re.compile(r"[0-9a-f]{32}\Z")


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- the journal


def _rows(home: Path) -> List[Dict[str, Any]]:
    path = draft_apply.journal_path(home)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("apply_id"), str):
            rows.append(row)
    return rows


def history(home: Path) -> List[Dict[str, Any]]:
    """Every journalled apply and rollback in order, each with its outcome.

    `status` is `completed`, `failed`, `recovered`, `abandoned` or `open` (an intent with no
    terminal row). A failed row that did not finish its restore leaves the intent open.
    """
    entries: Dict[str, Dict[str, Any]] = {}
    for row in _rows(home):
        identity = row["apply_id"]
        phase = row.get("phase")
        if phase == "intent":
            entries[identity] = {"apply_id": identity, "intent": row, "status": "open",
                                 "kind": row.get("kind") or "apply",
                                 "reverses": row.get("reverses") or ""}
        elif identity in entries and entries[identity]["status"] == "open":
            if phase in draft_apply.TERMINAL_PHASES:
                entries[identity]["status"] = phase
            elif phase == "failed" and row.get("restored") is True:
                entries[identity]["status"] = "failed"
    return list(entries.values())


def _touched(entry: Mapping[str, Any], target: bool = False) -> List[str]:
    """What an entry wrote: its keys and files, and the personal root it registered or wrote in.

    The root counts for the rolled-back entry only when it registered the root, and for a later
    entry when it wrote a file there too: un-registering a root would orphan that later file.
    """
    intent = entry["intent"]
    keys = [str(item.get("key")) for item in intent.get("config", []) if isinstance(item, dict)]
    files = [str(item.get("path")) for item in intent.get("files", []) if isinstance(item, dict)]
    destination = str(intent.get("destination") or "")
    touched = ["configuration key " + key for key in keys]
    touched += [str(Path(destination) / path) for path in files]
    if destination and ("primitive_roots" in keys or (files and not target)):
        touched.append("personal root " + destination)
    return touched


def _in_effect(entries: List[Dict[str, Any]]) -> List[str]:
    """Applies and rollbacks whose writes are still in effect, oldest first.

    A completed rollback cancels the entry it reversed when that entry is in effect, and is then in
    effect itself only when it re-applied something (a rollback of a rollback). An abandoned apply
    kept its writes, so it counts too.
    """
    effective: List[str] = []
    for entry in entries:
        if entry["status"] == "completed" and entry["kind"] == "rollback" \
                and entry["reverses"] in effective:
            effective.remove(entry["reverses"])
        elif entry["status"] in ("completed", "abandoned"):
            effective.append(entry["apply_id"])
    return effective


def _label(entry: Mapping[str, Any]) -> str:
    intent = entry["intent"]
    noun = "rollback" if entry["kind"] == "rollback" else "apply"
    return "the %s %s of draft %s at %s" % (noun, entry["apply_id"][:12], intent.get("draft", "?"),
                                            intent.get("ts", "?"))


def _summary(entry: Mapping[str, Any]) -> Dict[str, Any]:
    intent = entry["intent"]
    return {"apply_id": entry["apply_id"], "kind": entry["kind"], "status": entry["status"],
            "draft": str(intent.get("draft") or ""), "revision": str(intent.get("revision") or ""),
            "ts": str(intent.get("ts") or ""), "actor": str(intent.get("actor") or ""),
            "reverses": entry["reverses"]}


# --------------------------------------------------------------------------- the plan


def _restorable(home: Path, apply_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The target and its restore, or `_Refused` naming every later change in the way."""
    if not isinstance(apply_id, str) or not APPLY_ID.match(apply_id):
        raise _Refused("invalid-request", "an apply id is the 32 hex characters `citizen draft "
                                          "apply` reported")
    entries = history(home)
    by_id = {entry["apply_id"]: entry for entry in entries}
    target = by_id.get(apply_id)
    if target is None:
        raise _Refused("unknown-apply", "no apply %s is recorded in the apply journal" % apply_id)
    unfinished = [entry for entry in entries if entry["status"] == "open"]
    if unfinished:
        raise _Refused("interrupted-apply", (
            "an earlier apply of draft %s was interrupted; restore it (`citizen draft recover`) or "
            "abandon it (`citizen draft recover --abandon`) first"
            % unfinished[-1]["intent"].get("draft", "?")))
    if target["status"] != "completed":
        reason = {"abandoned": "its interrupted writes were abandoned and kept as they are",
                  "failed": "it failed and was restored, so nothing of it is live",
                  "recovered": "it was interrupted and already restored"}[target["status"]]
        raise _Refused("not-applied", "%s cannot be rolled back: %s" % (_label(target), reason))
    position = entries.index(target)
    reversed_by = [entry for entry in entries[position + 1:] if entry["status"] == "completed"
                   and entry["kind"] == "rollback" and entry["reverses"] == apply_id]
    if reversed_by:
        raise _Refused("already-rolled-back", "%s was already reversed by %s; roll that back "
                       "instead" % (_label(target), _label(reversed_by[-1])))
    touched = set(_touched(target, target=True))
    effective = set(_in_effect(entries))
    later = [entry for entry in entries[position + 1:]
             if entry["apply_id"] in effective and touched & set(_touched(entry))]
    if later:
        raise _Refused("later-apply", (
            "%s changed %s again since %s; roll %s back first. Nothing was changed" % (
                "; ".join(_label(entry) for entry in later),
                ", ".join(sorted(touched & set().union(*(_touched(entry) for entry in later)))),
                _label(target), "them" if len(later) > 1 else "it")))
    return target, _restore_plan(home, target)


def _restore_plan(home: Path, target: Mapping[str, Any]) -> Dict[str, Any]:
    """Each key and file the target wrote, its current value and the value it returns to."""
    intent = target["intent"]
    live_path = draft_apply.config_file(home)
    if live_path.is_symlink():
        raise _Refused("invalid-config", "the live configuration is a symlink; rollback refuses it")
    dest = Path(str(intent.get("destination") or draft_apply.destination(home)))
    try:
        prior_config = draft_apply._decoded(intent.get("prior_config"))
        prior_document = draft_apply._read_json_bytes(prior_config)
        current_bytes = live_path.read_bytes() if live_path.is_file() else None
        current_document = draft_apply._read_json_bytes(current_bytes)
    except (draft_apply.ApplyError, ValueError, TypeError) as exc:
        raise _Refused("journal-unreadable", "the apply's journal row could not be read back (%s)"
                       % exc) from exc
    prior = draft_apply._flatten(prior_document)
    current = draft_apply._flatten(current_document)
    conflicts: List[str] = []
    keys: List[Dict[str, Any]] = []
    for item in intent.get("config", []):
        key = str(item.get("key"))
        applied_present = item.get("action") != "unset"
        now_present = key in current
        restored = {"key": key, "current": current.get(key), "current_present": now_present,
                    "restored": prior.get(key), "restored_present": key in prior}
        if now_present == (key in prior) and (
                not now_present or draft_apply._canonical(current[key]) == draft_apply._canonical(prior[key])):
            restored["action"] = "none"  # already back at its prior value
        elif now_present == applied_present and (
                not now_present
                or draft_apply._canonical(current[key]) == draft_apply._canonical(item.get("applied"))):
            restored["action"] = "set" if key in prior else "unset"
        else:
            conflicts.append("configuration key %s (now %s)" % (
                key, draft_apply._value_text(current[key]) if now_present else "unset"))
            restored["action"] = "conflict"
        keys.append(restored)
    files: List[Dict[str, Any]] = []
    for item in intent.get("files", []):
        try:
            target_path = draft_apply._contained(dest, str(item["path"]))
            live, mode = draft_apply._live_file(target_path, str(item["path"]))
            prior_bytes = draft_apply._decoded(item.get("prior"))
        except (draft_apply.ApplyError, KeyError, ValueError, TypeError) as exc:
            conflicts.append("%s (%s)" % (item.get("path"), exc))
            continue
        row = {"path": str(item["path"]), "current": live, "current_mode": mode,
               "prior": prior_bytes, "prior_mode": item.get("prior_mode") or 0o644,
               "restored_sha256": draft_apply._digest(prior_bytes)}
        if draft_apply._digest(live) == draft_apply._digest(prior_bytes):
            row["action"] = "none"
        elif draft_apply._digest(live) == item.get("applied_sha256"):
            row["action"] = "delete" if prior_bytes is None else "write"
        else:
            conflicts.append(str(target_path))
            row["action"] = "conflict"
        files.append(row)
    if conflicts:
        raise _Refused("rollback-conflict", (
            "%s no longer hold what %s wrote: %s. Rollback never overwrites a later edit; put "
            "them back yourself, or leave the apply in place. Nothing was changed" % (
                "these" if len(conflicts) > 1 else "this", _label(target), "; ".join(conflicts))))
    written = {row["key"] for row in keys if row["action"] in ("set", "unset")}
    untouched = {key: value for key, value in current.items() if key not in written}
    if prior_config is not None and untouched == {
            key: value for key, value in prior.items() if key not in written}:
        restored_bytes: Optional[bytes] = prior_config  # nothing else moved: byte for byte
    elif prior_config is None and not untouched:
        restored_bytes = None
    else:
        document = copy.deepcopy(current_document)
        for key in written:
            draft_apply._set_path(document, key, key in prior, prior.get(key))
        if any(key.startswith("stances.") for key in written):
            draft_apply._set_path(document, "init_defaults", "init_defaults" in prior_document,
                                  prior_document.get("init_defaults"))
        restored_bytes = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    created = list(intent.get("created") or [])
    for row in files:
        parent = (dest / row["path"]).parent
        while parent != dest and str(parent) not in created and dest in parent.parents:
            created.append(str(parent))
            parent = parent.parent
    return {"dest": dest, "live_path": live_path, "current_bytes": current_bytes,
            "current_mode": stat.S_IMODE(os.stat(str(live_path)).st_mode) if current_bytes is not None
            else 0o600,
            "restored_bytes": restored_bytes,
            "restored_mode": int(intent.get("prior_mode") or 0o600),
            "keys": keys, "files": files, "created": created,
            "changes": bool(written) or any(row["action"] != "none" for row in files)}


def _public(target: Optional[Mapping[str, Any]], plan: Optional[Mapping[str, Any]],
            refusals: List[Dict[str, str]], apply_id: str) -> Dict[str, Any]:
    intent = target["intent"] if target else {}
    draft = str(intent.get("draft") or "")
    return {
        "schema_version": SCHEMA_VERSION,
        "apply": _summary(target) if target else {},
        "destination": str(plan["dest"]) if plan else "",
        "config": [{key: row[key] for key in ("key", "action", "current", "current_present",
                                              "restored", "restored_present")}
                   for row in (plan["keys"] if plan else [])],
        "files": [{"path": row["path"], "action": row["action"]}
                  for row in (plan["files"] if plan else [])],
        "commands": [{"step": "rollback", "command": command(apply_id, draft)},
                     {"step": "sync", "command": "citizen sync"},
                     {"step": "check", "command": "citizen doctor"}] if plan else [],
        "refusals": refusals,
        "can_rollback": not refusals,
        "rollback_command": command(apply_id, draft) if target else "",
        "nothing_changed": True,
    }


def command(apply_id: str, draft: str) -> str:
    return " ".join(CLI_COMMANDS["rollback"]).format(apply_id=shlex.quote(apply_id),
                                                     draft=shlex.quote(draft or "?"))


def _assess(home: Path, apply_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]],
                                                  List[Dict[str, str]]]:
    target = None
    try:
        target, plan = _restorable(home, apply_id)
    except _Refused as exc:
        if target is None:
            target = {entry["apply_id"]: entry for entry in history(home)}.get(apply_id) \
                if isinstance(apply_id, str) else None
        return target, None, [{"code": exc.code, "message": str(exc)}]
    refusals = [] if plan["changes"] else [{
        "code": "nothing-to-roll-back",
        "message": "every key and file %s wrote already holds its prior value" % _label(target)}]
    return target, plan, refusals


def preview(apply_id: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """The reverse diff a rollback would restore, or why it is refused. Writes nothing."""
    home = draft_apply.home_dir() if home is None else Path(home)
    running = draft_apply.running_apply(home)
    target, plan, refusals = _assess(home, apply_id)
    if running:
        refusals = [{"code": "apply-running", "message": running + " is running now; preview "
                                                                    "again when it finishes"}] + refusals
    return _public(target, plan, refusals, apply_id)


# --------------------------------------------------------------------------- the rollback


def rollback(apply_id: str, operations: draft_apply.Operations, draft: str = "",
             actor: str = "citizen", home: Optional[Path] = None,
             log: Optional[List[str]] = None) -> Dict[str, Any]:
    """Restore what one completed apply (or rollback) wrote, under the sync and config locks.

    `draft`, when given, must name the target's draft, as the Studio's confirmation does. The
    result has the apply result's shape; `apply_id` is this rollback's own journal id, and `review`
    is the preview it ran.
    """
    home = draft_apply.home_dir() if home is None else Path(home)
    log = [] if log is None else log
    holder = "citizen draft rollback %s%s" % (apply_id[:12] if isinstance(apply_id, str) else "?",
                                              " from Studio" if actor == "studio" else "")
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(operations.lock(holder))
            stack.enter_context(operations.config_lock(holder))
        except ValueError as exc:
            text = str(exc)
            return draft_apply._result("refused", "busy", text + "; nothing was changed",
                                       holder=text.partition(": ")[2])
        target, plan, refusals = _assess(home, apply_id)
        public = _public(target, plan, refusals, apply_id)
        if refusals:
            first = refusals[0]
            return _outcome("refused", first["code"], first["message"], public)
        assert target is not None and plan is not None
        if draft and draft != target["intent"].get("draft"):
            return _outcome("refused", "confirmation-mismatch", (
                "%s is of draft %s, not %s; nothing was changed"
                % (_label(target), target["intent"].get("draft"), draft)), public)
        return _execute(home, target, plan, public, operations, actor, log)


def _outcome(status: str, code: str, message: str, public: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
    result = draft_apply._result(status, code, message, **extra)
    result["applied"] = False
    result["review"] = dict(public, nothing_changed=status != "rolled-back")
    return result


def _execute(home: Path, target: Mapping[str, Any], plan: Dict[str, Any], public: Dict[str, Any],
             operations: draft_apply.Operations, actor: str, log: List[str]) -> Dict[str, Any]:
    intent = target["intent"]
    rollback_id = uuid.uuid4().hex
    dest: Path = plan["dest"]
    live_path: Path = plan["live_path"]
    restoring = [row for row in plan["keys"] if row["action"] in ("set", "unset")]
    files = [row for row in plan["files"] if row["action"] in ("write", "delete")]
    identity = {"apply_id": rollback_id, "kind": "rollback", "reverses": target["apply_id"],
                "actor": actor, "draft": intent.get("draft"), "draft_id": intent.get("draft_id"),
                "revision": intent.get("revision"), "base_revision": intent.get("base_revision"),
                "destination": str(dest)}
    try:
        baseline = operations.sync(True)
    except (Exception, SystemExit):
        baseline = {"code": 1, "attention": [], "refused": True}
    attention = sorted(baseline.get("attention") or [])
    try:
        # Recorded in the apply's own shape: `citizen draft recover` restores an interrupted
        # rollback, and a completed one can be rolled back in turn.
        draft_apply._journal(home, dict(
            identity, ts=draft_apply._now(), phase="intent",
            prior_config=draft_apply._encoded(plan["current_bytes"]), prior_mode=plan["current_mode"],
            attention=attention,
            config=[{"key": row["key"], "action": row["action"], "value": None,
                     "applied": row["restored"]} for row in restoring],
            files=[{"path": row["path"], "action": row["action"],
                    "prior": draft_apply._encoded(row["current"]), "prior_mode": row["current_mode"],
                    "applied_sha256": row["restored_sha256"]} for row in files],
            created=[str(dest)] if not os.path.lexists(str(dest)) else []))
    except OSError as exc:
        return _outcome("refused", "journal-unavailable",
                        "the apply journal could not be written (%s); nothing was changed" % exc, public)
    names = [row["path"] for row in files]
    undo = [{"path": row["path"], "prior": row["current"], "prior_mode": row["current_mode"]}
            for row in files]
    synced = False
    try:
        if plan["restored_bytes"] is None:
            if live_path.exists():
                live_path.unlink()
        elif plan["restored_bytes"] != plan["current_bytes"]:
            draft_apply.drafts._atomic_bytes(live_path, plan["restored_bytes"], plan["restored_mode"])
        draft_apply._restore_root(dest, [{"path": row["path"], "prior": row["prior"],
                                          "prior_mode": row["prior_mode"]} for row in files],
                                  plan["created"])
        synced = True
        settled, new = draft_apply._sync_settled(operations.sync(False), attention)
        if not settled:
            raise draft_apply.ApplyError("sync-refused", "citizen sync did not complete the restored "
                                         "configuration" + (": " + "; ".join(new) if new else ""))
    except BaseException as exc:  # noqa: B036 - an interrupt must restore too, then propagate
        restored = draft_apply._restore(live_path, plan["current_bytes"], plan["current_mode"], dest,
                                        undo, [], operations, synced, attention)
        message = str(exc) or exc.__class__.__name__
        note = draft_apply._journal_outcome(home, dict(identity, ts=draft_apply._now(), phase="failed",
                                                       reason=message, restored=restored))
        _record(operations, identity, names, "failed", "Rollback failed and was undone: " + message)
        if not isinstance(exc, (Exception, SystemExit)):
            raise
        return _outcome("failed", getattr(exc, "code", "rollback-failed"), message + (
            "; the configuration and files were put back as they were before the rollback"
            if restored else "; undoing the rollback did not complete and stays open, so run "
                             "`citizen draft recover`") + note,
            public, apply_id=rollback_id, restored=restored, log=log[-draft_apply.MAX_LOG_LINES:])
    try:
        doctor = draft_apply._doctor(operations.doctor())
    except (Exception, SystemExit):
        doctor = {"status": "unavailable", "checks": []}
    note = draft_apply._journal_outcome(home, dict(identity, ts=draft_apply._now(), phase="completed",
                                                   doctor=doctor["status"]))
    _record(operations, identity, names, "completed",
            "Rolled back %s; its configuration keys and files hold their prior values again."
            % _label(target))
    return _outcome("rolled-back", "", (
        "Rolled back %s; sync and the doctor checks ran. Roll this rollback back with `%s`"
        % (_label(target), command(rollback_id, str(intent.get("draft") or "")))) + note,
        public, apply_id=rollback_id, doctor=doctor, restored=True,
        log=log[-draft_apply.MAX_LOG_LINES:])


def _record(operations: draft_apply.Operations, identity: Mapping[str, Any], files: List[str],
            outcome: str, reason: str) -> None:
    """A `studio.rollback` event naming the apply it reverses, which Activity links to."""
    try:
        operations.record({
            "kind": "event", "event": "studio.rollback", "id": identity["apply_id"],
            "ts": draft_apply._now(),
            "detail": {"draft": identity["draft"], "files": files, "outcome": outcome,
                       "reason": reason, "reverses": identity["reverses"],
                       "command": command(str(identity["reverses"]), str(identity["draft"] or "")),
                       "actor": "Studio" if identity["actor"] == "studio" else "citizen",
                       "revision": identity["revision"]},
        })
    except (OSError, ValueError):
        pass
