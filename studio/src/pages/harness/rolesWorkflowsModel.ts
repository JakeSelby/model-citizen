// The Roles and Workflows pages' columns and their Settings rows. Settings are read from the
// module library (`GET /api/library`): a role's class from its definition, its model and effort
// from the Claude Code projection of its adapter binding, and each module's on/off switch.
// Only the switch is saved to a draft; class, model and effort are shown read-only.

import type { LibraryModule, LibraryPayload } from "../../library/model";
import type { DraftChange, MetricColumn, SettingField, SettingRow } from "../../harness/model";

export const ROLE_METRICS: MetricColumn[] = [
  { key: "spawns", heading: "Spawns" },
  { key: "model", heading: "Model that ran" },
  { key: "effort", heading: "Effort that ran" },
  { key: "cost", heading: "Cost" },
  { key: "cost_per_spawn", heading: "Cost p50 · p75" },
  { key: "unpriced", heading: "Unpriced" },
  { key: "over_budget", heading: "Over budget" },
  { key: "tier_rewrites", heading: "Tier rewrites" },
];

export const WORKFLOW_METRICS: MetricColumn[] = [
  { key: "runs", heading: "Runs" },
  { key: "agents", heading: "Agents" },
  { key: "agents_per_run", heading: "Agents per run" },
  { key: "cost", heading: "Cost" },
  { key: "cost_per_run", heading: "Cost per run p50 · p75" },
  { key: "unpriced_runs", heading: "Unpriced runs" },
];

export const READ_ONLY = "read-only";

export const ROLE_FIELDS: SettingField[] = [
  { key: "tier", heading: `Tier (${READ_ONLY})` },
  { key: "model", heading: `Model, Claude Code (${READ_ONLY})` },
  { key: "effort", heading: `Effort (${READ_ONLY})` },
  { key: "enabled", heading: "Enabled" },
];

export const WORKFLOW_FIELDS: SettingField[] = [
  { key: "source", heading: `Source (${READ_ONLY})` },
  { key: "enabled", heading: "Enabled" },
];

export const SWITCH = ["on", "off"];

export const BUILT_IN_ROW: SettingRow = {
  item_id: "built-in",
  item_label: "Built-in and unnamed agents",
  item_detail: "Explore, Plan, general-purpose and a runtime's own agents",
  values: { tier: "Session model", model: "Session model", effort: "Session default", enabled: "Set by the runtime" },
  last_changed: null,
  last_change_note: "runtime default",
};

export const WORKFLOW_TOOL_ROW: SettingRow = {
  item_id: "workflow-tool",
  item_label: "Workflow tool runs",
  item_detail: "Scripted fan-outs; no role budget applies",
  values: { source: "Runtime", enabled: "Set by the runtime" },
  last_changed: null,
  last_change_note: "runtime default",
};

/** One `key: value` line of a Markdown front matter or a projected agent definition. */
export function headerField(text: string, key: string): string {
  const header = /^---\n([\s\S]*?)\n---/.exec(text)?.[1] ?? "";
  const line = header.split("\n").find((entry) => entry.startsWith(`${key}:`));
  return line ? line.slice(key.length + 1).trim() : "";
}

/** One module per name: a name found in two roots is one switch, so the first root's row stands. */
function core(modules: LibraryModule[], kind: string): LibraryModule[] {
  const seen = new Set<string>();
  return modules.filter((module) => module.kind === kind && !seen.has(module.name) && Boolean(seen.add(module.name)))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function switchValues(module: LibraryModule): { value: string; options?: string[] } {
  return module.state.switchable ? { value: module.state.value, options: SWITCH } : { value: module.state.value };
}

function layerNote(module: LibraryModule): string {
  return module.state.layer === "default" ? "default selection" : `set by ${module.state.layer}`;
}

export function roleSettings(library: LibraryPayload): SettingRow[] {
  const rows = core(library.modules, "roles").map((module): SettingRow => {
    const enabled = switchValues(module);
    return {
      item_id: module.name,
      item_label: module.name,
      item_detail: headerField(module.source.text, "description").split(". ")[0] || undefined,
      values: {
        tier: headerField(module.source.text, "tier") || "not set",
        model: headerField(module.rendered.text, "model") || "inherits the session",
        effort: headerField(module.rendered.text, "effort") || "session default",
        enabled: enabled.value,
      },
      options: enabled.options ? { enabled: enabled.options } : undefined,
      last_changed: null,
      last_change_note: `${layerNote(module)}; class and effort live in the role definition and adapter binding`,
    };
  });
  return [...rows, BUILT_IN_ROW];
}

export function workflowSettings(library: LibraryPayload): SettingRow[] {
  const rows = core(library.modules, "workflows").map((module): SettingRow => {
    const enabled = switchValues(module);
    return {
      item_id: module.name,
      item_label: `/${module.name}`,
      item_detail: headerField(module.source.text, "description") || undefined,
      values: { source: module.root.label, enabled: enabled.value },
      options: enabled.options ? { enabled: enabled.options } : undefined,
      last_changed: null,
      last_change_note: layerNote(module),
    };
  });
  return [...rows, WORKFLOW_TOOL_ROW];
}

/**
 * The selection changes a draft records for these edits, keyed `<kind>.<unit>` as the Changes
 * page's selection editor saves them. Only the on/off switch is editable here.
 */
export function selectionChanges(kind: "roles" | "workflows", changes: DraftChange[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const change of changes) {
    if (change.field === "enabled" && SWITCH.includes(change.to)) out[`${kind}.${change.item_id}`] = change.to;
  }
  return out;
}
