import type { EvalCatalog, EvalPreview, EvalRunResult, PaidTierInput, ResolvedPaidTier } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return json<T>(await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify(body),
  }));
}

export async function loadEvalCatalog(): Promise<EvalCatalog> {
  return json<EvalCatalog>(await fetch("/api/evals/catalog", { credentials: "same-origin" }));
}

export function startFreeTier(suite: string, raw?: string): Promise<{ run_id: string; command: string }> {
  return post("/api/evals/run", raw === undefined ? { suite } : { suite, raw });
}

export function previewPaidTier(request: PaidTierInput): Promise<EvalPreview> {
  return post<EvalPreview>("/api/evals/preview", { request });
}

export function startPaidTier(request: ResolvedPaidTier, confirmationToken: string): Promise<{ run_id: string }> {
  return post("/api/evals/start", { request, confirmation_token: confirmationToken });
}

export function loadEvalResult(runId: string): Promise<EvalRunResult> {
  return post<EvalRunResult>("/api/evals/result", { run_id: runId });
}
