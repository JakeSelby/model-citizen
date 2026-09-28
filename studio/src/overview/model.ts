export type DoctorCheck = {
  id: string;
  status: "attention" | "informational";
  message: string;
  fix: string | null;
  fixes: string[];
};

export type RunSummary = {
  run_id: string;
  suite_id: string;
  status: string;
  created_at: string;
  target_kind: string;
};

export type Overview = {
  schema_version: number;
  generated_at: string;
  installed: { status: "current" | "failed"; version: string | null };
  release: {
    status: "current" | "update_available" | "unavailable";
    version: string | null;
    changelog_url: string | null;
  };
  mode: { status: "current" | "failed"; value: string | null; source: string | null };
  doctor: { status: "current" | "failed"; message: string | null; checks: DoctorCheck[] };
  drift: {
    status: "current" | "drift" | "failed";
    message: string | null;
    items: string[];
    command: string;
  };
  runs: { status: "current" | "failed"; message: string | null; items: RunSummary[] };
  commands: { doctor: string; diff: string; catalog: string; sync: string };
};

export function attentionCount(overview: Overview): number {
  return overview.doctor.checks.filter(({ status }) => status === "attention").length
    + (overview.drift.status === "drift" ? overview.drift.items.length : 0);
}

export function systemSummary(overview: Overview): { label: string; tone: "success" | "warning" | "danger" } {
  if (overview.drift.status === "failed") return { label: "Drift unknown", tone: "danger" };
  const attention = attentionCount(overview);
  return attention
    ? { label: `${attention} item${attention === 1 ? "" : "s"} to review`, tone: "warning" }
    : { label: "No detected drift", tone: "success" };
}

export function releaseSummary(overview: Overview): string {
  if (overview.release.status === "update_available") return `Version ${overview.release.version} is available`;
  if (overview.release.status === "current") return "Installed release is current";
  return "Stable release could not be checked";
}
