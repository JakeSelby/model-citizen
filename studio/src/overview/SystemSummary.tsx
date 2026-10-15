import { Anchor, Text } from "@mantine/core";

import { StatusBadge } from "../components/StudioKit";
import { releaseSummary, systemSummary, type Overview } from "./model";

/** The installed version, doctor checks, drift and release line, shown on Baseline. */
export function SystemSummary({ overview }: { overview: Overview }) {
  const summary = systemSummary(overview);
  const installed = overview.installed.version ? `v${overview.installed.version}` : "Unavailable";
  const attention = overview.doctor.checks.filter((check) => check.status === "attention");
  const driftTone = overview.drift.status === "drift" ? "warning" : overview.drift.status === "current" ? "success" : "danger";
  const driftLabel = overview.drift.status === "drift" ? `${overview.drift.items.length} changed` : overview.drift.status === "current" ? "In sync" : "Unavailable";
  return (
    <section aria-labelledby="installed-system-title" className="system-summary">
      <dl className="number-strip">
        <div className="strip-item">
          <dt id="installed-system-title">Installed system</dt>
          <dd className="strip-value">{installed}</dd>
          <dd className="strip-note">Mode {overview.mode.value ?? "unavailable"}{overview.mode.source ? ` · ${overview.mode.source}` : ""}</dd>
        </div>
        <div className="strip-item">
          <dt>Doctor checks</dt>
          {overview.doctor.status === "failed"
            ? <dd className="strip-value" data-tone="danger">Unavailable</dd>
            : <>
              <dd className="strip-value" data-tone={attention.length ? "warning" : undefined}>{attention.length}</dd>
              <dd className="strip-note">Needs attention · {overview.doctor.checks.length} checks</dd>
            </>}
        </div>
        <div className="strip-item">
          <dt>Projection drift</dt>
          <dd className="strip-value" data-tone={driftTone}>{driftLabel}</dd>
        </div>
      </dl>
      <div className="release-line">
        <StatusBadge tone={summary.tone}>{summary.label}</StatusBadge>
        <Text fw={600} size="sm">{releaseSummary(overview)}</Text>
        <Text c="dimmed" size="sm">Updates are never installed automatically.</Text>
        {overview.release.changelog_url && <Anchor href={overview.release.changelog_url} size="sm" target="_blank" rel="noreferrer">Read changelog</Anchor>}
      </div>
    </section>
  );
}
