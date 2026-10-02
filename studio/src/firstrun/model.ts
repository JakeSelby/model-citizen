export const FIRST_RUN_DRAFT = "first-run";

export type FirstRunState = "not-started" | "in-progress" | "interrupted" | "complete";
export type StepId = "health" | "draft" | "identity" | "preferences" | "check" | "apply" | "done";

export type FirstRunChoice = {
  key: string;
  action: "set" | "unset";
  value: string | null;
  command: string;
};

export type ApplyOffer = { apply_id?: string; draft?: string; recover_command?: string; abandon_command?: string };

export type FirstRunStatus = {
  schema_version: number;
  state: FirstRunState;
  fresh: boolean;
  nothing_live_changed: boolean;
  draft_name: string;
  draft: { name?: string; draft_id?: string; revision?: string; base_revision?: string; behind_installed?: boolean; created_at?: string };
  applied: { apply_id?: string; ts?: string; revision?: string; doctor?: string };
  interrupted: ApplyOffer;
  blocked_by: ApplyOffer;
  steps: Array<{ id: StepId; label: string; command: string }>;
  choices: FirstRunChoice[];
  commands: { headless: string[]; agent: string[]; status: string };
};

export const STEP_ORDER: StepId[] = ["health", "draft", "identity", "preferences", "check", "apply", "done"];

/** The Studio opens on the guide only for a fresh install that has not started it. */
export function opensGuide(status: FirstRunStatus | null, alreadyOpened: boolean): boolean {
  return !alreadyOpened && status !== null && status.fresh && status.state === "not-started";
}

/** Where the guide picks up: a kept draft resumes after its creation, an open apply at review. */
export function resumeStep(status: FirstRunStatus): StepId {
  if (status.state === "complete") return "done";
  if (status.state === "interrupted") return "apply";
  if (status.state === "in-progress") return "identity";
  return "health";
}

/** A step past the draft needs the draft; done needs a completed apply. */
export function stepReachable(status: FirstRunStatus, step: StepId): boolean {
  if (step === "health" || step === "draft") return true;
  if (step === "done") return status.state === "complete";
  return status.state !== "not-started" && Boolean(status.draft.revision);
}

export function nextStep(step: StepId): StepId {
  return STEP_ORDER[Math.min(STEP_ORDER.indexOf(step) + 1, STEP_ORDER.length - 1)];
}

export function entryCopy(status: FirstRunStatus): { title: string; body: string; action: string } | null {
  if (status.state === "complete") return null;
  if (status.state === "interrupted") {
    return {
      title: "Setup stopped during an apply",
      body: `An apply of draft ${status.draft_name} was interrupted. Restore it or abandon it before anything else applies.`,
      action: "Open setup",
    };
  }
  if (status.state === "in-progress") {
    return {
      title: "Setup is waiting for you",
      body: `Draft ${status.draft_name} keeps your choices so far. Setup has changed nothing live.`,
      action: "Resume setup",
    };
  }
  // A home already set up from the CLI is not offered a first run it does not need.
  if (!status.fresh) return null;
  return {
    title: "Set up the harness",
    body: "A guided first run takes a draft from this install to an applied, checked setup. Nothing changes live until you apply.",
    action: "Start setup",
  };
}

export function liveChangeNotice(status: FirstRunStatus): string {
  if (status.state === "complete") return "Applied through the governed path.";
  if (status.state === "interrupted") return "An apply was interrupted; recover it before continuing.";
  return status.state === "in-progress"
    ? `Setup has changed nothing live. Draft ${status.draft_name} is kept so you can resume.`
    : "Nothing changes live until you review and apply.";
}

export function doctorPassed(status: FirstRunStatus): boolean {
  return status.state === "complete" && status.applied.doctor === "passed";
}

const ERRORS: Record<string, string> = {
  first_run_busy: "The draft is busy saving a checkpoint. Try again in a moment.",
  "create-timeout": "Creating the draft took too long and was stopped. What it left was removed; start again.",
  "partial-draft-kept": "A leftover draft branch from an earlier attempt may hold work, so setup left it alone. Inspect it with `git worktree list`, remove it if it holds nothing you need, then start again.",
  "stale-again": "The draft changed again while saving. Your entries are kept; select Save again to save them on its latest checkpoint.",
  "create-unavailable": "The draft could not be created because the CLI did not answer. Start again, or run the command shown.",
  "create-failed": "The draft could not be created. Run the command shown in a terminal to see why.",
  "create-cleanup-failed": "Creating the draft failed and what it left could not be removed. Find the draft branch with `git worktree list`, remove it, then start again.",
  unreadable: "Setup could not read its state from this machine. Run `citizen draft first-run --json` to see why.",
  "stale-revision": "The draft changed elsewhere. Reload the page to continue from its latest checkpoint.",
};

/** A human sentence for an error code a first-run route or save returned. */
export function errorMessage(reason: unknown): string {
  const code = reason instanceof Error ? reason.message : String(reason ?? "");
  return ERRORS[code] ?? (code ? `Setup stopped: ${code}.` : "Setup stopped for an unknown reason.");
}

let guideOpened = false;

/** True once per page load for a fresh, unstarted install, so leaving the guide does not loop. */
export function claimGuideOpen(status: FirstRunStatus | null): boolean {
  if (!opensGuide(status, guideOpened)) return false;
  guideOpened = true;
  return true;
}

export function resetGuideClaim(): void {
  guideOpened = false;
}
