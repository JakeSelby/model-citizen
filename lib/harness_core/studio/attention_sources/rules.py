# SPDX-License-Identifier: MIT
"""Home item: a rule gone silent. A rule switched on whose detectors fired in no measured session
across the widest window `rule_health` could read, as the Rules page reports it. Hits come from
this machine's own sessions, so the item says the reading is exploratory."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from .. import home as studio_home
from ..attention import register_attention
from ..surfaces import rules as rule_surface

NAME = "rules"
HREF = "/harness/rules"

studio_home.register_read(NAME, lambda _home: rule_surface.load_report())


def items(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for unit, row in sorted(rule_surface.rules(document).items()):
        hits = row.get("hits") or {}
        last = hits.get("last_fired") or {}
        if row.get("selection_state") != "on" or hits.get("status") != "measured" \
                or last.get("state") != "not-fired":
            continue
        days = last.get("days")
        detail = ("Its detectors found nothing in %s days of measured sessions: the rule may be "
                  "followed without a trace, redundant, or its detector may miss. Exploratory: "
                  "these are this machine's own sessions." % days)
        if hits.get("reliable") is False:
            detail += " The detector is under the precision floor, so the reading is unreliable."
        out.append({"id": "%s:%s" % (NAME, unit), "source": NAME, "kind": "rule-silent",
                    "severity": "low", "title": "%s has not fired in %s days" % (row.get("rule") or unit, days),
                    "detail": detail, "at": None,
                    "action": {"label": "Review rule", "href": HREF}})
    return out


def source(home: Path) -> List[Dict[str, Any]]:
    return items(studio_home.read(NAME, home))


register_attention(NAME, source)
