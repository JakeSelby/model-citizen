import { parseEventStream, type RunCatalog, type RunRecord, type RunUpdate } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function csrfToken(): Promise<string> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  return session.csrf_token;
}

async function post(path: string, body: Record<string, unknown>): Promise<Response> {
  const csrf = await csrfToken();
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": csrf },
    body: JSON.stringify(body),
  });
}

export async function loadCatalog(): Promise<RunCatalog> {
  return json<RunCatalog>(await fetch("/api/runs/catalog", { credentials: "same-origin" }));
}

export async function startRun(suiteId: string, root: string, selectedCase: string): Promise<RunRecord> {
  const parameters: Record<string, string> = { root };
  if (suiteId === "unit-tests") parameters.case = selectedCase;
  return json<RunRecord>(await post("/api/runs/start", {
    suite_id: suiteId,
    parameters,
    target_kind: "installed",
    target_ref: root,
  }));
}

export async function cancelRun(runId: string): Promise<RunRecord> {
  return json<RunRecord>(await post("/api/runs/cancel", { run_id: runId }));
}

export async function streamRun(
  runId: string,
  stdoutCursor: number,
  stderrCursor: number,
): Promise<RunUpdate[]> {
  const response = await post("/api/runs/stream", {
    run_id: runId,
    stdout_cursor: stdoutCursor,
    stderr_cursor: stderrCursor,
  });
  if (!response.ok) {
    const error = await response.json() as { error?: string };
    throw new Error(error.error ?? `Request failed (${response.status}).`);
  }
  return parseEventStream(await response.text());
}
