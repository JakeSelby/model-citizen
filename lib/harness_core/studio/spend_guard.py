"""Admission, result validation and ledger accounting for Studio runs that spend usage."""
from __future__ import annotations

import json
import math
import os
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Sequence

from harness_core import decision

PRICING_SOURCES = frozenset(("api_credit", "subscription"))
STOP_REASONS = frozenset(("spend_cap", "usage_limit", "runner_failure"))
RESULT_NAME = "spend-result.json"
MAX_RESULT_BYTES = 1024 * 1024


class SpendGuardError(ValueError):
    """A paid run has not supplied a safe, complete spend contract."""


def _money_text(value: Any, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SpendGuardError(name + " must be a finite positive dollar amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise SpendGuardError(name + " must be a finite positive dollar amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise SpendGuardError(name + " must be a finite positive dollar amount")
    return format(amount, "f")


def _reported_money(value: Any, name: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise SpendGuardError(name + " must be a finite nonnegative dollar amount")
    return round(float(value), 6)


def valid_money_text(value: Any) -> bool:
    try:
        return isinstance(value, str) and _money_text(value, "amount") == value
    except SpendGuardError:
        return False


def estimate(records: Iterable[Mapping[str, Any]], suite_id: str,
             case_identities: Sequence[str]) -> Dict[str, Any]:
    """Estimate from terminal observations for the same suite and exact case count."""
    scale = len(case_identities)
    samples = []
    for record in records:
        actual = record.get("spend_actual")
        cases = record.get("case_identities")
        results = record.get("case_results")
        if (record.get("suite_id") == suite_id and record.get("status") == "succeeded"
                and isinstance(results, list) and results
                and all(isinstance(item, dict) and item.get("status") == "completed"
                        for item in results)
                and len(results) == len(cases or []) and isinstance(cases, list)
                and len(cases) == scale and isinstance(actual, (int, float))
                and not isinstance(actual, bool) and math.isfinite(actual) and actual >= 0):
            samples.append(float(actual))
    if not samples:
        return {"amount_usd": None, "basis": "no_history", "sample_count": 0,
                "suite_id": suite_id, "case_count": scale}
    return {"amount_usd": round(float(median(samples)), 6),
            "basis": "median_same_suite_scale", "sample_count": len(samples),
            "suite_id": suite_id, "case_count": scale}


def plan(records: Iterable[Mapping[str, Any]], suite_id: str,
         case_identities: Sequence[str], max_budget_usd: Any, spend_cap_usd: Any,
         pricing_source: Any) -> Dict[str, Any]:
    maximum = _money_text(max_budget_usd, "--max-budget-usd")
    cap = _money_text(spend_cap_usd, "--spend-cap")
    if Decimal(maximum) > Decimal(cap):
        raise SpendGuardError("--max-budget-usd cannot exceed --spend-cap")
    if pricing_source not in PRICING_SOURCES:
        raise SpendGuardError("pricing source must be api_credit or subscription")
    return {
        "estimate": estimate(records, suite_id, case_identities),
        "caps": {"max_budget_usd": maximum, "spend_cap_usd": cap},
        "pricing": {"source": pricing_source,
                    "basis": ("money charged to API credit" if pricing_source == "api_credit"
                              else "list-price equivalent against subscription limits")},
        "confirmation_required": True,
    }


def confirmation_request(suite_id: str, suite_version: int, parameters: Mapping[str, str],
                         target_kind: str, target_ref: str, case_identities: Sequence[str],
                         plan_value: Mapping[str, Any]) -> Dict[str, Any]:
    """The complete immutable request one random, single-use confirmation authorizes."""
    return {
        "suite_id": suite_id,
        "suite_version": suite_version,
        "parameters": dict(sorted(parameters.items())),
        "target": {"kind": target_kind, "ref": target_ref},
        "case_identities": list(case_identities),
        "estimate": plan_value["estimate"],
        "caps": plan_value["caps"],
        "pricing": plan_value["pricing"],
    }


def confirmation_digest(request: Mapping[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def guarded_argv(argv: Sequence[str], plan_value: Mapping[str, Any]) -> list:
    """Put the two enforced caps on the allowlisted runner command exactly once."""
    if "--max-budget-usd" in argv or "--spend-cap" in argv:
        raise SpendGuardError("paid suite argv must not predeclare Studio spend flags")
    caps = plan_value["caps"]
    return list(argv) + ["--max-budget-usd", caps["max_budget_usd"],
                         "--spend-cap", caps["spend_cap_usd"]]


def read_result(directory_fd: int, run_id: str,
                case_identities: Sequence[str]) -> Dict[str, Any]:
    """Read one bounded, no-follow result written by the allowlisted paid runner."""
    descriptor = -1
    try:
        descriptor = os.open(RESULT_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise SpendGuardError("paid suite result must be a bounded mode-0600 regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise SpendGuardError("paid suite result is owned by another user")
        data = os.read(descriptor, MAX_RESULT_BYTES + 1)
        if len(data) != info.st_size:
            raise SpendGuardError("paid suite result changed while it was read")
        value = json.loads(data.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError("non-finite number")))
    except FileNotFoundError as exc:
        raise SpendGuardError("paid suite did not report spend") from exc
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SpendGuardError):
            raise
        raise SpendGuardError("paid suite result is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    allowed = {"schema_version", "run_id", "spend_usd", "cases", "stop_reason"}
    if (not isinstance(value, dict) or set(value) - allowed
            or value.get("schema_version") != 1 or value.get("run_id") != run_id):
        raise SpendGuardError("paid suite result has an invalid identity or schema")
    spend = _reported_money(value.get("spend_usd"), "reported spend")
    stop_reason = value.get("stop_reason")
    if stop_reason is not None and stop_reason not in STOP_REASONS:
        raise SpendGuardError("paid suite result has an invalid stop reason")
    rows = value.get("cases")
    if not isinstance(rows, list):
        raise SpendGuardError("paid suite result has invalid cases")
    expected = list(case_identities)
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {"id", "status", "spend_usd"}
                or row.get("id") not in expected or row.get("id") in by_id
                or row.get("status") not in ("completed", "not_run")):
            raise SpendGuardError("paid suite result has invalid cases")
        amount = _reported_money(row.get("spend_usd", 0), "case spend")
        if row["status"] == "not_run" and amount != 0:
            raise SpendGuardError("a not-run case cannot report spend")
        by_id[row["id"]] = {"id": row["id"], "status": row["status"],
                            "spend_usd": amount}
    missing = [case_id for case_id in expected if case_id not in by_id]
    if (missing or any(row["status"] == "not_run" for row in by_id.values())) \
            and stop_reason is None:
        raise SpendGuardError("paid suite result omitted cases without a stop reason")
    for case_id in missing:
        by_id[case_id] = {"id": case_id, "status": "not_run", "spend_usd": 0.0}
    case_spend = round(sum(row["spend_usd"] for row in by_id.values()), 6)
    if abs(case_spend - spend) > 0.000001:
        raise SpendGuardError("paid suite case spend does not equal total spend")
    return {"spend_usd": spend, "stop_reason": stop_reason,
            "cases": [by_id[case_id] for case_id in expected]}


def upsert_usage(path: Path, record: Mapping[str, Any]) -> bool:
    """Idempotently write one terminal Studio run through the ledger's schema writer."""
    usage = decision._hook_module("usage-log")
    if usage is None:
        return False
    row = {
        "kind": "studio_run",
        "runtime": "studio",
        "run_id": record["run_id"],
        "suite_id": record["suite_id"],
        "pricing_source": record["pricing_identity"]["source"],
        "spend_usd": record["spend_actual"],
        "spend_cap_usd": record["spend_cap"]["spend_cap_usd"],
        "ended": record.get("completed_at"),
    }
    try:
        usage.upsert(row, path=path)
        return True
    except Exception as exc:
        try:
            usage.record_error(exc, path=path, where="studio-run")
        except Exception:
            pass
        return False
