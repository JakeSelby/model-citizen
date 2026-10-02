"""The guided first run: one draft taken from a fresh install to an applied, doctor-checked harness.

The first run keeps no record of its own. Its progress is read from the draft it creates and from
the apply journal, so abandoning it at any step changes nothing live (only governed apply writes
live configuration) and resuming needs nothing beyond the draft itself. Every step is an existing
draft operation, and the status names the `citizen` command that does each one headless.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import apply as draft_apply
from . import drafts

SCHEMA_VERSION = 1
DRAFT = "first-run"
CLI_COMMANDS = {
    "status": ("citizen", "draft", "first-run", "{draft}", "--json"),
    "start": ("citizen", "draft", "create", "{draft}", "--json"),
}
STATES = ("not-started", "in-progress", "interrupted", "complete")
# The order the guide walks; each step's command is the headless equivalent of what it does.
STEPS = (
    ("health", "Check the install", "citizen doctor"),
    ("draft", "Start a draft", "citizen draft create {draft} --json"),
    ("identity", "Say who you are", "citizen draft settings save {draft} --base-revision REVISION "
                                    "--idempotency-key KEY --changes changes.json --json"),
    ("preferences", "Pick a stance for each dimension",
     "citizen draft selection save {draft} --base-revision REVISION --idempotency-key KEY "
     "--changes changes.json --json"),
    ("check", "Run a free check", "citizen lint"),
    ("apply", "Review and apply", "citizen draft review {draft} --json"),
    ("done", "Confirm the doctor checks", "citizen doctor"),
)


class FirstRunError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def valid_name(name: Any) -> bool:
    return isinstance(name, str) and bool(drafts.NAME.fullmatch(name))


def _journal_rows(home: Path) -> List[Dict[str, Any]]:
    path = draft_apply.journal_path(home)
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("apply_id"), str):
            rows.append(row)
    return rows


def _choices(worktree: Path, _state: Mapping[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every configuration key the draft changes from its base, as the command that sets it."""
    base = drafts._config_value(drafts._paths(worktree)["base_config"])
    before, after = draft_apply._flatten(base), draft_apply._flatten(config)
    rows: List[Dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        if key in before and key in after and draft_apply._canonical(before[key]) == \
                draft_apply._canonical(after[key]):
            continue
        command = ({"action": "unset", "key": key, "value": None} if key not in after else
                   {"action": "set", "key": key, "value": draft_apply._value_text(after[key])})
        rows.append(command)
    rows.sort(key=draft_apply._priority)
    return [{"key": row["key"], "action": row["action"], "value": row["value"],
             "command": draft_apply._citizen(row)} for row in rows]


def _applied_choices(intent: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The configuration commands a completed apply recorded, in the order it ran them."""
    rows: List[Dict[str, Any]] = []
    for item in intent.get("config") or []:
        if not isinstance(item, dict) or item.get("action") not in ("set", "unset"):
            continue
        command = {"action": item["action"], "key": str(item.get("key", "")),
                   "value": None if item["action"] == "unset" else str(item.get("value", ""))}
        rows.append(dict(command, command=draft_apply._citizen(command)))
    return rows


def derive(name: str, draft: Optional[Mapping[str, Any]], rows: Iterable[Mapping[str, Any]],
           unfinished: Iterable[Mapping[str, Any]], other_drafts: Iterable[str]) -> Dict[str, Any]:
    """The first run's state from its draft, the apply journal, and the drafts beside it."""
    rows = list(rows)
    completed = [row for row in rows if row.get("phase") == "completed"]
    mine = [row for row in completed if row.get("draft") == name]
    interrupted = [row for row in unfinished if row.get("draft") == name]
    if interrupted:
        state = "interrupted"
    elif mine:
        state = "complete"
    elif draft is not None:
        state = "in-progress"
    else:
        state = "not-started"
    applied: Dict[str, Any] = {}
    if mine:
        last = mine[-1]
        intent = next((row for row in rows if row.get("phase") == "intent"
                       and row.get("apply_id") == last.get("apply_id")), {})
        applied = {"apply_id": str(last.get("apply_id", "")), "ts": str(last.get("ts", "")),
                   "revision": str(last.get("revision", "")),
                   "doctor": str(last.get("doctor", "unavailable")),
                   "choices": _applied_choices(intent)}
    return {
        "state": state,
        # A fresh install has never applied a draft and holds no draft but this one, so the
        # Studio opens on the guide; anyone past that reaches it from the Hub instead.
        "fresh": not completed and not any(item != name for item in other_drafts),
        "nothing_live_changed": state in ("not-started", "in-progress"),
        "applied": applied,
        "interrupted": draft_apply._interrupted_offer(interrupted[-1]) if interrupted else {},
    }


def _command(template: str, name: str) -> str:
    return template.replace("{draft}", shlex.quote(name))


def status(repo: Path, name: str = DRAFT, home: Optional[Path] = None) -> Dict[str, Any]:
    """Where the first run stands and the headless commands that reproduce it. Writes nothing."""
    if not valid_name(name):
        raise FirstRunError("invalid-name", "draft name must use only letters, numbers, dots, "
                                            "underscores, and hyphens")
    repo = Path(repo).resolve()
    home = draft_apply.home_dir() if home is None else Path(home)
    try:
        worktree, _state = drafts.find(repo, name)
        draft: Optional[Dict[str, Any]] = drafts.describe(repo, worktree)
    except drafts.DraftError as exc:
        if exc.code != "not-found":
            raise FirstRunError(exc.code, str(exc)) from exc
        draft = None
    others = [item["name"] for item in drafts.list_drafts(repo)]
    derived = derive(name, draft, _journal_rows(home), draft_apply.unfinished_applies(home), others)
    choices: List[Dict[str, Any]] = []
    if derived["applied"]:
        choices = derived["applied"].pop("choices")
    elif draft is not None:
        try:
            choices = drafts.read_snapshot(repo, name, _choices)
        except drafts.DraftError as exc:
            raise FirstRunError(exc.code, str(exc)) from exc
    public_draft = {} if draft is None else {
        key: draft[key] for key in ("name", "revision", "base_revision", "behind_installed", "created_at")}
    headless = [item["command"] for item in choices] + ["citizen sync", "citizen doctor"]
    agent = [_command(STEPS[1][2], name)] + [
        _command(template, name) for step, _label, template in STEPS[2:4]] + [
        _command("citizen draft review {draft} --json", name),
        _command(" ".join(draft_apply.CLI_COMMANDS["apply"]).replace("{revision}", "REVISION"), name),
    ]
    return dict(
        derived,
        schema_version=SCHEMA_VERSION,
        draft_name=name,
        draft=public_draft,
        steps=[{"id": step, "label": label, "command": _command(template, name)}
               for step, label, template in STEPS],
        choices=choices,
        commands={"headless": headless, "agent": agent,
                  "status": "citizen draft first-run --json" if name == DRAFT
                  else _command(" ".join(CLI_COMMANDS["status"]), name)},
    )
