import type { SelectionControls, SelectionSnapshot } from "./api";

export const SELECTION_AUTOSAVE_DELAY_MS = 750;

export function controlValues(
  controls: SelectionControls,
  current: SelectionSnapshot,
): Record<string, unknown> {
  const values: Record<string, unknown> = {
    mode: current.selection.mode ?? "",
    core_switches_acknowledged: controls.core_acknowledged,
  };
  for (const stance of controls.stances) values[`stances.${stance.name}`] = stance.value;
  for (const group of controls.switches) {
    for (const row of group.rows) values[`${group.kind}.${row.unit}`] = row.value;
  }
  return values;
}

export function cliCommands(changes: Record<string, unknown>, applied: string[] = []): string[] {
  const entries = Object.entries(changes);
  const ordered = applied.length ? applied.map((path) => [path, changes[path]] as [string, unknown]) : [
    ...entries.filter(([path, value]) => path === "core_switches_acknowledged" && value === true),
    ...entries.filter(([path]) => path !== "core_switches_acknowledged"),
    ...entries.filter(([path, value]) => path === "core_switches_acknowledged" && value !== true),
  ];
  return ordered.map(([path, value]) => (
    `citizen config set ${path} ${typeof value === "boolean" ? String(value) : String(value)}`
  ));
}

export function selectionRequestSignature(
  draft: string,
  revision: string,
  changes: Record<string, unknown>,
): string {
  return JSON.stringify([draft, revision, Object.entries(changes).sort(([left], [right]) => (
    left.localeCompare(right)
  ))]);
}

export function changedStances(before: SelectionSnapshot, after: SelectionSnapshot): string[] {
  const oldValues = before.selection.stances ?? {};
  const newValues = after.selection.stances ?? {};
  return [...new Set([...Object.keys(oldValues), ...Object.keys(newValues)])]
    .filter((name) => oldValues[name] !== newValues[name])
    .sort();
}

const NON_SWITCH_SELECTION_KEYS = new Set(["mode", "stances", "sources", "shadowed"]);

function switchGroup(snapshot: SelectionSnapshot, kind: string): Record<string, unknown> {
  const value = snapshot.selection[kind];
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function selectionSwitchKinds(
  before: SelectionSnapshot,
  after: SelectionSnapshot,
): string[] {
  return [...new Set([...Object.keys(before.selection), ...Object.keys(after.selection)])]
    .filter((kind) => !NON_SWITCH_SELECTION_KEYS.has(kind))
    .filter((kind) => Object.keys(switchGroup(before, kind)).length
      || Object.keys(switchGroup(after, kind)).length)
    .sort();
}

export function switchSelectionLines(snapshot: SelectionSnapshot, kind: string): string {
  return Object.entries(switchGroup(snapshot, kind))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([unit, value]) => `${unit}: ${String(value)}`)
    .join("\n");
}

export function changedSwitchUnits(
  before: SelectionSnapshot,
  after: SelectionSnapshot,
): string[] {
  return selectionSwitchKinds(before, after).flatMap((kind) => {
    const oldValues = switchGroup(before, kind);
    const newValues = switchGroup(after, kind);
    return [...new Set([...Object.keys(oldValues), ...Object.keys(newValues)])]
      .filter((unit) => oldValues[unit] !== newValues[unit])
      .sort()
      .map((unit) => `${kind}.${unit}`);
  });
}

export type SelectionSaveFailureAction = "reload" | "new-identity" | "retry";

export function selectionSaveFailureAction(errorCode: string): SelectionSaveFailureAction {
  if (errorCode === "stale-revision") return "reload";
  if (errorCode === "idempotency-conflict") return "new-identity";
  return "retry";
}

export function needsCoreAcknowledgement(error: string): boolean {
  return error.includes("core_switches_acknowledged") || error.includes("core hook");
}
