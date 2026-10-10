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
  base_config_digest?: string;
};

export type ReplayLaunchInput = {
  targets: [ReplayTargetInput, ReplayTargetInput];
  model: string;
  repetitions: number;
  tasks: string[];
  max_budget_usd: string;
  spend_cap_usd: string;
  pre_registration: string;
  /** The evaluator pack, chosen by name, digest and set; null runs the repository's own tasks. */
  pack?: { name: string; digest: string; set?: string } | null;
  /** Set only by the definitive-evaluation action: the launch is held to the engine's registered budget. */
  definitive?: true;
};

/** A pack as the server resolved and pinned it. */
export type ResolvedPack = { name: string; version: string; commit: string; digest: string; source: string; set?: string };

export type ReplayRequest = Omit<ReplayLaunchInput, "targets" | "pre_registration" | "pack"> & {
  targets: [ReplayTarget, ReplayTarget];
  pre_registration: string | null;
  pack: ResolvedPack | null;
  evidence: "pre-registered" | "exploratory";
};

/** The registered sample as the pre-registration states it, read by the server. */
export type ReplaySampling = {
  evidence: "pre-registered" | "exploratory";
  targets?: Array<{ target: ReplayTarget; evidence: "pre-registered" | "exploratory" }>;
  registered: null | { tasks: number; long: number | null; trials: number; min_trials: number; power_calculation: string | null; have?: number[] | null };
  requested: { tasks: number; trials: number };
  note: string;
};

/** One target's `cost_bench.py summarise --json` output, or the engine's refusal. */
export type ReplayAnalysis = { target: number; result?: Record<string, unknown>; error?: string };

export type DraftComparison = {
  draft: string; revision: string; target: 1 | 2; base_target: 1 | 2;
  config_digest: string | null; base: ReplayTarget;
  tasks: string[]; model: string; trials: number; pack_digest: string | null;
  evidence: string; key: string; stale: boolean; stale_reason: string | null;
};

export type ReplayPack = {
  name: string;
  version: string;
  commit: string;
  digest: string;
  short_digest: string;
  /** The pack's set at the production tier: its production set, a rule-targeted set or an outcome subset. */
  set: string;
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
    measures?: "source" | "source and configuration";
    measured_config_digests?: (string | null)[];
    analysis?: ReplayAnalysis[] | null;
    analysis_error?: string | null;
    evidence?: "pre-registered" | "exploratory";
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
  replay_target_config_invalid:
    "The engine refused the draft's configuration: it is not a JSON object of finite values.",
  replay_target_config_root_unreadable:
    "The engine refused the draft's configuration: a primitive root outside the checkout is not a readable directory of regular files, so the arm has nothing to copy.",
  replay_target_config_root_unsupported:
    "The engine refused the draft's configuration: a primitive root adds a role or workflow, which the arm accepts only from the commit's own primitives.",
  replay_target_config_host_path:
    "The engine refused the draft's configuration: it names a path on this machine, which no arm may see.",
  replay_target_config_unresolved:
    "The engine refused the draft's configuration: the commit's resolver does not accept its switches, manifests or modes.",
  replay_target_config_unsupported:
    "A draft changed its configuration, but the benchmark builds the harness arm from the commit's defaults and cannot apply it. Checkpoint the change as source or restore the inherited configuration.",
  replay_target_busy: "A draft is being saved. Preview again in a moment.",
  replay_worktree_dirty:
    "A worktree target has uncommitted changes. Commit them or checkpoint them as a draft first.",
  replay_refused: "The replay was refused. Check both targets, the tasks and the caps.",
  definitive_budget_unregistered:
    "The engine has no registered budget for this run, so the definitive launch is refused. It waits on an engine budget reader; the Studio assumes no budget.",
  definitive_budget_unestimated:
    "The spend guard has no estimate for this run, so the definitive launch cannot be held to the registered whole-run cap and is refused.",
  definitive_launch_required: "A definitive request is launched only by the definitive evaluation action.",
  definitive_budget_exceeded:
    "The definitive launch exceeds the engine's registered budget: its per-run budget, spend cap or estimated spend is above it.",
};

export function replayErrorMessage(code: string): string {
  return refusals[code] ?? code;
}

export const MEASURES_NOTE =
  "A replay measures source. When a draft edited its configuration, the draft's harness arm runs it and the other target's runs the configuration the draft was created with, so the pair differs by the draft's edit alone; that replay is exploratory and writes no history row. Otherwise each harness arm runs its commit's defaults.";

/** The result table's caption: which target's configuration the engine measured, by the digest it
 * stamped on its rows, or that none was applied. */
export function measuredConfigCaption(digests: (string | null)[] = []): string {
  const measured = digests.map((digest, index) => digest ? `target ${index + 1} configuration ${digest}` : null)
    .filter((line): line is string => line !== null);
  return measured.length
    ? `Configuration measured by the engine: ${measured.join("; ")}. A replay that applies a configuration is exploratory and writes no history row.`
    : "Source only: target configuration was not applied.";
}

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

/** The picker key of one pack set: its full digest and its set name. */
export function packKey(pack: { digest: string; set?: string }): string {
  return `${pack.digest}/${pack.set ?? "production"}`;
}

/** One picker option per pack set: its name, version, set and short digest, keyed by `packKey`. */
export function packOptions(packs: ReplayPack[]): Array<{ value: string; label: string }> {
  return packs.map((pack) => ({ value: packKey(pack), label: `${pack.name} ${pack.version}, set ${pack.set} (${pack.short_digest})` }));
}

/**
 * The selection a chosen pack set sends: name and digest, and the set unless it is the production
 * set, so a production replay's command and pinned identity are what they always were. Never a path.
 */
export function packSelection(pack: ReplayPack): { name: string; digest: string; set?: string } {
  return pack.set === "production" ? { name: pack.name, digest: pack.digest } : { name: pack.name, digest: pack.digest, set: pack.set };
}

/** The pack a new replay starts on: the catalog's default, else the first, else none. */
export function initialPack(packs: ReplayPack[], defaultDigest: string | null): ReplayPack | null {
  return packs.find((pack) => pack.digest === defaultDigest) ?? packs[0] ?? null;
}

/** The task ids a replay may choose: the chosen pack set's (by `packKey`), or the repository's own without one. */
export function tasksFor(packs: ReplayPack[], key: string | null, repositoryTasks: string[]): string[] {
  const pack = packs.find((item) => packKey(item) === key);
  return pack ? pack.tasks.map((task) => task.id) : repositoryTasks;
}

/** An engine value exactly as the engine reported it: JSON text, so null stays null. */
export function engineValue(value: unknown): string {
  return value === undefined ? "absent" : JSON.stringify(value);
}

/**
 * Every leaf of an engine value as a path and its JSON text, in the engine's own order. A list of
 * plain values (an interval) is one leaf; nothing is dropped, so a new engine field still shows.
 */
export function engineLeaves(value: unknown, path = ""): Array<[string, string]> {
  const plain = (item: unknown) => item === null || typeof item !== "object" ||
    (Array.isArray(item) && item.every((inner) => inner === null || typeof inner !== "object"));
  if (plain(value)) return [[path || "value", engineValue(value)]];
  if (Array.isArray(value)) {
    return value.length ? value.flatMap((item, index) => engineLeaves(item, `${path}[${index}]`)) : [[path, "[]"]];
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return [[path || "value", "{}"]];
  return entries.flatMap(([key, item]) => engineLeaves(item, path ? `${path}.${key}` : key));
}

/** Label and value lines for one target's engine analysis: every field the engine reported. */
export function analysisLines(entry: ReplayAnalysis): Array<[string, string]> {
  if (entry.error !== undefined) return [["Engine refused", entry.error]];
  return engineLeaves(entry.result ?? {});
}

/** What the form says about the sample before a run. */
export function samplingLines(sampling: ReplaySampling | undefined): string[] {
  if (!sampling) return [];
  const lines = [sampling.note, `This replay: ${sampling.requested.tasks} task(s), ${sampling.requested.trials} trial(s) per task and arm.`];
  for (const item of sampling.targets ?? []) lines.push(`${item.target.kind} ${item.target.ref}: ${item.evidence}.`);
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

/** The form's message for a failed preview or start: the refusal's plain words, or a fallback. */
/** The sentence for a refusal, followed by the engine's own reason when it gave one. */
export function withEngineReason(message: string, error: unknown): string {
  const reason = error instanceof Error ? (error as Error & { reason?: unknown }).reason : undefined;
  return typeof reason === "string" && reason ? `${message} Engine: ${reason}` : message;
}

export function failureMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? withEngineReason(replayErrorMessage(error.message), error) : fallback;
}

/** The preview a confirm may use, with the request generation that produced it. */
export type PreviewSlot = { preview: ReplayPreview | null; generation: number };

export type PreviewAction =
  | { type: "begin"; generation: number }
  | { type: "loaded"; generation: number; value: ReplayPreview }
  | { type: "failed"; generation: number }
  | { type: "clear" };

/**
 * Preview state for the replay form. Any preview request clears the previous preview and its
 * token as it starts, and a failure leaves it cleared, so a refused definitive preview can never
 * fall back on an earlier plain one. Only the newest request's answer is taken.
 */
export function previewReducer(state: PreviewSlot, action: PreviewAction): PreviewSlot {
  switch (action.type) {
    case "begin": return { preview: null, generation: action.generation };
    case "loaded": return action.generation === state.generation ? { ...state, preview: action.value } : state;
    case "failed": return action.generation === state.generation ? { ...state, preview: null } : state;
    case "clear": return { ...state, preview: null };
  }
}

/** The token "Confirm and run" may send, or null when nothing confirmable is previewed. */
export function confirmableToken(slot: PreviewSlot): string | null {
  return slot.preview?.valid && slot.preview.confirmation_token ? slot.preview.confirmation_token : null;
}

/** Which launch a preview is for, as the form names it. */
export function launchKind(preview: ReplayPreview): string {
  return preview.request.definitive ? "Definitive evaluation" : "Plain replay";
}
