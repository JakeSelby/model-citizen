// The Skills page's columns, its lead summary and its Settings rows. Efficacy rows come from
// `GET /api/harness/periods?surface=skills`; the lead and the index state from
// `GET /api/harness/skills`. Both are observational readings of the user's own transcripts.
// Settings are read from the module library (`GET /api/library`): only the load switch saves to
// a draft, as a `skills.<name>` selection change.

import { useQuery } from "@tanstack/react-query";

import type { DraftChange, MetricColumn, PeriodRow, SettingField, SettingRow } from "../../harness/model";
import type { LibraryModule, LibraryPayload } from "../../library/model";

export type SkillUse = "invoked" | "loaded, never invoked" | "not loaded";

export type SkillSummaryRow = {
  skill: string;
  loaded_sessions: number;
  invoked_sessions: number;
  invocations: number;
  use: SkillUse;
};

export type SkillSummary = {
  window_days: number;
  sessions: number;
  indexing: boolean;
  pending: number;
  basis: string;
  skills: SkillSummaryRow[];
};

export const NEVER_INVOKED: SkillUse = "loaded, never invoked";

export async function loadSkillSummary(signal?: AbortSignal): Promise<SkillSummary> {
  const response = await fetch("/api/harness/skills", { method: "GET", credentials: "same-origin", signal });
  let body: (SkillSummary & { error?: string }) | null = null;
  try {
    body = await response.json() as SkillSummary & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body || !Array.isArray(body.skills)) throw new Error("The skills response has no skills.");
  return body;
}

/** While the index warms, the summary is read again every few seconds until it is complete. */
export const INDEXING_POLL_MS = 3_000;

export const skillSummaryQuery = {
  queryKey: ["harness-skills-summary"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadSkillSummary(signal),
  staleTime: 30_000,
};

export function summaryPoll(data: SkillSummary | undefined): number | false {
  return data?.indexing ? INDEXING_POLL_MS : false;
}

export function useSkillSummary() {
  return useQuery({ ...skillSummaryQuery, refetchInterval: (query) => summaryPoll(query.state.data) });
}

/** Each skill's description tokens, a soft estimate, read from the Settings rows. */
export function tokensByName(rows: SettingRow[] | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const row of rows ?? []) {
    const tokens = Number(row.values.tokens);
    if (Number.isFinite(tokens)) out.set(row.item_id, tokens);
  }
  return out;
}

export type LeadFigures = {
  invoked: number;
  loaded: number;
  never: SkillSummaryRow[];
  /** Description tokens a session spends on the skills invoked in none; undefined unmeasured. */
  neverTokens?: number;
  /** Description tokens a session spends on every loaded skill; undefined unmeasured. */
  loadedTokens?: number;
};

function tokenSum(rows: SkillSummaryRow[], tokens: Map<string, number>): number | undefined {
  if (!tokens.size || !rows.length) return undefined;
  return rows.reduce((sum, row) => sum + (tokens.get(row.skill) ?? 0), 0);
}

/** The lead's figures: skills invoked of those loaded, loaded-never-invoked, and their tokens. */
export function leadFigures(summary: SkillSummary, tokens: Map<string, number> = new Map()): LeadFigures {
  const loaded = summary.skills.filter((row) => row.loaded_sessions > 0);
  const never = loaded.filter((row) => row.use === NEVER_INVOKED);
  return {
    invoked: loaded.filter((row) => row.invoked_sessions > 0).length,
    loaded: loaded.length,
    never,
    neverTokens: tokenSum(never, tokens),
    loadedTokens: tokenSum(loaded, tokens),
  };
}

/** The lead's one sentence: how many loaded skills no session invoked. */
export function leadHeadline(summary: SkillSummary, figures: LeadFigures): string {
  const days = `the last ${summary.window_days} days`;
  if (!figures.loaded) return `No session in ${days} listed a skill.`;
  if (!figures.never.length) return `Every skill loaded in ${days} was invoked at least once.`;
  const skills = figures.never.length === 1 ? "skill" : "skills";
  return `${figures.never.length} of ${figures.loaded} loaded ${skills} were invoked in no session in ${days}.`;
}

export function indexingText(summary: SkillSummary): string {
  if (!summary.indexing) return "";
  const pending = summary.pending === 1 ? "1 transcript" : `${summary.pending} transcripts`;
  return summary.pending
    ? `Still indexing your transcripts: ${pending} not read yet, so these counts are short until it finishes.`
    : "Still indexing your transcripts, so these counts may be short until it finishes.";
}

/** "2 of 5 sessions": invoked in, of the sessions that loaded it. */
export function invokedText(row: PeriodRow): string {
  const invoked = row.metrics.invoked_in;
  const loaded = row.metrics.loaded_in;
  if (typeof invoked !== "number") return "not measured";
  return typeof loaded === "number" && loaded > 0 ? `${invoked} of ${loaded}` : String(invoked);
}

/** The switch a period ran under, in the words of the Settings tab. */
export function loadLabel(value: string): string {
  if (value === "off") return "Off";
  if (value === "on" || value === "default") return "Always";
  return value;
}

/** The use a period's figures add up to; the API sends it as `use`. */
export function useOf(row: PeriodRow): string {
  return typeof row.metrics.use === "string" ? row.metrics.use : "not measured";
}

const USE_RANK: Record<string, number> = { invoked: 0, [NEVER_INVOKED]: 1, "not loaded": 2 };

/**
 * Invoked skills first, then those loaded and never invoked, then the rest, each by name; an item
 * ranks by its newest period. The switch reads as the Settings tab names it: Always or Off.
 */
export function presentRows(rows: PeriodRow[]): PeriodRow[] {
  const newest = new Map<string, PeriodRow>();
  for (const row of rows) {
    const seen = newest.get(row.item_id);
    if (!seen || row.period.start > seen.period.start) newest.set(row.item_id, row);
  }
  const rank = (row: PeriodRow) => USE_RANK[useOf(newest.get(row.item_id) ?? row)] ?? 3;
  return rows.map((row) => ({ ...row, setting_label: loadLabel(row.setting_label) }))
    .sort((a, b) => rank(a) - rank(b) || a.item_label.localeCompare(b.item_label));
}

/** The share of loading sessions that invoked the skill, 0 to 1; undefined when none loaded it. */
export function invokedShare(row: PeriodRow): number | undefined {
  const invoked = row.metrics.invoked_in;
  const loaded = row.metrics.loaded_in;
  return typeof invoked === "number" && typeof loaded === "number" && loaded > 0 ? Math.min(1, invoked / loaded) : undefined;
}

export const SKILL_METRICS: MetricColumn[] = [
  { key: "sessions", heading: "Sessions" },
  { key: "invocations", heading: "Invocations" },
];

// --- Settings ----------------------------------------------------------------------------------

export const LOAD_OPTIONS = ["Always", "Off"];

export const SKILL_FIELDS: SettingField[] = [
  { key: "load", heading: "Load" },
  { key: "source", heading: "Source" },
  { key: "tokens", heading: "Description tokens" },
];

/** A description longer than this is cut at a word and shown whole on hover. */
export const DETAIL_CHARS = 60;

/** Said once under the Skills settings, not on every row. */
export const SKILL_READ_ONLY_NOTE = "Only Load saves to a draft, as Always or Off; the harness has no on-demand or per-repository skill setting yet. Source and description tokens, a soft estimate, are read-only.";

function core(modules: LibraryModule[]): LibraryModule[] {
  const seen = new Set<string>();
  return modules.filter((module) => module.kind === "skills" && !seen.has(module.name) && Boolean(seen.add(module.name)))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function description(module: LibraryModule): string | undefined {
  const header = /^---\n([\s\S]*?)\n---/.exec(module.source.text)?.[1] ?? "";
  const line = header.split("\n").find((entry) => entry.startsWith("description:"));
  const text = line ? line.slice("description:".length).trim().replace(/^["']|["']$/g, "") : "";
  return text ? text.split(". ")[0].replace(/\.$/, "") : undefined;
}

/** The description's first sentence, cut at a word to one line; the whole sentence is the title. */
export function shortDetail(text: string | undefined): { detail?: string; title?: string } {
  if (!text || text.length <= DETAIL_CHARS) return { detail: text };
  const cut = text.slice(0, DETAIL_CHARS + 1);
  const end = cut.lastIndexOf(" ");
  return { detail: `${cut.slice(0, end > 0 ? end : DETAIL_CHARS).replace(/[,;:—–-]+$/, "")}…`, title: text };
}

export function skillSettings(library: LibraryPayload): SettingRow[] {
  return core(library.modules).map((module): SettingRow => {
    const { detail, title } = shortDetail(description(module));
    return {
      item_id: module.name,
      item_label: module.name,
      item_detail: detail,
      item_detail_title: title,
      values: {
        load: module.state.value === "off" ? "Off" : "Always",
        source: module.root.label,
        tokens: String(module.context_cost.tokens),
      },
      options: module.state.switchable ? { load: LOAD_OPTIONS } : undefined,
      last_changed: null,
      last_change_note: module.state.layer === "default" ? "Default selection" : `Set by ${module.state.layer}`,
    };
  });
}

/**
 * Each switched skill's Settings row dated by the period its switch started, from the Efficacy
 * rows: a skill with more than one period was switched on its newest period's first day.
 */
export function withSwitchDates(rows: SettingRow[], periods: PeriodRow[] | undefined): SettingRow[] {
  const spans = new Map<string, PeriodRow[]>();
  for (const row of periods ?? []) spans.set(row.item_id, [...(spans.get(row.item_id) ?? []), row]);
  return rows.map((row) => {
    const seen = spans.get(row.item_id) ?? [];
    if (seen.length < 2) return row;
    const newest = seen.reduce((a, b) => (b.period.start > a.period.start ? b : a));
    return { ...row, last_changed: newest.period.start, last_change_note: `Switched to ${loadLabel(newest.setting_label)}` };
  });
}

/** The selection changes a draft records: `skills.<name>` on or off. Read-only fields never save. */
export function skillSelectionChanges(changes: DraftChange[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const change of changes) {
    if (change.field !== "load") continue;
    if (change.to === "Always") out[`skills.${change.item_id}`] = "on";
    else if (change.to === "Off") out[`skills.${change.item_id}`] = "off";
  }
  return out;
}
