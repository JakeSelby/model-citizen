import { saveDraft, type Save } from "../../configure/api";
import { groupRows, measuringText, type DraftChange, type MetricColumn, type PeriodRow, type SettingField, type SettingRow } from "../../harness/model";

export const SOURCES_SURFACE = "sources";

export const SOURCES_QUESTION = "Where everything that loads comes from. Load is always shown against the harness version that was in effect.";

export const SOURCES_METRICS: MetricColumn[] = [
  { key: "sessions", heading: "Sessions", align: "right" },
  { key: "tokens_per_session", heading: "Tokens per session", align: "right" },
  { key: "modules", heading: "Modules", align: "right" },
];

export const SOURCES_FIELDS: SettingField[] = [
  { key: "source", heading: "Kind" },
  { key: "on", heading: "On" },
  { key: "editing", heading: "Editing" },
];

export const TEAM_NOTE = "Team: not set up. Nothing in the configuration marks a primitive root as a team's, so a team-published root is labelled Personal.";

export const READ_ONLY = {
  Core: "read-only: no Studio route edits the core root",
  "Third-party": "read-only: plugins are enabled in Claude Code",
} as const;

export const PERSONAL_EDITING = "pick a draft to switch it off";

const PERSONAL = "personal:";

function text(value: number | string | undefined, fallback = ""): string {
  return value === undefined ? fallback : String(value);
}

/** The latest period of each source, read-only: its label, root, enabled state and editing route. */
export function settingRowsFromPeriods(rows: PeriodRow[]): SettingRow[] {
  return groupRows(rows).map((group) => {
    const latest = group.rows[group.rows.length - 1];
    const source = text(latest.metrics.source, "Core");
    const changed = group.rows.length > 1;
    return {
      item_id: group.item_id,
      item_label: group.item_label,
      item_detail: shortPath(text(latest.metrics.root)) || undefined,
      item_detail_title: text(latest.metrics.root) || undefined,
      values: {
        source,
        on: text(latest.metrics.enabled, "on"),
        editing: READ_ONLY[source as keyof typeof READ_ONLY] ?? PERSONAL_EDITING,
      },
      last_changed: changed ? latest.period.start : null,
      last_change_note: changed ? `harness ${latest.setting_label} · ${measuringText(latest)}` : undefined,
    };
  });
}

/** A root path's last two segments, "…/repos/agent-harness"; the full path goes in the row's title. */
export function shortPath(path: string): string {
  const parts = path.replace(/\/+$/, "").split("/").filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : path;
}

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

/**
 * With a draft loaded, the draft's own `primitive_roots` replace the installed personal roots and
 * each can be switched off; Core and plugins stay read-only.
 */
export function settingRowsFromDraft(rows: PeriodRow[], draftRoots: string[]): SettingRow[] {
  const installed = settingRowsFromPeriods(rows).filter((row) => row.values.source !== "Personal");
  const personal: SettingRow[] = draftRoots.map((path) => ({
    item_id: PERSONAL + path,
    item_label: `Personal: ${basename(path)}`,
    item_detail: shortPath(path),
    item_detail_title: path,
    values: { source: "Personal", on: "on", editing: "in this draft" },
    options: { on: ["on", "off"] },
    last_changed: null,
  }));
  const core = installed.filter((row) => row.values.source === "Core");
  return [...core, ...personal, ...installed.filter((row) => row.values.source !== "Core")];
}

/** The draft's `primitive_roots` after the edits: a root switched off is dropped. */
export function rootsAfter(draftRoots: string[], changes: DraftChange[]): string[] {
  const off = new Set(changes.filter((change) => change.field === "on" && change.to === "off")
    .map((change) => change.item_id.slice(PERSONAL.length)));
  return draftRoots.filter((path) => !off.has(path));
}

export function draftRoots(values: Record<string, unknown>): string[] {
  const roots = values.primitive_roots;
  return Array.isArray(roots) ? roots.filter((root): root is string => typeof root === "string") : [];
}

type SaveFn = (draft: string, baseRevision: string, changes: Record<string, unknown>) => Promise<Save>;

/** Saves the edited root list to the draft through `/api/configure/save`; the new revision. */
export async function saveSourcesDraft(
  draft: string, revision: string, roots: string[], changes: DraftChange[], save: SaveFn = saveDraft,
): Promise<string> {
  const next = rootsAfter(roots, changes);
  if (next.length === roots.length) return revision;
  const saved = await save(draft, revision, { primitive_roots: next });
  if (!saved.saved) throw new Error(saved.errors[0]?.message ?? "The draft refused the source change.");
  return saved.result?.revision ?? revision;
}
