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

const APPLY_ID = /^[0-9a-f]{32}$/;

/** The id the engine says this entry can be rolled back by, or "". */
export function rollbackTarget(entry: ActivityEntry): string {
  return entry.rollback_target ?? "";
}

/** The apply id an `?apply=` link names, when it is a well-formed one. */
export function focusedEntry(search: string): string {
  const value = new URLSearchParams(search).get("apply") ?? "";
  return APPLY_ID.test(value) ? value : "";
}

/** Whether an Activity entry is the apply or rollback an `?apply=` link names. */
export function isFocused(entry: ActivityEntry, focus: string): boolean {
  return focus !== "" && entry.apply_id === focus;
}

/** What to say when the linked entry is not among the loaded ones, or "". */
export function missingFocus(entries: ActivityEntry[], focus: string, more: boolean): string {
  if (focus === "" || entries.some((entry) => isFocused(entry, focus))) return "";
  return more
    ? `The linked change ${focus.slice(0, 12)} is not in the activity loaded so far. Load older activity to find it.`
    : `The linked change ${focus.slice(0, 12)} is not in the activity log.`;
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

/**
 * A finished rollback as Activity keeps it on screen: outside the list a refresh replaces, until
 * it is dismissed. `result` is the engine's answer; `error` is set instead when none arrived.
 */
export type RollbackNotice = { applyId: string; draft: string; result: RollbackResult | null; error: string };

export function rollbackNotice(applyId: string, draft: string, result: RollbackResult | null, error = ""): RollbackNotice {
  return { applyId, draft, result, error: result ? "" : error || "The rollback did not report a result. Check Activity before retrying." };
}

/** The one line the status region announces for a notice. */
export function noticeHeadline(notice: RollbackNotice): string {
  return notice.result ? rollbackHeadline(notice.result) : `Rollback did not complete. ${notice.error}`;
}

export function noticeTone(notice: RollbackNotice): "success" | "warning" | "danger" {
  if (notice.result?.status !== "rolled-back") return "danger";
  return notice.result.doctor.status === "attention" ? "warning" : "success";
}

/** What a completed rollback restored, read from the review the engine returns with it. */
export function restoredChanges(notice: RollbackNotice): { keys: RollbackPreview["config"]; files: RollbackPreview["files"] } {
  if (notice.result?.status !== "rolled-back") return { keys: [], files: [] };
  const review = notice.result.review;
  return {
    keys: (review.config ?? []).filter((row) => row.action !== "none"),
    files: (review.files ?? []).filter((row) => row.action !== "none"),
  };
}
