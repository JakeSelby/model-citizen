# SPDX-License-Identifier: MIT
"""Home item: projection drift, as `GET /api/overview` reports it. The drift lines show on Home
itself, with the governed repair command, so the item links there. Drift the overview could not
read is this source's error, never an empty queue."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from .. import home as studio_home
from ..attention import register_attention

NAME = "drift"
HREF = "/home#drift"


def items(block: Mapping[str, Any]) -> List[Dict[str, Any]]:
    status = block.get("status")
    if status == "current":
        return []
    if status != "drift":
        raise RuntimeError(block.get("message") or "projection drift is unavailable")
    lines = [line for line in block.get("items") or () if isinstance(line, str)]
    count = len(lines)
    return [{"id": NAME + ":projection", "source": NAME, "kind": "drift", "severity": "medium",
             "title": "%d projected %s drifted from the selection" % (count, "file has" if count == 1 else "files have"),
             "detail": (lines[0] if lines else "") + (" and %d more." % (count - 1) if count > 1 else ""),
             "at": None, "action": {"label": "See drift", "href": HREF}}]


def source(home: Path) -> List[Dict[str, Any]]:
    return items(studio_home.read(NAME, home))


register_attention(NAME, source)
