import type { FirstRunStatus } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  return json<T>(await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify(body),
  }));
}

export function loadFirstRun(draft: string): Promise<FirstRunStatus> {
  return post<FirstRunStatus>("/api/first-run", { draft });
}

export function startFirstRun(draft: string): Promise<FirstRunStatus> {
  return post<FirstRunStatus>("/api/first-run/start", { draft });
}
