// The routines routes: `GET /api/routines` and the three POSTs, each with the session's CSRF token.
import type { ChangeAction, RoutineRun, RoutineStatus, RoutinesListing } from "./model";

export class RoutineRefused extends Error {
  readonly code: string;
  readonly reason: string;

  constructor(code: string, reason = "") {
    super(reason || code);
    this.code = code;
    this.reason = reason;
  }
}

async function read<T>(response: Response): Promise<T> {
  let body: (T & { error?: string; reason?: string }) | null = null;
  try {
    body = await response.json() as T & { error?: string; reason?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (!response.ok) throw new RoutineRefused(body?.error ?? `Request failed (${response.status}).`, body?.reason ?? "");
  if (body === null) throw new Error("The routines response is empty.");
  return body;
}

export async function loadRoutines(signal?: AbortSignal): Promise<RoutinesListing> {
  const body = await read<RoutinesListing>(await fetch("/api/routines", { credentials: "same-origin", signal }));
  if (!Array.isArray(body.routines) || typeof body.history !== "object" || body.history === null) {
    throw new Error("The routines response is incomplete.");
  }
  return body;
}

export const routinesQuery = {
  queryKey: ["routines"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadRoutines(signal),
  staleTime: 15_000,
};

async function post<T>(path: string, body: Record<string, string>): Promise<T> {
  const session = await read<{ csrf_token: string }>(await fetch("/api/session", { credentials: "same-origin" }));
  return read<T>(await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify(body),
  }));
}

export async function runRoutine(name: string): Promise<RoutineRun> {
  return (await post<{ run: RoutineRun }>("/api/routines/run", { name })).run;
}

/** Install or remove the routine's launchd agent; the Mac then asks its person to confirm. */
export async function changeRoutine(action: ChangeAction, name: string, confirm: string): Promise<RoutineStatus> {
  return (await post<{ routine: RoutineStatus }>(`/api/routines/${action}`, { name, confirm })).routine;
}
