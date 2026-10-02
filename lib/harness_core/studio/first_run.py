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
STATUS_LOCK_TIMEOUT = 5.0
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


def _matches(row: Mapping[str, Any], name: str, draft: Optional[Mapping[str, Any]]) -> bool:
    """A journal row of this run: of this very draft while it exists, else of the name."""
    if draft is not None:
        return row.get("draft_id") == draft.get("draft_id")
    return row.get("draft") == name


def derive(name: str, draft: Optional[Mapping[str, Any]], rows: Iterable[Mapping[str, Any]],
           unfinished: Iterable[Mapping[str, Any]], other_drafts: Iterable[str],
           configured: bool = False) -> Dict[str, Any]:
    """The first run's state from its draft, the apply journal, the drafts beside it, and whether
    the home was already set up from the CLI."""
    rows = list(rows)
    unfinished = list(unfinished)
    completed = [row for row in rows if row.get("phase") == "completed"]
    mine = [row for row in completed if _matches(row, name, draft)]
    interrupted = [row for row in unfinished if _matches(row, name, draft)]
    blocking = [row for row in unfinished if not _matches(row, name, draft)]
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
        # Fresh: nothing was ever applied, no sync has projected the harness into this home, and
        # no other draft exists. Only then does the Studio open on the guide by itself.
        "fresh": not completed and not configured and not any(item != name for item in other_drafts),
        # Whether this run has changed anything live; a configured home may differ from defaults.
        "nothing_live_changed": state in ("not-started", "in-progress"),
        "applied": applied,
        "interrupted": draft_apply._interrupted_offer(interrupted[-1]) if interrupted else {},
        "blocked_by": draft_apply._interrupted_offer(blocking[-1]) if blocking else {},
    }


def _chose(config: Mapping[str, Any]) -> bool:
    """The live configuration holds a choice the user made rather than what init wrote: a stance
    init did not record as its default, a mode, or a configuration older than that record."""
    stances = config.get("stances")
    if not isinstance(stances, dict):
        return False
    marked = config.get("init_defaults")
    marks = marked.get("stances") if isinstance(marked, dict) else None
    if not isinstance(marks, dict):
        return bool(stances)
    if config.get("mode"):
        return True
    return any(name not in marks or marks[name] != value for name, value in stances.items())


def configured(home: Path) -> bool:
    """This home was set up from the CLI: a sync projected the harness into it (the manifest
    doctor reports as installed) and its live configuration holds the user's own choices."""
    if not (draft_apply.journal_path(home).parent / "manifest.json").is_file():
        return False
    path = draft_apply.config_file(home)
    if not path.is_file():
        return False
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return True  # unreadable is never treated as fresh
    return not isinstance(config, dict) or _chose(config)


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
        try:
            worktree, _state = drafts.find(repo, name)
            draft: Optional[Dict[str, Any]] = drafts.describe(repo, worktree)
        except drafts.DraftError as exc:
            if exc.code != "not-found":
                raise
            draft = None
        others = [item["name"] for item in drafts.list_drafts(repo)]
        derived = derive(name, draft, _journal_rows(home), draft_apply.unfinished_applies(home),
                         others, configured(home))
        choices: List[Dict[str, Any]] = []
        if derived["applied"]:
            choices = derived["applied"].pop("choices")
        elif draft is not None:
            # Bounded: a save's checks hold the writer lock for minutes; report busy instead.
            choices = drafts.read_snapshot(repo, name, _choices, lock_timeout=STATUS_LOCK_TIMEOUT)
    except drafts.DraftError as exc:
        raise FirstRunError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise FirstRunError("unreadable", "first-run state could not be read: %s" % exc) from exc
    public_draft = {} if draft is None else {
        key: draft[key] for key in ("name", "draft_id", "revision", "base_revision", "behind_installed", "created_at")}
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


def clear_partial(repo: Path, name: str) -> bool:
    """Remove what an interrupted `draft create` left: a branch and worktree with no draft state.

    A create killed partway (a timeout) can leave `draft/NAME` checked out with no state file, so
    `find` reports not-found and every later create fails on the existing branch. Only a leftover
    with no draft state and no commit beyond the installed revision is removed; anything else is
    left for the user and reported by the next create.
    """
    repo = Path(repo).resolve()
    branch = "draft/" + name
    shown = drafts._git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, check=False)
    listed = drafts._git(repo, "worktree", "list", "--porcelain", check=False).stdout
    worktree = None
    for block in listed.strip().split("\n\n"):
        lines = block.splitlines()
        if "branch refs/heads/" + branch in lines:
            worktree = Path(lines[0][len("worktree "):])
    if shown.returncode != 0 and worktree is None:
        return False
    if worktree is not None and worktree.is_dir():
        try:
            if drafts._paths(worktree)["state"].is_file():
                return False
        except drafts.DraftError:
            pass
    if shown.returncode == 0:
        ahead = drafts._git(repo, "rev-list", "--count", "HEAD.." + branch, check=False)
        if ahead.returncode != 0 or ahead.stdout.strip() != "0":
            return False
    if worktree is not None:
        drafts._git(repo, "worktree", "remove", "--force", str(worktree), check=False)
    drafts._git(repo, "worktree", "prune", check=False)
    if shown.returncode == 0:
        drafts._git(repo, "branch", "-D", branch, check=False)
    return True
