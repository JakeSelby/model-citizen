import type { ApplyResult } from "../configure/applyModel";
import type { ActivityEntry } from "./model";

export type RollbackPreview = {
  schema_version: number;
  apply: {
    apply_id?: string; kind?: "apply" | "rollback"; status?: string; draft?: string; revision?: string;
    ts?: string; actor?: string; reverses?: string;
  };
  destination: string;
  config: Array<{
    key: string; action: "set" | "unset" | "none" | "conflict";
    current: unknown; current_present: boolean; restored: unknown; restored_present: boolean;
  }>;
  files: Array<{ path: string; action: "write" | "delete" | "none" | "conflict" }>;
  commands: Array<{ step: "rollback" | "sync" | "check"; command: string }>;
  refusals: Array<{ code: string; message: string }>;
  can_rollback: boolean;
  rollback_command: string;
  nothing_changed: boolean;
};

export type RollbackResult = Omit<ApplyResult, "status" | "review"> & {
  status: "rolled-back" | "refused" | "failed";
  review: Partial<RollbackPreview>;
};

// Activity ids are `studio:<journal id>@<ledger offset>`; a link names the journal id alone.
const STUDIO_ID = /^studio:([0-9a-f]{32})(?:@\d+)?$/;
const LINK_ID = /^studio:[0-9a-f]{32}$/;

/** The journal id of a completed apply or rollback this Activity entry records, or "". */
export function rollbackTarget(entry: ActivityEntry): string {
  if (entry.source !== "studio-action" || entry.outcome !== "completed") return "";
  if (entry.kind !== "apply" && entry.kind !== "rollback") return "";
  return STUDIO_ID.exec(entry.id)?.[1] ?? "";
}

/** The Activity id a `?entry=` link points at, when it is one this page can show. */
export function focusedEntry(search: string): string {
  const value = new URLSearchParams(search).get("entry") ?? "";
  return LINK_ID.test(value) ? value : "";
}

/** Whether an Activity entry is the one a `?entry=` link names. */
export function isFocused(entry: ActivityEntry, focus: string): boolean {
  return focus !== "" && entry.id.split("@")[0] === focus;
}

/** Rolling back needs a clean preview on screen and the applied draft's name typed back. */
export function rollbackBlocker(preview: RollbackPreview | null, confirmation: string): string {
  if (preview === null) return "Preview the rollback first.";
  if (!preview.can_rollback) return "Rollback is refused until every finding below is resolved.";
  const draft = preview.apply.draft ?? "";
  if (!draft || confirmation !== draft) return `Type ${draft || "the draft name"} to confirm.`;
  return "";
}

export function shownValue(present: boolean, value: unknown): string {
  if (!present) return "unset";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** The headline the live region announces after a rollback. */
export function rollbackHeadline(result: RollbackResult): string {
  if (result.status === "rolled-back") return result.doctor.status === "attention"
    ? "Rolled back. The doctor checks need attention."
    : "Rolled back. The doctor checks ran.";
  if (result.error_code === "busy") return result.holder
    ? `Not rolled back: ${result.holder} holds the sync lock. Nothing changed.`
    : "Not rolled back: another operation holds the sync lock. Nothing changed.";
  if (result.status === "failed") return result.restored
    ? "Rollback failed safely. The configuration and files are as they were before it."
    : "Rollback failed and was not fully undone. Run citizen draft recover.";
  return "Not rolled back. Nothing changed.";
}
