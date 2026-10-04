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

A point is pre-registered only when its row says so: its own `evidence` label, or the delegation
block's `registered`, the engine's record that every row of the set named a pre-registration.
Anything else reads exploratory, as `docs/evidence-standard.md` requires, and a line holding an
exploratory point says so. Rows are read generically: a field this module does not know is ignored.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import evaluation

SCHEMA_VERSION = 1
HISTORY_SUITE = "cost-benchmark-history"
STATIC_SUITE = "static-cost"
PAGE = 200
MAX_RECORDS = 2000
MAX_BUNDLES = 16
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


def _figure(value: Any, interval: Any = None, kind: Optional[str] = None) -> Dict[str, Any]:
    stored = _interval(interval)
    return {"value": _number(value), "interval": stored,
            "interval_kind": kind if stored is not None else None}


def _records(store: Store, suite_id: str, maximum: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Every indexed record of one suite, newest first, up to `maximum`; True when cut short."""
    out: List[Dict[str, Any]] = []
    cursor = None
    while True:
        page = store.history(limit=PAGE, cursor=cursor, suite_id=suite_id)
        for item in page.get("items") or []:
            if len(out) >= maximum:
                return out, True
            out.append(store.get(item["run_id"]))
        cursor = page.get("next_cursor")
        if not cursor:
            return out, False


def collect(store: Store) -> Dict[str, Any]:
    """The run store reads, kept apart so a server runs them on the thread that owns SQLite."""
    history, history_cut = _records(store, HISTORY_SUITE, MAX_RECORDS)
    static, static_cut = _records(store, STATIC_SUITE, MAX_RECORDS)
    return {"history": history, "static": static, "truncated": history_cut or static_cut}


def evidence_label(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The row's own pre-registration record; with none, exploratory."""
    label = row.get("evidence")
    registration = _text(row.get("pre_registration"))
    if label in (EXPLORATORY, PREREGISTERED):
        return {"label": label, "pre_registration": registration,
                "reason": "the row is labelled " + label}
    delegation = row.get("delegation")
    if isinstance(delegation, dict) and delegation.get("registered") is True:
        return {"label": PREREGISTERED, "pre_registration": registration,
                "reason": "the row's delegation block records every run of its set as pre-registered"}
    return {"label": EXPLORATORY, "pre_registration": registration,
            "reason": "the row records no pre-registration, so it reads exploratory"}


def _sm2(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("sm2")
    value = value if isinstance(value, dict) else {}
    return {"verdict": _text(value.get("verdict")), "reason": _text(value.get("reason")),
            "limitation": _text(value.get("limitation")),
            "eligible": value.get("sm2_eligible") if isinstance(value.get("sm2_eligible"), bool) else None,
            "confidence": _number(value.get("confidence")),
            "unavailable": _text(value.get("unavailable"))
            or (None if value else "the row carries no SM-2 result")}


def _delegation(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    value = row.get("delegation")
    if not isinstance(value, dict) or not isinstance(value.get("verdicts"), dict):
        return None
    verdicts = {name: count for name, count in value["verdicts"].items()
                if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool)}
    return {"label": _text(value.get("label")), "verdicts": verdicts}


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
    return {
        "run_id": record.get("run_id"),
        "date": _text(row.get("date")), "harness_version": _text(row.get("harness_version")),
        "tag": _text(row.get("tag")), "harness_sha": _text(row.get("harness_sha")),
        "model": _text(row.get("model")), "cli_version": _text(row.get("cli_version")),
        "series": _text(row.get("series")) or "", "bucket": _text(row.get("bucket")) or "",
        "change_note": _text(row.get("change_note")) or "",
        "status": _text(row.get("status")), "cache_basis": _text(row.get("cache_basis")),
        "reps": row.get("reps") if isinstance(row.get("reps"), int) else None,
        "evidence": evidence_label(row),
        "measures": {
            "ratio_sm2": _figure(sm2.get("ratio"), sm2.get("ratio_interval"), "SM-2"),
            "ratio": _figure(row.get("ratio")),
            "ratio_cache_normalised": _figure(row.get("ratio_cache_normalised")),
            "pass_rate_harness": arm("harness"),
            "pass_rate_bare": arm("bare"),
            "pass_rate_difference": _figure(sm2.get("difference"), sm2.get("difference_interval"), "SM-2"),
        },
        "sm2": _sm2(row),
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
        members = sorted(members, key=lambda p: (p["date"] or "", p["harness_version"] or "",
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
    return sorted(out, key=lambda p: p["harness_version"])


def _plain(value: Any) -> Any:
    """A card's declared figure as JSON a page can print; a shape it cannot is left out."""
    if value is None or isinstance(value, (str, bool)) or _number(value) is not None:
        return value
    if isinstance(value, list) and all(_number(item) is not None for item in value):
        return list(value)
    return None


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
        cards.append({"id": _text(card.get("id")), "claim": _text(card.get("claim")),
                      "estimand": _text(card.get("estimand")), "figure": _plain(figure.get("value")),
                      "interval": _plain(interval.get("value")),
                      "verify_status": card.get("verify_status") is True,
                      "published": None if declared is None else {"field": declared["field"],
                                                                  "text": declared["text"]}})
    checks = result.get("checks") if isinstance(result.get("checks"), Mapping) else {}
    return {"bundle": relative, "status": proof["status"], "bundle_id": proof["bundle_id"],
            "errors": [str(item) for item in proof["errors"]],
            "unknown": [str(item) for item in proof["unknown"]],
            "checks": {str(name): value is True for name, value in checks.items()},
            "cards": cards, "command": "citizen evidence verify --json " + relative}


def proof_set(repository: Path, verify: Optional[Verifier] = None) -> Dict[str, Any]:
    """Every bound bundle as the verifier judged it, and what `product.json` publishes from it."""
    verify = verify or evaluation.verify_bundle
    published = _declarations(repository)
    bound = evaluation.bound_bundles(repository)
    bundles = [_bundle(repository, relative, published, verify) for relative in bound[:MAX_BUNDLES]]
    status = {bundle["bundle"]: bundle for bundle in bundles}
    claims = []
    for item in published:
        bundle = status.get(item["bundle"])
        card = next((c for c in bundle["cards"] if c["id"] == item["card"]), None) if bundle else None
        claims.append({"field": item["field"], "text": item["text"], "bundle": item["bundle"],
                       "card": item["card"],
                       "verify_status": bool(bundle and bundle["status"] == "verified"
                                             and card and card["verify_status"])})
    return {"statement": None if published else NO_CLAIM, "claims": claims, "bundles": bundles,
            "unverified_bundles": bound[MAX_BUNDLES:]}


def report(repository: Path, collected: Mapping[str, Any],
           verify: Optional[Verifier] = None) -> Dict[str, Any]:
    """The trends page: lines, static context and the proof set, each as its source stated it."""
    points = [point for point in (history_point(r) for r in collected.get("history") or [])
              if point is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ratio_note": RATIO_NOTE,
        "measures": [dict(item) for item in MEASURES],
        "lines": lines(points),
        "static": {"measure": dict(STATIC_MEASURE), "points": static_points(collected.get("static") or [])},
        "not_tracked": [dict(item) for item in NOT_TRACKED],
        "truncated": bool(collected.get("truncated")),
        "proof": proof_set(repository, verify),
        "commands": dict(COMMANDS),
    }
