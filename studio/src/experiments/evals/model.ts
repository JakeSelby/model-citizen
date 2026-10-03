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
  default_model: string;
  default_repetitions: number;
  commands: Record<string, string>;
};

export type PaidTierInput = {
  suite: "micro-tier" | "unit-eval";
  target: { kind: string; ref: string };
  unit?: string;
  model?: string;
  repetitions?: number;
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

/** The engine's compact hook matrix: a base cell per row and call, and only the variants that differ. */
export type HookMatrix = {
  schema_version: number;
  runtime: string;
  variants: string[];
  rows: string[];
  calls: string[];
  cells: Record<string, Record<string, Record<string, string>>>;
};

export type HookMatrixResult = { matrix: HookMatrix; moved: string[]; base: string; same: string };

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

/** One cell, read the way the engine's `expand` reads it: a missing entry is `as-dispatcher` throughout. */
export function hookCell(matrix: HookMatrix, row: string, call: string, variant: string, base = "base", same = "as-dispatcher"): string {
  const entry = matrix.cells[row]?.[call] ?? { [base]: same };
  return entry[variant] ?? entry[base];
}

/** Every call of one hook row against every variant, in the engine's order. */
export function hookGrid(matrix: HookMatrix, row: string, base = "base", same = "as-dispatcher"): Array<{ call: string; cells: string[] }> {
  return matrix.calls.map((call) => ({
    call,
    cells: matrix.variants.map((variant) => hookCell(matrix, row, call, variant, base, same)),
  }));
}

/** Every expanded cell as `row|call|variant=cell`, sorted; the parity fixture digests the same lines. */
export function hookCellLines(matrix: HookMatrix): string[] {
  const lines: string[] = [];
  for (const row of matrix.rows) {
    for (const call of matrix.calls) {
      for (const variant of matrix.variants) lines.push(`${row}|${call}|${variant}=${hookCell(matrix, row, call, variant)}`);
    }
  }
  return lines.sort();
}

/** The engine's analysis as path and value lines, every field, through the generic analysis view. */
export function paidAnalysisLines(analysis: PaidAnalysis): Array<[string, string]> {
  if (analysis.result === null) return [["Engine refused", analysis.error ?? "no analysis"]];
  return engineLeaves(analysis.result);
}

export function isHookMatrix(result: EvalRunResult["result"]): result is HookMatrixResult {
  return result !== null && typeof result === "object" && "matrix" in result && "moved" in result;
}

export function isPaidAnalysis(result: EvalRunResult["result"]): result is PaidAnalysis {
  return result !== null && typeof result === "object" && "replay_command" in result;
}

/** The request a paid tier sends; the micro tier pins its tasks, model and reps. */
export function paidInput(suite: PaidTierInput["suite"], form: {
  kind: string; ref: string; unit?: string; model: string; repetitions: number; maxBudget: string; spendCap: string;
}): PaidTierInput {
  const input: PaidTierInput = {
    suite, target: { kind: form.kind, ref: form.ref.trim() },
    max_budget_usd: form.maxBudget.trim(), spend_cap_usd: form.spendCap.trim(),
  };
  if (suite === "unit-eval") Object.assign(input, { unit: form.unit, model: form.model.trim(), repetitions: form.repetitions });
  return input;
}
