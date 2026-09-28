export type RunCase = {
  id: string;
  outcome: "passed" | "failed" | "skipped" | "unknown";
  commit: string | null;
  flaky: boolean;
};

export type RunSummary = {
  run_id: string;
  suite_id: string;
  status: string;
  created_at: string | null;
  completed_at: string | null;
  target: { kind: string | null; ref: string | null; commit: string | null };
  cost_usd: number | null;
  duration_ms: number | null;
  rerun_of: string | null;
  case_count: number;
  flaky_count: number;
};

export type HistoryFilters = {
  suite_id: string | null;
  target: string | null;
  status: string | null;
  created_from: string | null;
  created_to: string | null;
  min_cost_usd: number | null;
  max_cost_usd: number | null;
  min_duration_ms: number | null;
  max_duration_ms: number | null;
};

export type HistoryPage = { items: RunSummary[]; next_cursor: string | null };

export type RunDetail = RunSummary & {
  cases: RunCase[];
  reruns: { items: string[]; next_cursor: string | null };
  exact_command: string | null;
  rerun: { available: boolean; reason: string | null };
  artifacts: Array<{ id: string; label: string; available?: boolean }>;
};

export type CaseHistory = {
  case_id: string;
  items: Array<RunSummary & { case: RunCase | null }>;
  next_cursor: string | null;
};

export const emptyFilters: HistoryFilters = {
  suite_id: null, target: null, status: null, created_from: null, created_to: null,
  min_cost_usd: null, max_cost_usd: null, min_duration_ms: null, max_duration_ms: null,
};

export function displayUnknown(value: string | number | null, suffix = ""): string {
  return value === null ? "Unknown" : `${value}${suffix}`;
}
