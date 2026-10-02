import type { CompareInput, CompareResult } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

/** Two finished replay targets compared by the engine (`citizen runs compare`). */
export async function compareRuns(input: CompareInput): Promise<CompareResult> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  return json<CompareResult>(await fetch("/api/runs/compare", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({
      base: { run_id: input.base.run_id.trim(), target: input.base.target },
      candidate: { run_id: input.candidate.run_id.trim(), target: input.candidate.target },
    }),
  }));
}
