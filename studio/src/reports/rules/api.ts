import type { RuleHealth, TryWithoutResult } from "./model";

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

/** Every rule as the engines report it (`citizen usage --rules` and its siblings). */
export function loadRuleHealth(): Promise<RuleHealth> {
  return post<RuleHealth>("/api/rules/health", {});
}

/** A new draft with the rule switched off; nothing live changes. */
export function tryWithout(rule: string): Promise<TryWithoutResult> {
  return post<TryWithoutResult>("/api/rules/try-without", { rule });
}
