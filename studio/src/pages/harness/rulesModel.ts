// The Rules page's data: Efficacy reads `/api/harness/periods?surface=rules`; Settings joins the
// selection in force (`POST /api/selection`) with the rule-health report (`POST /api/rules/health`).
import type { DraftChange, MetricColumn, PeriodRow, SettingField, SettingRow } from "../../harness/model";
import type { RuleHealth, RuleRow, TryWithoutResult } from "../../reports/rules/model";
import { tokensText, tryWithoutErrorMessage } from "../../reports/rules/model";
import { sourceLabel, type SelectionReport } from "../../selection/model";

export const RULES_SURFACE = "rules";

export const RULE_METRICS: MetricColumn[] = [
  { key: "sessions", heading: "Sessions" },
  { key: "hits", heading: "Detector hits" },
  { key: "fired_in", heading: "Fired in" },
  { key: "precision", heading: "Detector precision" },
  { key: "context_tokens", heading: "Context tokens" },
];

export const RULE_FIELDS: SettingField[] = [
  { key: "on", heading: "On" },
  { key: "source", heading: "Set by" },
  { key: "tokens", heading: "Context cost" },
];

export const HITS_NOTE = "Detector hits come from this machine's own sessions, so they are exploratory. A detector "
  + "fires on what its rule names, which can be the breach rather than the compliance: a hit is shown as fired, "
  + "never as followed. Precision and context cost describe each rule as it is now.";

function healthRows(health: RuleHealth): Map<string, RuleRow> {
  const rows = new Map<string, RuleRow>();
  for (const row of health.rows) if (row.kind === "rules" && !rows.has(row.unit)) rows.set(row.unit, row);
  return rows;
}

/** When the item's setting last changed, read from its periods: the start of its newest one. */
function lastChange(periods: PeriodRow[], unit: string): { at: string | null; to?: string } {
  const own = periods.filter((row) => row.item_id === unit)
    .sort((a, b) => a.period.start.localeCompare(b.period.start));
  if (own.length < 2) return { at: null };
  const latest = own[own.length - 1];
  return { at: latest.period.start, to: latest.setting_label };
}

/**
 * One row per rule the selection switches, its value and the layer that set it. Only a rule the
 * rule-health report offers "try without it" for is editable, and only to off: that is the path
 * a Settings save takes.
 */
export function ruleSettingRows(selection: SelectionReport, health: RuleHealth, periods: PeriodRow[] = []): SettingRow[] {
  const byUnit = healthRows(health);
  const group = selection.groups.find((item) => item.kind === "rules");
  return (group?.rows ?? []).map((row) => {
    const rule = byUnit.get(row.unit);
    const value = typeof row.value === "string" ? row.value : String(row.value ?? "");
    const offer = rule?.try_without;
    const detail = [rule?.path ?? `rules/${row.unit}`, offer && !offer.available ? offer.reason : ""]
      .filter(Boolean).join(" · ");
    const change = lastChange(periods, row.unit);
    return {
      item_id: row.unit,
      item_label: rule?.rule ?? row.unit,
      item_detail: detail,
      values: { on: value, source: sourceLabel(row), tokens: rule ? tokensText(rule.tokens) : "not measured" },
      options: offer?.available && value === "on" ? { on: ["on", "off"] } : undefined,
      last_changed: change.at,
      last_change_note: change.to ? `now ${change.to}` : undefined,
    };
  });
}

/**
 * With no draft picked, Settings saves go through "Try without it": each rule switched off becomes its own draft, made
 * by the existing route. Results made before a failure are kept, so no draft goes unreported.
 */
export async function draftRuleChanges(
  changes: DraftChange[],
  tryWithout: (rule: string) => Promise<TryWithoutResult>,
): Promise<{ results: TryWithoutResult[]; error: string | null }> {
  const results: TryWithoutResult[] = [];
  for (const change of changes) {
    if (change.field !== "on" || change.to !== "off") {
      return { results, error: `${change.item_id}: only switching a rule off can be drafted here; switch it on in Changes.` };
    }
    try {
      results.push(await tryWithout(change.item_id));
    } catch (caught) {
      return { results, error: `${change.item_id}: ${tryWithoutErrorMessage(caught instanceof Error ? caught.message : "")}` };
    }
  }
  return { results, error: null };
}

/** Switch edits as the selection save route records them in a picked draft: `{"rules.voice": "off"}`. */
export function ruleSelectionChanges(changes: DraftChange[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const change of changes) if (change.field === "on" && (change.to === "on" || change.to === "off")) out[`rules.${change.item_id}`] = change.to;
  return out;
}
