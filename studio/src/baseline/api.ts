import type { BaselineResponse, RecordResponse } from "./model";

async function json<T>(response: Response): Promise<T> {
  let body: (T & { error?: string }) | null = null;
  try {
    body = await response.json() as T & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body) throw new Error("The baseline response is empty.");
  return body;
}

/** What you have now and the baselines recorded so far. */
export async function loadBaseline(signal?: AbortSignal): Promise<BaselineResponse> {
  const body = await json<BaselineResponse>(await fetch("/api/baseline", {
    method: "GET", credentials: "same-origin", signal,
  }));
  if (!body.current || !Array.isArray(body.baselines)) throw new Error("The baseline response is incomplete.");
  return body;
}

/** Record the current config and plugins as a baseline; writes Studio state only. */
export async function recordBaseline(label: string): Promise<RecordResponse> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return json<RecordResponse>(await fetch("/api/baseline/record", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({ label }),
  }));
}

export const baselineQuery = {
  queryKey: ["baseline"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadBaseline(signal),
  staleTime: 30_000,
};
