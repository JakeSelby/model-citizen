import { Button, Group, NativeSelect, Paper, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type ChangeEvent, useState } from "react";

import { CommandChip, DataTable, EvidenceState, StatusBadge, type DataColumn } from "../components/StudioKit";
import { loadSpend } from "./api";
import {
  formatCount,
  formatShare,
  formatUsd,
  GROUPINGS,
  moneyLabel,
  pageOf,
  partialNotes,
  pricingDate,
  usageMoneyLabel,
  WINDOWS,
  type Grouping,
  type RebuildCause,
  type RebuildLedger,
  type RoleGroup,
  type RoleLedger,
  type SpendReport,
  type UsageGroup,
  type UsageLedger,
} from "./model";

type Basis = SpendReport["basis"];

/** A dollar figure with its list-price label and pricing date, visible to every reader. */
export function Money({ value, basis, label = moneyLabel(basis) }: { value: number | null; basis: Basis; label?: string }) {
  return <span className="list-price" title={label}>
    {formatUsd(value)}<span className="visually-hidden"> ({label})</span>
  </span>;
}

function usdHeading(basis: Basis, name = "USD"): string {
  return `${name} · ${basis.label} · ${pricingDate(basis)}`;
}

function Pager({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (page: number) => void }) {
  if (pages <= 1) return null;
  return <nav aria-label="Spend table pages">
    <Group gap="sm">
      <Button disabled={page === 0} size="compact-sm" variant="default" onClick={() => onPage(page - 1)}>Previous</Button>
      <Text aria-live="polite" role="status" size="sm">Page {page + 1} of {pages} · {total.toLocaleString("en-US")} groups</Text>
      <Button disabled={page >= pages - 1} size="compact-sm" variant="default" onClick={() => onPage(page + 1)}>Next</Button>
    </Group>
  </nav>;
}

type UsageRow = UsageGroup & { total: boolean };

function UsageTable({ ledger, basis }: { ledger: UsageLedger; basis: Basis }) {
  const [requested, setPage] = useState(0);
  const columns: DataColumn<UsageRow>[] = [
    { key: "name", heading: GROUPINGS.find((item) => item.value === ledger.by)?.label ?? ledger.by, cell: (row) => row.total ? <strong>Total</strong> : row.name },
    { key: "runs", heading: "Runs", cell: (row) => formatCount(row.runs) },
    { key: "input", heading: "Input", cell: (row) => formatCount(row.tokens.input) },
    { key: "output", heading: "Output", cell: (row) => formatCount(row.tokens.output) },
    { key: "cache_read", heading: "Cache read", cell: (row) => formatCount(row.tokens.cache_read) },
    { key: "cache_write", heading: "Cache write", cell: (row) => formatCount(row.tokens.cache_write) },
    { key: "hit", heading: "Cache hit", cell: (row) => formatShare(row.cache_hit_rate) },
    { key: "usd", heading: usdHeading(basis), cell: (row) => <Money basis={basis} label={usageMoneyLabel(row, basis)} value={row.usd} /> },
    { key: "unpriced", heading: "Unpriced runs", cell: (row) => formatCount(row.unpriced_runs) },
  ];
  // The total row is the ledger's own `totals`, never a sum of the rows above it.
  const shown = pageOf(ledger.groups, requested);
  const rows: UsageRow[] = shown.rows.length
    ? [...shown.rows.map((group) => ({ ...group, total: false })), { ...ledger.totals, total: true }]
    : [];
  return <Stack gap="xs">
    <DataTable caption={`Spend by ${ledger.by} over ${ledger.days} days, from the local usage ledger`} columns={columns} empty="No sessions recorded in this window" rowKey={(row) => `${row.total ? "total" : "group"}:${row.name}`} rows={rows} />
    <Pager onPage={setPage} page={shown.page} pages={shown.pages} total={ledger.groups.length} />
  </Stack>;
}

function RoleTable({ ledger, basis }: { ledger: RoleLedger; basis: Basis }) {
  const [requested, setPage] = useState(0);
  const columns: DataColumn<RoleGroup>[] = [
    { key: "name", heading: "Role", cell: (row) => row.name },
    { key: "runs", heading: "Runs", cell: (row) => formatCount(row.runs) },
    { key: "sample", heading: "Sample", cell: (row) => row.sample_sufficient ? "30 or more" : <StatusBadge tone="warning">n&lt;30</StatusBadge> },
    { key: "out", heading: "Output p50 / p75 / p90", cell: (row) => [row.output.p50, row.output.p75, row.output.p90].map(formatCount).join(" / ") },
    { key: "usd50", heading: usdHeading(basis, "USD p50"), cell: (row) => <Money basis={basis} value={row.usd.p50} /> },
    { key: "usd75", heading: usdHeading(basis, "USD p75"), cell: (row) => <Money basis={basis} value={row.usd.p75} /> },
    { key: "tools", heading: "Tool calls p50 / p90", cell: (row) => [row.tools.p50, row.tools.p90].map(formatCount).join(" / ") },
    { key: "unpriced", heading: "Unpriced runs", cell: (row) => formatCount(row.unpriced_runs) },
  ];
  const shown = pageOf(ledger.groups, requested);
  return <Stack gap="xs">
    <DataTable caption={`Subagent and worker spend per role over ${ledger.days} days`} columns={columns} empty="No subagent or worker runs recorded in this window" rowKey={(row) => row.name} rows={shown.rows} />
    <Pager onPage={setPage} page={shown.page} pages={shown.pages} total={ledger.groups.length} />
  </Stack>;
}

function RebuildTables({ ledger, basis }: { ledger: RebuildLedger; basis: Basis }) {
  if (!ledger.groups.length) return <EvidenceState kind="empty" title="No transcripts in this window" />;
  return <Stack gap="md">{ledger.groups.map((scope) => {
    const columns: DataColumn<RebuildCause>[] = [
      { key: "cause", heading: "Cause", cell: (row) => row.cause },
      { key: "breaks", heading: "Breaks", cell: (row) => formatCount(row.breaks) },
      { key: "rewritten", heading: "Rewritten tokens", cell: (row) => formatCount(row.rewritten_tokens) },
      { key: "excess", heading: usdHeading(basis, "Excess USD"), cell: (row) => <Money basis={basis} value={row.excess_usd} /> },
      { key: "per", heading: usdHeading(basis, "USD per break"), cell: (row) => <Money basis={basis} value={row.cost_per_break} /> },
    ];
    return <div key={scope.scope}>
      <Text fw={650}>{scope.scope === "long" ? "Long sessions" : "All sessions"}: {formatCount(scope.sessions)} sessions, {formatCount(scope.calls)} calls, priced spend <Money basis={basis} value={scope.priced_spend_usd} /></Text>
      <Text c="dimmed" size="sm">{formatCount(scope.unpriced_calls)} unpriced call(s) excluded from priced spend · {formatCount(scope.unpriced_breaks)} unpriced break(s) · {formatCount(scope.unknown_breaks)} unexplained break(s)</Text>
      <DataTable caption={`Cache rebuilds by cause, ${scope.scope === "long" ? "long sessions" : "all sessions"}, over ${ledger.days} days`} columns={columns} empty="No cache rebuilds observed" rowKey={(row) => row.cause} rows={scope.causes} />
    </div>;
  })}</Stack>;
}

/** Why no report is shown: a busy grouping is not a broken CLI, so it says which. */
export function SpendFailure({ error, by, days }: { error: Error; by: Grouping; days: number }) {
  if (error.message === "spend_busy") {
    return <EvidenceState kind="refused" title="Spend report busy">A {by} report is already being read, in this tab or another. Try again when it finishes.</EvidenceState>;
  }
  return <><EvidenceState kind="error" title="Spend unavailable">citizen usage did not answer; run the command below in a terminal to see why.</EvidenceState><CommandChip command={`citizen usage --json --by ${by} --days ${days}`} /></>;
}

/** The identity a grouping table is keyed by, so a new grouping or window starts on page one. */
export function tableKey(report: SpendReport): string {
  return `${report.by}:${report.days}`;
}

/** The report as the ledger gave it: header notes, the table, the same notes in the footer. */
export function SpendReportView({ report }: { report: SpendReport }) {
  const { ledger, basis } = report;
  const notes = partialNotes(ledger);
  return <Stack gap="md">
    <Paper className="spend-basis" p="md" withBorder>
      <Text fw={650}>Every dollar figure is a {basis.label}, {pricingDate(basis)}.</Text>
      <Text c="dimmed" size="sm">Computed from token counts at list price; not an invoice. A Studio run's dollars are the spend its runner recorded and are labelled so. Read from the local usage ledger only; nothing is exported.</Text>
      {notes.length ? <Text className="spend-partial" size="sm">Partial data: {notes.join(" ")}</Text> : null}
    </Paper>
    {/* Keyed by the report, so switching grouping or window starts on page one. */}
    {ledger.report === "usage" ? <UsageTable basis={basis} key={tableKey(report)} ledger={ledger} />
      : ledger.report === "roles" ? <RoleTable basis={basis} key={tableKey(report)} ledger={ledger} />
        : <RebuildTables basis={basis} ledger={ledger} />}
    <Text c="dimmed" component="footer" size="sm">{notes.length ? `Partial data: ${notes.join(" ")}` : "Unpriced: none."} Figures are {basis.label}, {pricingDate(basis)}.</Text>
    <CommandChip command={report.command} />
  </Stack>;
}

export function SpendPage() {
  const [by, setBy] = useState<Grouping>("day");
  const [days, setDays] = useState<number>(30);
  const query = useQuery({
    queryKey: ["spend", by, days],
    queryFn: ({ signal }) => loadSpend(by, days, signal),
    staleTime: 30_000,
  });
  return <Stack gap="lg">
    <div>
      <Text className="eyebrow">Studio / Reports</Text>
      <Title order={1}>Spend and usage</Title>
      <Text c="dimmed" mt="xs">Spend by day, model, role, repository and session, exactly as <code>citizen usage --json</code> reports it.</Text>
    </div>
    <Group align="end" gap="md">
      <NativeSelect data={GROUPINGS.map((item) => ({ value: item.value, label: item.label }))} label="Group by" value={by} onChange={(event: ChangeEvent<HTMLSelectElement>) => setBy(event.currentTarget.value as Grouping)} />
      <NativeSelect data={WINDOWS.map((value) => ({ value: String(value), label: `Last ${value} days` }))} label="Window" value={String(days)} onChange={(event: ChangeEvent<HTMLSelectElement>) => setDays(Number(event.currentTarget.value))} />
    </Group>
    {by === "rebuild" ? <Text c="dimmed" size="sm">Cache-rebuild attribution reads session transcripts on every request, so it can take longer than the ledger groupings.</Text> : null}
    {query.isError ? <SpendFailure by={by} days={days} error={query.error} /> : null}
    {query.isPending ? <EvidenceState kind="loading" title="Reading the local usage ledger" /> : null}
    {query.data ? <SpendReportView report={query.data} /> : null}
  </Stack>;
}
