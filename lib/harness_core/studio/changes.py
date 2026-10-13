# SPDX-License-Identifier: MIT
"""The Changes inbox: every managed draft with where it stands, and the apply journal's history.

A draft's status reads only what already exists: the apply journal (`apply.journal_path`) and the
draft-test records (`draft_tests.read_records`). It is `applied` while the draft's current revision
is the one its latest completed apply wrote and no rollback still in effect reverses it, `rolled
back` when one does, `tested` when a test record names its current revision, and `open` otherwise.
Applying, testing and rolling back stay on their own governed paths; this module never writes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import draft_tests, drafts
from . import rollback as draft_rollback

SCHEMA_VERSION = 1
STATUSES = ("open", "tested", "applied", "rolled back")
CLI_COMMANDS = {
    "list": ("citizen", "draft", "list", "--json"),
    "create": ("citizen", "draft", "create", "{draft}", "--source", "{source}", "--json"),
    "history": ("citizen", "draft", "history", "--json"),
}


def _summaries(home: Path) -> List[Dict[str, Any]]:
    """Each journalled apply and rollback in journal order, with the draft id it acted on."""
    rows = []
    for entry in draft_rollback.history(home):
        summary = draft_rollback._summary(entry)
        summary["draft_id"] = str(entry["intent"].get("draft_id") or "")
        rows.append(summary)
    return rows


def _in_effect_reversal(entries: List[Mapping[str, Any]], apply_id: str) -> Optional[Mapping[str, Any]]:
    """The completed rollback of `apply_id` that is not itself rolled back, if any."""
    for entry in reversed(entries):
        if entry["kind"] == "rollback" and entry["reverses"] == apply_id and entry["status"] == "completed":
            if _in_effect_reversal(entries, entry["apply_id"]) is None:
                return entry
    return None


def _status(draft: Mapping[str, Any], entries: List[Mapping[str, Any]],
            records: List[Mapping[str, Any]]) -> Dict[str, Any]:
    applies = [entry for entry in entries if entry["kind"] == "apply" and entry["status"] == "completed"
               and entry["draft_id"] == draft["draft_id"]]
    latest = applies[-1] if applies else None
    reversal = _in_effect_reversal(entries, latest["apply_id"]) if latest else None
    tests = [record for record in records if record.get("draft_id") == draft["draft_id"]]
    tested = any(record.get("revision") == draft["revision"] for record in tests)
    if latest is not None and latest["revision"] == draft["revision"]:
        status = "rolled back" if reversal is not None else "applied"
    else:
        status = "tested" if tested else "open"
    return {"status": status, "tests": len(tests), "applied": dict(latest) if latest else None,
            "rolled_back": dict(reversal) if reversal else None}


def inbox(repo: Path, home: Path, tests_root: Path) -> Dict[str, Any]:
    """Every managed draft (`drafts.list_drafts`) with its status and its latest apply."""
    entries = _summaries(home)
    try:
        records, _unreadable = draft_tests.read_records(tests_root)
    except draft_tests.DraftTestError:
        # Records that are not a private directory are never read; no draft reads as tested.
        records = []
    listed = []
    for draft in drafts.list_drafts(repo):
        item = dict(draft)
        item.update(_status(draft, entries, records))
        listed.append(item)
    return {"schema_version": SCHEMA_VERSION, "drafts": listed}


def history(home: Path) -> Dict[str, Any]:
    """Every apply and rollback the journal holds, newest first."""
    return {"schema_version": SCHEMA_VERSION, "entries": list(reversed(_summaries(home)))}
