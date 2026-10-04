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
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

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
# The words the page shows for each `cost_basis` the CLI can report. A basis not listed here is
# shown as unknown, never as a list price it may not be.
LABELS = {"list_price_equivalent": "list-price equivalent"}
UNKNOWN_BASIS = "unknown basis"
# One child per grouping: a second request for a grouping already being read waits this long for
# it, then is refused, so switching groupings back and forth cannot stack transcript scans.
SLOT_WAIT = 5.0
POLL = 0.05
_SLOTS = {by: threading.Lock() for by in GROUPINGS}


class SpendError(ValueError):
    """The request names no grouping or window the ledger reports."""


class SpendUnavailable(RuntimeError):
    """`citizen usage --json` did not answer with a usage document."""


class SpendBusy(RuntimeError):
    """A report for this grouping is already being read."""


class SpendCancelled(RuntimeError):
    """The client went away; its child was stopped and nothing is answered."""


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


def _stop(child: "subprocess.Popen[bytes]") -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        child.kill()
    child.wait()


def _run(argv, repo_root: Path, environment: Dict[str, str], timeout: float,
         cancelled: Callable[[], bool]) -> Tuple[int, str]:
    """Run one child to completion, stopping it at the deadline or when the client leaves."""
    # A file rather than a pipe, so a large document cannot fill a pipe the poll loop is not reading.
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(argv, cwd=str(repo_root), env=environment, stdout=output,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        while True:
            try:
                child.wait(timeout=POLL)
                break
            except subprocess.TimeoutExpired:
                if cancelled():
                    _stop(child)
                    raise SpendCancelled("the client went away")
                if time.monotonic() > deadline:
                    _stop(child)
                    raise SpendUnavailable("citizen usage did not finish in time")
        output.seek(0)
        return child.returncode, output.read().decode("utf-8", "replace")


def report(repo_root: Path, by: str, days: int,
           cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Run the CLI's own report and wrap its document, unchanged, with its provenance.

    `cancelled` is polled while the child runs; when it answers true the child is killed and
    `SpendCancelled` raised. At most one child runs per grouping (`SpendBusy` past `SLOT_WAIT`).
    """
    argv = [sys.executable, str(Path(repo_root) / "bin" / "harness")] + list(command(by, days)[1:])
    # HARNESS_QUIET silences the CLI's stdout, which is where the JSON document is written.
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    slot = _SLOTS[by]
    if not slot.acquire(timeout=SLOT_WAIT):
        raise SpendBusy("a %s report is already being read" % by)
    try:
        code, stdout = _run(argv, repo_root, environment, TIMEOUTS.get(by, DEFAULT_TIMEOUT),
                            cancelled or (lambda: False))
    except OSError as exc:
        raise SpendUnavailable("citizen usage did not start") from exc
    finally:
        slot.release()
    if code != 0:
        raise SpendUnavailable("citizen usage exited %d" % code)
    try:
        document = json.loads(stdout, parse_constant=_refuse_constant)
    except ValueError as exc:
        raise SpendUnavailable("citizen usage did not print one JSON document") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or document.get("report") != REPORTS[by] or document.get("by") != by
            or document.get("days") != days or not isinstance(document.get("groups"), list)):
        raise SpendUnavailable("citizen usage answered with a different report")
    as_of = document.get("price_as_of")
    basis = document.get("cost_basis")
    return {
        "schema_version": SCHEMA_VERSION,
        "by": by,
        "days": days,
        "command": " ".join(command(by, days)),
        "basis": {"label": LABELS.get(basis, UNKNOWN_BASIS) if isinstance(basis, str)
                  else UNKNOWN_BASIS,
                  "cost_basis": basis if isinstance(basis, str) else None,
                  "price_as_of": as_of if isinstance(as_of, str) and as_of else None},
        "ledger": document,
    }


def _refuse_constant(name: str) -> Any:
    raise ValueError("non-standard JSON number " + name)
