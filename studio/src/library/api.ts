import type { LibraryPayload } from "./model";

export async function loadLibrary(): Promise<LibraryPayload> {
  const response = await fetch("/api/library", { credentials: "same-origin" });
  const body = await response.json() as LibraryPayload & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}
