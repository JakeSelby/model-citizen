"""Spend and usage for the Studio, read through `citizen usage --json` and nothing else.

The ledger owns every total, percentile, price and unknown count. The Studio runs the same
command a developer would, in a child process, and returns its document unchanged inside an
envelope that names the command and the pricing basis. Nothing here sums, prices or filters a
row, so a Studio figure and a CLI figure cannot disagree, and nothing here exports: the command
is never `usage export`, never `--rescan`, and opens no connection.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

SCHEMA_VERSION = 1
# Ledger groupings read the usage ledger and answer within the page budget. `rebuild` reads the
# session transcripts on every call, so the Studio asks for it only when the developer does.
LEDGER_GROUPINGS = ("day", "model", "role", "repo", "session")
GROUPINGS = LEDGER_GROUPINGS + ("rebuild",)
REPORTS = {"day": "usage", "model": "usage", "repo": "usage", "session": "usage",
           "role": "roles", "rebuild": "rebuild"}
MAX_DAYS = 3660
TIMEOUTS = {"rebuild": 300}
DEFAULT_TIMEOUT = 60
LABEL = "list-price equivalent"


class SpendError(ValueError):
    """The request names no grouping or window the ledger reports."""


class SpendUnavailable(RuntimeError):
    """`citizen usage --json` did not answer with a usage document."""


def parse(request: Mapping[str, Any]) -> Tuple[str, int]:
    if not isinstance(request, Mapping) or set(request) - {"by", "days"}:
        raise SpendError("spend takes by and days")
    by, days = request.get("by"), request.get("days")
    if by not in GROUPINGS:
        raise SpendError("by must be one of " + ", ".join(GROUPINGS))
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise SpendError("days must be a whole number from 1 to %d" % MAX_DAYS)
    return by, days


def command(by: str, days: int) -> Tuple[str, ...]:
    """The exact `citizen` command whose document the Studio shows."""
    return ("citizen", "usage", "--json", "--by", by, "--days", str(days))


def report(repo_root: Path, by: str, days: int) -> Dict[str, Any]:
    """Run the CLI's own report and wrap its document, unchanged, with its provenance."""
    argv = [sys.executable, str(Path(repo_root) / "bin" / "harness")] + list(command(by, days)[1:])
    # HARNESS_QUIET silences the CLI's stdout, which is where the JSON document is written.
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    try:
        done = subprocess.run(argv, cwd=str(repo_root), env=environment, capture_output=True,
                              text=True, timeout=TIMEOUTS.get(by, DEFAULT_TIMEOUT))
    except (OSError, subprocess.SubprocessError) as exc:
        raise SpendUnavailable("citizen usage did not finish") from exc
    if done.returncode != 0:
        raise SpendUnavailable("citizen usage exited %d" % done.returncode)
    try:
        document = json.loads(done.stdout, parse_constant=_refuse_constant)
    except ValueError as exc:
        raise SpendUnavailable("citizen usage did not print one JSON document") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or document.get("report") != REPORTS[by] or document.get("by") != by
            or document.get("days") != days or not isinstance(document.get("groups"), list)):
        raise SpendUnavailable("citizen usage answered with a different report")
    as_of = document.get("price_as_of")
    return {
        "schema_version": SCHEMA_VERSION,
        "by": by,
        "days": days,
        "command": " ".join(command(by, days)),
        "basis": {"label": LABEL,
                  "cost_basis": document.get("cost_basis"),
                  "price_as_of": as_of if isinstance(as_of, str) and as_of else None},
        "ledger": document,
    }


def _refuse_constant(name: str) -> Any:
    raise ValueError("non-standard JSON number " + name)
