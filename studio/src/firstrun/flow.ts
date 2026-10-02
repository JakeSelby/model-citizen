import { loadDraft, previewDraft, saveDraft } from "../configure/api";
import { loadFirstRun, startFirstRun } from "./api";
import { errorMessage, resumeStep, type FirstRunStatus, type StepId } from "./model";

export type IdentityOutcome =
  | { saved: true; revision: string; message: string }
  | { saved: false; revision: string; message: string; errors: Record<string, string> };

const BUSY_RETRIES = 3;
const BUSY_DELAY_MS = 1500;

function stale(errors: Array<{ path: string; message: string }>): boolean {
  return errors.some((item) => item.path === "draft" && /revision changed|moved outside/.test(item.message));
}

/**
 * Check and checkpoint identity fields. A stale revision (a save elsewhere) reloads the draft's
 * latest revision and retries once, since the changes are path-keyed; a second refusal is reported.
 */
export async function saveIdentity(
  draft: string, revision: string, changes: Record<string, unknown>,
): Promise<IdentityOutcome> {
  const planned = await previewDraft(draft, changes);
  if (!planned.valid) {
    return { saved: false, revision, message: "Fix the highlighted fields. Nothing was saved.",
      errors: Object.fromEntries(planned.errors.map((item) => [item.path, item.message])) };
  }
  let current = revision;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const saved = await saveDraft(draft, current, changes);
    if (saved.saved) {
      return { saved: true, revision: saved.result?.revision ?? current, message: "Saved in the draft. Nothing was applied." };
    }
    if (!stale(saved.errors)) {
      // Field errors stay on their fields; anything about the draft itself becomes the message.
      const fields = Object.fromEntries(saved.errors.filter((item) => item.path !== "draft")
        .map((item) => [item.path, item.message]));
      const reason = saved.errors.filter((item) => item.path === "draft").map((item) => item.message).join("; ");
      return { saved: false, revision: current, errors: fields,
        message: reason ? `Nothing was saved: ${reason}.` : "Nothing was saved. Fix the highlighted fields." };
    }
    if (attempt > 0) {
      return { saved: false, revision: current, errors: {}, message: errorMessage("stale-again") };
    }
    const latest = await loadDraft(draft);
    if (!latest.draft.revision || latest.draft.revision === current) {
      return { saved: false, revision: current, errors: {}, message: errorMessage("stale-revision") };
    }
    current = latest.draft.revision;
  }
  return { saved: false, revision: current, errors: {}, message: errorMessage("stale-revision") };
}

/** Read the run, waiting out a draft that is busy saving a few times before giving up. */
export async function loadStatus(draft: string, delay = BUSY_DELAY_MS): Promise<FirstRunStatus> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await loadFirstRun(draft);
    } catch (reason) {
      const busy = reason instanceof Error && reason.message === "first_run_busy";
      if (!busy || attempt + 1 >= BUSY_RETRIES) throw new Error(errorMessage(reason));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}

/** Create or resume the draft; a failure becomes a sentence the user can act on. */
export async function beginSetup(draft: string): Promise<FirstRunStatus> {
  try {
    return await startFirstRun(draft);
  } catch (reason) {
    throw new Error(errorMessage(reason));
  }
}

/** After an apply or a recovery: where the guide goes next. */
export async function afterApply(draft: string, delay = BUSY_DELAY_MS): Promise<{ status: FirstRunStatus; step: StepId }> {
  const status = await loadStatus(draft, delay);
  return { status, step: status.state === "complete" ? "done" : status.state === "not-started" ? resumeStep(status) : "apply" };
}
