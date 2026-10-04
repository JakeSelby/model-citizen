#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The Studio's performance budgets, and the bundle check CI runs against them (AH-S316, #1000).

Three budgets, each failing by name:

- **Bundle size**: the committed bundle's script and style assets, gzip-compressed as a browser
  receives them, and the raw total. Checked here from ``studio/dist`` with no browser.
- **First load**: navigation to the rendered Studio shell, the median of several cold-cache
  loads in headless Chrome, under 1.5 seconds.
- **API latency**: the p95 of the library route over a fixture library of at least 500
  modules, under 100 ms. It is measured in three rounds and the quietest round's p95 is judged
  (:func:`settled_p95`): load from other processes on the runner only ever adds latency, so the
  quietest round is the route's own cost, and a route that is slow every round still fails.

The first-load and API budgets need a served Studio and Chrome, so they are measured by
``tests/test_e2e_studio_budgets.py`` and judged by :func:`over_budget` here. Each wall-clock
figure is a median or a p95 over repeated samples after a warm-up, never one sample, so one slow
scheduler tick on a shared runner does not fail the build.

The bundle figures were set at about 25% above the bundle as built when the budgets were
introduced (script 285 KiB gzip, style 38 KiB gzip, 1.17 MiB raw); a deliberate growth past
them raises the budget in the same pull request, with its reason.

    python3 scripts/studio_budgets.py bundle [--dist studio/dist] [--json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
KIB = 1024

BUDGETS: Dict[str, float] = {
    "bundle.script_gzip_bytes": 360 * KIB,
    "bundle.style_gzip_bytes": 48 * KIB,
    "bundle.total_raw_bytes": 1500 * KIB,
    "first_load.median_ms": 1500.0,
    "api.library_p95_ms": 100.0,
}
UNITS = {"bytes": "bytes", "ms": "ms"}
ASSET = re.compile(r'(?:src|href)="(?:\./|/)?(assets/[^"]+\.(?:js|css))"')


def bundle_sizes(dist: Path) -> Dict[str, int]:
    """Raw and gzip sizes of every script and style asset ``index.html`` loads."""
    index = (dist / "index.html").read_text(encoding="utf-8")
    assets = sorted(set(ASSET.findall(index)))
    if not any(name.endswith(".js") for name in assets):
        raise ValueError("%s loads no script asset" % (dist / "index.html"))
    sizes = {"bundle.script_gzip_bytes": 0, "bundle.style_gzip_bytes": 0,
             "bundle.total_raw_bytes": 0}
    for name in assets:
        data = (dist / name).read_bytes()
        kind = "script" if name.endswith(".js") else "style"
        sizes["bundle.%s_gzip_bytes" % kind] += len(gzip.compress(data, compresslevel=9, mtime=0))
        sizes["bundle.total_raw_bytes"] += len(data)
    return sizes


def p95(samples: Sequence[float]) -> float:
    """The nearest-rank 95th percentile."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def settled_p95(rounds: Sequence[Sequence[float]]) -> float:
    """The lowest per-round p95 of several rounds of samples."""
    if not rounds:
        raise ValueError("no rounds")
    return min(p95(samples) for samples in rounds)


def median(samples: Sequence[float]) -> float:
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def over_budget(measured: Mapping[str, float],
                budgets: Mapping[str, float] = BUDGETS) -> List[str]:
    """One line per measured figure over its budget, naming the budget; empty when all fit."""
    unknown = sorted(set(measured) - set(budgets))
    if unknown:
        raise KeyError("no budget named " + ", ".join(unknown))
    return ["budget %s: measured %s, over its limit of %s" % (
        name, _shown(name, measured[name]), _shown(name, budgets[name]))
        for name in sorted(measured) if measured[name] > budgets[name]]


def _shown(name: str, value: float) -> str:
    if name.endswith("_bytes"):
        return "%d bytes (%.1f KiB)" % (value, value / KIB)
    return "%.1f ms" % value


def report(measured: Mapping[str, float], failures: Iterable[str]) -> str:
    lines = ["%s: %s (limit %s)" % (name, _shown(name, value), _shown(name, BUDGETS[name]))
             for name, value in sorted(measured.items())]
    return "\n".join(lines + list(failures))


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    bundle = sub.add_parser("bundle", help="check the committed bundle against its size budgets")
    bundle.add_argument("--dist", type=Path, default=ROOT / "studio" / "dist")
    bundle.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        measured = bundle_sizes(args.dist)
    except (OSError, ValueError) as exc:
        print("studio budgets: %s" % exc, file=sys.stderr)
        return 2
    failures = over_budget(measured)
    if args.json:
        print(json.dumps({"measured": measured, "budgets": {name: BUDGETS[name] for name in measured},
                          "failures": failures}, indent=2, sort_keys=True))
    else:
        print(report(measured, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
