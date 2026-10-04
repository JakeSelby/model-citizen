/** The spend report is `citizen usage --json` verbatim; nothing here sums, prices or re-derives. */

export const GROUPINGS = [
  { value: "day", label: "Day" },
  { value: "model", label: "Model" },
  { value: "role", label: "Role (subagents and workers)" },
  { value: "repo", label: "Repository" },
  { value: "session", label: "Session" },
  { value: "rebuild", label: "Cache rebuilds by cause" },
] as const;

export const WINDOWS = [7, 30, 90, 365] as const;

export type Grouping = (typeof GROUPINGS)[number]["value"];

export type TokenField = "input" | "output" | "cache_read" | "cache_write";

export type UsageGroup = {
  name: string;
  runs: number;
  tokens: Record<TokenField, number | null>;
  unknown_token_runs: Record<TokenField, number>;
  cache_hit_rate: number | null;
  cache_hit_denominator: number | null;
  usd: number | null;
  unpriced_runs: number;
};

export type RoleGroup = {
  name: string;
  runs: number;
  output: { p50: number | null; p75: number | null; p90: number | null };
  usd: { p50: number | null; p75: number | null };
  tools: { p50: number | null; p75: number | null; p90: number | null };
  unknown_output_runs: number;
  unpriced_runs: number;
  unmeasured_tool_calls: number;
  sample_sufficient: boolean;
};

export type RebuildCause = {
  cause: string;
  breaks: number;
  rewritten_tokens: number;
  unpriced_breaks: number;
  excess_usd: number | null;
  cost_per_break: number | null;
};

export type RebuildGroup = {
  scope: string;
  sessions: number;
  calls: number;
  priced_spend_usd: number;
  unpriced_calls: number;
  unpriced_breaks: number;
  unknown_breaks: number;
  causes: RebuildCause[];
};

type LedgerCommon = {
  schema_version: 1;
  days: number;
  cost_basis?: string;
  price_as_of?: string | null;
};

export type UsageLedger = LedgerCommon & {
  report: "usage";
  by: "day" | "model" | "repo" | "session";
  groups: UsageGroup[];
  totals: UsageGroup;
  unpriced: number;
  unknown_metrics: string[];
  raw_vs_deduped: { ratio: number | null; runs: number; unknown_runs: number };
  price_table: boolean;
};

export type RoleLedger = LedgerCommon & {
  report: "roles";
  by: "role";
  groups: RoleGroup[];
  unpriced: number;
  workflow_runs: number;
  price_table: boolean;
};

export type RebuildLedger = LedgerCommon & {
  report: "rebuild";
  by: "rebuild";
  groups: RebuildGroup[];
  unpriced: number;
  unpriced_calls: number;
};

export type SpendReport = {
  schema_version: 1;
  by: Grouping;
  days: number;
  command: string;
  basis: { label: string; cost_basis: string | null; price_as_of: string | null };
  ledger: UsageLedger | RoleLedger | RebuildLedger;
};

/** Rows per page in a grouping table; the ledger's total row is shown on every page. */
export const PAGE_SIZE = 50;

/** One page of rows, clamped, so a year of sessions never renders thousands of rows at once. */
export function pageOf<Row>(rows: Row[], page: number): { rows: Row[]; page: number; pages: number } {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(Math.max(page, 0), pages - 1);
  return { rows: rows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE), page: current, pages };
}

/** The CLI's `price_as_of` in words: a date, "unknown", or nothing priced from the table. */
export function pricingDate(basis: SpendReport["basis"]): string {
  if (basis.price_as_of === null) return "no figure priced from the price table";
  if (basis.price_as_of === "unknown") return "pricing date unknown";
  return `prices as of ${basis.price_as_of}`;
}

/** The label every dollar figure carries: what it is, and which price snapshot made it. */
export function moneyLabel(basis: SpendReport["basis"]): string {
  return `${basis.label}, ${pricingDate(basis)}`;
}

/** A recorded dollar figure as text; `null` stays unpriced and never reads as zero. */
export function formatUsd(value: number | null): string {
  if (value === null) return "unpriced";
  const digits = value !== 0 && Math.abs(value) < 1 ? 4 : 2;
  return `$${value.toFixed(digits)}`;
}

export function formatCount(value: number | null): string {
  return value === null ? "unknown" : value.toLocaleString("en-US");
}

export function formatShare(value: number | null): string {
  return value === null ? "unknown" : `${Math.round(value * 100)}%`;
}

/** What the ledger left partial, in its own words, for the header and the footer alike. */
export function partialNotes(ledger: SpendReport["ledger"]): string[] {
  const notes: string[] = [];
  if (ledger.report === "usage") {
    if (ledger.unpriced) notes.push(`${ledger.unpriced} run(s) unpriced: unknown model or partial tokens.`);
    if (ledger.unknown_metrics.length) notes.push(`Unavailable metrics are excluded: ${ledger.unknown_metrics.join(", ")}.`);
  } else if (ledger.report === "roles") {
    if (ledger.unpriced) notes.push(`${ledger.unpriced} run(s) unpriced: unknown model or partial tokens.`);
    if (ledger.workflow_runs) notes.push(`${ledger.workflow_runs} Workflow-tool run(s) are grouped under (workflow), not under any role.`);
  } else {
    if (ledger.unpriced) notes.push(`${ledger.unpriced} rebuild(s) unpriced: their excess dollars are unknown.`);
    if (ledger.unpriced_calls) notes.push(`${ledger.unpriced_calls} call(s) unpriced: priced spend excludes them.`);
  }
  if (!("price_table" in ledger) || ledger.price_table !== false) return notes;
  return [...notes, "No price table was readable, so no figure is priced."];
}
