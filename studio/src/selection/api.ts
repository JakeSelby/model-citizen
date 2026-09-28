import type { SelectionReport } from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

export async function loadSelection(repository: string, projectFile: string): Promise<SelectionReport> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  return json<SelectionReport>(await fetch("/api/selection", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({ repository: repository.trim(), project_file: projectFile.trim() }),
  }));
}
