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

export type RunArtifact = {
  id: string;
  label: string;
  available?: boolean;
  kind?: "html-report";
  href?: string;
};

export type RunDetail = RunSummary & {
  cases: RunCase[];
  reruns: { items: string[]; next_cursor: string | null };
  exact_command: string | null;
  rerun: { available: boolean; reason: string | null };
  artifacts: RunArtifact[];
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

const REPORT_HREF = /^\/api\/runs\/plugin-eval-report\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The same-origin report link an imported plugin eval declares, or null for any other artifact. */
export function reportHref(artifact: RunArtifact): string | null {
  if (artifact.kind !== "html-report" || artifact.available === false) return null;
  return typeof artifact.href === "string" && REPORT_HREF.test(artifact.href) ? artifact.href : null;
}
