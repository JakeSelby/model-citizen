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
  /** The server's deviations from the registration the plan named; null when it named none. */
  registration: { registration_id: string; deviations: string[] } | null;
};

export type DraftTestVerdictName =
  | "running" | "unavailable" | "not_compared" | "engine_refused" | "exploratory"
  | "inconclusive" | "helped" | "worse";

export type DraftTestSide = { run_id: string; target: 1 | 2 };

/** A pre-registration of a draft test (`citizen draft test --register`), never edited once written. */
export type DraftTestRegistration = {
  registration_id: string; draft: string; draft_id: string; revision: string; config_digest: string | null;
  base_revision: string; model: string; tasks: string[]; repetitions: number;
  pack: { name: string; version: string | null; commit: string; digest: string } | null;
  manifest_digest: string; effect: number; cv: number | null; power: DraftTestPower; plan: string;
  plan_commit: string; plan_sha256: string; created_at: string;
  stale: boolean; stale_reason: string | null; problems: string[];
  /** The one run the registration backs, once a run has claimed it. */
  used_by: string | null;
};

export type DraftTestVerdict = {
  run_id: string; revision: string; base_revision: string; created_at: string;
  power: DraftTestPower; power_line: string;
  /** "pre-registered" only when the run matched an intact registration written before it. */
  evidence: "pre-registered" | "exploratory"; registration: string | null; deviations: string[];
  stale: boolean; stale_reason: string | null; stale_copy: string | null;
  comparison: { base: DraftTestSide; candidate: DraftTestSide; command: string };
  status: string | null; verdict: DraftTestVerdictName; reasons: string[];
  readings: Record<string, EngineMeasure>; spend_usd: number | null; headline: string;
};

export type DraftTestCheckpoint = { revision: string; current: boolean; latest: DraftTestVerdict; tests: number };

export type DraftTestVerdicts = {
  schema_version: number; draft: string; revision: string; base_revision: string;
  evidence_note: string;
  /** Every test, newest first; only each checkpoint's latest is scored (`checkpoints`). */
  tests: Array<{ run_id: string; revision: string; created_at: string; latest: boolean }>;
  checkpoints: DraftTestCheckpoint[];
  /** Every registration of the draft, newest first; a stale one stays listed. */
  registrations: DraftTestRegistration[];
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

/** The register route's body: the sample a registration fixes, without targets or caps. Power is
 * planned from the repository's declared variance, so no coefficient of variation is sent. */
export function registerBody(draft: string, form: DraftTestForm): Record<string, unknown> {
  const body = planBody(draft, form);
  const request = body.request as Record<string, unknown>;
  return {
    draft, effect: body.effect, cv: null,
    request: { model: request.model, repetitions: request.repetitions, tasks: request.tasks, pack: request.pack },
  };
}

/** The registration a new test may run under: the newest that is current, intact and unused. */
export function currentRegistration(verdicts: DraftTestVerdicts | null): DraftTestRegistration | null {
  return verdicts?.registrations.find((item) => !item.stale && item.problems.length === 0 && item.used_by === null) ?? null;
}

/** Why the test cannot start yet as asked; null when it can. Ticking pre-register without a
 * current registration would otherwise start an unregistered run the developer took for registered. */
export function startBlocked(preRegister: boolean, registration: DraftTestRegistration | null): string | null {
  return preRegister && !registration ? "Register the test before you start it, or untick pre-register." : null;
}

/** The evidence badge beside a verdict: a pre-registered run is filled, an exploratory one is not. */
export function evidenceBadge(test: DraftTestVerdict): { label: string; variant: "filled" | "light" } {
  return test.evidence === "pre-registered"
    ? { label: "Pre-registered", variant: "filled" }
    : { label: test.registration ? "Registered, deviated: exploratory" : "Exploratory", variant: "light" };
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
    draft_test_unchanged: "This draft has no checkpoint beyond its base yet, so there is nothing to test.",
    draft_test_records_unsafe: "The Studio's test records are not a private directory; nothing was read or written.",
    draft_test_underpowered: "Too few trials to register: check power and use the trials it asks for.",
    draft_test_effect_too_large: "The evidence standard caps the change to register at 15%.",
    draft_test_registration_stale: "The draft changed since it was registered; register the test again.",
    draft_test_registration_not_found: "That registration no longer exists; register the test again.",
    draft_test_registration_mismatch: "That registration belongs to another draft.",
    draft_test_registration_failed: "The registration could not be written; nothing was registered.",
    draft_test_registration_used: "That registration already backs a run; register the test again.",
    draft_test_cv_not_declared: "A registration plans from the repository's declared variance; clear the coefficient of variation.",
    draft_test_registration_subset: "Only the whole task set can be registered; choose every task.",
    replay_target_busy: "The draft is being saved; try again in a moment.",
    replay_target_config_unsupported: "This draft changed its configuration, which a replay cannot measure.",
  };
  return messages[code] ?? `The draft test was refused (${code}).`;
}
