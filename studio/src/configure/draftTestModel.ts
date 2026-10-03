import { engineValue, type ReplayPreview } from "../experiments/replay/model";
import type { EngineMeasure } from "../experiments/compare/model";

/** The form for testing a draft against its base. The targets are the server's to set. */
export type DraftTestForm = {
  model: string;
  repetitions: number;
  tasks: string[];
  max_budget_usd: string;
  spend_cap_usd: string;
  pack: { name: string; digest: string } | null;
  /** The change worth detecting, in percent (15 means 15%). */
  effect_percent: number;
  /** A planning coefficient of variation; empty uses the repository's declared assumption. */
  cv: string;
};

export type DraftTestPower = {
  effect: number; cv: number; cv_source: string; tasks: number; trials: number;
  attempts_per_side: number; minimum_detectable_effect: number | null; min_trials: number;
  power: number; confidence: number; enough: boolean; reasons: string[];
  needed_trials: number | null; max_trials: number;
};

export type DraftTestPlan = {
  schema_version: number;
  draft: { draft: string; draft_id: string; base_ref: string; base_revision: string; revision: string };
  power: DraftTestPower;
  power_line: string;
  evidence_note: string;
  preview: ReplayPreview;
};

export type DraftTestVerdictName =
  | "running" | "unavailable" | "not_compared" | "engine_refused" | "exploratory"
  | "inconclusive" | "helped" | "worse";

export type DraftTestSide = { run_id: string; target: 1 | 2 };

export type DraftTestVerdict = {
  run_id: string; revision: string; base_revision: string; created_at: string;
  power: DraftTestPower; power_line: string;
  stale: boolean; stale_reason: string | null; stale_copy: string | null;
  comparison: { base: DraftTestSide; candidate: DraftTestSide; command: string };
  status: string | null; verdict: DraftTestVerdictName; reasons: string[];
  readings: Record<string, EngineMeasure>; spend_usd: number | null; headline: string;
};

export type DraftTestCheckpoint = { revision: string; current: boolean; latest: DraftTestVerdict; tests: number };

export type DraftTestVerdicts = {
  schema_version: number; draft: string; revision: string; base_revision: string;
  evidence_note: string; tests: DraftTestVerdict[]; checkpoints: DraftTestCheckpoint[];
  unreadable_records: number;
};

export function initialForm(defaultModel: string, pack: { name: string; digest: string } | null): DraftTestForm {
  return {
    model: defaultModel, repetitions: 5, tasks: [], max_budget_usd: "2", spend_cap_usd: "20",
    pack, effect_percent: 15, cv: "",
  };
}

/** What stops the form from asking for a plan, one line each. */
export function validateDraftTest(form: DraftTestForm): string[] {
  const errors: string[] = [];
  if (!form.model.trim()) errors.push("Name the model both sides run.");
  if (!form.tasks.length) errors.push("Choose at least one task.");
  if (!Number.isInteger(form.repetitions) || form.repetitions < 1 || form.repetitions > 20) {
    errors.push("Trials per task must be a whole number from 1 to 20.");
  }
  if (!(form.effect_percent > 0 && form.effect_percent < 100)) {
    errors.push("The change worth detecting must be above 0% and below 100%.");
  }
  if (form.cv.trim() && !(Number(form.cv) > 0 && Number(form.cv) <= 10)) {
    errors.push("The coefficient of variation must be above 0 and at most 10, or left empty.");
  }
  for (const [label, value] of [["Per-run budget", form.max_budget_usd], ["Spend cap", form.spend_cap_usd]] as const) {
    if (!(Number(value) > 0)) errors.push(`${label} must be a positive dollar amount.`);
  }
  return errors;
}

/** The plan route's body: the replay form, the effect as a fraction and the optional cv. */
export function planBody(draft: string, form: DraftTestForm): Record<string, unknown> {
  return {
    draft,
    request: {
      model: form.model.trim(), repetitions: form.repetitions, tasks: form.tasks,
      max_budget_usd: form.max_budget_usd.trim(), spend_cap_usd: form.spend_cap_usd.trim(), pack: form.pack,
    },
    effect: form.effect_percent / 100,
    cv: form.cv.trim() ? Number(form.cv) : null,
  };
}

/** The badge for a verdict: helped, worse and the rest differ in fill and text, not colour alone. */
export function verdictBadge(name: DraftTestVerdictName): { label: string; color: string; variant: "filled" | "outline" | "light" } {
  if (name === "helped") return { label: "Helped", color: "teal", variant: "filled" };
  if (name === "worse") return { label: "Worse", color: "red", variant: "outline" };
  const labels: Record<string, string> = {
    running: "Running", unavailable: "No verdict", not_compared: "Not compared",
    engine_refused: "No verdict", exploratory: "Exploratory: no claim", inconclusive: "Inconclusive",
  };
  return { label: labels[name] ?? name, color: "gray", variant: "light" };
}

const LABELS: Record<string, string> = { cost_per_passed: "Cost-of-Pass", pass_rate: "Pass rate" };

/** One line per judged measure, built only from the engine's fields: the reading, never a
 * direction, with the estimate, the interval and both trial counts beside it. */
export function readingLines(test: DraftTestVerdict): string[] {
  return Object.entries(test.readings).map(([key, m]) => [
    `${LABELS[key] ?? key}: ${String(m.reading ?? "unknown")}`,
    `estimate ${engineValue(m.estimate)}, effect ${engineValue(m.effect)}`,
    `interval ${engineValue(m.interval)}`,
    `n ${engineValue(m.n)} against ${engineValue(m.control_n)}`,
    ...(m.reason ? [String(m.reason)] : []),
  ].join("; "));
}

export function spendLine(test: DraftTestVerdict): string {
  return test.spend_usd === null ? "Spend: not recorded yet." : `Spend to find out: $${test.spend_usd.toFixed(2)}.`;
}

/** The form with the trials the power check asked for. */
export function withNeededTrials(form: DraftTestForm, power: DraftTestPower): DraftTestForm {
  return power.needed_trials === null ? form : { ...form, repetitions: power.needed_trials };
}

export function draftTestErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    invalid_request: "Check the test's fields and try again.",
    draft_not_found: "This draft no longer exists.",
    draft_unavailable: "The draft is being saved; try again in a moment.",
    draft_test_mismatch: "The draft changed since the plan; check power and spend again.",
    draft_test_planning_unavailable: "State a coefficient of variation: no planning assumption is declared.",
    draft_test_unrecorded: "The test started, but it could not be linked to this draft.",
    replay_target_busy: "The draft is being saved; try again in a moment.",
    replay_target_config_unsupported: "This draft changed its configuration, which a replay cannot measure.",
  };
  return messages[code] ?? `The draft test was refused (${code}).`;
}
