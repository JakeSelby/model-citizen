export type ReplayTargetKind = "installed" | "release" | "branch" | "worktree" | "draft";

export type ReplayTargetInput = {
  kind: ReplayTargetKind;
  ref: string;
};

export type ReplayTarget = ReplayTargetInput & {
  revision: string;
  version: string | null;
  draft: string | null;
  config_digest?: string | null;
};

export type ReplayLaunchInput = {
  targets: [ReplayTargetInput, ReplayTargetInput];
  model: string;
  repetitions: number;
  tasks: string[];
  max_budget_usd: string;
  spend_cap_usd: string;
  pre_registration: string;
};

export type ReplayRequest = Omit<ReplayLaunchInput, "targets" | "pre_registration"> & {
  targets: [ReplayTarget, ReplayTarget];
  pre_registration: string | null;
};

export type ReplayMetricRow = {
  target: ReplayTarget;
  task: string;
  arm: "bare" | "harness";
  runs: number;
  passed: number;
  pass_rate: number | null;
  cost_per_passed: number | null;
};

export type ReplayProgressRow = {
  target: ReplayTarget;
  task: string;
  arm: "bare" | "harness";
  repetition: number;
  status: "pending" | "completed" | "errored";
  passed: boolean | null;
  cost_usd: number | null;
};

export type ReplayCatalog = {
  schema_version: number;
  tasks: Array<{ id: string; label: string }>;
  target_kinds: ReplayTargetKind[];
  default_model: string;
  commands: { run: string };
};

export type ReplayRunResult = {
  schema_version: number;
  run: { run_id: string; status: string };
  progress: ReplayProgressRow[];
  result: null | {
    targets: ReplayTarget[];
    table: ReplayMetricRow[];
    spend_usd: number;
    reported_spend_usd: number;
    spend_cap_usd: string;
    stopped_at_cap: boolean;
  };
};

export type ReplayPreview = {
  valid: boolean;
  errors: string[];
  estimate: { amount_usd: number | null; basis: string; sample_count: number };
  caps: { max_budget_usd: string; spend_cap_usd: string };
  confirmation_token?: string;
  command: string;
  request: ReplayRequest;
};

export class ReplayRequestGate {
  private current = 0;
  private paidGeneration: number | null = null;

  next(): number {
    this.current += 1;
    return this.current;
  }

  accepts(generation: number): boolean {
    return generation === this.current;
  }

  beginEdit(): boolean {
    if (this.paidGeneration !== null) return false;
    this.next();
    return true;
  }

  beginPaid(): number | null {
    if (this.paidGeneration !== null) return null;
    this.paidGeneration = this.next();
    return this.paidGeneration;
  }

  acceptsPaid(generation: number): boolean {
    return this.paidGeneration === generation && this.accepts(generation);
  }

  finishPaid(generation: number): boolean {
    if (this.paidGeneration !== generation) return false;
    this.paidGeneration = null;
    return true;
  }

  paidBusy(): boolean {
    return this.paidGeneration !== null;
  }
}

const money = (value: string) => Number(value);

export function validateReplay(draft: ReplayLaunchInput): string[] {
  const errors: string[] = [];
  if (draft.targets.length !== 2 || draft.targets.some((target) => !target.ref.trim())) {
    errors.push("Choose two explicit targets.");
  } else if (draft.targets[0].kind === draft.targets[1].kind &&
             draft.targets[0].ref.trim() === draft.targets[1].ref.trim()) {
    errors.push("Choose two different targets.");
  }
  if (!draft.model.trim()) errors.push("Choose a model.");
  if (!Number.isInteger(draft.repetitions) || draft.repetitions < 1 || draft.repetitions > 20) {
    errors.push("Repetitions must be between 1 and 20.");
  }
  if (!draft.tasks.length || new Set(draft.tasks).size !== draft.tasks.length) {
    errors.push("Choose one or more unique tasks.");
  }
  if (!(money(draft.max_budget_usd) > 0) || !(money(draft.spend_cap_usd) > 0) ||
      money(draft.max_budget_usd) > money(draft.spend_cap_usd)) {
    errors.push("The per-run budget must be positive and no greater than the spend cap.");
  }
  if (draft.targets.some((target) => target.kind === "release") && !draft.pre_registration.trim()) {
    errors.push("A release replay needs a pre-registration before it can write history.");
  }
  return errors;
}

const refusals: Record<string, string> = {
  replay_target_config_unsupported:
    "A target carries its own configuration, but the benchmark builds the harness arm from the commit's defaults. Checkpoint the change as source or clear the draft configuration.",
  replay_worktree_dirty:
    "A worktree target has uncommitted changes. Commit them or checkpoint them as a draft first.",
  replay_refused: "The replay was refused. Check both targets, the tasks and the caps.",
};

export function replayErrorMessage(code: string): string {
  return refusals[code] ?? code;
}

export function progressResult(row: ReplayProgressRow): string {
  if (row.status === "errored") return "Errored";
  if (row.passed === null) return "Pending";
  return row.passed ? "Passed" : "Failed";
}

export function formatPercent(value: number | null): string {
  return value === null ? "Unavailable" : `${(value * 100).toFixed(1)}%`;
}

export function formatCost(value: number | null): string {
  return value === null ? "Unavailable" : `$${value.toFixed(4)}`;
}
