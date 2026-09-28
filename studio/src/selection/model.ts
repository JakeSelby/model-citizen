export type Provenance = {
  source: string;
  value: unknown;
  source_file: string;
  saved: boolean;
};

export type SelectionRow = Provenance & {
  unit: string;
  overridden: Provenance[];
};

export type SelectionGroup = { kind: string; rows: SelectionRow[] };

export type Budget = {
  runtime: string;
  label: string;
  managed: boolean;
  used_tokens: number;
  token_cap: number;
  used_lines: number;
  line_cap: number;
  selected_lines: number;
};

export type SelectionReport = {
  schema_version: number;
  repository: string;
  project_file: string;
  selection: Record<string, unknown>;
  mode: Provenance & { overridden: Provenance[] };
  groups: SelectionGroup[];
  budgets: Budget[];
  commands: { selection: string; budget: string };
};

export function budgetPercent(budget: Budget): number {
  if (budget.token_cap <= 0) return 0;
  return Math.min(100, Math.max(0, budget.used_tokens / budget.token_cap * 100));
}

export function sourceLabel(provenance: Provenance): string {
  if (!provenance.saved) return "session environment";
  if (provenance.source.startsWith("mode:")) return provenance.source.replace(":", " / ");
  return provenance.source;
}

export function selectedCount(report: SelectionReport): number {
  return report.groups.reduce((total, group) => total + group.rows.length, 0);
}

export class SelectionRequestGate {
  private current = 0;

  next(): number {
    this.current += 1;
    return this.current;
  }

  accepts(generation: number): boolean {
    return generation === this.current;
  }
}
