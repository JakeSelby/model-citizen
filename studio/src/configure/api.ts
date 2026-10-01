import type { ConfigureSchema, ValidationError } from "./model";
import type { LibraryPayload, ModuleProjection as LibraryProjection, ProjectedModuleText } from "../library/model";
import type { AuthoringPreview, AuthoringRead, AuthoringRequest, AuthoringSave } from "./authoringModel";
import type { ApplyResult, ApplyReview } from "./applyModel";

export type DraftRead = {
  status: "ready" | "error" | "unavailable";
  message: string;
  draft: { name?: string; revision?: string };
  values: Record<string, unknown>;
  warnings: string[];
};

export type Preview = {
  valid: boolean;
  errors: ValidationError[];
  warnings: string[];
  changed: string[];
  preview: Record<string, unknown>;
  base_revision: string;
};

export type Save = Preview & { saved: boolean; result: { revision?: string } | null };

export type SelectionSnapshot = {
  selection: Record<string, unknown> & { mode?: string | null; stances?: Record<string, string> };
  budget: {
    selected_lines: number;
    worst_case_lines: number;
    line_cap: number;
    worst_case_tokens: number;
    token_cap: number;
  };
  stance_text: Record<string, string>;
};

export type SelectionControls = {
  modes: string[];
  stances: Array<{ name: string; value: string | null; options: string[] }>;
  switches: Array<{ kind: string; rows: Array<{ unit: string; value: "on" | "off"; core: boolean }> }>;
  core_acknowledged: boolean;
};

export type DraftSelectionRead = {
  status: "ready" | "unavailable";
  message: string;
  draft: { name?: string; revision?: string };
  controls: SelectionControls;
  current: SelectionSnapshot;
};

export type DraftSelectionPreview = {
  valid: boolean;
  error: string;
  error_code: string;
  changed: string[];
  unchanged: boolean;
  base_revision: string;
  before: SelectionSnapshot | Record<string, never>;
  after: SelectionSnapshot | Record<string, never>;
  controls: SelectionControls | Record<string, never>;
  applied: string[];
};

export type DraftSelectionSave = DraftSelectionPreview & {
  saved: boolean;
  result: { revision?: string; replayed?: boolean } | null;
};

export type EditableModule = {
  key: string;
  name: string;
  kind: "rules" | "skills" | "stances";
  root: { id: string; label: string };
  projections: LibraryProjection[];
  context_cost: { tokens: number; estimate: string; method: string };
};

export type ModuleDiagnostic = { message: string; line: number | null; severity: "error" };
export type ModuleBudget = {
  runtime: string;
  label: string;
  lines: number;
  line_delta: number;
  line_cap: number;
  tokens: number;
  token_delta: number;
  token_cap: number;
  over_cap: boolean;
};
export type ModuleProjection = ProjectedModuleText;
export type ModuleRead = {
  status: "ready" | "unavailable";
  message: string;
  error_code: string;
  draft: { name?: string; revision?: string };
  modules: EditableModule[];
  module: EditableModule | null;
  content: string;
  source_digest: string;
  nothing_applied: boolean;
};
export type ModulePreview = {
  valid: boolean;
  error: string;
  error_code: string;
  base_revision: string;
  source_digest: string;
  content_digest: string;
  unchanged: boolean;
  module: EditableModule | null;
  diagnostics: ModuleDiagnostic[];
  budgets: ModuleBudget[];
  projections: ModuleProjection[];
  nothing_applied: boolean;
};
export type ModuleSave = ModulePreview & {
  saved: boolean;
  result: { revision?: string; replayed?: boolean } | null;
  saved_lint: ModuleDiagnostic[];
};

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function csrfToken(): Promise<string> {
  const response = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  return response.csrf_token;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const csrf = await csrfToken();
  return json<T>(await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": csrf },
    body: JSON.stringify(body),
  }));
}

export async function loadSchema(): Promise<ConfigureSchema> {
  return json<ConfigureSchema>(await fetch("/api/configure/schema", { credentials: "same-origin" }));
}

export async function loadDraft(draft: string): Promise<DraftRead> {
  return post<DraftRead>("/api/configure/read", { draft: draft.trim() });
}

export async function previewDraft(draft: string, changes: Record<string, unknown>): Promise<Preview> {
  return post<Preview>("/api/configure/preview", { draft, changes });
}

export async function saveDraft(
  draft: string,
  baseRevision: string,
  changes: Record<string, unknown>,
): Promise<Save> {
  return post<Save>("/api/configure/save", {
    draft,
    base_revision: baseRevision,
    idempotency_key: crypto.randomUUID(),
    changes,
  });
}

export async function loadDraftSelection(draft: string): Promise<DraftSelectionRead> {
  return post<DraftSelectionRead>("/api/configure/selection/read", { draft });
}

export async function previewDraftSelection(
  draft: string,
  changes: Record<string, unknown>,
): Promise<DraftSelectionPreview> {
  return post<DraftSelectionPreview>("/api/configure/selection/preview", { draft, changes });
}

export async function saveDraftSelection(
  draft: string,
  baseRevision: string,
  idempotencyKey: string,
  changes: Record<string, unknown>,
): Promise<DraftSelectionSave> {
  return post<DraftSelectionSave>("/api/configure/selection/save", {
    draft,
    base_revision: baseRevision,
    idempotency_key: idempotencyKey,
    changes,
  });
}

export async function loadDraftModule(draft: string, module = ""): Promise<ModuleRead> {
  return post<ModuleRead>("/api/configure/module/read", { draft, module });
}

export async function previewDraftModule(
  draft: string, module: string, content: string,
): Promise<ModulePreview> {
  return post<ModulePreview>("/api/configure/module/preview", { draft, module, content });
}

export async function saveDraftModule(
  draft: string,
  module: string,
  baseRevision: string,
  sourceDigest: string,
  idempotencyKey: string,
  content: string,
): Promise<ModuleSave> {
  return post<ModuleSave>("/api/configure/module/save", {
    draft, module, base_revision: baseRevision, source_digest: sourceDigest,
    idempotency_key: idempotencyKey, content,
  });
}

export async function loadAuthoring(draft: string): Promise<AuthoringRead> {
  return post<AuthoringRead>("/api/configure/authoring/read", { draft });
}

export async function previewAuthoring(draft: string, request: AuthoringRequest): Promise<AuthoringPreview> {
  return post<AuthoringPreview>("/api/configure/authoring/preview", { draft, request });
}

export async function saveAuthoring(
  draft: string, baseRevision: string, idempotencyKey: string, request: AuthoringRequest,
): Promise<AuthoringSave> {
  return post<AuthoringSave>("/api/configure/authoring/save", {
    draft, base_revision: baseRevision, idempotency_key: idempotencyKey, request,
  });
}

export async function loadDraftLibrary(draft: string): Promise<LibraryPayload> {
  return post<LibraryPayload>("/api/configure/authoring/library", { draft });
}

export async function reviewDraftApply(draft: string): Promise<ApplyReview> {
  return post<ApplyReview>("/api/configure/apply/review", { draft });
}

/** `confirm` is the draft's name typed back; the server refuses an apply without it. */
export async function applyDraft(draft: string, revision: string, confirm: string): Promise<ApplyResult> {
  return post<ApplyResult>("/api/configure/apply", { draft, revision, confirm });
}

/** Restore or abandon an interrupted apply; `confirm` is its draft's name typed back. */
export async function recoverApply(action: "restore" | "abandon", confirm: string): Promise<ApplyResult> {
  return post<ApplyResult>("/api/configure/apply/recover", { action, confirm });
}
