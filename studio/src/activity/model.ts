export type ActivityEntry = {
  id: string;
  timestamp: string;
  source: "decision-log" | "studio-action";
  kind: string;
  title: string;
  outcome: string;
  reason: string;
  actor: string;
  session: string;
  repository: string;
  hook: string;
  grade: string;
  command: string;
  draft: string;
  files: string[];
  evidence_href: string;
  evidence_label: string;
  /** The journal id of a Studio apply or rollback, or "". */
  apply_id: string;
  /** The id `citizen draft rollback` takes when this entry can be rolled back, or "". */
  rollback_target: string;
};

export type ActivitySource = {
  id: "decision-log" | "ownership-journal";
  status: "current" | "partial" | "empty" | "failed";
  message: string;
};

export type ActivityFilters = {
  session: string;
  repository: string;
  hook: string;
  outcome: string;
};

export type ActivityPage = {
  schema_version: number;
  entries: ActivityEntry[];
  next_cursor: string;
  next_command: string;
  sources: ActivitySource[];
  filters: ActivityFilters;
  command: string;
};

export const EMPTY_FILTERS: ActivityFilters = {
  session: "",
  repository: "",
  hook: "",
  outcome: "",
};

export function mergeEntries(current: ActivityEntry[], next: ActivityEntry[]): ActivityEntry[] {
  const seen = new Set(current.map(({ id }) => id));
  return [...current, ...next.filter(({ id }) => !seen.has(id))];
}

const SOURCE_SEVERITY: Record<ActivitySource["status"], number> = {
  current: 0,
  empty: 1,
  partial: 2,
  failed: 3,
};

export function mergeSources(
  current: ActivitySource[],
  next: ActivitySource[],
): ActivitySource[] {
  const prior = new Map(current.map((source) => [source.id, source]));
  return next.map((source) => {
    const existing = prior.get(source.id);
    if (!existing) return source;
    return SOURCE_SEVERITY[existing.status] >= SOURCE_SEVERITY[source.status]
      ? existing
      : source;
  });
}

export function continuationLabel(entries: ActivityEntry[]): string {
  return entries.length ? "Load older entries" : "Search older activity";
}

export function activityTone(outcome: string): "success" | "warning" | "danger" | "neutral" {
  if (outcome === "refused" || outcome === "failed") return "danger";
  if (outcome === "confirmation-required" || outcome === "partial") return "warning";
  if (outcome === "allowed" || outcome === "completed") return "success";
  return "neutral";
}

export type ActivityRequest = { generation: number; signal: AbortSignal };

export class ActivityRequestGate {
  private current = 0;
  private controller: AbortController | null = null;

  next(): ActivityRequest {
    this.controller?.abort();
    this.current += 1;
    this.controller = new AbortController();
    return { generation: this.current, signal: this.controller.signal };
  }

  accepts(generation: number): boolean {
    return generation === this.current;
  }

  cancel(generation: number): void {
    if (!this.accepts(generation)) return;
    this.invalidate();
  }

  invalidate(): void {
    this.controller?.abort();
    this.current += 1;
  }
}
