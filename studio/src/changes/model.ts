import type { EvidenceTone } from "../components/evidence";

/** One journalled apply or rollback, as `GET /api/changes/history` and `citizen draft history` print it. */
export type ChangeEntry = {
  apply_id: string;
  kind: "apply" | "rollback";
  status: "completed" | "failed" | "recovered" | "abandoned" | "open";
  draft: string;
  draft_id: string;
  revision: string;
  ts: string;
  actor: string;
  reverses: string;
};

export type DraftStatus = "open" | "tested" | "applied" | "rolled back";

/** One draft in the inbox, as `GET /api/changes/drafts` and `citizen draft list` print it. */
export type ChangesDraft = {
  draft_id: string;
  name: string;
  path: string;
  branch: string;
  base: string;
  revision: string;
  behind_by: number | null;
  behind_installed: boolean;
  created_at: string;
  source: string;
  status: DraftStatus;
  tests: number;
  applied: ChangeEntry | null;
  rolled_back: ChangeEntry | null;
};

export type DraftsResponse = { schema_version: number; drafts: ChangesDraft[] };
export type HistoryResponse = { schema_version: number; entries: ChangeEntry[] };

// The steps a draft passes through, in order; a rolled-back draft has passed Applied and come back.
export const STEPS: ReadonlyArray<{ status: DraftStatus; label: string }> = [
  { status: "open", label: "Drafted" },
  { status: "tested", label: "Tested" },
  { status: "applied", label: "Applied" },
  { status: "rolled back", label: "Rolled back" },
];

const TONES: Record<DraftStatus, EvidenceTone> = {
  open: "neutral", tested: "info", applied: "success", "rolled back": "warning",
};

export function statusTone(status: DraftStatus): EvidenceTone {
  return TONES[status] ?? "neutral";
}

export function statusLabel(status: DraftStatus): string {
  return STEPS.find((step) => step.status === status)?.label ?? status;
}

/** Drafts still waiting on you (open or tested) first, then those applied or rolled back. */
export function inboxGroups(drafts: ChangesDraft[]): { waiting: ChangesDraft[]; settled: ChangesDraft[] } {
  const newest = [...drafts].sort((a, b) => b.created_at.localeCompare(a.created_at) || a.name.localeCompare(b.name));
  return {
    waiting: newest.filter((draft) => draft.status === "open" || draft.status === "tested"),
    settled: newest.filter((draft) => draft.status === "applied" || draft.status === "rolled back"),
  };
}

/** The apply a rollback from the inbox would reverse: the draft's latest apply, while it is in effect. */
export function rollbackTarget(draft: ChangesDraft): string {
  return draft.status === "applied" && draft.applied ? draft.applied.apply_id : "";
}

/** Whether a history row can be rolled back from the Studio: a completed apply or rollback. */
export function canUndo(entry: ChangeEntry): boolean {
  return entry.status === "completed";
}

export function entryLabel(entry: ChangeEntry): string {
  if (entry.kind === "rollback") return `Rolled back ${entry.reverses.slice(0, 12)} of ${entry.draft || "a draft"}`;
  return `Applied ${entry.draft || "a draft"}`;
}

export function shortTime(value: string): string {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value || "unknown time";
  return new Date(parsed).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

// The rule `citizen draft create` applies to a name (`drafts.NAME`).
const NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** Why a new draft name cannot be created, or "" when it can. */
export function newDraftError(name: string, drafts: ChangesDraft[]): string {
  const trimmed = name.trim();
  if (!trimmed) return "Name the new draft.";
  if (!NAME.test(trimmed)) return "Use letters, numbers, dots, underscores and hyphens, starting with a letter or number.";
  if (drafts.some((draft) => draft.name === trimmed)) return `A draft named ${trimmed} already exists; pick it instead.`;
  return "";
}

/** The picker's options: every draft, newest first, labelled with its status. */
export function pickerOptions(drafts: ChangesDraft[]): { value: string; label: string }[] {
  const { waiting, settled } = inboxGroups(drafts);
  return [...waiting, ...settled].map((draft) => ({ value: draft.name, label: `${draft.name} (${statusLabel(draft.status).toLowerCase()})` }));
}
