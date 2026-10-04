import { isTerminal, type EvalCatalog, type EvalPreview, type EvalRunResult, type PaidTierInput, type ResolvedPaidTier } from "./model";

/** A refusal the server answered: `status` tells a final answer (4xx) from a transient one. */
export class EvalApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new EvalApiError(body.error ?? `Request failed (${response.status}).`, response.status);
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

export function loadEvalCatalog(): Promise<EvalCatalog> {
  return post<EvalCatalog>("/api/evals/catalog", {});
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

export type EvalRunState = { result: EvalRunResult | null; error: string };

export const POLL_MS = 1500;

/**
 * Poll one tier run until it ends; the engine's result arrives with the terminal status. A refusal
 * the server answered (4xx, such as an unreadable result) is final: polling stops and it is shown.
 * Returns the function that stops polling. `useEvalRun` is this, bound to a component.
 */
export function pollEvalRun(runId: string, onState: (state: EvalRunState) => void, deps: {
  load?: (runId: string) => Promise<EvalRunResult>;
  wait?: (next: () => void, ms: number) => unknown;
  clear?: (handle: unknown) => void;
} = {}): () => void {
  const load = deps.load ?? loadEvalResult;
  const wait = deps.wait ?? ((next: () => void, ms: number) => setTimeout(next, ms));
  const clear = deps.clear ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  let stopped = false;
  let handle: unknown;
  let current: EvalRunState = { result: null, error: "" };
  const tick = () => {
    void load(runId).then((value) => {
      if (stopped) return;
      current = { result: value, error: "" };
      onState(current);
      if (!isTerminal(value.run.status)) handle = wait(tick, POLL_MS);
    }).catch((caught: unknown) => {
      if (stopped) return;
      if (caught instanceof EvalApiError && caught.status >= 400 && caught.status < 500) {
        current = { ...current, error: caught.message };
        onState(current);
        return;
      }
      handle = wait(tick, POLL_MS * 2);
    });
  };
  onState(current);
  tick();
  return () => { stopped = true; if (handle !== undefined) clear(handle); };
}
