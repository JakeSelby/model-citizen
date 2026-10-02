export const FIRST_RUN_DRAFT = "first-run";

export type FirstRunState = "not-started" | "in-progress" | "interrupted" | "complete";
export type StepId = "health" | "draft" | "identity" | "preferences" | "check" | "apply" | "done";

export type FirstRunChoice = {
  key: string;
  action: "set" | "unset";
  value: string | null;
  command: string;
};

export type FirstRunStatus = {
  schema_version: number;
  state: FirstRunState;
  fresh: boolean;
  nothing_live_changed: boolean;
  draft_name: string;
  draft: { name?: string; revision?: string; base_revision?: string; behind_installed?: boolean; created_at?: string };
  applied: { apply_id?: string; ts?: string; revision?: string; doctor?: string };
  interrupted: { apply_id?: string; draft?: string; recover_command?: string; abandon_command?: string };
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
      body: `Draft ${status.draft_name} keeps your choices so far. Nothing live has changed.`,
      action: "Resume setup",
    };
  }
  return {
    title: "Set up the harness",
    body: "A guided first run takes a draft from this install to an applied, checked setup. Nothing live changes until you apply.",
    action: "Start setup",
  };
}

export function liveChangeNotice(status: FirstRunStatus): string {
  if (status.state === "complete") return "Applied through the governed path.";
  if (status.state === "interrupted") return "An apply was interrupted; recover it before continuing.";
  return status.state === "in-progress"
    ? `Nothing live has changed. Draft ${status.draft_name} is kept so you can resume.`
    : "Nothing live has changed.";
}

export function doctorPassed(status: FirstRunStatus): boolean {
  return status.state === "complete" && status.applied.doctor === "passed";
}
