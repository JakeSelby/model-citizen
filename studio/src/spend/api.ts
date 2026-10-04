import type { Grouping, SpendReport } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

/** One `citizen usage --json` report, read from the local ledger through the Studio. */
export async function loadSpend(by: Grouping, days: number, signal?: AbortSignal): Promise<SpendReport> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
    signal,
  }));
  return json<SpendReport>(await fetch("/api/reports/spend", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({ by, days }),
    signal,
  }));
}
