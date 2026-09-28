import type { Overview } from "./model";

export async function loadOverview(): Promise<Overview> {
  const response = await fetch("/api/overview", { credentials: "same-origin" });
  const body = await response.json() as Overview & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}
