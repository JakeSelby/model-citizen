import type { LibraryModule } from "../library/model";

export type AuthoringKind = "rules" | "skills" | "stances" | "modes";
export type AuthoringAction = "add" | "fork";

export type AuthoringTemplate = { kind: AuthoringKind; label: string; name_hint: string; detail: string };
export type ForkableModule = { key: string; kind: string; name: string };

export type AuthoringRead = {
  status: "ready" | "unavailable";
  message: string;
  draft: { name?: string; revision?: string };
  root: { id: string; label: string } | null;
  offer: { label?: string; registers?: string };
  templates: AuthoringTemplate[];
  forkable: ForkableModule[];
  nothing_applied: boolean;
  error_code: string;
};

export type AuthoringRequest = {
  action: AuthoringAction;
  kind?: AuthoringKind;
  source?: string;
  name: string;
  description: string;
  create_root: boolean;
};

export type AuthoringPreview = {
  valid: boolean;
  error: string;
  error_code: string;
  base_revision: string;
  action: string;
  module: { key: string; kind: string; name: string } | null;
  root: { id: string; label: string; created: boolean } | null;
  files: Array<{ path: string; text: string }>;
  manifest: Record<string, unknown> | null;
  fork: { source: string; version: string; revision: string } | null;
  config_changes: Array<{ path: string; value: string }>;
  findings: string[];
  nothing_applied: boolean;
};

export type AuthoringSave = AuthoringPreview & { saved: boolean; result: { revision?: string; replayed?: boolean } | null };

export type AuthoringForm = {
  action: AuthoringAction;
  kind: AuthoringKind;
  source: string;
  name: string;
  description: string;
  createRoot: boolean;
};

export const EMPTY_FORM: AuthoringForm = {
  action: "add", kind: "rules", source: "", name: "", description: "", createRoot: false,
};

const IDENTIFIER = /^[a-z][a-z0-9-]*$/;

/** The same name rule the resolver applies; the server stays the authority. */
export function nameProblem(form: AuthoringForm): string {
  if (form.action === "fork" && !form.name.trim()) return "";
  const name = form.name.trim();
  if (!name) return "Name the module.";
  if (form.action === "add" && form.kind === "stances") {
    const parts = name.split("/");
    return parts.length === 2 && parts.every((part) => IDENTIFIER.test(part))
      ? "" : "A stance variant is dimension/variant, each lowercase letters, digits and hyphens.";
  }
  return IDENTIFIER.test(name) ? "" : "Use lowercase letters, digits and hyphens, starting with a letter.";
}

/** Whether the draft must be offered a personal root before this request can be saved. */
export function needsRoot(read: Pick<AuthoringRead, "root"> | null): boolean {
  return read !== null && read.root === null;
}

export function toRequest(form: AuthoringForm): AuthoringRequest {
  const base = { name: form.name.trim(), description: form.description.trim(), create_root: form.createRoot };
  return form.action === "add"
    ? { action: "add", kind: form.kind, ...base }
    : { action: "fork", source: form.source, ...base };
}

export function canPreview(form: AuthoringForm, read: AuthoringRead | null): boolean {
  if (!read || read.status !== "ready" || nameProblem(form)) return false;
  if (needsRoot(read) && !form.createRoot) return false;
  if (form.action === "fork") return form.source !== "";
  return form.description.trim() !== "";
}

/** The draft's own modules, forks first, for the library view under the form. */
export function draftOwnedModules(modules: LibraryModule[]): LibraryModule[] {
  return modules.filter((module) => !module.root.core)
    .sort((left, right) => Number(right.fork !== null) - Number(left.fork !== null)
      || left.key.localeCompare(right.key));
}

export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function authoringCommand(draft: string, revision: string, save: boolean): string {
  const parts = ["citizen", "draft", "module", save ? "add" : "plan", shellQuote(draft), "--request", "request.json"];
  if (save) parts.push("--base-revision", shellQuote(revision), "--idempotency-key", "KEY");
  return [...parts, "--json"].join(" ");
}
