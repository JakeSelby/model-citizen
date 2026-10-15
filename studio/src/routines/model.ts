// The Routines page's data, as `GET /api/routines` (`citizen studio routine list --json`) answers.

export type RoutineOutcome = "ok" | "failed" | "running";

export type RoutineRunSummary = {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  outcome: RoutineOutcome;
  summary: string;
};

export type RoutineRun = RoutineRunSummary & {
  routine: string;
  trigger: "citizen" | "studio" | "scheduled";
  details: Record<string, unknown>;
};

export type RoutineStatus = {
  name: string;
  title: string;
  description: string;
  command: string;
  installed: boolean;
  schedule: { interval_seconds: number; label: string };
  last_run: RoutineRunSummary | null;
  outcome: RoutineOutcome | "never";
  next_run: string | null;
  overdue: boolean;
};

export type RoutinesListing = { routines: RoutineStatus[]; history: Record<string, RoutineRun[]> };

export type ChangeAction = "install" | "remove";

const OUTCOME_LABELS: Record<RoutineStatus["outcome"], string> = {
  ok: "Passed", failed: "Failed", running: "Running", never: "Never run",
};
const OUTCOME_COLORS: Record<RoutineStatus["outcome"], string> = {
  ok: "teal", failed: "red", running: "blue", never: "gray",
};
const TRIGGER_LABELS: Record<RoutineRun["trigger"], string> = {
  scheduled: "Scheduled", studio: "From the Studio", citizen: "From the CLI",
};

export function outcomeLabel(outcome: RoutineStatus["outcome"]): string {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

export function outcomeColor(outcome: RoutineStatus["outcome"]): string {
  return OUTCOME_COLORS[outcome] ?? "gray";
}

export function triggerLabel(trigger: RoutineRun["trigger"]): string {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

/** A timestamp as "Oct 15 14:00 UTC"; an empty value as a dash. */
export function when(value: string | null | undefined): string {
  if (!value) return "–";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const month = date.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const time = date.toISOString().slice(11, 16);
  return `${month} ${date.getUTCDate()} ${time} UTC`;
}

/** How long a finished run took, as "4 s" or "2 min 5 s"; a dash while it runs. */
export function duration(run: RoutineRunSummary): string {
  if (!run.finished_at) return "–";
  const seconds = Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000));
  if (Number.isNaN(seconds)) return "–";
  if (seconds < 60) return `${seconds} s`;
  const rest = seconds % 60;
  return `${Math.floor(seconds / 60)} min${rest ? ` ${rest} s` : ""}`;
}

/** The schedule cell: its interval, then the next run or why there is none. */
export function scheduleText(routine: RoutineStatus): string {
  if (!routine.installed) return `${routine.schedule.label} · not installed`;
  return `${routine.schedule.label} · next ${when(routine.next_run)}`;
}

/** The words the typed confirmation asks for, the routine's own name, as an apply asks for its draft's. */
export function confirmationBlocker(routine: RoutineStatus, typed: string): string {
  return typed === routine.name ? "" : `Type ${routine.name} to confirm.`;
}

/** Which change the routine offers: install when it has no agent, remove when it has one. */
export function changeFor(routine: RoutineStatus): ChangeAction {
  return routine.installed ? "remove" : "install";
}

/** The message a refused request names, in words. */
export function refusalText(error: string): string {
  switch (error) {
    case "person_confirmation_required":
      return "Nobody confirmed at this Mac with Touch ID or the login password, so nothing changed.";
    case "confirmation_required":
      return "Type the routine's name to confirm.";
    case "routine_running":
      return "This routine is already running.";
    case "unknown_routine":
      return "No routine has that name.";
    default:
      return error;
  }
}
