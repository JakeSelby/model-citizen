# SPDX-License-Identifier: MIT
"""Sources: the harness version each session ran under, read from usage rows."""
from __future__ import annotations

from typing import Any, Dict

from .. import periods


def items(ctx: "periods.Context") -> Dict[str, Dict[str, Any]]:
    observations = [(row["_ts"], str(row["harness_version"]), periods.stream_of(row))
                    for row in ctx.usage if row.get("harness_version")]
    if not observations:
        return {}
    return {"harness_version": {"label": "Harness version", "observations": observations,
                                "current": None}}


periods.register_surface("sources", items, periods.usage_metrics)
