# SPDX-License-Identifier: MIT
"""Home item: a draft waiting. Every managed draft the Changes inbox lists as tested (waiting to be
applied) or open (waiting for a test), linked to its card in Changes."""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .. import changes, periods
from ..attention import register_attention

NAME = "drafts"
ROOT = Path(__file__).resolve().parents[4]
WAITING = {
    "tested": ("medium", "Draft %s is tested and waiting to be applied",
               "Its test record names its current revision; review the result and apply it or discard it."),
    "open": ("low", "Draft %s is waiting for a test",
             "Nothing in it is live; test it, apply it, or discard it."),
}


def href(name: str) -> str:
    return "/changes?" + urllib.parse.urlencode({"draft": name})


def items(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for draft in document.get("drafts") or ():
        waiting = WAITING.get(draft.get("status"))
        name = draft.get("name")
        if waiting is None or not isinstance(name, str) or not name:
            continue
        severity, title, detail = waiting
        out.append({"id": "%s:%s" % (NAME, name), "source": NAME, "kind": "draft-waiting",
                    "severity": severity, "title": title % name, "detail": detail,
                    "at": draft.get("created_at") if isinstance(draft.get("created_at"), str) else None,
                    "action": {"label": "Review draft", "href": href(name)}})
    return out


def source(home: Path) -> List[Dict[str, Any]]:
    # The same inbox `GET /api/changes/drafts` and `citizen draft list` answer.
    return items(changes.inbox(ROOT, home, periods.state_dir(home) / "studio"))


register_attention(NAME, source)
