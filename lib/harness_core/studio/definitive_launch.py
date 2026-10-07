"""Launching the definitive evaluation (#1175) through the replay path, behind its registered budget.

The run's design and budget are the engine's to register (#1187). The budget comes from one
injectable provider, `engine_budget`, which answers `{per_run_usd, whole_run_cap_usd}` for a
resolved request or None. No engine reader exists yet, so in production it answers None and every
definitive launch is refused at preview and at confirm with `definitive_budget_unregistered`.
The Studio parses no plan text and has no default budget.

With a budget, a launch whose per-run budget or spend cap is above it is refused, as is one whose
spend-guard estimate is above the whole-run cap: at preview, and again at start, where the
supervisor re-estimates under its lock (`spend_guard.check_ceiling`). Every other replay is
untouched by this module.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from . import replay, runs, spend_guard

UNREGISTERED = "definitive_budget_unregistered"
EXCEEDED = "definitive_budget_exceeded"
BUDGET_KEYS = frozenset(("per_run_usd", "whole_run_cap_usd"))
BudgetProvider = Callable[[Path, "replay.ReplayRequest"], Optional[Mapping[str, Any]]]


def engine_budget(repository: Path, request: "replay.ReplayRequest") -> Optional[Mapping[str, Any]]:
    """The engine's registered budget for this run. No engine reader exists yet (#1187), so none."""
    return None


class DefinitiveLaunch:
    """Preview, confirm and start one definitive launch over a `replay.ReplayAdmission`."""

    def __init__(self, admission: "replay.ReplayAdmission",
                 budget_provider: BudgetProvider = engine_budget):
        self.admission = admission
        self.budget_provider = budget_provider

    def budget(self, request: "replay.ReplayRequest") -> Dict[str, str]:
        found = self.budget_provider(self.admission.repository, request)
        if found is None:
            raise replay.ReplayRefusal(
                UNREGISTERED, "the engine has no registered budget for this run; the definitive "
                "launch waits on an engine budget reader (#1187)")
        if not isinstance(found, Mapping) or set(found) != BUDGET_KEYS or not all(
                spend_guard.valid_money_text(found[key]) for key in BUDGET_KEYS):
            raise replay.ReplayError("the engine's registered budget is malformed")
        return {key: str(found[key]) for key in BUDGET_KEYS}

    @staticmethod
    def _within(request: "replay.ReplayRequest", budget: Mapping[str, str],
                estimate_usd: Any = None) -> None:
        exceeded = []
        if Decimal(request.max_budget_usd) > Decimal(budget["per_run_usd"]):
            exceeded.append("the per-run budget %s USD is above the registered %s USD"
                            % (request.max_budget_usd, budget["per_run_usd"]))
        if Decimal(request.spend_cap_usd) > Decimal(budget["whole_run_cap_usd"]):
            exceeded.append("the spend cap %s USD is above the registered whole-run cap %s USD"
                            % (request.spend_cap_usd, budget["whole_run_cap_usd"]))
        if (isinstance(estimate_usd, (int, float)) and not isinstance(estimate_usd, bool)
                and Decimal(str(estimate_usd)) > Decimal(budget["whole_run_cap_usd"])):
            exceeded.append("the estimated spend %s USD is above the registered whole-run cap %s USD"
                            % (estimate_usd, budget["whole_run_cap_usd"]))
        if exceeded:
            raise replay.ReplayRefusal(EXCEEDED, "; ".join(exceeded))

    def preview(self, value: Any) -> Dict[str, Any]:
        request = self.admission.resolve(value)
        budget = self.budget(request)
        self._within(request, budget)
        preview = self.admission.preview_resolved(request)
        self._within(request, budget, (preview.get("estimate") or {}).get("amount_usd"))
        return dict(preview, registered_budget=budget)

    def confirm(self, value: Any) -> "replay.ReplayRequest":
        request = self.admission.confirm(value)
        self._within(request, self.budget(request))
        return request

    def start(self, value: Any, confirmation_token: Any) -> Dict[str, Any]:
        request = self.confirm(value)
        budget = self.budget(request)
        launch = replay.launch_payload(request, confirmation_token, self.admission.repository)
        try:
            started = self.admission.supervisor.start(
                launch["suite_id"], launch["parameters"], launch["target_kind"],
                launch["target_ref"], confirmed=launch["confirmed"],
                max_budget_usd=launch["max_budget_usd"], spend_cap_usd=launch["spend_cap_usd"],
                pricing_source=launch["pricing_source"],
                case_identities=launch["case_identities"],
                estimate_ceiling_usd=budget["whole_run_cap_usd"])
        except runs.RunError as exc:
            if "above the ceiling" in str(exc):
                raise replay.ReplayRefusal(EXCEEDED, str(exc)) from exc
            raise replay.ReplayError(str(exc)) from exc
        return {"run_id": started["run_id"], "status": started["status"],
                "targets": [target.as_dict() for target in request.targets]}
