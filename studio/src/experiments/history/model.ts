import { engineLeaves } from "../replay/model";

export type RunCase = {
  id: string;
  outcome: "passed" | "failed" | "skipped" | "unknown";
  commit: string | null;
  flaky: boolean;
};

export type RunSummary = {
  run_id: string;
  suite_id: string;
  status: string;
  created_at: string | null;
  completed_at: string | null;
  target: { kind: string | null; ref: string | null; commit: string | null };
  cost_usd: number | null;
  duration_ms: number | null;
  rerun_of: string | null;
  case_count: number;
  flaky_count: number;
};

export type HistoryFilters = {
  suite_id: string | null;
  target: string | null;
  status: string | null;
  created_from: string | null;
  created_to: string | null;
  min_cost_usd: number | null;
  max_cost_usd: number | null;
  min_duration_ms: number | null;
  max_duration_ms: number | null;
};

export type HistoryPage = { items: RunSummary[]; next_cursor: string | null };

export type RunArtifact = {
  id: string;
  label: string;
  available?: boolean;
  kind?: "html-report";
  href?: string;
};

export type ProofStatus = {
  status: "verified" | "failed";
  bundle_id: string | null;
  errors: string[];
  unknown: string[];
};

/** The landed Measured contract an indexed source declared; the engine's values, never re-derived. */
export type EvaluationContract = {
  shape: "two-arm" | "pair" | "variable-arm" | "four-cell" | "config-arm" | "proof-bundle";
  arms?: string[];
  pack?: { pack: string; pack_version: string; pack_commit: string; pack_digest: string } | null;
  ablation?: { name: string; sha256: string; schema: number } | null;
  design?: { name: string; schema: number; manifest_sha256: string } | null;
  arm_config?: { name: string; sha256: string; schema?: number; stances?: Record<string, string> } | null;
  registration?: { evidence: string | null; pre_registration: string | null; pre_registration_commit: string | null } | null;
  proof?: ProofStatus;
};

export type RunDetail = RunSummary & {
  cases: RunCase[];
  reruns: { items: string[]; next_cursor: string | null };
  exact_command: string | null;
  rerun: { available: boolean; reason: string | null };
  artifacts: RunArtifact[];
  evaluation?: EvaluationContract | null;
  /** An imported benchmark row's engine fields (stratum, arm config, metrics, cache basis, long-session keys), verbatim. */
  engine_row?: Record<string, unknown> | null;
  /** The definitive evaluation's engine documents found beside the row's results, verbatim. */
  engine_reports?: EngineReports | null;
};

export type EngineReports = {
  scorecard: Record<string, unknown> | null;
  judge: Record<string, unknown> | null;
  errors: string[];
};

export type CaseHistory = {
  case_id: string;
  items: Array<RunSummary & { case: RunCase | null }>;
  next_cursor: string | null;
};

export const emptyFilters: HistoryFilters = {
  suite_id: null, target: null, status: null, created_from: null, created_to: null,
  min_cost_usd: null, max_cost_usd: null, min_duration_ms: null, max_duration_ms: null,
};

export function displayUnknown(value: string | number | null, suffix = ""): string {
  return value === null ? "Unknown" : `${value}${suffix}`;
}

const REPORT_HREF = /^\/api\/runs\/plugin-eval-report\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The same-origin report link an imported plugin eval declares, or null for any other artifact. */
export function reportHref(artifact: RunArtifact): string | null {
  if (artifact.kind !== "html-report" || artifact.available === false) return null;
  return typeof artifact.href === "string" && REPORT_HREF.test(artifact.href) ? artifact.href : null;
}

const SHAPE_LABELS: Record<EvaluationContract["shape"], string> = {
  "two-arm": "Two-arm replay", pair: "One-policy pair", "variable-arm": "Variable-arm ablation",
  "four-cell": "Four-cell design", "config-arm": "Config arm", "proof-bundle": "Proof bundle",
};

/**
 * Label and value lines for an evaluation contract, in display order. A missing evidence label
 * reads "Unlabelled", never "Exploratory" or "Pre-registered"; a proof is "Verified" only when the
 * verifier said so.
 */
export function evaluationLines(contract: EvaluationContract | null | undefined): Array<[string, string]> {
  if (!contract) return [];
  const lines: Array<[string, string]> = [["Contract", SHAPE_LABELS[contract.shape] ?? `Unsupported (${String(contract.shape)})`]];
  if (contract.arms && contract.arms.length) lines.push(["Arms", contract.arms.join(", ")]);
  if (contract.pack) lines.push(["Pack", `${contract.pack.pack} ${contract.pack.pack_version} @ ${contract.pack.pack_commit}`],
    ["Pack digest", contract.pack.pack_digest]);
  if (contract.ablation) lines.push(["Ablation", `${contract.ablation.name} (schema ${contract.ablation.schema})`],
    ["Ablation digest", contract.ablation.sha256]);
  if (contract.design) lines.push(["Design", `${contract.design.name} (schema ${contract.design.schema})`],
    ["Design digest", contract.design.manifest_sha256]);
  if (contract.arm_config) lines.push(["Arm config", contract.arm_config.name], ["Arm config digest", contract.arm_config.sha256]);
  if (contract.registration) {
    const label = contract.registration.evidence;
    lines.push(["Evidence label", label === "pre-registered" ? "Pre-registered" : label === "exploratory" ? "Exploratory" : "Unlabelled"]);
    if (contract.registration.pre_registration) lines.push(["Pre-registration", contract.registration.pre_registration]);
  }
  if (contract.proof) {
    lines.push(["Proof", contract.proof.status === "verified" ? "Verified" : "Verification failed"]);
    if (contract.proof.bundle_id) lines.push(["Bundle", contract.proof.bundle_id]);
    contract.proof.errors.forEach((error) => lines.push(["Failed check", error]));
    contract.proof.unknown.forEach((item) => lines.push(["Unknown, not verified", item]));
  }
  return lines;
}

/**
 * The words an engine equivalence verdict reads as. Only the engine's own `equivalent` reads
 * "Equivalent within bounds"; an informative null the engine called inconclusive, a missing
 * verdict or an unknown word is never promoted to one.
 */
export function equivalenceLabel(value: unknown): string {
  if (value === "equivalent") return "Equivalent within bounds";
  if (value === "not equivalent") return "Not equivalent";
  if (value === "inconclusive") return "Inconclusive";
  return value === undefined || value === null ? "No equivalence verdict" : String(value);
}

const bare = (segment: string) => segment.replace(/\[\d+\]$/, "");

/** True for the leaf holding an engine equivalence verdict: an `assessment`, or a `verdict` inside an `equivalence` record. */
function isEquivalenceVerdict(path: string): boolean {
  const segments = path.split(".").map(bare);
  const key = segments[segments.length - 1];
  return key === "assessment" || (key === "verdict" && segments.slice(0, -1).includes("equivalence"));
}

/**
 * Every leaf of an engine document as a path and its text, in the engine's order. An engine
 * equivalence verdict also carries the reading `equivalenceLabel` gives the engine's own word;
 * nothing else is reworded and nothing is computed.
 */
export function reportLines(document: unknown): Array<[string, string]> {
  return engineLeaves(document ?? {}).map(([path, text]) => {
    if (!isEquivalenceVerdict(path)) return [path, text];
    let value: unknown = text;
    try { value = JSON.parse(text); } catch { /* the engine text stays as written */ }
    return [path, `${text} (${equivalenceLabel(value)})`];
  });
}

/** The engine row's fields as label and JSON text, in the engine's order, with nothing dropped. */
export function engineRowLines(row: Record<string, unknown> | null | undefined): Array<[string, string]> {
  return row && Object.keys(row).length ? engineLeaves(row) : [];
}
