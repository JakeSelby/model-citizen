import type { Line, Point } from "../reports/trends/model";

/** The shapes `GET /api/evals/*` answer with; `lib/harness_core/studio/evals.py` builds them. */
export type Preregistration = {
  path: string; file: string; title: string; date: string | null; status: string | null;
  question: string | null; deviations: number; label: "pre-registered";
};
export type Preregistrations = { schema_version: number; directory: string; items: Preregistration[] };

export type QualificationClient = {
  id: string; runtime: string | null; client: string | null; platform: string | null; status: string;
  required_for_release: boolean; runtime_version: string | null; client_version: string | null;
  records: Array<{ path: string; sha256: string }>;
};
export type Qualification = {
  schema_version: number; state: "ready" | "unavailable"; reason: string | null; harness_version: string | null;
  qualification_source: "current" | "drifted" | null; required_cases: string[]; clients: QualificationClient[];
  limitations: string[];
};

/** Every observational answer carries these, and nothing else marks it. */
export type ObservationalLabel = { label: "observational"; standard_label: "exploratory"; note: string };

export type Side = {
  setting: string; period: { start: string; end: string | null }; status: "measured" | "measuring";
  metrics: Record<string, number | string>; measuring_day?: number;
};
export type MetricChange = { key: string; before: number | string | null; after: number | string | null; delta: number | null };
export type Boundary = {
  id: string; surface: string; item_id: string; item_label: string; at: string; source: string;
  from: string | null; to: string; before: Side; after: Side; changes: MetricChange[]; href: string;
};
export type BeforeAfter = ObservationalLabel & {
  schema_version: number; days: number; window_days: number; surfaces: Record<string, "ok" | "error">; boundaries: Boundary[];
};

export type ContextSummary = { calls: number; median_context: number | null; mean_context: number | null; over_warn_share: number | null };
export type AutoTrim = ObservationalLabel & {
  schema_version: number; days: number; context_warn: number; state: "ready" | "empty" | "unavailable"; reason: string | null;
  sessions_with_reset: number; sessions_without_reset: number; before: ContextSummary; after: ContextSummary;
  median_change: number | null;
  sessions: Array<{ session_id: string; reset_at: string; before: ContextSummary; after: ContextSummary }>;
};

export type EvalsTab = "experiments" | "before-after" | "qualification";
export const OBSERVATIONAL_BADGE = "Observational · exploratory";

/** The tab in the URL; a Harness page's `?surface=&item=` link opens Before and after. */
export function parseEvalsTab(search: string | URLSearchParams): EvalsTab {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const tab = params.get("tab");
  if (tab === "experiments" || tab === "before-after" || tab === "qualification") return tab;
  return params.has("surface") || params.has("item") ? "before-after" : "experiments";
}

/** Boundaries narrowed to the surface and item a deep link names. */
export function filterBoundaries(boundaries: Boundary[], surface: string | null, item: string | null): Boundary[] {
  return boundaries.filter((b) => (!surface || b.surface === surface) && (!item || b.item_id === item));
}

function sameFile(registered: string, path: string): boolean {
  const name = (value: string) => value.split("/").pop() ?? value;
  return registered === path || name(registered) === name(path);
}

/** The trend points a pre-registration governs: only those the engine itself labelled pre-registered. */
export function registeredResults(item: Preregistration, lines: Line[]): Point[] {
  return lines.flatMap((line) => line.points)
    .filter((point) => point.evidence.label === "pre-registered" && point.evidence.pre_registration
      && sameFile(point.evidence.pre_registration, item.path))
    .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));
}

export function resultText(points: Point[]): string {
  if (!points.length) return "No result yet";
  const latest = points[0];
  return `${points.length} run${points.length === 1 ? "" : "s"}, latest ${latest.date ?? "undated"}`;
}

export function tokensText(value: number | null): string {
  if (value === null) return "–";
  return value >= 1000 ? `${Math.round(value / 1000).toLocaleString("en-US")}k` : value.toLocaleString("en-US");
}

export function shareText(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
}

export function changeText(change: MetricChange): string {
  if (change.delta === null) return "–";
  const sign = change.delta > 0 ? "+" : "";
  return /_(share|rate)$/.test(change.key) ? `${sign}${(change.delta * 100).toFixed(1)} pts` : `${sign}${change.delta.toLocaleString("en-US")}`;
}

export function statusTone(status: string): "teal" | "gray" | "orange" {
  return status === "qualified" ? "teal" : status === "planned" ? "gray" : "orange";
}
