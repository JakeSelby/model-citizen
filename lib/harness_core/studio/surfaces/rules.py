# SPDX-License-Identifier: MIT
"""Rules: one item per rule file the selection can switch, its on/off as the setting.

The rule list, each rule's detectors, their corpus precision and the rule's context cost are
`rule_health.report`'s, the document the Studio's rule-health route serves. Detector hits are
counted per setting period from the usage rows inside it, the way `citizen usage --rules` counts
them: session rows only, a row without a readable `rules` map left out of the denominator, and a
renamed detector folded into its successor. Precision and context cost describe the rule as it is
now, so every period of a rule shows the same figure for them.

A rule's switch lives at `rules.<unit>` in `config.json`, so an apply or a committed config change
that switches it starts a period; usage rows carry no per-rule stamp.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .. import periods, rule_health, selection

ROOT = Path(__file__).resolve().parents[4]
REPORT_TTL = 60.0
NO_DETECTOR = "no detector"

_LOCK = threading.Lock()
_REPORT: Dict[str, Any] = {}


def load_report() -> Dict[str, Any]:
    """`rule_health.report` for this checkout, reused for `REPORT_TTL` seconds: it runs the
    engines as subprocesses, and one page load asks for it once per rule period."""
    with _LOCK:
        if _REPORT and time.monotonic() - _REPORT["at"] < REPORT_TTL:
            return _REPORT["document"]
    document = rule_health.report(ROOT)
    with _LOCK:
        _REPORT.update(at=time.monotonic(), document=document)
    return document


def clear_cache() -> None:
    with _LOCK:
        _REPORT.clear()


def rules(document: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`{unit: report row}` for each switchable rule; a later file of a taken unit is shadowed by
    the first and is left out, as the selection switches only the first."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in document.get("rows") or ():
        if isinstance(row, dict) and row.get("kind") == "rules" and row.get("unit") not in out:
            out[row["unit"]] = row
    return out


def precision_text(detectors: Sequence[Mapping[str, Any]]) -> str:
    """The lowest corpus precision among a rule's detectors, and whether the engine's floor
    verdict names any of them."""
    if not detectors:
        return NO_DETECTOR
    if any(d.get("status") == rule_health.UNAVAILABLE for d in detectors):
        return rule_health.UNAVAILABLE
    scored = [d for d in detectors if d.get("status") == "measured"
              and isinstance(d.get("precision"), (int, float))]
    if not scored:
        return rule_health.NOT_MEASURED
    text = "%.2f" % min(d["precision"] for d in scored)
    if len(detectors) > 1:
        text += " (lowest of %d)" % len(detectors)
    if any(d.get("below_floor") for d in detectors):
        text += ", below floor"
    elif len(scored) < len(detectors):
        text += ", %d unscored" % (len(detectors) - len(scored))
    return text


def tokens_value(tokens: Any) -> Any:
    if isinstance(tokens, dict) and tokens.get("status") == "measured" \
            and isinstance(tokens.get("tokens"), int):
        return tokens["tokens"]
    return rule_health.NOT_MEASURED


def config_value(config: Mapping[str, Any], unit: str) -> str:
    """The switch one version of `config.json` sets; "default" when it leaves the rule to the
    selection's default."""
    switches = config.get("rules")
    value = switches.get(unit) if isinstance(switches, dict) else None
    return value if isinstance(value, str) and value else "default"


def items(_ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    return {unit: {"label": row.get("rule") or unit, "observations": [],
                   "current": row.get("selection_state")}
            for unit, row in rules(load_report()).items()}


def _measured_sessions(rows: Sequence[Mapping[str, Any]], harness: Any) -> List[Mapping[str, Any]]:
    """The rows `usage --rules` counts: sessions whose detector map was read without error."""
    out = []
    for row in rows:
        values = row.get("rules")
        if (row.get("kind") or "session") != "session" or not isinstance(values, dict):
            continue
        if row.get("rules_error") or row.get("rules_errors"):
            continue
        if any(harness.usage_count(value) is None for value in values.values()):
            continue
        out.append(row)
    return out


def _fired_text(fired: int, sessions: int) -> str:
    """Sessions a detector fired in, as a count and a share; a fire is never shown as 0%."""
    if not sessions:
        return "no measured session"
    share = 100.0 * fired / sessions
    return "%d (%s)" % (fired, "<1%" if 0 < share < 0.5 else "%d%%" % round(share))


def metrics(rows: Sequence[Mapping[str, Any]], unit: str, _value: str = "") -> Dict[str, Any]:
    row = rules(load_report()).get(unit) or {}
    harness = selection._harness_module(ROOT)
    sessions = _measured_sessions(rows, harness)
    detector_ids = [d.get("id") for d in row.get("detectors") or () if isinstance(d, dict)]
    out: Dict[str, Any] = {"sessions": len(sessions)}
    if not detector_ids:
        out.update(hits=NO_DETECTOR, fired_in=NO_DETECTOR)
    else:
        renamed = harness.renamed_ids()
        counts = [sum(folded.get(did, 0) for did in detector_ids)
                  for folded in (harness.folded_rules(dict(r), renamed) for r in sessions)]
        fired = sum(1 for count in counts if count > 0)
        out["hits"] = sum(counts)
        out["fired_in"] = _fired_text(fired, len(sessions))
    out["precision"] = precision_text(row.get("detectors") or ())
    out["context_tokens"] = tokens_value(row.get("tokens"))
    return out


periods.register_surface("rules", items, metrics,
                         config_key=lambda unit: "rules." + unit, config_value=config_value)
