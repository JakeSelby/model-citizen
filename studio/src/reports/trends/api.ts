import type { Trends } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

/** Every benchmark line, the static figure and the proof set, as the run store and verifier hold them. */
export async function loadTrends(): Promise<Trends> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return json<Trends>(await fetch("/api/reports/trends", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({}),
  }));
}
