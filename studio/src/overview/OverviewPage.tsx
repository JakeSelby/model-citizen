import {
  Anchor,
  Box,
  Button,
  Group,
  List,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { CommandChip, EvidenceState, StatusBadge } from "../components/StudioKit";
import { useLiveUpdates } from "../live/LiveUpdates";
import { loadOverview } from "./api";
import { releaseSummary, systemSummary, type DoctorCheck, type Overview } from "./model";
import "./overview.css";

export const reportCards = [
  { label: "System health", measure: "Doctor evidence", detail: "Every check keeps the CLI message and repair command.", href: "/reports#system-health" },
  { label: "Hook performance", measure: "No timing window", detail: "Invocation counts and latency stay linked to their source window.", href: "/reports#hook-performance" },
  { label: "Efficacy", measure: "No comparison yet", detail: "Run a paired experiment before judging a draft.", href: "/reports#efficacy" },
  { label: "Usage", measure: "No local total", detail: "Estimated and unpriced usage are reported separately.", href: "/reports/usage" },
  { label: "Trends", measure: "By version and date", detail: "Each measure beside the project's proof set, labelled exploratory or pre-registered.", href: "/reports/trends" },
] as const;

function ReportRow({ label, measure, detail, href }: (typeof reportCards)[number]) {
  return <li><NavLink className="report-row" to={href}>
    <span className="report-label">{label}</span>
    <span className="report-measure">{measure}</span>
    <span className="report-detail">{detail}</span>
    <span className="card-link">Open report <span aria-hidden="true">↗</span></span>
  </NavLink></li>;
}

/** Every report the Hub links to, one row each. */
export function ReportCards() {
  return <ul className="report-list">{reportCards.map((card) => <ReportRow key={card.label} {...card} />)}</ul>;
}

function DoctorRow({ check }: { check: DoctorCheck }) {
  return <li className="doctor-row">
    <StatusBadge tone={check.status === "attention" ? "warning" : "neutral"}>
      {check.status === "attention" ? "Needs attention" : "Information"}
    </StatusBadge>
    <Text className="doctor-message" size="sm">{check.message}</Text>
    {!!check.fixes.length && <details className="doctor-repair">
      <summary>Show {check.fixes.length === 1 ? "repair command" : `${check.fixes.length} repair commands`}</summary>
      <Stack gap="xs">{check.fixes.map((fix) =>
        <CommandChip command={fix} key={fix} label="Doctor fix" />)}</Stack>
    </details>}
  </li>;
}

function runTone(status: string) {
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : "neutral";
}

export function DeterministicOverview({ overview, children }: { overview: Overview; children?: ReactNode }) {
  const summary = systemSummary(overview);
  const installed = overview.installed.version ? `v${overview.installed.version}` : "Unavailable";
  const attention = overview.doctor.checks.filter((check) => check.status === "attention");
  const informational = overview.doctor.checks.filter((check) => check.status !== "attention");
  const driftTone = overview.drift.status === "drift" ? "warning" : overview.drift.status === "current" ? "success" : "danger";
  const driftLabel = overview.drift.status === "drift" ? `${overview.drift.items.length} changed` : overview.drift.status === "current" ? "In sync" : "Unavailable";
  return <div className="hub">
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

    <section aria-labelledby="doctor-checks-title" className="hub-section">
      <Group gap="sm"><Title id="doctor-checks-title" order={2}>Doctor checks</Title><StatusBadge>{overview.doctor.checks.length} checks</StatusBadge></Group>
      {overview.doctor.status === "failed"
        ? <EvidenceState kind="error" title="Doctor unavailable">{overview.doctor.message}</EvidenceState>
        : overview.doctor.checks.length
          ? <>
            {!!attention.length && <ul className="doctor-list">{attention.map((check) => <DoctorRow check={check} key={check.id} />)}</ul>}
            {!!informational.length && <details className="doctor-information">
              <summary>{informational.length} informational {informational.length === 1 ? "check" : "checks"}</summary>
              <ul className="doctor-list">{informational.map((check) => <DoctorRow check={check} key={check.id} />)}</ul>
            </details>}
          </>
          : <EvidenceState kind="empty" title="No doctor output">The CLI returned no checks.</EvidenceState>}
    </section>

    <section aria-labelledby="projection-drift-title" className="hub-section">
      <Group gap="sm"><Title id="projection-drift-title" order={2}>Projection drift</Title><StatusBadge tone={driftTone}>{driftLabel}</StatusBadge></Group>
      {overview.drift.status === "failed" && <EvidenceState kind="error" title="Drift unavailable">{overview.drift.message}</EvidenceState>}
      {overview.drift.status === "current" && <EvidenceState kind="empty" title="No projection drift">The current selection matches the projected files.</EvidenceState>}
      {overview.drift.status === "drift" && <div className="drift-detail">
        <List className="drift-list" size="sm">{overview.drift.items.map((item) => <List.Item key={item}>{item}</List.Item>)}</List>
        <Stack align="flex-start" gap="sm">
          <CommandChip command={overview.drift.command} label="Governed repair" />
          <Button component={NavLink} size="compact-sm" to="/configure#governed-sync" variant="light">Review sync before applying</Button>
        </Stack>
      </div>}
    </section>

    <section aria-labelledby="recent-runs-title" className="hub-section">
      <Group justify="space-between"><Title id="recent-runs-title" order={2}>Recent runs</Title><Anchor component={NavLink} size="sm" to="/experiments">All experiments</Anchor></Group>
      {overview.runs.status === "failed" && <EvidenceState kind="error" title="Runs unavailable">{overview.runs.message}</EvidenceState>}
      {overview.runs.status === "current" && !overview.runs.items.length && <EvidenceState kind="empty" title="No runs yet">Completed and active experiments will appear here.</EvidenceState>}
      {!!overview.runs.items.length && <ul className="run-list">{overview.runs.items.map((run) => <li key={run.run_id}>
        <code className="run-id">{run.run_id}</code>
        <Text fw={600} size="sm">{run.suite_id}</Text>
        <Text c="dimmed" size="sm">{run.target_kind} · {run.created_at}</Text>
        <StatusBadge tone={runTone(run.status)}>{run.status}</StatusBadge>
      </li>)}</ul>}
    </section>

    {children}
  </div>;
}

export function OverviewPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: loadOverview });
  useLiveUpdates(["overview", "runs"], () => { void overview.refetch(); });
  return <Stack gap={0}>
    <Group align="flex-end" className="page-heading" justify="space-between">
      <div><Text className="eyebrow">Studio / Hub</Text><Title order={1}>Your harness at a glance.</Title><Text c="dimmed" size="sm">Installed state, local evidence, and the next useful action.</Text></div>
      <Button component={NavLink} size="compact-sm" to="/experiments" variant="subtle">Open experiments</Button>
    </Group>

    {overview.isPending && <section className="hub-section"><EvidenceState kind="loading" title="Loading operational evidence">Reading the same local sources as citizen doctor, diff, and catalog.</EvidenceState></section>}
    {overview.isError && <section className="hub-section"><EvidenceState action={<Button onClick={() => { void overview.refetch(); }} size="compact-sm" variant="light">Try again</Button>} kind="error" title="Overview unavailable">The last screen remains read-only. No command was run.</EvidenceState></section>}
    {overview.data ? <DeterministicOverview overview={overview.data}><OverviewInsights /></DeterministicOverview> : <OverviewInsights />}
  </Stack>;
}

function OverviewInsights() {
  return <div className="hub-section hub-grid">
    <Box aria-labelledby="ai-overview-title" className="overview-card" component="section" py="md">
      <Group gap="sm"><Title id="ai-overview-title" order={2}>AI health overview</Title><StatusBadge>Off</StatusBadge></Group>
      <Text className="overview-lead">Enable a read-only assessment when you want one.</Text>
      <Text c="dimmed" size="sm">Studio makes no model calls until you opt in. Provider, model, evidence scope, refresh policy, and daily cap stay visible.</Text>
      <Group gap="md" mt="sm"><Button component={NavLink} size="compact-sm" to="/configure#ai-overview" variant="light">Review AI settings</Button><Anchor component={NavLink} size="sm" to="/reports">Browse deterministic reports</Anchor></Group>
    </Box>
    <ReportCards />
  </div>;
}
