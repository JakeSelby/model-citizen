export type NativeVerdict = "passed" | "failed" | "unverified" | "pending";

export type NativeClient = {
  id: string;
  runtime: string;
  platform: string;
  observed: boolean;
  spend_cap_supported: boolean;
  unavailable_reason: string | null;
};

export type NativeCase = { id: string; description: string };

export type NativeCatalog = {
  schema_version: 1;
  clients: NativeClient[];
  cases: NativeCase[];
  default_model: string;
  commands: Record<"run" | "resume" | "retry_failed", string>;
  target: { kind: string; ref: string; source_commit: string | null };
  initial: NativeSelection;
};

export type NativeSelection = {
  client: string;
  cases: string[];
  model: string;
  source_commit: string;
  progress_id: string;
  target_kind: "installed" | "release" | "branch" | "worktree" | "draft";
  target_ref: string;
  retry_source?: string;
  retry_case?: string;
};

export type NativeCaseResult = {
  id: string;
  status: NativeVerdict;
  observation: string | null;
  seconds: number | null;
  sessions: number | null;
  spend_usd: number | null;
  price_as_of: string | null;
};

export type NativeSnapshot = {
  schema_version: 1;
  selection: NativeSelection;
  cases: NativeCaseResult[];
  settled_count: number;
  eligible_count: number;
  interrupted: boolean;
  warnings: string[];
  resume_cases: string[];
  command: string;
  run_status?: string;
};

export type SpendRequest = {
  max_budget_usd: string;
  spend_cap_usd: string;
  pricing_source: "api_credit" | "subscription";
};

export type SpendPreview = {
  estimate: {
    amount_usd: number | null;
    basis: string;
    sample_count: number;
    suite_id: string;
    case_count: number;
  };
  caps: { max_budget_usd: string; spend_cap_usd: string };
  pricing: { source: SpendRequest["pricing_source"]; basis: string };
  confirmation_required: true;
  confirmation_token: string;
  case_identities: string[];
};

export type NativeRun = {
  run_id: string;
  status: string;
  selection: NativeSelection;
  target: { kind: string; ref: string; source_commit: string; version: string };
};

export function selectedClient(catalog: NativeCatalog, id: string): NativeClient | undefined {
  return catalog.clients.find((client) => client.id === id);
}

export function selectionReady(catalog: NativeCatalog, selection: NativeSelection): boolean {
  const client = selectedClient(catalog, selection.client);
  const known = new Set(catalog.cases.map((item) => item.id));
  const retry = Boolean(selection.retry_source || selection.retry_case);
  const caseCountReady = retry
    ? Boolean(selection.retry_source && selection.retry_case
      && selection.cases.length === 1 && selection.cases[0] === selection.retry_case)
    : selection.cases.length === 3;
  return Boolean(client?.spend_cap_supported && selection.model.trim()
    && (!selection.source_commit || /^[0-9a-f]{40}$/.test(selection.source_commit))
    && ["installed", "release", "branch", "worktree", "draft"].includes(selection.target_kind)
    && Boolean(selection.target_ref)
    && /^[a-z0-9][a-z0-9-]{0,63}$/.test(selection.progress_id)
    && caseCountReady && new Set(selection.cases).size === selection.cases.length
    && selection.cases.every((item) => known.has(item)));
}

export function spendReady(value: SpendRequest): boolean {
  const money = (text: string) => /^(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)$/.test(text)
    && Number.isFinite(Number(text)) && Number(text) > 0;
  return money(value.max_budget_usd) && money(value.spend_cap_usd)
    && Number(value.max_budget_usd) <= Number(value.spend_cap_usd)
    && (value.pricing_source === "api_credit" || value.pricing_source === "subscription");
}

export function nextPreviewGeneration(current: number): number {
  return current + 1;
}

export function previewGenerationIsCurrent(ticket: number, current: number): boolean {
  return ticket === current;
}

export type NativeRequestPhase = "idle" | "previewing" | "starting";

export function phaseAfterEdit(phase: NativeRequestPhase): NativeRequestPhase {
  return phase === "starting" ? "starting" : "idle";
}

export function mayBeginRequest(phase: NativeRequestPhase): boolean {
  return phase === "idle";
}

export function selectionAfterStart(run: NativeRun): NativeSelection {
  return run.selection;
}

export function stateAfterStart(run: NativeRun): {
  run: NativeRun; selection: NativeSelection; snapshot: null;
} {
  return { run, selection: run.selection, snapshot: null };
}

export function selectionForResume(snapshot: NativeSnapshot): NativeSelection {
  return snapshot.selection;
}

export function completion(snapshot: NativeSnapshot): number {
  return snapshot.eligible_count
    ? Math.round((snapshot.settled_count / snapshot.eligible_count) * 100)
    : 0;
}

export function retryable(snapshot: NativeSnapshot): string[] {
  return snapshot.cases.filter((item) => item.status === "failed").map((item) => item.id);
}

export function verdictColor(status: NativeVerdict): string {
  if (status === "passed") return "teal";
  if (status === "failed") return "red";
  if (status === "unverified") return "yellow";
  return "gray";
}
