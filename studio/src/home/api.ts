// `GET /api/home` (`citizen home --json`): the daily digest, projection drift and the queue.
export type Severity = "high" | "medium" | "low";

export type AttentionItem = {
  id: string;
  source: string;
  kind: string;
  severity: Severity;
  title: string;
  detail: string;
  at: string | null;
  action: { label: string; href: string };
};

export type Queue = { items: AttentionItem[]; total: number; sources: Record<string, "ok" | "error"> };

export type ReadyDigest = {
  status: "ready";
  days: number;
  basis: string;
  indexing: boolean;
  pending: number;
  sessions: number;
  calls: number;
  cost: number | null;
  cost_complete: boolean;
  median_context: number | null;
  max_context: number | null;
  calls_over_warn: number;
  calls_over_limit: number;
  sessions_over_limit: number;
  context_warn: number;
  context_limit: number;
};

export type Digest = ReadyDigest | { status: "unavailable"; message: string };

export type Drift = {
  status: "current" | "drift" | "failed";
  items: string[];
  command?: string;
  message?: string | null;
};

export type HomeReport = {
  schema_version: number;
  generated_at: string;
  digest: Digest;
  drift: Drift;
  queue: Queue;
};

export async function loadHome(signal?: AbortSignal): Promise<HomeReport> {
  const response = await fetch("/api/home", { method: "GET", credentials: "same-origin", signal });
  let body: (HomeReport & { error?: string }) | null = null;
  try {
    body = await response.json() as HomeReport & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body || !body.digest || !body.drift || !body.queue || !Array.isArray(body.queue.items)) {
    throw new Error("The Home response is incomplete.");
  }
  return body;
}

export const homeQuery = {
  queryKey: ["home"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadHome(signal),
  staleTime: 30_000,
};
