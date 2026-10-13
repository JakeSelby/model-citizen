// The Stances page's data: period rows from the `stances` surface, settings from a draft's
// selection controls, and edits saved as `stances.<name>` changes through selection editing.
import { saveDraftSelection, type DraftSelectionSave, type SelectionControls } from "../../configure/api";
import type { DraftChange, MetricColumn, PeriodRow, SettingField, SettingRow } from "../../harness/model";

export const STANCES_SURFACE = "stances";

export const STANCE_METRICS: MetricColumn[] = [
  { key: "sessions", heading: "Sessions" },
  { key: "output_tokens", heading: "Output tokens" },
  { key: "cache_read_share", heading: "Cache-read share" },
];

export const VARIANT_FIELD = "variant";
export const STANCE_FIELDS: SettingField[] = [{ key: VARIANT_FIELD, heading: "Variant" }];

/** The provider's label for a stance name: "plan-ceremony" reads "Plan ceremony". */
export function stanceLabel(name: string): string {
  const spaced = name.replace(/-/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

type Change = { at: string; note?: string };

/**
 * Each stance's latest variant switch inside the window. A stance with one period has none: its
 * period starts at the window's edge, not at a change.
 */
export function lastChanges(rows: PeriodRow[]): Record<string, Change> {
  const byItem: Record<string, PeriodRow[]> = {};
  for (const row of rows) (byItem[row.item_id] ??= []).push(row);
  const out: Record<string, Change> = {};
  for (const [item, periods] of Object.entries(byItem)) {
    if (periods.length < 2) continue;
    const latest = [...periods].sort((a, b) => a.period.start.localeCompare(b.period.start)).at(-1)!;
    out[item] = latest.status === "measuring" && typeof latest.measuring_day === "number"
      ? { at: latest.period.start, note: `variant · measuring, day ${latest.measuring_day} of ${latest.window_days}` }
      : { at: latest.period.start, note: "variant" };
  }
  return out;
}

/** The variant each stance runs now, read-only, before a draft is loaded to edit it. */
export function settingRowsFromPeriods(rows: PeriodRow[]): SettingRow[] {
  const current: Record<string, PeriodRow> = {};
  for (const row of rows) {
    const seen = current[row.item_id];
    if (!seen || (row.period.end === null && seen.period.end !== null)
      || (seen.period.end !== null && row.period.start > seen.period.start)) current[row.item_id] = row;
  }
  const changes = lastChanges(rows);
  return Object.values(current).map((row) => ({
    item_id: row.item_id,
    item_label: row.item_label,
    values: { [VARIANT_FIELD]: row.setting_label === "(unset)" ? "" : row.setting_label },
    last_changed: changes[row.item_id]?.at ?? null,
    last_change_note: changes[row.item_id]?.note,
  }));
}

/** Each stance's selected variant in the draft, with its alternatives as options. */
export function settingRowsFromControls(controls: SelectionControls, rows: PeriodRow[] = []): SettingRow[] {
  const changes = lastChanges(rows);
  return controls.stances.map((stance) => {
    const value = stance.value ?? "";
    const options = value && !stance.options.includes(value) ? [value, ...stance.options] : stance.options;
    return {
      item_id: stance.name,
      item_label: stanceLabel(stance.name),
      values: { [VARIANT_FIELD]: value },
      options: { [VARIANT_FIELD]: value ? options : ["", ...options] },
      last_changed: changes[stance.name]?.at ?? null,
      last_change_note: changes[stance.name]?.note,
    };
  });
}

/** The page's edits as the selection-editing change map: `{"stances.voice": "concise"}`. */
export function selectionChanges(changes: DraftChange[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const change of changes) if (change.field === VARIANT_FIELD) out[`stances.${change.item_id}`] = change.to;
  return out;
}

type Save = (draft: string, revision: string, key: string, changes: Record<string, unknown>) => Promise<DraftSelectionSave>;

/**
 * Checkpoints the edits into the draft and returns its new revision. Selection editing validates
 * them; a refusal throws its message so the Settings tab shows it and keeps the edits.
 */
export async function saveStanceDraft(
  draft: string, revision: string, changes: DraftChange[],
  save: Save = saveDraftSelection, key: () => string = () => crypto.randomUUID(),
): Promise<string> {
  const payload = selectionChanges(changes);
  if (!Object.keys(payload).length) return revision;
  const saved = await save(draft, revision, key(), payload);
  if (saved.unchanged) return revision;
  if (!saved.saved) throw new Error(saved.error || "The draft refused the stance change.");
  return saved.result?.replayed ? saved.base_revision : saved.result?.revision ?? revision;
}
