import type { ActivityFilters, ActivityPage } from "./model";

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
