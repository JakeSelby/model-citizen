import type { ActivityFilters, ActivityPage } from "./model";
import type { RollbackPreview, RollbackResult } from "./rollbackModel";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function csrfToken(signal?: AbortSignal): Promise<string> {
  const response = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
    signal,
  }));
  return response.csrf_token;
}

export async function loadActivity(
  filters: ActivityFilters,
  cursor = "",
  limit = 25,
  signal?: AbortSignal,
): Promise<ActivityPage> {
  const csrf = await csrfToken(signal);
  return json<ActivityPage>(await fetch("/api/activity", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": csrf },
    body: JSON.stringify({ ...filters, cursor, limit }),
    signal,
  }));
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

/** What rolling back one apply would restore, or why it is refused. Changes nothing. */
export async function previewRollback(applyId: string): Promise<RollbackPreview> {
  return post<RollbackPreview>("/api/configure/apply/rollback/preview", { apply_id: applyId });
}

/** `confirm` is the applied draft's name typed back; the CLI refuses any other draft. */
export async function rollBack(applyId: string, confirm: string): Promise<RollbackResult> {
  return post<RollbackResult>("/api/configure/apply/rollback", { apply_id: applyId, confirm });
}
