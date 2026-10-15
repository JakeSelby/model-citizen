import { useQuery } from "@tanstack/react-query";

import { loadTrends } from "../reports/trends/api";
import type { AutoTrim, BeforeAfter, Preregistrations, Qualification } from "./model";

export const EVALS_DAYS = 30;

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { method: "GET", credentials: "same-origin", signal });
  let body: (T & { error?: string }) | null = null;
  try {
    body = await response.json() as T & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body) throw new Error("The response was empty.");
  return body;
}

export const evalsQueries = {
  preregistrations: () => ({
    queryKey: ["evals", "preregistrations"] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => get<Preregistrations>("/api/evals/preregistrations", signal),
    staleTime: 60_000,
  }),
  qualification: () => ({
    queryKey: ["evals", "qualification"] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => get<Qualification>("/api/evals/evidence", signal),
    staleTime: 60_000,
  }),
  beforeAfter: (days = EVALS_DAYS) => ({
    queryKey: ["evals", "before-after", days] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => get<BeforeAfter>(`/api/evals/before-after?days=${days}`, signal),
    staleTime: 30_000,
  }),
  autoTrim: (days = EVALS_DAYS) => ({
    queryKey: ["evals", "auto-trim", days] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => get<AutoTrim>(`/api/evals/auto-trim?days=${days}`, signal),
    staleTime: 30_000,
  }),
  trends: () => ({
    queryKey: ["evals", "trends"] as const,
    queryFn: () => loadTrends(),
    staleTime: 60_000,
  }),
};

export const usePreregistrations = () => useQuery(evalsQueries.preregistrations());
// A tab's readings load when it is first opened; the Experiments tab never waits on them.
export const useQualification = (enabled = true) => useQuery({ ...evalsQueries.qualification(), enabled });
export const useBeforeAfter = (enabled = true) => useQuery({ ...evalsQueries.beforeAfter(), enabled });
export const useAutoTrim = (enabled = true) => useQuery({ ...evalsQueries.autoTrim(), enabled });
export const useEvalsTrends = () => useQuery(evalsQueries.trends());
