// The Hooks page's data: liveness and figures from the `hooks` surface, the Safety log from the
// decision log's refusals, prompts and warnings, and the per-hook settings a draft can change.
import type { ActivityEntry } from "../../activity/model";
import type { SelectionControls } from "../../configure/api";
import type { ConfigureSchema } from "../../configure/model";
import type { DraftChange, MetricColumn, PeriodRow, SettingField, SettingRow } from "../../harness/model";

export const SURFACE = "hooks";

export const HOOK_METRICS: MetricColumn[] = [
  { key: "fires", heading: "Fires" },
  { key: "blocks", heading: "Blocks" },
  { key: "warns", heading: "Warns" },
  { key: "errors", heading: "Errors" },
  { key: "last_fired", heading: "Last fired" },
  { key: "liveness", heading: "Liveness" },
  { key: "points", heading: "Decision points" },
];

export const HOOK_FIELDS: SettingField[] = [
  { key: "enabled", heading: "On" },
  { key: "mode", heading: "Decision mode" },
];

export const NO_MODE = "no mode";
export const MODES_PATH = "governance.jev.modes";
export const DEFAULT_MODE_PATH = "governance.jev.mode";

/** Liveness labels the provider writes, worst first. */
export const LIVENESS_ORDER = ["erroring", "silent", "not recorded", "off", "not observable", "unknown", "built in", "live"] as const;

export type LivenessCount = { liveness: string; count: number };

/** Per-hook rows only: a point no hook owns has no switch and no settings. */
export function isHook(row: Pick<PeriodRow, "item_id">): boolean {
  return !row.item_id.startsWith("point:");
}

export function livenessOf(row: PeriodRow): string {
  const value = row.metrics.liveness;
  return typeof value === "string" ? value : "unknown";
}

/** Liveness counts over the latest period of each item, worst first; empty labels are left out. */
export function livenessCounts(rows: PeriodRow[]): LivenessCount[] {
  const latest = new Map<string, PeriodRow>();
  for (const row of rows) {
    const seen = latest.get(row.item_id);
    if (!seen || row.period.start > seen.period.start) latest.set(row.item_id, row);
  }
  const counts = new Map<string, number>();
  for (const row of latest.values()) counts.set(livenessOf(row), (counts.get(livenessOf(row)) ?? 0) + 1);
  const rank = (label: string) => {
    const index = (LIVENESS_ORDER as readonly string[]).indexOf(label);
    return index < 0 ? LIVENESS_ORDER.length : index;
  };
  return [...counts.entries()].map(([liveness, count]) => ({ liveness, count }))
    .sort((a, b) => rank(a.liveness) - rank(b.liveness));
}

export function livenessColor(label: string): string {
  if (label === "erroring") return "red";
  if (label === "silent" || label === "not recorded") return "yellow";
  if (label === "live") return "green";
  return "gray";
}

// --- Safety log ---------------------------------------------------------------------------

/** The Activity outcomes the Safety log reads, worst first. */
export const SAFETY_OUTCOMES = ["refused", "confirmation-required", "warn"] as const;

export function severity(outcome: string): number {
  const index = (SAFETY_OUTCOMES as readonly string[]).indexOf(outcome);
  return index < 0 ? SAFETY_OUTCOMES.length : index;
}

export function safetyLabel(outcome: string): string {
  if (outcome === "refused") return "Blocked";
  if (outcome === "confirmation-required") return "Asked";
  if (outcome === "warn") return "Warned";
  return outcome;
}

/** Decision-log entries only, one per id, worst outcome first and newest first within it. */
export function worstFirst(entries: ActivityEntry[]): ActivityEntry[] {
  const unique = new Map<string, ActivityEntry>();
  for (const entry of entries) {
    if (entry.source === "decision-log" && !unique.has(entry.id)) unique.set(entry.id, entry);
  }
  return [...unique.values()].sort((a, b) => severity(a.outcome) - severity(b.outcome)
    || (b.timestamp > a.timestamp ? 1 : b.timestamp < a.timestamp ? -1 : 0));
}

// --- Settings -----------------------------------------------------------------------------

export type DraftState = {
  draft: string;
  revision: string;
  controls: SelectionControls;
  values: Record<string, unknown>;
};

/** The decision points a mode may be set for, and the modes, read from the configure schema. */
export function modeChoices(schema: ConfigureSchema | null): { points: string[]; modes: string[] } {
  const field = schema?.sections.flatMap((section) => section.fields).find((item) => item.path === MODES_PATH);
  const keys = field?.constraints.keys;
  const values = field?.constraints.values;
  return {
    points: Array.isArray(keys) ? keys.filter((key): key is string => typeof key === "string") : [],
    modes: Array.isArray(values) ? values.filter((value): value is string => typeof value === "string") : [],
  };
}

function lookup(values: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) =>
    value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined, values);
}

function pointModes(values: Record<string, unknown>): Record<string, string> {
  const modes = lookup(values, MODES_PATH);
  if (!modes || typeof modes !== "object" || Array.isArray(modes)) return {};
  return Object.fromEntries(Object.entries(modes as Record<string, unknown>)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string"));
}

/**
 * One settings row per hook. Without a loaded draft the rows are read-only and show what the
 * hooks surface reports; with one, the switch and (where the hook's point takes one) the mode
 * come from the draft and are editable.
 */
export function settingRows(rows: PeriodRow[], draft: DraftState | null, choices: { points: string[]; modes: string[] }): SettingRow[] {
  const switches = new Map((draft?.controls.switches.find((group) => group.kind === "hooks")?.rows ?? [])
    .map((row) => [row.unit, row]));
  const modes = draft ? pointModes(draft.values) : {};
  const fallback = draft ? lookup(draft.values, DEFAULT_MODE_PATH) : undefined;
  const seen = new Set<string>();
  const out: SettingRow[] = [];
  for (const row of rows) {
    if (!isHook(row) || seen.has(row.item_id)) continue;
    seen.add(row.item_id);
    const control = switches.get(row.item_id);
    const hasMode = choices.points.includes(row.item_id);
    const mode = !hasMode ? NO_MODE
      : modes[row.item_id] ?? (typeof fallback === "string" && fallback ? fallback : draft ? "off" : "load a draft");
    const options: Record<string, string[]> = {};
    if (draft && control) options.enabled = ["on", "off"];
    if (draft && hasMode && choices.modes.length) options.mode = choices.modes;
    out.push({
      item_id: row.item_id,
      item_label: row.item_label,
      item_detail: control?.core ? `${row.item_id} · core` : row.item_id,
      values: { enabled: control?.value ?? row.setting_label, mode },
      options: Object.keys(options).length ? options : undefined,
      last_changed: null,
    });
  }
  return out;
}

/** Edits split by save path: switches go to the draft's selection, modes to its configuration. */
export function splitChanges(changes: DraftChange[]): { selection: Record<string, string>; modes: Record<string, string> } {
  const selection: Record<string, string> = {};
  const modes: Record<string, string> = {};
  for (const change of changes) {
    if (change.field === "enabled") selection[`hooks.${change.item_id}`] = change.to;
    else if (change.field === "mode" && change.to !== NO_MODE) modes[change.item_id] = change.to;
  }
  return { selection, modes };
}

export function nextModes(values: Record<string, unknown>, modes: Record<string, string>): Record<string, string> {
  return { ...pointModes(values), ...modes };
}
