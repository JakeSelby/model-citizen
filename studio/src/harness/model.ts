// The shared Harness page frame: one row per item per setting period on Efficacy, the item's
// configuration on Settings. Rows come from `GET /api/harness/periods?surface=<name>`.
import type { ReactNode } from "react";

/** "log" is a page's own third tab (Hooks' Safety log); a page without one shows Efficacy for it. */
export type HarnessTab = "efficacy" | "settings" | "log";

export type PeriodRow = {
  item_id: string;
  item_label: string;
  setting_label: string;
  period: { start: string; end: string | null };
  status: "measured" | "measuring";
  measuring_day?: number;
  window_days: 7;
  metrics: Record<string, number | string>;
  evals_href?: string;
};

export type PeriodsResponse = { surface: string; rows: PeriodRow[]; note?: string };

export type MetricColumn = {
  key: string;
  heading: string;
  /** Renders the cell instead of `metricText`, for a merged or badged value. */
  render?: (row: PeriodRow) => ReactNode;
  /** Overrides the alignment `isNumericColumn` infers from the rows. */
  align?: "left" | "right";
};

export type SettingField = { key: string; heading: string };

export type SettingRow = {
  item_id: string;
  item_label: string;
  item_detail?: string;
  /** The full text when `item_detail` is shortened, shown on hover. */
  item_detail_title?: string;
  values: Record<string, string>;
  /** A field with options is editable; one without is shown read-only. */
  options?: Record<string, string[]>;
  last_changed: string | null;
  last_change_note?: string;
};

/** Pending edits by item, then field. */
export type SettingEdits = Record<string, Record<string, string>>;

export type DraftChange = { item_id: string; field: string; from: string; to: string };

export type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | ({ state: "ready" } & T);

export const DEFAULT_EVALS_HREF = "/evals";

export function parseTab(search: string | URLSearchParams): HarnessTab {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const tab = params.get("tab");
  return tab === "settings" || tab === "log" ? tab : "efficacy";
}

/**
 * The query string for `tab`, keeping every other parameter; Efficacy is the bare URL. Leaving
 * the log drops `?apply=`, the log entry a link focused, so it cannot reopen the log.
 */
export function tabSearch(search: string | URLSearchParams, tab: HarnessTab): URLSearchParams {
  const next = new URLSearchParams(search);
  if (tab === "efficacy") next.delete("tab");
  else next.set("tab", tab);
  if (tab !== "log") next.delete("apply");
  return next;
}

function toDate(value: string): Date {
  return new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
}

export function shortDate(value: string): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function periodText(period: PeriodRow["period"]): string {
  return period.end ? `${shortDate(period.start)} to ${shortDate(period.end)}` : `since ${shortDate(period.start)}`;
}

export function measuringText(row: PeriodRow): string {
  if (row.status !== "measuring") return "measured";
  return typeof row.measuring_day === "number"
    ? `measuring, day ${row.measuring_day} of ${row.window_days}`
    : `measuring, ${row.window_days}-day window`;
}

/** A metric ending in `_share` or `_rate` is a 0 to 1 fraction. */
export function isShareMetric(key: string): boolean {
  return /_(share|rate)$/.test(key);
}

/** A share or rate reads as a percentage with one decimal; any other number gets separators. */
export function metricText(value: number | string | undefined, key = ""): string {
  if (value === undefined) return "not measured";
  if (typeof value !== "number") return value;
  if (isShareMetric(key)) {
    return `${(value * 100).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
  }
  return value.toLocaleString("en-US");
}

const FIGURE = String.raw`[-+~≈$€£]?\d[\d.,]*(%|[kKmM])?( \([^)]*\))?`;
const NUMERIC = new RegExp(`^${FIGURE}( · ${FIGURE})*$`);
/** Metric texts that mean "no figure": the table shows a dash, and says the text to a screen reader. */
export const NO_VALUE: ReadonlySet<string> = new Set(["", "–", "not measured", "not recorded", "no detector", "no measured session"]);

/** A column reads as figures, right-aligned, when every value it holds is a number or a figure. */
export function isNumericColumn(rows: PeriodRow[], key: string): boolean {
  let seen = false;
  for (const row of rows) {
    const value = row.metrics[key];
    if (value === undefined || (typeof value === "string" && NO_VALUE.has(value))) continue;
    if (typeof value === "string" && !NUMERIC.test(value)) return false;
    seen = true;
  }
  return seen;
}

/** Metric columns in first-seen order when a page names none. */
export function metricColumns(rows: PeriodRow[]): MetricColumn[] {
  const keys: string[] = [];
  for (const row of rows) for (const key of Object.keys(row.metrics)) if (!keys.includes(key)) keys.push(key);
  return keys.map((key) => ({ key, heading: key.replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase()) }));
}

/**
 * Rows grouped by item in first-seen order, each group's periods oldest first, so a changed
 * item's before row sits directly above its measuring row.
 */
export function groupRows(rows: PeriodRow[]): Array<{ item_id: string; item_label: string; rows: PeriodRow[] }> {
  const groups = new Map<string, { item_id: string; item_label: string; rows: PeriodRow[] }>();
  for (const row of rows) {
    const group = groups.get(row.item_id) ?? { item_id: row.item_id, item_label: row.item_label, rows: [] };
    group.rows.push(row);
    groups.set(row.item_id, group);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    rows: [...group.rows].sort((a, b) => toDate(a.period.start).getTime() - toDate(b.period.start).getTime()),
  }));
}

/** "Before" for a period followed by a measuring one, "After" for the measuring one, else none. */
export function periodRole(rows: PeriodRow[], index: number): "Before" | "After" | null {
  if (rows[index].status === "measuring") return rows.length > 1 ? "After" : null;
  return rows[index + 1]?.status === "measuring" ? "Before" : null;
}

export function efficacySummary(rows: PeriodRow[]): string {
  const items = new Set(rows.map((row) => row.item_id)).size;
  const measuring = rows.filter((row) => row.status === "measuring").length;
  return `${items} ${items === 1 ? "item" : "items"} · ${measuring} measuring`;
}

export function settingsSummary(rows: SettingRow[]): string {
  const latest = rows.map((row) => row.last_changed).filter((value): value is string => Boolean(value))
    .sort((a, b) => toDate(b).getTime() - toDate(a).getTime())[0];
  const count = `${rows.length} ${rows.length === 1 ? "item" : "items"}`;
  return latest ? `${count} · last change ${shortDate(latest)}` : count;
}

export function settingValue(row: SettingRow, field: string, edits: SettingEdits): string {
  return edits[row.item_id]?.[field] ?? row.values[field] ?? "";
}

/** Only edits that differ from the saved value become draft changes. */
export function draftChanges(rows: SettingRow[], edits: SettingEdits): DraftChange[] {
  const changes: DraftChange[] = [];
  for (const row of rows) {
    for (const [field, to] of Object.entries(edits[row.item_id] ?? {})) {
      const from = row.values[field] ?? "";
      if (to !== from) changes.push({ item_id: row.item_id, field, from, to });
    }
  }
  return changes;
}

export function withEdit(edits: SettingEdits, itemId: string, field: string, value: string): SettingEdits {
  return { ...edits, [itemId]: { ...edits[itemId], [field]: value } };
}
