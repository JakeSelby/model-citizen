import type {
  ReplayCatalog, ReplayLaunchInput, ReplayPreview, ReplayRequest, ReplayRunResult,
} from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string; reason?: string };
  if (!response.ok) {
    // The engine's own reason, when it refused a configuration, travels beside the code.
    throw Object.assign(new Error(body.error ?? `Request failed (${response.status}).`),
      body.reason ? { reason: body.reason } : {});
  }
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

export function previewReplay(request: ReplayLaunchInput): Promise<ReplayPreview> {
  return post<ReplayPreview>("/api/runs/replay/preview", { request });
}

export async function loadReplayCatalog(): Promise<ReplayCatalog> {
  return json<ReplayCatalog>(await fetch("/api/runs/replay/catalog", { credentials: "same-origin" }));
}

export function startReplay(request: ReplayRequest, confirmationToken: string): Promise<{ run_id: string }> {
  return post<{ run_id: string }>("/api/runs/replay/start", {
    request,
    confirmation_token: confirmationToken,
  });
}

export function loadReplayResult(runId: string): Promise<ReplayRunResult> {
  return post<ReplayRunResult>("/api/runs/replay/result", { run_id: runId });
}
