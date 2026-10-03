import { engineLeaves } from "../replay/model";

/** One evaluation tier whose engine is in this checkout; an absent engine is not listed. */
export type EvalTier = {
  id: "rule-detection" | "hook-matrix" | "micro-tier" | "unit-eval";
  label: string;
  description: string;
  issue: number;
  cost_class: "free" | "spends_usage";
  target_kinds: string[];
  command: string;
};

export type EvalCatalog = {
  schema_version: number;
  tiers: EvalTier[];
  units: string[];
  unit_model: string;
  commands: Record<string, string>;
};

export type PaidTierInput = {
  suite: "micro-tier" | "unit-eval";
  target: { kind: string; ref: string };
  unit?: string;
  max_budget_usd: string;
  spend_cap_usd: string;
};

export type ResolvedPaidTier = PaidTierInput & { revision: string };

export type EvalPreview = {
  estimate: { amount_usd: number | null; basis?: string; sample_count?: number };
  caps: { max_budget_usd: string; spend_cap_usd: string };
  pricing: { source: string; basis?: string };
  confirmation_required: boolean;
  confirmation_token: string;
  cost_class: string;
  case_identities: string[];
  request: ResolvedPaidTier;
  evidence: "exploratory";
  command: string;
};

/** The hook matrix as the engine expanded it: per hook, each recorded call's verdict under every variant. */
export type HookMatrixResult = {
  runtime: string;
  variants: string[];
  rows: string[];
  calls: string[];
  grid: Record<string, Array<{ call: string; cells: string[] }>>;
  moved: string[];
};

export type PaidAnalysis = {
  command: string[];
  replay_command: string[];
  replay_exit: number;
  summarise_exit?: number;
  spend_usd: number;
  stopped_at_cap: boolean;
  evidence: "exploratory";
  result: Record<string, unknown> | null;
  error: string | null;
};

export type EvalRunResult = {
  schema_version: number;
  run: { run_id: string; status: string; suite: EvalTier["id"] };
  result: HookMatrixResult | PaidAnalysis | Record<string, unknown> | null;
  analysis_error: string | null;
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "timed_out", "orphaned", "capped", "limited"]);

export function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.has(status);
}

export function tierById(catalog: EvalCatalog | null, id: EvalTier["id"]): EvalTier | undefined {
  return catalog?.tiers.find((tier) => tier.id === id);
}

/** The engine's `--unit` form for a library module, or null when no unit eval is offered for it. */
export function unitFor(catalog: EvalCatalog | null, kind: string, name: string): string | null {
  if (!catalog || kind !== "rules" || !tierById(catalog, "unit-eval")) return null;
  const unit = `rules.${name}`;
  return catalog.units.includes(unit) ? unit : null;
}

/** The engine's analysis as path and value lines, every field, through the generic analysis view. */
export function paidAnalysisLines(analysis: PaidAnalysis): Array<[string, string]> {
  if (analysis.result === null) return [["Engine refused", analysis.error ?? "no analysis"]];
  return engineLeaves(analysis.result);
}

export function isHookMatrix(result: EvalRunResult["result"]): result is HookMatrixResult {
  return result !== null && typeof result === "object" && "grid" in result && "moved" in result;
}

export function isPaidAnalysis(result: EvalRunResult["result"]): result is PaidAnalysis {
  return result !== null && typeof result === "object" && "replay_command" in result;
}

/** The request a paid tier sends: a target and caps, and a unit eval's unit; the engine's defaults do the rest. */
export function paidInput(suite: PaidTierInput["suite"], form: {
  kind: string; ref: string; unit?: string; maxBudget: string; spendCap: string;
}): PaidTierInput {
  const input: PaidTierInput = {
    suite, target: { kind: form.kind, ref: form.ref.trim() },
    max_budget_usd: form.maxBudget.trim(), spend_cap_usd: form.spendCap.trim(),
  };
  if (suite === "unit-eval") input.unit = form.unit;
  return input;
}
