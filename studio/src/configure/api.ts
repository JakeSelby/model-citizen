import type { ConfigureSchema, ValidationError } from "./model";

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
