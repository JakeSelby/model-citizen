// The Sessions page's data: `GET /api/sessions` (`citizen sessions list --json`) and
// `GET /api/sessions/session` (`citizen sessions show ID --json`), both read from the transcript
// index. Every figure is observed in the user's own transcripts, never a measured effect.

export type ContextLines = { warn: number; limit: number };

export type ResetEvent = {
  session_id: string;
  ts: string | null;
  kind: "compact" | "reseed";
  trigger: "auto" | "manual" | null;
  pre_tokens: number | null;
  post_tokens: number | null;
};

export type SessionRow = {
  session_id: string;
  title: string | null;
  project: string;
  repository: string;
  started: string | null;
  ended: string | null;
  models: string[];
  calls: number;
  subagent_calls: number;
  turns: number;
  cost: number | null;
  cost_complete: boolean;
  cost_per_turn: number | null;
  max_context: number;
  median_context: number;
  over_warn: number;
  over_limit: number;
  compacts: number;
  reseeds: number;
};

export type SessionsSummary = {
  sessions: number;
  calls: number;
  median_context: number;
  over_warn: number;
  over_limit: number;
  cost: number | null;
  unpriced_calls: number;
  turns: number;
  median_turn_cost: number | null;
  mean_turn_cost: number | null;
  compacts: number;
  auto_compacts: number;
  reseeds: number;
};

export type Bucket = { from: number; to: number | null; calls: number };

export type SessionsList = {
  window_days: number;
  indexing: boolean;
  pending: number;
  lines: ContextLines;
  basis: string;
  summary: SessionsSummary;
  histogram: Bucket[];
  sessions: SessionRow[];
  events: ResetEvent[];
};

export type CallPoint = {
  index: number;
  ts: string | null;
  model: string;
  turn: number;
  context_tokens: number;
  output: number;
  cost: number | null;
  event: "compact" | "reseed" | null;
};

export type TurnRow = { turn: number; ts: string | null; calls: number; cost: number | null; cost_complete: boolean; max_context: number };

export type SessionDetail = {
  session: SessionRow;
  lines: ContextLines;
  basis: string;
  calls: CallPoint[];
  turns: TurnRow[];
  events: ResetEvent[];
};

export const WINDOWS = [7, 30] as const;
export const TOP = 6;
/** The page's own label for every figure on it: an observation, not evidence. */
export const OBSERVED = "Observed in your transcripts";

export class UnknownSession extends Error {}

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { method: "GET", credentials: "same-origin", signal });
  let body: (T & { error?: string }) | null = null;
  try {
    body = await response.json() as T & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (response.status === 404) throw new UnknownSession(url);
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body) throw new Error("The sessions response is empty.");
  return body;
}

export async function loadSessions(days: number, signal?: AbortSignal): Promise<SessionsList> {
  const body = await read<SessionsList>(`/api/sessions?days=${days}`, signal);
  if (!Array.isArray(body.sessions) || !Array.isArray(body.histogram)) throw new Error("The sessions response is incomplete.");
  return body;
}

export async function loadSession(id: string, signal?: AbortSignal): Promise<SessionDetail> {
  const body = await read<SessionDetail>(`/api/sessions/session?id=${encodeURIComponent(id)}`, signal);
  if (!Array.isArray(body.calls) || !body.session) throw new Error("The session response is incomplete.");
  return body;
}

export function sessionsQuery(days: number) {
  return {
    queryKey: ["sessions", days] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => loadSessions(days, signal),
    staleTime: 30_000,
    // While the index warms, ask again until it has every transcript.
    refetchInterval: (query: { state: { data?: SessionsList } }) => query.state.data?.indexing ? 2_000 : false as const,
  };
}

export function sessionQuery(id: string) {
  return {
    queryKey: ["session", id] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => loadSession(id, signal),
    staleTime: 30_000,
    retry: (count: number, error: Error) => !(error instanceof UnknownSession) && count < 2,
  };
}

export function sessionHref(id: string): string {
  return `/sessions/${encodeURIComponent(id)}`;
}

/** The window from `?days=`, one of the page's windows, else 30. */
export function parseDays(params: URLSearchParams): number {
  const days = Number(params.get("days"));
  return (WINDOWS as readonly number[]).includes(days) ? days : 30;
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

export function dollars(n: number | null): string {
  if (n === null) return "unpriced";
  return n < 0.01 && n > 0 ? "<$0.01" : `$${n.toFixed(2)}`;
}

export function share(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : "0%";
}

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export type Band = "under" | "warn" | "limit";

/** Which side of the two lines a context falls: under 160k, between, or past 400k. */
export function band(context: number, lines: ContextLines): Band {
  return context > lines.limit ? "limit" : context > lines.warn ? "warn" : "under";
}

export const SORTS = { cost: "Cost", peak: "Peak context", calls: "Calls" } as const;
export type SortKey = keyof typeof SORTS;

export function topSessions(rows: SessionRow[], sort: SortKey, limit = TOP): SessionRow[] {
  const value = (row: SessionRow) => sort === "cost" ? (row.cost ?? -1) : sort === "peak" ? row.max_context : row.calls;
  return [...rows].sort((a, b) => value(b) - value(a) || a.session_id.localeCompare(b.session_id)).slice(0, limit);
}

/** The headline: the median call against the warn line, in words. */
export function headline(summary: SessionsSummary, lines: ContextLines): string {
  if (!summary.calls) return "No calls in this window yet.";
  const relation = summary.median_context > lines.warn ? "above" : "under";
  return `Your median call carried ${tokens(summary.median_context)} tokens of context, ${relation} your ${tokens(lines.warn)} line.`;
}
