import type {
  NativeCatalog, NativeRun, NativeSelection, NativeSnapshot, SpendPreview, SpendRequest,
} from "./model";

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

async function csrf(): Promise<string> {
  return (await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }))).csrf_token;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const token = await csrf();
  return json<T>(await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": token },
    body: JSON.stringify(body),
  }));
}

export async function loadNativeCatalog(): Promise<NativeCatalog> {
  return json<NativeCatalog>(await fetch("/api/experiments/native-acceptance/catalog", {
    credentials: "same-origin",
  }));
}

export async function loadNativeProgress(selection: NativeSelection): Promise<NativeSnapshot> {
  return post<NativeSnapshot>("/api/experiments/native-acceptance/progress", { selection });
}

export async function previewNativeRun(selection: NativeSelection, spend: SpendRequest) {
  return post<SpendPreview>("/api/experiments/native-acceptance/preview", {
    selection, spend,
  });
}

export async function startNativeRun(
  selection: NativeSelection,
  spend: SpendRequest,
  confirmationToken: string,
) {
  return post<NativeRun>("/api/experiments/native-acceptance/start", {
    selection, spend, confirmation_token: confirmationToken,
  });
}

export async function retryFailedCase(selection: NativeSelection, caseId: string) {
  return post<NativeSelection>("/api/experiments/native-acceptance/retry", {
    selection, case: caseId,
  });
}
