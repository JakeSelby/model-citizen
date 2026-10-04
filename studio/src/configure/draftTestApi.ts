import type { ReplayRequest } from "../experiments/replay/model";
import type { DraftTestPlan, DraftTestVerdicts } from "./draftTestModel";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return json<T>(await fetch(path, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify(body),
  }));
}

/** Power and spend for a draft test; nothing starts. */
export function planDraftTest(body: Record<string, unknown>): Promise<DraftTestPlan> {
  return post<DraftTestPlan>("/api/configure/test/plan", body);
}

/** Start the planned pair with its one-use spend confirmation. */
export function startDraftTest(draft: string, request: ReplayRequest, confirmationToken: string,
  effect: unknown, cv: unknown): Promise<{ run_id: string; status: string }> {
  return post("/api/configure/test/start", { draft, request, confirmation_token: confirmationToken, effect, cv });
}

/** Every test of the draft and the latest verdict per checkpoint (`citizen draft test`). */
export function loadDraftVerdicts(draft: string): Promise<DraftTestVerdicts> {
  return post<DraftTestVerdicts>("/api/configure/test/verdicts", { draft });
}
