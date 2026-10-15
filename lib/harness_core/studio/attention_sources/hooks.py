# SPDX-License-Identifier: MIT
"""Home item: a hook gone silent. A hook the Hooks page reads as silent (switched on, observable,
no decision in `SILENT_DAYS`) that did fire earlier in the last `DAYS`: it stopped, rather than
never ran, so a fresh install is not a queue of every hook. It links to the hook's own page."""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .. import periods
from ..attention import register_attention
from ..surfaces import hooks as hook_surface

NAME = "hooks"
DAYS = 30


def href(unit: str) -> str:
    return "/harness/hooks/" + urllib.parse.quote(unit, safe="")


def items(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for row in document.get("rows") or ():
        unit = row.get("item_id")
        metrics = row.get("metrics") or {}
        if (row.get("period") or {}).get("end") is not None or unit in seen \
                or metrics.get("liveness") != "silent" \
                or metrics.get("last_fired") in (None, "not in window"):
            continue
        seen.add(unit)
        out.append({"id": "%s:%s" % (NAME, unit), "source": NAME, "kind": "hook-silent",
                    "severity": "medium",
                    "title": "%s has written no decision in %d days" % (
                        row.get("item_label") or unit, hook_surface.SILENT_DAYS),
                    "detail": "It is switched on and wrote decisions before, so silence may mean it "
                              "stopped running. Last fired: %s." % metrics.get("last_fired"),
                    "at": None, "action": {"label": "Check hook", "href": href(unit)}})
    return out


def source(home: Path) -> List[Dict[str, Any]]:
    return items(periods.efficacy_rows("hooks", days=DAYS, home=home))


register_attention(NAME, source)
