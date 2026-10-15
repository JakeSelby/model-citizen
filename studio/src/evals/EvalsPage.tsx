import { Anchor, Badge, Group, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { Link, NavLink, useSearchParams } from "react-router-dom";

import "../harness/harness.css";
import { CommandChip, EvidenceState } from "../components/StudioKit";
import { ExperimentsPage } from "../experiments/ExperimentsPage";
import { periodText, shortDate } from "../harness/model";
import type { Line } from "../reports/trends/model";
import { useAutoTrim, useBeforeAfter, useEvalsTrends, usePreregistrations, useQualification } from "./api";
import {
  OBSERVATIONAL_BADGE, changeText, filterBoundaries, parseEvalsTab, registeredResults, resultText, shareText,
  statusTone, tokensText,
  type AutoTrim, type BeforeAfter, type Boundary, type EvalsTab, type ObservationalLabel, type Preregistrations,
  type Qualification,
} from "./model";

/** A query's state, as the views take it, so each view renders without fetching. */
export type Load<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

function fromQuery<T>(query: { data?: T; error: unknown; isPending: boolean }): Load<T> {
  if (query.data !== undefined) return { state: "ready", data: query.data };
  if (query.error) return { state: "error", message: query.error instanceof Error ? query.error.message : "Could not be read." };
  return { state: "loading" };
}

function Pending<T>({ load, what, children }: { load: Load<T>; what: string; children: (data: T) => ReactNode }) {
  if (load.state === "loading") return <Text size="sm" c="dimmed" role="status">Reading {what}…</Text>;
  if (load.state === "error") return <Text size="sm" c="red" role="alert">{what} could not be read: {load.message}</Text>;
  return <>{children(load.data)}</>;
}

// `withCaption` is off for the table that shares a tab with the Experiments panel, whose own
// results table is the first caption a reader (and its flow test) meets there; aria-label names it.
function HairlineTable({ caption, children, minWidth = 640, withCaption = true }: { caption: string; children: ReactNode; minWidth?: number; withCaption?: boolean }) {
  return <div className="harness-panel">
    <Table.ScrollContainer minWidth={minWidth}
      scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label={caption} className="data-table harness-table">
        {withCaption && <Table.Caption className="visually-hidden">{caption}</Table.Caption>}
        {children}
      </Table>
    </Table.ScrollContainer>
  </div>;
}

// --- pre-registered -------------------------------------------------------------------------

export function PreregisteredTable({ data, lines }: { data: Preregistrations; lines: Line[] }) {
  if (!data.items.length) {
    return <EvidenceState kind="empty" title="No pre-registrations yet">
      A plan committed under {data.directory} before its first trial is listed here.
    </EvidenceState>;
  }
  return <HairlineTable caption="Pre-registered experiments" withCaption={false}>
    <Table.Thead><Table.Tr>
      <Table.Th scope="col">Experiment</Table.Th>
      <Table.Th scope="col">Question</Table.Th>
      <Table.Th scope="col">Registered</Table.Th>
      <Table.Th scope="col">Status</Table.Th>
      <Table.Th scope="col">Result</Table.Th>
    </Table.Tr></Table.Thead>
    <Table.Tbody>
      {data.items.map((item) => {
        const results = registeredResults(item, lines);
        return <Table.Tr key={item.path} data-preregistration={item.file}>
          <Table.Th scope="row" fw={600}>
            {item.title}
            <Text size="xs" c="dimmed" className="harness-nowrap"><code>{item.path}</code></Text>
          </Table.Th>
          <Table.Td><Text size="sm">{item.question ?? "–"}</Text></Table.Td>
          <Table.Td className="harness-nowrap">{item.date ? shortDate(item.date) : "–"}</Table.Td>
          <Table.Td className="harness-nowrap">
            <Badge className="harness-badge" variant="outline" color="teal" tt="none">{item.status ?? "Registered"}</Badge>
            {item.deviations > 0 && <Text size="xs" c="dimmed">{item.deviations} logged deviation{item.deviations === 1 ? "" : "s"}</Text>}
          </Table.Td>
          <Table.Td className="harness-nowrap" data-results={results.length}>{resultText(results)}</Table.Td>
        </Table.Tr>;
      })}
    </Table.Tbody>
  </HairlineTable>;
}

// --- compatibility qualification -------------------------------------------------------------

export function QualificationTable({ data }: { data: Qualification }) {
  if (data.state === "unavailable") {
    return <EvidenceState kind="error" title="The compatibility catalog could not be read">{data.reason}</EvidenceState>;
  }
  return <Stack gap="sm">
    {data.qualification_source === "drifted" && <Text size="sm" c="dimmed">
      The runtime source has changed since this release was qualified; qualify it again before the next release.
    </Text>}
    <HairlineTable caption={`Runtime qualification, v${data.harness_version ?? "unknown"}`}>
      <Table.Thead><Table.Tr>
        <Table.Th scope="col">Target</Table.Th>
        <Table.Th scope="col">Client version</Table.Th>
        <Table.Th scope="col">Status</Table.Th>
        <Table.Th scope="col">Records</Table.Th>
      </Table.Tr></Table.Thead>
      <Table.Tbody>
        {data.clients.map((client) => <Table.Tr key={client.id} data-client={client.id} data-status={client.status}>
          <Table.Th scope="row" fw={600}>{client.id}{client.required_for_release && <Text size="xs" c="dimmed">required for release</Text>}</Table.Th>
          <Table.Td className="harness-nowrap">{client.client_version ?? "–"}</Table.Td>
          <Table.Td className="harness-nowrap">
            <Badge className="harness-badge" variant="outline" color={statusTone(client.status)} tt="none">{client.status}</Badge>
          </Table.Td>
          <Table.Td>{client.records.length
            ? client.records.map((record) => <Text key={record.path} size="xs"><code>{record.path}</code></Text>)
            : <Text size="sm" c="dimmed">–</Text>}</Table.Td>
        </Table.Tr>)}
      </Table.Tbody>
    </HairlineTable>
    {data.limitations.length > 0 && <Stack gap={4} maw="80ch">
      {data.limitations.map((text) => <Text key={text} size="sm" c="dimmed">{text}</Text>)}
    </Stack>}
  </Stack>;
}

// --- observational --------------------------------------------------------------------------

/** The label every observational reading wears, in the standard's words. */
export function ObservationalNote({ label }: { label: ObservationalLabel }) {
  return <Group gap="sm" align="flex-start" wrap="nowrap" className="observational-note" data-label={label.label}>
    <Badge className="harness-badge" variant="outline" color="gray" tt="none">{OBSERVATIONAL_BADGE}</Badge>
    <Text size="sm" c="dimmed" maw="80ch">{label.note}</Text>
  </Group>;
}

function boundaryWhen(boundary: Boundary): string {
  return `${shortDate(boundary.at)} · ${boundary.source}`;
}

export function BeforeAfterView({ data, surface = null, item = null }: { data: BeforeAfter; surface?: string | null; item?: string | null }) {
  const shown = filterBoundaries(data.boundaries, surface, item);
  const failed = Object.entries(data.surfaces).filter(([, state]) => state === "error").map(([name]) => name);
  return <section aria-label="Before and after each setting change" data-label={data.label} className="observational">
    <Stack gap="md">
      <Title order={2}>Before and after each setting change</Title>
      <ObservationalNote label={data} />
      {(surface || item) && <Text size="sm">
        Showing {[surface, item].filter(Boolean).join(" · ")}. <Anchor component={Link} to="/evals?tab=before-after">Show every change</Anchor>
      </Text>}
      {shown.length === 0
        ? <Text size="sm" c="dimmed" role="status">No setting changed in the last {data.days} days, so there is no before and after yet.</Text>
        : <HairlineTable caption="Before and after, one row per metric" minWidth={760}>
          <Table.Thead><Table.Tr>
            <Table.Th scope="col" className="harness-sticky">Change</Table.Th>
            <Table.Th scope="col">Metric</Table.Th>
            <Table.Th scope="col" className="harness-num">Before</Table.Th>
            <Table.Th scope="col" className="harness-num">After</Table.Th>
            <Table.Th scope="col" className="harness-num">Change</Table.Th>
          </Table.Tr></Table.Thead>
          {shown.map((boundary) => <Table.Tbody key={boundary.id} data-boundary={boundary.id} data-source={boundary.source}>
            {(boundary.changes.length ? boundary.changes : [null]).map((change, index) => <Table.Tr key={change?.key ?? "none"}>
              {index === 0 && <Table.Th scope="rowgroup" rowSpan={Math.max(1, boundary.changes.length)} className="harness-sticky">
                <Text size="sm" fw={600}>{boundary.item_label}</Text>
                <Text size="sm">{boundary.from ?? "(earlier setting)"} → {boundary.to}</Text>
                <Text size="xs" c="dimmed">{boundaryWhen(boundary)}</Text>
                <Text size="xs" c="dimmed">Before {periodText(boundary.before.period)}; after {periodText(boundary.after.period)}{boundary.after.status === "measuring" ? `, measuring day ${boundary.after.measuring_day ?? 1} of ${data.window_days}` : ""}</Text>
              </Table.Th>}
              <Table.Td>{change?.key.replace(/_/g, " ") ?? "No metrics"}</Table.Td>
              <Table.Td className="harness-num">{change?.before ?? "–"}</Table.Td>
              <Table.Td className="harness-num">{change?.after ?? "–"}</Table.Td>
              <Table.Td className="harness-num">{change ? changeText(change) : "–"}</Table.Td>
            </Table.Tr>)}
          </Table.Tbody>)}
        </HairlineTable>}
      {failed.length > 0 && <Text size="sm" c="dimmed">Could not read: {failed.join(", ")}.</Text>}
    </Stack>
  </section>;
}

export function AutoTrimView({ data }: { data: AutoTrim }) {
  const rows: Array<[string, (s: AutoTrim["before"]) => string]> = [
    ["Calls", (s) => s.calls.toLocaleString("en-US")],
    ["Median context per call", (s) => tokensText(s.median_context)],
    ["Mean context per call", (s) => tokensText(s.mean_context)],
    [`Calls over ${tokensText(data.context_warn)}`, (s) => shareText(s.over_warn_share)],
  ];
  return <section aria-label="Auto-trim reading" data-label={data.label} data-state={data.state} className="observational">
    <Stack gap="md">
      <Title order={2}>Auto-trim reading</Title>
      <Text size="sm" maw="80ch">Context per call before and after each session's first reset (a compaction or a reseed), over the last {data.days} days.</Text>
      <ObservationalNote label={data} />
      {data.state === "ready" && data.indexing && data.reason
        ? <Text size="sm" c="dimmed" role="status" data-indexing="true">{data.reason}</Text>
        : null}
      {data.state !== "ready"
        ? <Text size="sm" c="dimmed" role="status" data-indexing={data.state === "indexing" ? "true" : undefined}>{data.reason}</Text>
        : <>
          <HairlineTable caption="Context per call before and after the reset" minWidth={480}>
            <Table.Thead><Table.Tr>
              <Table.Th scope="col">Measure</Table.Th>
              <Table.Th scope="col" className="harness-num">Before the reset</Table.Th>
              <Table.Th scope="col" className="harness-num">After the reset</Table.Th>
            </Table.Tr></Table.Thead>
            <Table.Tbody>
              {rows.map(([label, read]) => <Table.Tr key={label}>
                <Table.Th scope="row">{label}</Table.Th>
                <Table.Td className="harness-num">{read(data.before)}</Table.Td>
                <Table.Td className="harness-num">{read(data.after)}</Table.Td>
              </Table.Tr>)}
            </Table.Tbody>
          </HairlineTable>
          <Text size="sm" c="dimmed">
            {data.sessions_with_reset} session{data.sessions_with_reset === 1 ? "" : "s"} reset; {data.sessions_without_reset} did not and are left out.
          </Text>
        </>}
    </Stack>
  </section>;
}

// --- the page -------------------------------------------------------------------------------

export type EvalsViewProps = {
  tab: EvalsTab;
  onTab: (tab: EvalsTab) => void;
  preregistrations: Load<Preregistrations>;
  lines: Line[];
  qualification: Load<Qualification>;
  beforeAfter: Load<BeforeAfter>;
  autoTrim: Load<AutoTrim>;
  surface?: string | null;
  item?: string | null;
  /** The Experiments panel; tests pass a stand-in so the view renders without fetching. */
  experiments?: ReactNode;
};

export function EvalsView({ tab, onTab, preregistrations, lines, qualification, beforeAfter, autoTrim, surface, item, experiments }: EvalsViewProps) {
  return <Stack gap="lg">
    <Group align="flex-end" justify="space-between" className="page-heading">
      <div>
        <Title order={1}>Evals</Title>
        <Text c="dimmed">What the experiments have shown, what each setting change did, and which runtimes are qualified.</Text>
      </div>
      <Anchor component={NavLink} to="/evals/trends" size="sm">Trends by version and date</Anchor>
    </Group>
    <Tabs value={tab} onChange={(value) => onTab(value === "before-after" || value === "qualification" ? value : "experiments")}
      keepMounted={false} className="harness-tabs">
      <Tabs.List aria-label="Evals views">
        <Tabs.Tab value="experiments">Experiments</Tabs.Tab>
        <Tabs.Tab value="before-after">Before and after</Tabs.Tab>
        <Tabs.Tab value="qualification">Runtime qualification</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="experiments" pt="lg">
        <Stack gap="xl">
          <section aria-label="Pre-registered experiments" data-label="pre-registered">
            <Stack gap="md">
              <Group justify="space-between" align="flex-end">
                <Title order={2}>Pre-registered experiments</Title>
                <CommandChip label="List pre-registrations" command="citizen evals preregistrations" />
              </Group>
              <Text size="sm" c="dimmed" maw="80ch">
                Each plan was committed before its first trial. A result is listed only when the run
                recorded it as pre-registered; everything else on this page is exploratory.
              </Text>
              <Pending load={preregistrations} what="Pre-registrations">{(data) => <PreregisteredTable data={data} lines={lines} />}</Pending>
            </Stack>
          </section>
          {experiments}
        </Stack>
      </Tabs.Panel>
      <Tabs.Panel value="before-after" pt="lg">
        <Stack gap="xl">
          <Pending load={beforeAfter} what="Before and after">{(data) => <BeforeAfterView data={data} surface={surface} item={item} />}</Pending>
          <Pending load={autoTrim} what="Auto-trim reading">{(data) => <AutoTrimView data={data} />}</Pending>
          <CommandChip label="Same readings in the terminal" command="citizen evals before-after --days 30" />
        </Stack>
      </Tabs.Panel>
      <Tabs.Panel value="qualification" pt="lg">
        <Stack gap="md">
          <Title order={2}>Runtime qualification</Title>
          <Text size="sm" c="dimmed" maw="80ch">Native qualification records, as the compatibility catalog validates them.</Text>
          <Pending load={qualification} what="The compatibility catalog">{(data) => <QualificationTable data={data} />}</Pending>
          <CommandChip label="Same records in the terminal" command="citizen evals evidence" />
        </Stack>
      </Tabs.Panel>
    </Tabs>
  </Stack>;
}

export function EvalsPage() {
  const [params, setParams] = useSearchParams();
  const tab = parseEvalsTab(params);
  const preregistrations = usePreregistrations();
  const trends = useEvalsTrends();
  const qualification = useQualification(tab === "qualification");
  const beforeAfter = useBeforeAfter(tab === "before-after");
  const autoTrim = useAutoTrim(tab === "before-after");
  return <EvalsView
    tab={tab}
    onTab={(next) => setParams((current) => {
      const out = new URLSearchParams(current);
      out.set("tab", next);
      return out;
    })}
    preregistrations={fromQuery(preregistrations)}
    lines={trends.data?.lines ?? []}
    qualification={fromQuery(qualification)}
    beforeAfter={fromQuery(beforeAfter)}
    autoTrim={fromQuery(autoTrim)}
    surface={params.get("surface")}
    item={params.get("item")}
    experiments={<ExperimentsPage embedded />}
  />;
}
