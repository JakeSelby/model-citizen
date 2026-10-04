import type { EvidenceTone } from "../../components/evidence";

/** The rule-health document `POST /api/rules/health` serves (`rule_health.report`). */
export type RuleState = "measured" | "dark" | "unmeasured";

export type HitGroup = { hits: number; sessions: number; of: number; share: number; note: string };

export type HitWindow = {
  status: "measured" | "not measured";
  reason: string;
  measured_sessions: number | null;
  detectors: Record<string, HitGroup | null>;
};

export type RuleHits = {
  status: "measured" | "not measured";
  label: string;
  reason: string;
  reliable: boolean | null;
  windows: Record<string, HitWindow>;
  last_fired_within_days: number | null;
};

export type DetectorPrecision = {
  id: string;
  status: "measured" | "not measured";
  precision: number | null;
  recall: number | null;
  below_floor: boolean | null;
  reason: string;
};

export type Measured<T> = ({ status: "measured"; reason: string } & T) | { status: "not measured"; reason: string };

export type RuleRow = {
  rule: string;
  module: string;
  kind: "rules" | "stances";
  unit: string;
  path: string;
  state: RuleState;
  reason: string;
  detectors: DetectorPrecision[];
  hits: RuleHits;
  advice: { status: "measured" | "not measured"; reason: string; groups: Record<string, unknown>[]; footer?: string };
  selection_state: string | null;
  tokens: Measured<{ tokens: number; estimand: string }>;
  effect: Measured<{ effect: number | null; interval: [number, number] | null; n: number | null; reading: string; arm: string; exploratory: boolean | null }>;
  try_without: { available: boolean; reason: string };
};

export type RuleHealth = {
  schema_version: number;
  generated_at: string;
  status: "ready" | "partial";
  summary: { rules: number; measured: number; dark: number; unmeasured: number; measured_text: string };
  windows: number[];
  precision_floor: number | null;
  exploratory_note: string;
  sources: Record<string, { status: string; message: string }>;
  commands: Record<string, string>;
  findings: { path: string; line: number; reason: string }[];
  rows: RuleRow[];
};

export type TryWithoutResult = {
  schema_version: number;
  rule: string;
  draft: { name: string; revision: string };
  changes: Record<string, string>;
  commands: string[];
  message: string;
};

export const NOT_MEASURED = "not measured";

/** Tone per coverage state; the state's word is always printed beside it, never colour alone. */
export function stateTone(state: RuleState): EvidenceTone {
  return state === "measured" ? "success" : state === "dark" ? "neutral" : "warning";
}

function percent(share: number): string {
  return `${(share * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

/** One detector in one window, as `usage --rules` counted it. */
export function hitLine(id: string, group: HitGroup | null | undefined): string {
  if (!group) return `${id}: not in the report`;
  return `${id}: fired in ${group.sessions} of ${group.of} sessions (${percent(group.share)}), ${group.hits} hit${group.hits === 1 ? "" : "s"}${group.note ? `, ${group.note}` : ""}`;
}

/** A window's figure for the table cell: every detector's sessions, or why there is none. */
export function windowText(hits: RuleHits, days: number): string {
  if (hits.status !== "measured") return NOT_MEASURED;
  const window = hits.windows[String(days)];
  if (!window || window.status !== "measured") return `${NOT_MEASURED}: ${window?.reason ?? "no report"}`;
  return Object.entries(window.detectors).map(([id, group]) =>
    group ? `${group.sessions}/${group.of} (${percent(group.share)})` : `${id}: none`).join("; ");
}

export function lastFiredText(hits: RuleHits, windows: number[]): string {
  if (hits.status !== "measured") return NOT_MEASURED;
  if (hits.last_fired_within_days !== null) return `within ${hits.last_fired_within_days} days`;
  const widest = windows.length ? Math.max(...windows) : 0;
  const measured = Object.values(hits.windows).some((window) => window.status === "measured");
  return measured ? `not in ${widest} days` : NOT_MEASURED;
}

/** Whether the hit figures can be read at face value, with the engine's reason when not. */
export function reliability(hits: RuleHits): { label: string; tone: EvidenceTone; reason: string } | null {
  if (hits.status !== "measured" || hits.reliable === null) return null;
  return hits.reliable
    ? { label: "precision at floor", tone: "success", reason: "" }
    : { label: "unreliable", tone: "danger", reason: hits.reason };
}

export function precisionText(detector: DetectorPrecision, floor: number | null): string {
  if (detector.status !== "measured" || detector.precision === null) return `${detector.id}: precision ${NOT_MEASURED} (${detector.reason})`;
  const under = detector.below_floor ? `, under the ${floor ?? "?"} floor` : "";
  return `${detector.id}: precision ${detector.precision}, recall ${detector.recall ?? NOT_MEASURED}${under}`;
}

export function tokensText(tokens: RuleRow["tokens"]): string {
  return tokens.status === "measured" ? `${tokens.tokens.toLocaleString("en-US")} tokens (${tokens.estimand})` : `${NOT_MEASURED}: ${tokens.reason}`;
}

export function effectText(effect: RuleRow["effect"]): string {
  if (effect.status !== "measured") return `${NOT_MEASURED}: ${effect.reason}`;
  const value = effect.effect === null ? "undefined" : `${effect.effect > 0 ? "+" : ""}${(effect.effect * 100).toFixed(1)}%`;
  const span = effect.interval ? ` [${(effect.interval[0] * 100).toFixed(1)}%, ${(effect.interval[1] * 100).toFixed(1)}%]` : "";
  return `cost ${value}${span}, n ${effect.n ?? "?"}, ${effect.reading}${effect.exploratory ? ", exploratory" : ""} (arm ${effect.arm})`;
}

export function adviceText(advice: RuleRow["advice"]): string {
  return advice.status === "measured" ? `${advice.groups.length} recommendation group(s) from the adherence report` : `${NOT_MEASURED}: ${advice.reason}`;
}

/** Rules that cost context yet never fired in the widest window come first: the pruning candidates. */
export function sortRows(rows: RuleRow[]): RuleRow[] {
  const order: Record<RuleState, number> = { dark: 0, unmeasured: 1, measured: 2 };
  return [...rows].sort((a, b) => order[a.state] - order[b.state] || a.rule.localeCompare(b.rule));
}

export function summaryLine(health: RuleHealth): string {
  const { measured, dark, unmeasured, measured_text: text } = health.summary;
  return `${measured} measured, ${dark} dark, ${unmeasured} unmeasured${text ? ` ${text}` : ""}`;
}

export function unavailableSources(health: RuleHealth): string[] {
  return Object.entries(health.sources)
    .filter(([, source]) => source.status !== "ready")
    .map(([name, source]) => `${name}: ${source.message || "unavailable"}`);
}

export function tryWithoutErrorMessage(code: string): string {
  if (code === "rule-not-switchable") return "This rule is not switched on in the selection, so there is nothing to switch off.";
  if (code === "invalid_request") return "The rule name was refused.";
  if (code === "switch-failed") return "The draft was created but the switch could not be saved; open it in Configure.";
  return `The draft could not be created (${code}).`;
}
