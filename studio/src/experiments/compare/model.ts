import { engineValue, type ReplayAnalysis, type ReplayTarget } from "../replay/model";

/** One side as the input form holds it: a finished replay run and one of its two targets. */
export type CompareSideInput = { run_id: string; target: 1 | 2 };
export type CompareInput = { base: CompareSideInput; candidate: CompareSideInput };

export type CompareSide = {
  run_id: string; target: number; ref: ReplayTarget; tasks: string[]; model: string;
  trials: number; max_budget_usd: string; run_status: string; stopped_at_cap: boolean;
  pack_digest: string | null; key: string; evidence: string;
  finished: Record<string, number>; stamps: Record<string, unknown[]>; freshness: "current" | "stale" | "not checked";
  stale: boolean; stale_reason: string | null;
  analysis: ReplayAnalysis | null;
};

/** One measure exactly as `replay_stats.compare` reports it. */
export type EngineMeasure = {
  label?: string; kind?: string; estimate?: number | null; effect?: number | null;
  interval?: [number, number] | null; n?: number; control_n?: number;
  reading?: string; reason?: string | null; [field: string]: unknown;
};

export type EngineArm = {
  arm: string; tasks: number; trials: number; exploratory: boolean;
  exploratory_reasons: string[]; measures: Record<string, EngineMeasure>;
};

export type EngineCompare = { confidence?: number; arms?: EngineArm[]; [field: string]: unknown };

export type CompareResult = {
  schema_version: number; engine: string; control: string;
  base: CompareSide; candidate: CompareSide; key_match: boolean;
  comparable: boolean; refusals: string[]; stale: string[]; notes: string[];
  direction_withheld: string[];
  preferred: Record<string, "lower" | "higher">; result: EngineCompare | null; error: string | null;
};

const RUN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** What stops the form from asking: one line per side whose run id is not a run id. */
export function validateCompare(input: CompareInput): string[] {
  const errors: string[] = [];
  for (const name of ["base", "candidate"] as const) {
    if (!RUN_ID.test(input[name].run_id.trim())) errors.push(`The ${name} needs a run id.`);
  }
  return errors;
}

export function compareErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    invalid_request: "Name a run id and target 1 or 2 for each side.",
    compare_not_found: "One of the runs is not known to this Studio.",
    compare_not_replay: "Only live replay runs can be compared.",
    compare_not_finished: "Both runs must have finished before they are compared.",
    compare_unavailable: "One side has no readable native rows or replay result.",
  };
  return messages[code] ?? `The comparison could not be read (${code}).`;
}

/** One line naming what a side measured: target, revision, tasks, model, trials and freshness. */
export function sideLine(side: CompareSide): string {
  const state = side.freshness === "stale" ? `Stale: ${side.stale_reason ?? "the draft changed"}`
    : side.freshness === "current" ? "Current" : "Freshness not checked";
  return `Run ${side.run_id}, target ${side.target}: ${side.ref.kind} ${side.ref.ref} at ` +
    `${side.ref.revision.slice(0, 12)}; ${side.tasks.length} task(s), ${side.model}, ` +
    `${side.trials} trial(s), ${side.evidence}. ${state}.`;
}

/** The engine's measure order (`replay_stats.MEASURES`); a measure it adds later follows them. */
export const MEASURE_ORDER = [
  "cost_usd", "output_tokens", "first_call_context", "turns", "tool_calls", "pass_rate",
  "cost_per_passed",
];

/** A headline per measure, built only from the engine's own fields. A figure never appears
 * without its interval and its trial counts beside it; an interval the engine could not give
 * shows as the engine's `null` with its reason. */
export type Direction = "better" | "worse" | null;

/** The engine's reading with the preferred direction from the route's one table: `lower (better)`,
 * `higher (worse)`, or the reading alone when it is inconclusive or the measure has no direction. */
export function verdictOf(reading: string, preferred: "lower" | "higher" | undefined): { label: string; direction: Direction } {
  if (!preferred || (reading !== "lower" && reading !== "higher")) return { label: reading, direction: null };
  const direction: Direction = reading === preferred ? "better" : "worse";
  return { label: `${reading} (${direction})`, direction };
}

/** The direction table the headlines may use: none while the route withholds a direction. */
export function directionsFor(result: CompareResult): Record<string, "lower" | "higher"> {
  return result.direction_withheld.length ? {} : result.preferred;
}

export function measureHeadlines(result: EngineCompare | null, preferred: Record<string, "lower" | "higher"> = {}): Array<{
  key: string; arm: string; reading: string; verdict: string; direction: Direction; line: string;
}> {
  if (!result?.arms) return [];
  const confidence = engineValue(result.confidence);
  return result.arms.flatMap((arm) => {
    const keys = [...MEASURE_ORDER.filter((key) => key in arm.measures),
      ...Object.keys(arm.measures).filter((key) => !MEASURE_ORDER.includes(key))];
    return keys.map((key) => {
      const m = arm.measures[key];
      const reading = String(m.reading ?? "unknown");
      const verdict = verdictOf(reading, preferred[key]);
      const parts = [
        `${m.label ?? key}: ${verdict.label}`,
        `estimand ${engineValue(m.estimand)}`,
        `estimate ${engineValue(m.estimate)}, effect ${engineValue(m.effect)}`,
        `interval ${engineValue(m.interval)} at confidence ${confidence}`,
        `n ${engineValue(m.n)} against ${engineValue(m.control_n)}`,
        `${engineValue(arm.trials)} paired trial(s) per task over ${engineValue(arm.tasks)} task(s)`,
      ];
      if (arm.exploratory) parts.push(`exploratory: ${arm.exploratory_reasons.join("; ") || "the engine marked it exploratory"}`);
      if (m.reason) parts.push(String(m.reason));
      return { key, arm: arm.arm, reading, verdict: verdict.label, direction: verdict.direction, line: parts.join("; ") };
    });
  });
}
