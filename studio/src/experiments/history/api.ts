import type { CaseHistory, HistoryFilters, HistoryPage, RunDetail } from "./model";
import type { RunRecord } from "../model";

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
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify(body),
  }));
}

export function loadHistory(filters: HistoryFilters, cursor: string | null = null): Promise<HistoryPage> {
  return post("/api/runs/history", { limit: 50, cursor, ...filters });
}

export function loadRunDetail(runId: string, lineageCursor: string | null = null): Promise<RunDetail> {
  return post("/api/runs/detail", { run_id: runId, lineage_limit: 50, lineage_cursor: lineageCursor });
}

export function loadCaseHistory(caseId: string, cursor: string | null = null): Promise<CaseHistory> {
  return post("/api/runs/case-history", { case_id: caseId, limit: 50, cursor });
}

export function loadEvidence(runId: string, artifact: string): Promise<{ content: string }> {
  return post("/api/runs/evidence", { run_id: runId, artifact });
}

export function rerun(runId: string): Promise<RunRecord> {
  return post("/api/runs/rerun", { run_id: runId });
}
