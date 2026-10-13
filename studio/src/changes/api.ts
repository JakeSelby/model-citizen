import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ChangesDraft, DraftsResponse, HistoryResponse } from "./model";

async function json<T>(response: Response): Promise<T> {
  let body: (T & { error?: string }) | null = null;
  try {
    body = await response.json() as T & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok || body === null) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  return body;
}

/** Every managed draft with its status; `citizen draft list --json` prints the same. */
export async function loadDrafts(signal?: AbortSignal): Promise<DraftsResponse> {
  return json<DraftsResponse>(await fetch("/api/changes/drafts", { credentials: "same-origin", signal }));
}

/** Every apply and rollback, newest first; `citizen draft history --json` prints the same. */
export async function loadHistory(signal?: AbortSignal): Promise<HistoryResponse> {
  return json<HistoryResponse>(await fetch("/api/changes/history", { credentials: "same-origin", signal }));
}

/** `citizen draft create NAME --source PAGE`: a new managed draft from the installed version. */
export async function createDraft(name: string, source = ""): Promise<ChangesDraft> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return json<ChangesDraft>(await fetch("/api/changes/drafts/create", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({ draft: name.trim(), source }),
  }));
}

export const draftsQuery = {
  queryKey: ["changes-drafts"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadDrafts(signal),
  staleTime: 10_000,
};

export const historyQuery = {
  queryKey: ["changes-history"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadHistory(signal),
  staleTime: 10_000,
};

export type DraftsState = {
  drafts: ChangesDraft[];
  status: "loading" | "error" | "ready";
  error: string;
  /** Read the list again, as after an apply, test or rollback elsewhere. */
  refresh: () => Promise<void>;
  /** Create a draft and refresh the list; resolves to the new draft, rejects with the refusal. */
  create: (name: string, source?: string) => Promise<ChangesDraft>;
};

/**
 * The managed drafts, for the Changes inbox and for any page that picks or creates a draft
 * (`DraftPicker`). One cached read is shared by every caller on the page.
 */
export function useDrafts(): DraftsState {
  const queries = useQueryClient();
  const read = useQuery(draftsQuery);
  async function refresh() {
    await queries.invalidateQueries({ queryKey: draftsQuery.queryKey });
    await queries.invalidateQueries({ queryKey: historyQuery.queryKey });
  }
  return {
    drafts: read.data?.drafts ?? [],
    status: read.isError ? "error" : read.isPending ? "loading" : "ready",
    error: read.error instanceof Error ? read.error.message : "",
    refresh,
    create: async (name, source = "") => {
      const created = await createDraft(name, source);
      await refresh();
      return created;
    },
  };
}

export function useHistory() {
  return useQuery(historyQuery);
}
