/** The trends route's payload (`lib/harness_core/studio/trends.py`). Nothing here computes a figure. */
export type Figure = { value: number | null; interval: [number, number] | null; interval_kind: string | null };
export type Evidence = { label: "exploratory" | "pre-registered"; reason: string; pre_registration: string | null };
export type MeasureId = "ratio_sm2" | "ratio" | "ratio_cache_normalised" | "pass_rate_harness" | "pass_rate_bare" | "pass_rate_difference";
export type Measure = { id: string; label: string; unit: string; note: string };
export type Point = {
  run_id: string | null; date: string | null; harness_version: string | null; tag: string | null;
  harness_sha: string | null; model: string | null; cli_version: string | null; series: string; bucket: string;
  change_note: string; status: string | null; cache_basis: string | null; reps: number | null;
  evidence: Evidence; measures: Record<string, Figure>;
  sm2: { verdict: string | null; reason: string | null; limitation: string | null; eligible: boolean | null; confidence: number | null; unavailable: string | null };
  delegation: { label: string | null; verdicts: Record<string, number> } | null;
  source: { path: string | null; line: number | null };
};
export type Line = { id: string; series: string; bucket: string; evidence: "exploratory" | "pre-registered" | "mixed"; evidence_note: string; points: Point[] };
export type StaticPoint = { run_id: string | null; harness_version: string; value: number | null; files: number | null };
export type Card = { id: string | null; claim: string | null; estimand: string | null; figure: unknown; interval: unknown; verify_status: boolean; published: { field: string; text: string } | null };
export type Bundle = { bundle: string; status: "verified" | "failed"; bundle_id: string | null; errors: string[]; unknown: string[]; checks: Record<string, boolean>; cards: Card[]; command: string };
export type Claim = { field: string; text: string; bundle: string; card: string; verify_status: boolean };
export type Trends = {
  schema_version: number; generated_at: string; ratio_note: string; measures: Measure[]; lines: Line[];
  static: { measure: Measure; points: StaticPoint[] };
  not_tracked: { measure: string; reason: string; where: string }[];
  truncated: boolean;
  proof: { statement: string | null; claims: Claim[]; bundles: Bundle[]; unverified_bundles: string[] };
  commands: Record<string, string>;
};

/** A stored number as text; a share or points are shown as stored, never rescaled. */
export function numberText(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(Number(value.toFixed(4))) : "not recorded";
}

/** The stored figure and its interval, word for word; no interval is ever made up. */
export function figureText(figure: Figure | undefined): string {
  if (!figure || figure.value === null) return "not recorded";
  if (!figure.interval) return numberText(figure.value);
  const kind = figure.interval_kind === "descriptive" ? "descriptive interval" : `${figure.interval_kind ?? "stored"} interval`;
  return `${numberText(figure.value)} (${kind} ${numberText(figure.interval[0])} to ${numberText(figure.interval[1])})`;
}

/** The x-axis label of a point: its version and date. */
export function pointLabel(point: Point): string {
  return `${point.harness_version ?? "unknown version"} · ${point.date ?? "undated"}`;
}

export function evidenceText(point: Point): string {
  const label = point.evidence.label === "pre-registered" ? "pre-registered" : "exploratory";
  return point.evidence.pre_registration ? `${label} (${point.evidence.pre_registration})` : label;
}

export function sm2Text(point: Point): string {
  if (point.sm2.unavailable) return `SM-2 unavailable: ${point.sm2.unavailable}`;
  const parts = [point.sm2.verdict ? `SM-2 reads ${point.sm2.verdict}` : "SM-2 states no verdict"];
  if (point.sm2.reason) parts.push(point.sm2.reason);
  if (point.sm2.limitation) parts.push(point.sm2.limitation);
  return parts.join("; ");
}

export function delegationText(point: Point): string {
  if (!point.delegation) return "no delegation tally";
  const counts = Object.entries(point.delegation.verdicts).map(([name, count]) => `${name} ${count}`).join(", ");
  return `${point.delegation.label ?? "delegation"}: ${counts || "no tasks"}`;
}

export function cardValue(value: unknown): string {
  if (value === null || value === undefined) return "not stated";
  if (Array.isArray(value)) return value.map((item) => numberText(item as number)).join(" to ");
  return typeof value === "number" ? numberText(value) : String(value);
}

/** The y-range a chart needs to hold every stored value and interval end of one measure. */
export function extent(points: Point[], measure: string): [number, number] | null {
  const values: number[] = [];
  for (const point of points) {
    const figure = point.measures[measure];
    if (!figure || figure.value === null) continue;
    values.push(figure.value);
    if (figure.interval) values.push(figure.interval[0], figure.interval[1]);
  }
  if (!values.length) return null;
  const low = Math.min(...values);
  const high = Math.max(...values);
  return low === high ? [low - 0.5, high + 0.5] : [low, high];
}

export function proofSummary(proof: Trends["proof"]): string {
  if (proof.statement) return proof.statement;
  const verified = proof.claims.filter((claim) => claim.verify_status).length;
  return `${proof.claims.length} published claim(s); ${verified} verified now by citizen evidence verify.`;
}
