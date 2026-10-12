import { useQuery } from "@tanstack/react-query";

import type { PeriodsResponse } from "./model";

/** One surface's setting periods, read from the local ledgers at request time. */
export async function loadPeriods(surface: string, signal?: AbortSignal): Promise<PeriodsResponse> {
  const response = await fetch(`/api/harness/periods?surface=${encodeURIComponent(surface)}`, {
    method: "GET",
    credentials: "same-origin",
    signal,
  });
  let body: (PeriodsResponse & { error?: string }) | null = null;
  try {
    body = await response.json() as PeriodsResponse & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body || !Array.isArray(body.rows)) throw new Error("The periods response has no rows.");
  return body;
}

export function periodsQuery(surface: string) {
  return {
    queryKey: ["harness-periods", surface] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => loadPeriods(surface, signal),
    staleTime: 30_000,
  };
}

export function usePeriods(surface: string) {
  return useQuery(periodsQuery(surface));
}
