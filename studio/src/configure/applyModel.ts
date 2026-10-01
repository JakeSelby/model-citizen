export type ApplyRefusal = { code: string; message: string };

export type ApplyFile = { path: string; status: "added" | "modified" | "deleted"; scope: "personal" | "core" };

export type ApplyConfigRow = {
  key: string;
  action: "set" | "unset" | "none" | "conflict";
  before: unknown;
  after: unknown;
  live: unknown;
};

export type ApplyReview = {
  schema_version: number;
  draft: { name?: string; revision?: string; base_revision?: string; branch?: string; path?: string };
  destination: string;
  files: ApplyFile[];
  root: Array<{ path: string; action: "write" | "delete" }>;
  config: ApplyConfigRow[];
  checks: { status: "passed" | "failed" | "skipped" | "unavailable"; findings: string[]; truncated: boolean; command: string };
  budget: { before_lines: number; after_lines: number; delta_lines: number; line_cap: number } | null;
  commands: Array<{ step: "root" | "config" | "sync" | "check"; command: string }>;
  core: {
    files: string[];
    fork: { modules: Array<{ source: string; kind: string; name: string }>; command: string; note: string };
    branch: { name: string; revision: string; commands: string[] };
  } | null;
  refusals: ApplyRefusal[];
  can_apply: boolean;
  apply_command: string;
  nothing_applied: boolean;
};

export type DoctorCheck = { id: string; status: "attention" | "informational"; message: string; fix: string | null };

export type ApplyResult = {
  schema_version: number;
  status: "applied" | "refused" | "failed";
  applied: boolean;
  error_code: string;
  message: string;
  holder: string;
  apply_id: string;
  review: Partial<ApplyReview>;
  doctor: { status: "passed" | "attention" | "skipped" | "unavailable"; checks: DoctorCheck[] };
  restored: boolean;
  log: string[];
};

/** Apply needs a clean review of the revision on screen and the draft's own name typed back. */
export function canApply(review: ApplyReview | null, revision: string, draft: string, confirmation: string): boolean {
  return review !== null
    && review.can_apply
    && review.draft.revision === revision
    && draft.length > 0
    && confirmation === draft;
}

/** Why Apply is unavailable, in the words the button's description reads out. */
export function applyBlocker(review: ApplyReview | null, revision: string, draft: string, confirmation: string): string {
  if (review === null) return "Review the draft first.";
  if (review.draft.revision !== revision) return "The draft changed since this review. Review it again.";
  if (!review.can_apply) return "Apply is refused until every finding below is resolved.";
  if (confirmation !== draft) return `Type ${draft} to confirm.`;
  return "";
}

export function personalFiles(review: ApplyReview): ApplyFile[] {
  return review.files.filter((file) => file.scope === "personal");
}

export function coreFiles(review: ApplyReview): ApplyFile[] {
  return review.files.filter((file) => file.scope === "core");
}

export function budgetDelta(budget: ApplyReview["budget"]): string {
  if (budget === null) return "The context budget is unavailable for this draft.";
  const sign = budget.delta_lines > 0 ? "+" : "";
  return `${sign}${budget.delta_lines} always-loaded line(s): ${budget.before_lines} now, ${budget.after_lines} after apply, cap ${budget.line_cap}.`;
}

export function shown(value: unknown): string {
  if (value === undefined || value === null) return "unset";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** The headline the live region announces after an apply. */
export function resultHeadline(result: ApplyResult): string {
  if (result.applied) return result.doctor.status === "attention"
    ? "Applied. The doctor checks need attention."
    : "Applied. The doctor checks ran.";
  if (result.error_code === "busy") return result.holder
    ? `Not applied: ${result.holder} holds the sync lock. Nothing changed.`
    : "Not applied: another operation holds the sync lock. Nothing changed.";
  if (result.status === "failed") return result.restored
    ? "Apply failed safely. The previous configuration and files were restored."
    : "Apply failed and the previous state was not fully restored. Run citizen doctor.";
  return "Not applied. Nothing changed.";
}

/**
 * What an apply response may still do once it lands. The live harness changed whichever draft is
 * on screen, so an applied outcome always refreshes the overview; it is shown only on the panel
 * that started it, never under a draft opened since.
 */
export function outcomeAction(applied: boolean, started: number, current: number): { refreshOverview: boolean; show: boolean } {
  return { refreshOverview: applied, show: started === current };
}
