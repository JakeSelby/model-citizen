import {
  Anchor,
  Button,
  Card,
  Group,
  List,
  Paper,
  SimpleGrid,
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

const reportCards = [
  { label: "System health", measure: "Doctor evidence", detail: "Every check keeps the CLI message and repair command.", href: "/reports#system-health" },
  { label: "Hook performance", measure: "No timing window", detail: "Invocation counts and latency stay linked to their source window.", href: "/reports#hook-performance" },
  { label: "Efficacy", measure: "No comparison yet", detail: "Run a paired experiment before judging a draft.", href: "/reports#efficacy" },
  { label: "Usage", measure: "No local total", detail: "Estimated and unpriced usage are reported separately.", href: "/reports#usage" },
] as const;

function ReportCard({ label, measure, detail, href }: (typeof reportCards)[number]) {
  return <Card className="report-card" component={NavLink} to={href} withBorder>
    <Text className="eyebrow">{label}</Text>
    <Text className="report-measure" fw={650}>{measure}</Text>
    <Text c="dimmed" size="sm">{detail}</Text>
    <Text className="card-link" fw={600} size="sm">Open report <span aria-hidden="true">↗</span></Text>
  </Card>;
}

function DoctorRow({ check }: { check: DoctorCheck }) {
  return <li className="doctor-row">
    <div>
      <StatusBadge tone={check.status === "attention" ? "warning" : "neutral"}>
        {check.status === "attention" ? "Needs attention" : "Information"}
      </StatusBadge>
      <Text mt="xs" size="sm">{check.message}</Text>
    </div>
    {!!check.fixes.length && <details className="doctor-repair">
      <summary>Show {check.fixes.length === 1 ? "repair command" : `${check.fixes.length} repair commands`}</summary>
      <Stack gap="xs">{check.fixes.map((fix) =>
        <CommandChip command={fix} key={fix} label="Doctor fix" />)}</Stack>
    </details>}
  </li>;
}

export function DeterministicOverview({ overview, children }: { overview: Overview; children?: ReactNode }) {
  const summary = systemSummary(overview);
  const installed = overview.installed.version ? `v${overview.installed.version}` : "Unavailable";
  const attention = overview.doctor.checks.filter((check) => check.status === "attention");
  const informational = overview.doctor.checks.filter((check) => check.status !== "attention");
  return <Stack gap="lg">
    <Paper className="system-summary" p="xl" withBorder>
      <Group align="flex-start" justify="space-between">
        <div>
          <Text className="eyebrow">Installed system</Text>
          <Title order={2}>{installed}</Title>
          <Text c="dimmed" mt="xs">
            Mode {overview.mode.value ?? "unavailable"}{overview.mode.source ? ` · ${overview.mode.source}` : ""}
          </Text>
        </div>
        <StatusBadge tone={summary.tone}>
          {summary.label}
        </StatusBadge>
      </Group>
      <div className="release-line">
        <div>
          <Text fw={650}>{releaseSummary(overview)}</Text>
          <Text c="dimmed" size="sm">Updates are never installed automatically.</Text>
        </div>
        {overview.release.changelog_url && <Anchor href={overview.release.changelog_url} target="_blank" rel="noreferrer">Read changelog</Anchor>}
      </div>
    </Paper>

    {children}

    <SimpleGrid className="diagnostic-grid" cols={{ base: 1, md: 2 }} spacing="lg">
      <Paper p="xl" withBorder>
        <Group justify="space-between"><Title order={2}>Doctor checks</Title><StatusBadge>{overview.doctor.checks.length} checks</StatusBadge></Group>
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
      </Paper>

      <Stack className="diagnostic-secondary" gap="lg">
      <Paper p="xl" withBorder>
        <Group justify="space-between">
          <Title order={2}>Projection drift</Title>
          <StatusBadge tone={overview.drift.status === "drift" ? "warning" : overview.drift.status === "current" ? "success" : "danger"}>
            {overview.drift.status === "drift" ? `${overview.drift.items.length} changed` : overview.drift.status === "current" ? "In sync" : "Unavailable"}
          </StatusBadge>
        </Group>
        {overview.drift.status === "failed" && <EvidenceState kind="error" title="Drift unavailable">{overview.drift.message}</EvidenceState>}
        {overview.drift.status === "current" && <EvidenceState kind="empty" title="No projection drift">The current selection matches the projected files.</EvidenceState>}
        {overview.drift.status === "drift" && <Stack mt="lg">
          <List className="drift-list" size="sm">{overview.drift.items.map((item) => <List.Item key={item}>{item}</List.Item>)}</List>
          <CommandChip command={overview.drift.command} label="Governed repair" />
          <Button component={NavLink} to="/configure#governed-sync" variant="light">Review sync before applying</Button>
        </Stack>}
      </Paper>

    <Paper p="xl" withBorder>
      <Group justify="space-between"><Title order={2}>Recent runs</Title><Anchor component={NavLink} to="/experiments">All experiments</Anchor></Group>
      {overview.runs.status === "failed" && <EvidenceState kind="error" title="Runs unavailable">{overview.runs.message}</EvidenceState>}
      {overview.runs.status === "current" && !overview.runs.items.length && <EvidenceState kind="empty" title="No runs yet">Completed and active experiments will appear here.</EvidenceState>}
      {!!overview.runs.items.length && <ul className="run-list">{overview.runs.items.map((run) => <li key={run.run_id}>
        <div><Text fw={650}>{run.suite_id}</Text><Text c="dimmed" size="sm">{run.target_kind} · {run.created_at}</Text></div>
        <StatusBadge tone={run.status === "succeeded" ? "success" : run.status === "failed" ? "danger" : "info"}>{run.status}</StatusBadge>
      </li>)}</ul>}
    </Paper>
      </Stack>
    </SimpleGrid>
  </Stack>;
}

export function OverviewPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: loadOverview });
  useLiveUpdates(["overview", "runs"], () => { void overview.refetch(); });
  return <Stack gap="xl">
    <Group align="flex-end" className="page-heading" justify="space-between">
      <div><Text className="eyebrow">Studio / Hub</Text><Title order={1}>Your harness at a glance.</Title><Text c="dimmed" mt="xs">Installed state, local evidence, and the next useful action.</Text></div>
      <Button component={NavLink} to="/experiments" variant="filled">Open experiments</Button>
    </Group>

    {overview.isPending && <Paper p="xl" withBorder><EvidenceState kind="loading" title="Loading operational evidence">Reading the same local sources as citizen doctor, diff, and catalog.</EvidenceState></Paper>}
    {overview.isError && <Paper p="xl" withBorder><EvidenceState action={<Button onClick={() => { void overview.refetch(); }} variant="light">Try again</Button>} kind="error" title="Overview unavailable">The last screen remains read-only. No command was run.</EvidenceState></Paper>}
    {overview.data ? <DeterministicOverview overview={overview.data}><OverviewInsights /></DeterministicOverview> : <OverviewInsights />}
  </Stack>;
}

function OverviewInsights() {
  return <section aria-labelledby="ai-overview-title" className="hub-grid">
      <Paper className="overview-card" px={{ base: "var(--studio-space-5)", xs: "xl" }} py="xl" withBorder>
        <Group justify="space-between"><Title id="ai-overview-title" order={2}>AI health overview</Title><StatusBadge>Off</StatusBadge></Group>
        <Text className="overview-lead" mt="lg">Enable a read-only assessment when you want one.</Text>
        <Text c="dimmed" mt="sm">Studio makes no model calls until you opt in. Provider, model, evidence scope, refresh policy, and daily cap stay visible.</Text>
        <Group mt="xl"><Button component={NavLink} to="/configure#ai-overview" variant="light">Review AI settings</Button><Anchor component={NavLink} to="/reports">Browse deterministic reports</Anchor></Group>
      </Paper>
      <SimpleGrid className="report-grid" cols={{ base: 1, xs: 2 }} spacing="md">{reportCards.map((card) => <ReportCard key={card.label} {...card} />)}</SimpleGrid>
    </section>;
}
