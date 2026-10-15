// The Sessions page: cost per turn, context per call against the cost stance's 160k and 400k
// lines, reset events and the costliest sessions, from `GET /api/sessions`
// (`citizen sessions list --json`). Every figure is observed in the user's own transcripts.
import { Anchor, Badge, Box, Code, Group, SegmentedControl, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { EvidenceState } from "../../components/StudioKit";
import "../../harness/harness.css";
import { ContextHistogram } from "./ContextCharts";
import {
  OBSERVED, SORTS, WINDOWS, band, count, dollars, headline, parseDays, sessionHref, sessionsQuery, share,
  tokens, topSessions, type Band, type ContextLines, type ResetEvent, type SessionRow, type SessionsList, type SortKey,
} from "./sessionsModel";

export const BAND_LABELS: Record<Band, string> = { under: "under the line", warn: "past 160k", limit: "past 400k" };
const BAND_COLORS: Record<Band, string> = { under: "gray", warn: "yellow", limit: "red" };

export function Figure({ label, value, detail }: { label: string; value: ReactNode; detail: ReactNode }) {
  return <Box>
    <Text size="xs" c="dimmed">{label}</Text>
    <Text fw={700} size="xl" style={{ fontVariantNumeric: "tabular-nums" }}>{value}</Text>
    <Text size="xs" c="dimmed">{detail}</Text>
  </Box>;
}

export function BandBadge({ context, lines }: { context: number; lines: ContextLines }) {
  const which = band(context, lines);
  return <Badge className="harness-badge" variant="outline" tt="none" color={BAND_COLORS[which]} data-band={which}>{BAND_LABELS[which]}</Badge>;
}

/** The note every figure on the page carries: what it reads, and that it is only a reading. */
export function Observed({ basis }: { basis: string }) {
  return <Text size="sm" c="dimmed" maw="80ch" data-observed>{basis}</Text>;
}

export function TopSessions({ rows, lines, sort, onSort }: { rows: SessionRow[]; lines: ContextLines; sort: SortKey; onSort: (sort: SortKey) => void }) {
  const shown = topSessions(rows, sort);
  const caption = `Top ${shown.length} of ${count(rows.length)} sessions by ${SORTS[sort].toLowerCase()}`;
  return <Stack gap="xs" component="section" aria-label="Top sessions">
    <Group justify="space-between" align="flex-end">
      <div>
        <Title order={2} size="h4">Top sessions</Title>
        <Text size="sm" c="dimmed">{caption}. {OBSERVED}.</Text>
      </div>
      <SegmentedControl size="xs" value={sort} onChange={(value) => onSort(value as SortKey)} aria-label="Rank sessions by"
        data={Object.entries(SORTS).map(([value, label]) => ({ value, label }))} />
    </Group>
    {shown.length === 0
      ? <EvidenceState kind="empty" title="No sessions in this window">A session appears once its transcript has a call.</EvidenceState>
      : <div className="harness-panel">
        <Table.ScrollContainer minWidth={760}
          scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
          <Table verticalSpacing="sm" aria-label={caption} className="data-table harness-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col" className="harness-sticky">Session</Table.Th>
                <Table.Th scope="col">Repository</Table.Th>
                <Table.Th scope="col" className="harness-num">Calls</Table.Th>
                <Table.Th scope="col" className="harness-num">Cost</Table.Th>
                <Table.Th scope="col" className="harness-num">Cost per turn</Table.Th>
                <Table.Th scope="col" className="harness-num">Peak context</Table.Th>
                <Table.Th scope="col" className="harness-num">Calls past 400k</Table.Th>
                <Table.Th scope="col" className="harness-num">Resets</Table.Th>
                <Table.Th scope="col">Peak</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((row) => <Table.Tr key={row.session_id} data-session={row.session_id}>
                <Table.Th scope="row" fw={400} className="harness-sticky">
                  <Anchor component={Link} to={sessionHref(row.session_id)} title={row.title ?? row.session_id}>
                    <Code>{row.session_id.slice(0, 8)}</Code>
                  </Anchor>
                  {row.title && <Text size="xs" c="dimmed" lineClamp={1}>{row.title}</Text>}
                </Table.Th>
                <Table.Td className="harness-nowrap">{row.repository}</Table.Td>
                <Table.Td className="harness-num">{count(row.calls)}</Table.Td>
                <Table.Td className="harness-num">{dollars(row.cost)}{row.cost !== null && !row.cost_complete ? " +" : ""}</Table.Td>
                <Table.Td className="harness-num">{dollars(row.cost_per_turn)}</Table.Td>
                <Table.Td className="harness-num">{tokens(row.max_context)}</Table.Td>
                <Table.Td className="harness-num">{count(row.over_limit)}</Table.Td>
                <Table.Td className="harness-num">{count(row.compacts + row.reseeds)}</Table.Td>
                <Table.Td className="harness-nowrap"><BandBadge context={row.max_context} lines={lines} /></Table.Td>
              </Table.Tr>)}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>}
  </Stack>;
}

export function ResetEvents({ events }: { events: ResetEvent[] }) {
  return <Stack gap="xs" component="section" aria-label="Resets">
    <div>
      <Title order={2} size="h4">Auto-trim and compaction</Title>
      <Text size="sm" c="dimmed">Each compaction and each harness reseed in the window, newest first. {OBSERVED}.</Text>
    </div>
    {events.length === 0
      ? <EvidenceState kind="empty" title="No resets in this window">
        Once a session compacts or the harness reseeds it from its handoff, the reset appears here with the context before and after.
      </EvidenceState>
      : <div className="harness-panel">
        <Table.ScrollContainer minWidth={600}
          scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": "Resets, newest first", tabIndex: 0 } }}>
          <Table verticalSpacing="xs" aria-label="Resets, newest first" className="data-table harness-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">When</Table.Th>
                <Table.Th scope="col">Session</Table.Th>
                <Table.Th scope="col">Reset</Table.Th>
                <Table.Th scope="col" className="harness-num">Before</Table.Th>
                <Table.Th scope="col" className="harness-num">After</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {events.map((event, index) => <Table.Tr key={`${event.session_id}-${event.ts}-${index}`} data-kind={event.kind}>
                <Table.Td className="harness-nowrap"><time dateTime={event.ts ?? undefined}>{event.ts ?? "unknown"}</time></Table.Td>
                <Table.Td><Anchor component={Link} to={sessionHref(event.session_id)}><Code>{event.session_id.slice(0, 8)}</Code></Anchor></Table.Td>
                <Table.Td className="harness-nowrap">
                  <Badge className="harness-badge" variant="outline" tt="none" color={event.kind === "reseed" ? "blue" : "gray"}>
                    {event.kind === "reseed" ? "reseed" : `${event.trigger ?? ""} compact`.trim()}
                  </Badge>
                </Table.Td>
                <Table.Td className="harness-num">{event.pre_tokens === null ? "–" : tokens(event.pre_tokens)}</Table.Td>
                <Table.Td className="harness-num">{event.post_tokens === null ? "–" : tokens(event.post_tokens)}</Table.Td>
              </Table.Tr>)}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>}
  </Stack>;
}

/** The page body for a loaded window. Kept free of hooks so tests render it directly. */
export function SessionsView({ data, days, onDays, sort, onSort }: {
  data: SessionsList;
  days: number;
  onDays: (days: number) => void;
  sort: SortKey;
  onSort: (sort: SortKey) => void;
}) {
  const { summary, lines } = data;
  const under = summary.calls - summary.over_warn;
  return <Stack gap="lg">
    <Group justify="space-between" align="flex-start">
      <div>
        <Title order={1}>Sessions</Title>
        <Text c="dimmed">What each session cost and how much context it carried, against your context stance.</Text>
      </div>
      <SegmentedControl value={String(days)} onChange={(value) => onDays(Number(value))} aria-label="Window"
        data={WINDOWS.map((value) => ({ value: String(value), label: `Last ${value} days` }))} />
    </Group>
    {data.indexing && <Text size="sm" role="status" data-indexing>
      Still indexing {count(data.pending)} transcript{data.pending === 1 ? "" : "s"}; the figures fill in as it finishes.
    </Text>}
    <Stack gap={4} component="section" aria-label="Headline">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{OBSERVED}</Text>
      <Title order={2} size="h3">{headline(summary, lines)}</Title>
      <Text size="sm" c="dimmed">
        {count(summary.sessions)} sessions and {count(summary.calls)} main-thread calls in the last {data.window_days} days.
      </Text>
    </Stack>
    <SimpleGrid cols={{ base: 2, sm: 5 }} component="section" aria-label="Session figures" className="sessions-figures">
      <Figure label="Spend" value={dollars(summary.cost)}
        detail={summary.unpriced_calls ? `priced calls only; ${count(summary.unpriced_calls)} unpriced` : "every call priced"} />
      <Figure label="Cost per turn" value={dollars(summary.median_turn_cost)}
        detail={summary.mean_turn_cost === null ? "no priced turns" : `median; mean ${dollars(summary.mean_turn_cost)} over ${count(summary.turns)} turns`} />
      <Figure label="Median context" value={tokens(summary.median_context)} detail="per main-thread call" />
      <Figure label={`Calls past ${tokens(lines.warn)}`} value={share(summary.over_warn, summary.calls)}
        detail={`${count(summary.over_warn)} of ${count(summary.calls)}; each costs more`} />
      <Figure label={`Calls past ${tokens(lines.limit)}`} value={share(summary.over_limit, summary.calls)}
        detail={`${count(summary.over_limit)}; the turn's end is blocked once`} />
    </SimpleGrid>
    <Stack gap="xs" component="section" aria-label="Context per call">
      <div>
        <Title order={2} size="h4">Context per call</Title>
        <Text size="sm" c="dimmed">{count(summary.calls)} calls in 50k buckets against your stance's {tokens(lines.warn)} and {tokens(lines.limit)} lines. {OBSERVED}.</Text>
      </div>
      <div className="sessions-chart-scroll"><ContextHistogram buckets={data.histogram} lines={lines} /></div>
      <Group gap="lg" aria-label="Calls by line">
        <Text size="sm" data-count="under">Under the line <b>{count(under)}</b></Text>
        <Text size="sm" data-count="warn">{tokens(lines.warn)} to {tokens(lines.limit)}, costs more <b>{count(summary.over_warn - summary.over_limit)}</b></Text>
        <Text size="sm" data-count="limit">Past {tokens(lines.limit)}, turn end blocked once <b>{count(summary.over_limit)}</b></Text>
      </Group>
    </Stack>
    <ResetEvents events={data.events} />
    <TopSessions rows={data.sessions} lines={lines} sort={sort} onSort={onSort} />
    <Observed basis={data.basis} />
  </Stack>;
}

export function SessionsPage() {
  const [params, setParams] = useSearchParams();
  const days = parseDays(params);
  const [sort, setSort] = useState<SortKey>("cost");
  const query = useQuery(sessionsQuery(days));
  if (query.isPending) return <EvidenceState kind="loading" title="Reading your transcripts">Answered from the transcript index; a cold index fills in while you watch.</EvidenceState>;
  if (query.isError) return <EvidenceState kind="error" title="Sessions could not be read">{query.error.message}</EvidenceState>;
  return <SessionsView data={query.data} days={days} sort={sort} onSort={setSort}
    onDays={(value) => {
      const next = new URLSearchParams(params);
      next.set("days", String(value));
      setParams(next, { replace: true });
    }} />;
}

