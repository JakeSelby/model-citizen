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
  /** The evaluator pack, chosen by name and digest; null runs the repository's own tasks. */
  pack?: { name: string; digest: string } | null;
};

/** A pack as the server resolved and pinned it. */
export type ResolvedPack = { name: string; version: string; commit: string; digest: string; source: string };

export type ReplayRequest = Omit<ReplayLaunchInput, "targets" | "pre_registration" | "pack"> & {
  targets: [ReplayTarget, ReplayTarget];
  pre_registration: string | null;
  pack: ResolvedPack | null;
  evidence: "pre-registered" | "exploratory";
};

/** The registered sample as the pre-registration states it, read by the server. */
export type ReplaySampling = {
  evidence: "pre-registered" | "exploratory";
  registered: null | { tasks: number; long: number | null; trials: number; min_trials: number; power_calculation: string | null };
  requested: { tasks: number; trials: number };
  note: string;
};

/** One target's `cost_bench.py summarise --json` output, or the engine's refusal. */
export type ReplayAnalysis = { target: number; result?: Record<string, unknown>; error?: string };

export type DraftComparison = {
  draft: string; revision: string; config_digest: string | null; base: ReplayTarget;
  tasks: string[]; model: string; trials: number; pack_digest: string | null;
  evidence: string; key: string; stale: boolean; stale_reason: string | null;
};

export type ReplayPack = {
  name: string;
  version: string;
  commit: string;
  digest: string;
  short_digest: string;
  tasks: Array<{ id: string; label: string }>;
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
  packs: ReplayPack[];
  default_pack: string | null;
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
    measures?: "source";
    analysis?: ReplayAnalysis[] | null;
    comparisons?: DraftComparison[];
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
  sampling?: ReplaySampling;
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
    "A draft changed its configuration, but the benchmark builds the harness arm from the commit's defaults and cannot apply it. Checkpoint the change as source or restore the inherited configuration.",
  replay_target_busy: "A draft is being saved. Preview again in a moment.",
  replay_worktree_dirty:
    "A worktree target has uncommitted changes. Commit them or checkpoint them as a draft first.",
  replay_refused: "The replay was refused. Check both targets, the tasks and the caps.",
};

export function replayErrorMessage(code: string): string {
  return refusals[code] ?? code;
}

export const SOURCE_ONLY_NOTE =
  "A replay measures source only: each harness arm runs its commit's defaults, so a draft's inherited configuration is not applied.";

export function readinessSummary(errors: string[]): string {
  if (!errors.length) return "Ready to preview.";
  return `Replay is not ready: ${errors.length} ${errors.length === 1 ? "item needs" : "items need"} attention.`;
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

/** One picker option per pack: its name, version and short digest, keyed by the full digest. */
export function packOptions(packs: ReplayPack[]): Array<{ value: string; label: string }> {
  return packs.map((pack) => ({ value: pack.digest, label: `${pack.name} ${pack.version} (${pack.short_digest})` }));
}

/** The pack a new replay starts on: the catalog's default, else the first, else none. */
export function initialPack(packs: ReplayPack[], defaultDigest: string | null): ReplayPack | null {
  return packs.find((pack) => pack.digest === defaultDigest) ?? packs[0] ?? null;
}

/** The task ids a replay may choose: the chosen pack's, or the repository's own without one. */
export function tasksFor(packs: ReplayPack[], digest: string | null, repositoryTasks: string[]): string[] {
  const pack = packs.find((item) => item.digest === digest);
  return pack ? pack.tasks.map((task) => task.id) : repositoryTasks;
}

/** An engine value exactly as the engine reported it: JSON text, so null stays null. */
export function engineValue(value: unknown): string {
  return value === undefined ? "absent" : JSON.stringify(value);
}

/** Label and value lines for one target's engine analysis, in the engine's own terms. */
export function analysisLines(entry: ReplayAnalysis): Array<[string, string]> {
  if (entry.error !== undefined) return [["Engine refused", entry.error]];
  const result = entry.result ?? {};
  const lines: Array<[string, string]> = [];
  const arms = (result.arms ?? {}) as Record<string, Record<string, unknown>>;
  for (const arm of Object.keys(arms).sort()) {
    for (const field of ["attempts", "passes", "errors", "pass_rate", "cost_of_pass", "cost_usd"]) {
      lines.push([`${arm} ${field}`, engineValue(arms[arm][field])]);
    }
  }
  for (const field of ["ratio", "ratio_undefined", "ratio_interval", "difference", "difference_interval",
    "sm2_eligible", "limitation", "verdict", "reason", "claim"]) {
    if (field in result) lines.push([field, engineValue(result[field])]);
  }
  return lines;
}

/** What the form says about the sample before a run. */
export function samplingLines(sampling: ReplaySampling | undefined): string[] {
  if (!sampling) return [];
  const lines = [sampling.note, `This replay: ${sampling.requested.tasks} task(s), ${sampling.requested.trials} trial(s) per task and arm.`];
  if (sampling.registered) {
    lines.push(`Registered: ${sampling.registered.tasks} task(s), ${sampling.registered.trials} trial(s) per task and arm (floor ${sampling.registered.min_trials}).`);
    if (sampling.registered.power_calculation) lines.push(`Power calculation: ${sampling.registered.power_calculation}`);
  }
  return lines;
}

/** One line per draft comparison, naming its identity and whether it is stale. */
export function comparisonLine(item: DraftComparison): string {
  const state = item.stale ? `Stale: ${item.stale_reason ?? "the draft changed"}` : "Current";
  return `${item.draft} at ${item.revision.slice(0, 12)} against ${item.base.kind} ${item.base.ref}; ` +
    `${item.tasks.length} task(s), ${item.model}, ${item.trials} trial(s). ${state}.`;
}
