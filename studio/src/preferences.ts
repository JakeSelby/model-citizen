export type ColorScheme = "auto" | "light" | "dark";

let saveQueue: Promise<void> = Promise.resolve();

async function json<T>(response: Response): Promise<T> {
  const body = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `Request failed (${response.status}).`);
  return body;
}

export async function loadColorScheme(): Promise<ColorScheme> {
  const value = await json<{ color_scheme: ColorScheme }>(await fetch("/api/ui/preferences", {
    credentials: "same-origin",
  }));
  return value.color_scheme;
}

async function writeColorScheme(colorScheme: ColorScheme): Promise<void> {
  const session = await json<{ csrf_token: string }>(await fetch("/api/session", {
    credentials: "same-origin",
  }));
  await json(await fetch("/api/ui/preferences", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Studio-CSRF": session.csrf_token },
    body: JSON.stringify({ color_scheme: colorScheme }),
  }));
}

export function saveColorScheme(colorScheme: ColorScheme): Promise<void> {
  saveQueue = saveQueue.catch(() => undefined).then(() => writeColorScheme(colorScheme));
  return saveQueue;
}
