# SPDX-License-Identifier: MIT
"""Trends across versions and dates, beside the project's proof set (AH-S309, #992).

Nothing here measures, intervals or judges. Every figure is read as its engine stored it:

- **benchmark lines**: the run store's `cost-benchmark-history` records, each one
  `benchmarks/history.jsonl` row as `scripts/cost_bench.py history_row` wrote it. A line is one
  `series` and `bucket`, since a new series means the task set or the model changed. Each point
  carries the mean-of-reps ratio, the cache-normalised ratio, SM-2's ratio and pass rates with the
  intervals SM-2 stored, and the row's change note. Ratios only: dollars never cross days;
- **static context**: the run store's `static-cost` records, the estimated tokens per version;
- **proof set**: every bundle `product.json` binds a claim to, verified by the function
  `citizen evidence verify --json` prints (`evaluation.verify_bundle`), with its cards and which of
  them `product.json` publishes.

A point is pre-registered only when the engine's own rule says so wherever the row records it:
`delegation_verdict.registered` on the row's own label, and the delegation block's stored
`registered`. Either one false, or neither present, reads exploratory, as
`docs/evidence-standard.md` requires, and a line holding an exploratory point says so. Rows are
read generically: a field this module does not know is ignored. A section whose source cannot be
read says why and leaves the others standing.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import evaluation, replay

SCHEMA_VERSION = 1
HISTORY_SUITE = "cost-benchmark-history"
STATIC_SUITE = "static-cost"
PAGE = 200
MAX_RECORDS = 2000
MAX_BUNDLES = 16
# Each bundle's verifier runs git with a 30 s timeout per call; no bundle starts after this budget.
VERIFY_BUDGET_SECONDS = 60.0
NOT_FROM_REGISTERED = "exploratory, not from a registered run"  # `delegation_verdict.heading`
EXPLORATORY = "exploratory"
PREREGISTERED = "pre-registered"
CLI_COMMAND = ("citizen", "runs", "history", "--json")
COMMANDS = {
    "history": "citizen runs history --json (suite cost-benchmark-history), after citizen runs reindex",
    "static": "citizen runs history --json (suite static-cost), after citizen runs reindex",
    "proof": "citizen evidence verify --json <bundle>",
}
RATIO_NOTE = ("Each ratio is harness over bare on the same day and model. Compare ratios across "
              "days, never dollars. A new series means the task set or the model changed.")
MEASURES = (
    {"id": "ratio_sm2", "label": "Cost ratio, SM-2", "unit": "ratio",
     "note": "SM-2's cost of a pass, harness over bare, with the interval SM-2 stored."},
    {"id": "ratio", "label": "Cost ratio, mean of reps", "unit": "ratio",
     "note": "The older mean-of-reps ratio, kept so earlier rows stay comparable."},
    {"id": "ratio_cache_normalised", "label": "Cache-normalised cost ratio", "unit": "ratio",
     "note": "The mean-of-reps ratio on cache-normalised cost."},
    {"id": "pass_rate_harness", "label": "Pass rate, harness", "unit": "share",
     "note": "SM-2's harness pass rate; its interval is descriptive."},
    {"id": "pass_rate_bare", "label": "Pass rate, bare", "unit": "share",
     "note": "SM-2's bare pass rate; its interval is descriptive."},
    {"id": "pass_rate_difference", "label": "Pass-rate difference", "unit": "points",
     "note": "SM-2's harness minus bare pass rate, with the interval SM-2 stored."},
)
STATIC_MEASURE = {"id": "static_tokens", "label": "Static context, estimated tokens",
                  "unit": "tokens", "note": "benchmarks/static.json's estimated tokens per version."}
NOT_TRACKED = (
    {"measure": "Detector precision",
     "reason": "No engine keeps detector precision by version or date; Rule health shows the "
               "current labelled-corpus reading.", "where": "/reports/rules"},
    {"measure": "Rule adherence",
     "reason": "No engine keeps rule adherence by version or date beyond each benchmark row's "
               "delegation tally, shown on its point; Rule health shows the current reading.",
     "where": "/reports/rules"},
)
NO_CLAIM = "The project publishes no measured claim: product.json binds no evidence card."

Store = Any
Verifier = Callable[[Path], Mapping[str, Any]]
_ENGINE_LOCK = threading.Lock()
_DELEGATION = None


def _delegation_engine():
    """`scripts/delegation_verdict.py`, loaded once; it imports its sibling `experiment_protocol`."""
    global _DELEGATION
    with _ENGINE_LOCK:
        if _DELEGATION is None:
            protocol = replay._engine_module("experiment_protocol")
            prior = sys.modules.get("experiment_protocol")
            sys.modules["experiment_protocol"] = protocol
            try:
                spec = importlib.util.spec_from_file_location(
                    "studio_delegation_verdict", str(replay._SCRIPTS / "delegation_verdict.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            finally:
                if prior is None:
                    sys.modules.pop("experiment_protocol", None)
                else:
                    sys.modules["experiment_protocol"] = prior
            _DELEGATION = module
        return _DELEGATION


def version_key(version: Optional[str]) -> Tuple[Any, ...]:
    """Release order: numeric parts compare as numbers, so 0.10.0 follows 0.9.0."""
    text = (version or "").lstrip("v")
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part)
                 for part in re.split(r"[.+-]", text))


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _interval(value: Any) -> Optional[List[float]]:
    """A stored `[low, high]` pair as stored; anything else is no interval, never a made one."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low, high = _number(value[0]), _number(value[1])
    return None if low is None or high is None else [low, high]


def _figure(value: Any, interval: Any = None, kind: Optional[str] = None,
            undefined: Any = None) -> Dict[str, Any]:
    stored = _interval(interval)
    number = _number(value)
    return {"value": number, "interval": stored,
            "interval_kind": kind if stored is not None else None,
            "undefined": _text(undefined) if number is None else None}


def _section(maximum: int, store: Store, suite_id: str) -> Dict[str, Any]:
    """One suite's indexed records, newest first, up to `maximum`. A record that cannot be read is
    counted and skipped; an index that cannot be paged leaves the section unavailable."""
    out: List[Dict[str, Any]] = []
    unreadable = 0
    cursor = None
    try:
        while True:
            page = store.history(limit=PAGE, cursor=cursor, suite_id=suite_id)
            items = page.get("items") or []
            for item in items:
                if len(out) + unreadable >= maximum:
                    return {"records": out, "unreadable": unreadable, "truncated": True,
                            "error": None}
                try:
                    out.append(store.get(item["run_id"]))
                except ValueError:  # RunStoreError: a corrupt or oversized record
                    unreadable += 1
            cursor = page.get("next_cursor")
            if not cursor:
                return {"records": out, "unreadable": unreadable, "truncated": False,
                        "error": None}
    except ValueError as exc:
        return {"records": out, "unreadable": unreadable, "truncated": False,
                "error": "the run index could not be read: %s" % exc}


def collect(store: Store) -> Dict[str, Any]:
    """The run store reads, kept apart so a server runs them on the thread that owns SQLite."""
    return {"history": _section(MAX_RECORDS, store, HISTORY_SUITE),
            "static": _section(MAX_RECORDS, store, STATIC_SUITE)}


def unavailable(reason: str) -> Dict[str, Any]:
    """`collect`'s shape when the run store itself cannot be opened or read."""
    empty = {"records": [], "unreadable": 0, "truncated": False, "error": reason}
    return {"history": dict(empty), "static": dict(empty)}


def evidence_label(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Pre-registered only when every engine record on the row says so; otherwise exploratory."""
    registration = _text(row.get("pre_registration"))
    readings = []
    if "evidence" in row:
        # The engine's rule: labelled pre-registered and naming its pre-registration.
        readings.append(("the row's own label", _delegation_engine().registered([row])))
    delegation = row.get("delegation")
    if isinstance(delegation, dict) and isinstance(delegation.get("registered"), bool):
        readings.append(("the delegation block", delegation["registered"]))
    if not readings:
        return {"label": EXPLORATORY, "pre_registration": registration,
                "reason": "the row records no pre-registration, so it reads exploratory"}
    refusing = [name for name, value in readings if not value]
    if refusing:
        return {"label": EXPLORATORY, "pre_registration": registration,
                "reason": "%s does not record a pre-registered run" % " and ".join(refusing)}
    return {"label": PREREGISTERED, "pre_registration": registration,
            "reason": "%s records%s a pre-registered run" % (
                " and ".join(name for name, _ in readings), " each" if len(readings) > 1 else "")}


def _sm2(row: Mapping[str, Any], registered: bool) -> Dict[str, Any]:
    """SM-2's stored fields, and its verdict line in `replay_stats.render`'s words, marked with
    `delegation_verdict.heading`'s phrase when the point is not from a registered run."""
    value = row.get("sm2")
    value = value if isinstance(value, dict) else {}
    out = {"verdict": _text(value.get("verdict")), "reason": _text(value.get("reason")),
           "claim": _text(value.get("claim")), "limitation": _text(value.get("limitation")),
           "ratio_undefined": _text(value.get("ratio_undefined")),
           "eligible": value.get("sm2_eligible") if isinstance(value.get("sm2_eligible"), bool) else None,
           "confidence": _number(value.get("confidence")),
           "unavailable": _text(value.get("unavailable"))
           or (None if value else "the row carries no SM-2 result")}
    if out["unavailable"] or out["verdict"] is None:
        out["text"] = None
        return out
    verdict = out["verdict"] if registered else "%s (%s)" % (out["verdict"], NOT_FROM_REGISTERED)
    eligibility = "SM-2 eligibility: %s%s" % (
        "eligible" if out["eligible"] else "exploratory only",
        "; %s" % out["limitation"] if out["limitation"] else "")
    out["text"] = "%s. verdict: %s, because %s%s" % (
        eligibility, verdict, out["reason"] or "no reason stored",
        "; claim: %s" % out["claim"] if out["claim"] else "")
    return out


def _delegation(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The tally, and the block's heading as `delegation_verdict.heading` prints it."""
    value = row.get("delegation")
    if not isinstance(value, dict) or not isinstance(value.get("verdicts"), dict):
        return None
    verdicts = {name: count for name, count in value["verdicts"].items()
                if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool)}
    try:
        heading = _delegation_engine().heading(value)
    except (KeyError, TypeError, ValueError, AttributeError):
        heading = None  # an older or partial block the engine's own printer cannot read
    return {"label": _text(value.get("label")), "verdicts": verdicts, "heading": heading}


def history_point(record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """One benchmark row as a point; None for a record with no row to read."""
    row = record.get("raw")
    if not isinstance(row, dict):
        return None
    sm2 = row.get("sm2") if isinstance(row.get("sm2"), dict) else {}
    arms = sm2.get("arms") if isinstance(sm2.get("arms"), dict) else {}

    def arm(name: str) -> Dict[str, Any]:
        cell = arms.get(name) if isinstance(arms.get(name), dict) else {}
        return _figure(cell.get("pass_rate"), cell.get("pass_rate_interval_descriptive"), "descriptive")

    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    evidence = evidence_label(row)
    return {
        "run_id": record.get("run_id"),
        "date": _text(row.get("date")), "harness_version": _text(row.get("harness_version")),
        "tag": _text(row.get("tag")), "harness_sha": _text(row.get("harness_sha")),
        "model": _text(row.get("model")), "cli_version": _text(row.get("cli_version")),
        "series": _text(row.get("series")) or "", "bucket": _text(row.get("bucket")) or "",
        "change_note": _text(row.get("change_note")) or "",
        "status": _text(row.get("status")), "cache_basis": _text(row.get("cache_basis")),
        "reps": row.get("reps") if isinstance(row.get("reps"), int) else None,
        "evidence": evidence,
        "measures": {
            "ratio_sm2": _figure(sm2.get("ratio"), sm2.get("ratio_interval"), "SM-2",
                                 sm2.get("ratio_undefined")),
            "ratio": _figure(row.get("ratio")),
            "ratio_cache_normalised": _figure(row.get("ratio_cache_normalised")),
            "pass_rate_harness": arm("harness"),
            "pass_rate_bare": arm("bare"),
            "pass_rate_difference": _figure(sm2.get("difference"), sm2.get("difference_interval"), "SM-2"),
        },
        "sm2": _sm2(row, evidence["label"] == PREREGISTERED),
        "delegation": _delegation(row),
        "source": {"path": _text(source.get("path")), "line": source.get("line")},
    }


def _line_note(labels: Sequence[str]) -> Tuple[str, str]:
    kinds = set(labels)
    if kinds == {PREREGISTERED}:
        return PREREGISTERED, "Every point on this line comes from a pre-registered run."
    if kinds == {EXPLORATORY}:
        return EXPLORATORY, "Every point on this line is exploratory and cannot be cited as evidence."
    return "mixed", ("This line mixes exploratory and pre-registered points; the exploratory points "
                     "cannot be cited as evidence.")


def lines(points: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Points grouped by series and bucket, each in date then version order."""
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for point in points:
        grouped.setdefault((point["series"], point["bucket"]), []).append(point)
    out = []
    for (series, bucket), members in sorted(grouped.items()):
        members = sorted(members, key=lambda p: (p["date"] or "", version_key(p["harness_version"]),
                                                 p["harness_sha"] or ""))
        evidence, note = _line_note([p["evidence"]["label"] for p in members])
        out.append({"id": series + ("/" + bucket if bucket else ""), "series": series,
                    "bucket": bucket, "evidence": evidence, "evidence_note": note,
                    "points": list(members)})
    return out


def static_points(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for record in records:
        raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
        total = raw.get("total") if isinstance(raw.get("total"), dict) else {}
        version = _text(raw.get("harness_version"))
        if version is None:
            continue
        out.append({"run_id": record.get("run_id"), "harness_version": version,
                    "value": _number(total.get("est_tokens")),
                    "files": total.get("files") if isinstance(total.get("files"), int) else None})
    return sorted(out, key=lambda p: version_key(p["harness_version"]))


def _plain(value: Any) -> Any:
    """A card's declared figure as JSON a page can print; a shape it cannot is left out."""
    if value is None or isinstance(value, (str, bool)) or _number(value) is not None:
        return value
    if isinstance(value, list) and all(_number(item) is not None for item in value):
        return list(value)
    return None


def _not_checked(relative: str, reason: str) -> Dict[str, Any]:
    return {"bundle": relative, "status": "not checked", "reason": reason, "bundle_id": None,
            "errors": [], "unknown": [], "checks": {}, "cards": [],
            "command": "citizen evidence verify --json " + relative}


def _declarations(repository: Path) -> List[Dict[str, str]]:
    try:
        document = json.loads((Path(repository) / "product.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cards = document.get("evidence_cards") if isinstance(document, dict) else None
    out = []
    for card in cards if isinstance(cards, list) else []:
        if isinstance(card, dict) and all(_text(card.get(name)) for name in ("field", "text", "bundle", "card")):
            out.append({name: card[name] for name in ("field", "text", "bundle", "card")})
    return out


def _bundle(repository: Path, relative: str, published: Sequence[Mapping[str, str]],
            verify: Verifier) -> Dict[str, Any]:
    try:
        result = verify(Path(repository) / relative)
        if not isinstance(result, Mapping):
            raise TypeError("the verifier returned no result")
    except Exception as exc:  # a verifier that cannot run leaves the bundle failed, never verified
        result = {"ok": False, "bundle_id": None, "errors": ["the bundle could not be verified: %s" % exc],
                  "unknown": [], "cards": [], "checks": {}}
    proof = evaluation.proof_status(result)
    mine = {item["card"]: item for item in published if item["bundle"] == relative}
    cards = []
    for card in result.get("cards") or []:
        if not isinstance(card, Mapping):
            continue
        figure = card.get("figure") if isinstance(card.get("figure"), Mapping) else {}
        interval = card.get("interval") if isinstance(card.get("interval"), Mapping) else {}
        declared = mine.get(card.get("id")) if isinstance(card.get("id"), str) else None
        own = card.get("verify_status") is True
        cards.append({"id": _text(card.get("id")), "claim": _text(card.get("claim")),
                      "estimand": _text(card.get("estimand")), "figure": _plain(figure.get("value")),
                      "interval": _plain(interval.get("value")),
                      "verify_status": own,
                      # A card counts as verified only inside a bundle the verifier passed.
                      "verified": own and proof["status"] == "verified",
                      "published": None if declared is None else {"field": declared["field"],
                                                                  "text": declared["text"]}})
    checks = result.get("checks") if isinstance(result.get("checks"), Mapping) else {}
    return {"bundle": relative, "status": proof["status"], "reason": None, "bundle_id": proof["bundle_id"],
            "errors": [str(item) for item in proof["errors"]],
            "unknown": [str(item) for item in proof["unknown"]],
            "checks": {str(name): value is True for name, value in checks.items()},
            "cards": cards, "command": "citizen evidence verify --json " + relative}


def proof_set(repository: Path, verify: Optional[Verifier] = None,
              clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Every bound bundle as the verifier judged it, and what `product.json` publishes from it.

    The verifier runs git in subprocesses, so no bundle starts once `VERIFY_BUDGET_SECONDS` is
    spent, and none past `MAX_BUNDLES`; each of those reads "not checked", never failed."""
    verify = verify or evaluation.verify_bundle
    published = _declarations(repository)
    bound = evaluation.bound_bundles(repository)
    deadline = clock() + VERIFY_BUDGET_SECONDS
    bundles = []
    for number, relative in enumerate(bound):
        if number >= MAX_BUNDLES:
            bundles.append(_not_checked(relative, "past the page's limit of %d bundles" % MAX_BUNDLES))
        elif clock() >= deadline:
            bundles.append(_not_checked(relative, "the page's %g-second verification budget ran out"
                                        % VERIFY_BUDGET_SECONDS))
        else:
            bundles.append(_bundle(repository, relative, published, verify))
    status = {bundle["bundle"]: bundle for bundle in bundles}
    claims = []
    for item in published:
        bundle = status.get(item["bundle"])
        card = next((c for c in bundle["cards"] if c["id"] == item["card"]), None) if bundle else None
        if bundle is None:
            state, reason = "not checked", "the bundle path is absolute or leaves the repository"
        elif bundle["status"] == "not checked":
            state, reason = "not checked", bundle["reason"]
        elif card is None:
            state, reason = "failed", "the verifier reported no card %s" % item["card"]
        elif card["verified"]:
            state, reason = "verified", None
        else:
            state, reason = "failed", ("the card failed verification" if bundle["status"] == "verified"
                                       else "its bundle failed verification")
        claims.append({"field": item["field"], "text": item["text"], "bundle": item["bundle"],
                       "card": item["card"], "status": state, "reason": reason})
    return {"statement": None if published else NO_CLAIM, "claims": claims, "bundles": bundles}


def _state(section: Mapping[str, Any]) -> Dict[str, Any]:
    return {"status": "unavailable" if section.get("error") else "ready",
            "reason": section.get("error"), "unreadable": section.get("unreadable", 0),
            "truncated": bool(section.get("truncated"))}


def report(repository: Path, collected: Mapping[str, Any],
           verify: Optional[Verifier] = None) -> Dict[str, Any]:
    """The trends page: lines, static context and the proof set, each as its source stated it."""
    history, static = collected["history"], collected["static"]
    points = [point for point in (history_point(r) for r in history["records"]) if point is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ratio_note": RATIO_NOTE,
        "measures": [dict(item) for item in MEASURES],
        "lines": lines(points),
        "static": {"measure": dict(STATIC_MEASURE), "points": static_points(static["records"])},
        "not_tracked": [dict(item) for item in NOT_TRACKED],
        "sections": {"history": _state(history), "static": _state(static)},
        "max_records": MAX_RECORDS,
        "proof": proof_set(repository, verify),
        "commands": dict(COMMANDS),
    }
