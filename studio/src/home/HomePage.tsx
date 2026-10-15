// Home: the daily digest and at most five things that need you, each with one action, read from
// `GET /api/home` (`citizen home --json`). It replaces the Hub as the Studio's first page.
import { Badge, Box, Button, Group, List, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

import { CommandChip, EvidenceState } from "../components/StudioKit";
import "../harness/harness.css";
import { homeQuery, type AttentionItem, type Digest, type Drift, type HomeReport, type Queue } from "./api";
import { digestFigures, failedSources, pendingSources, SEVERITY_COLORS, SEVERITY_LABELS, shownItems, SOURCE_LABELS } from "./model";
import "./home.css";

export function DigestStrip({ digest }: { digest: Digest }) {
  return <Stack gap="xs" component="section" aria-labelledby="home-digest-title">
    <Group justify="space-between" align="baseline">
      <Title id="home-digest-title" order={2} size="h4">Last 24 hours</Title>
      {digest.status === "ready" && digest.indexing && <Text size="xs" c="dimmed" role="status">
        Still indexing {digest.pending} {digest.pending === 1 ? "transcript" : "transcripts"}; figures will grow.
      </Text>}
    </Group>
    {digest.status === "unavailable"
      ? <EvidenceState kind="error" title="The digest could not be read">{digest.message}</EvidenceState>
      : <>
        <ul className="home-digest" aria-label="Daily digest">
          {digestFigures(digest).map((figure) => <li key={figure.label}>
            <Link className="home-figure" to={figure.href} data-over={figure.over ? "true" : undefined}>
              <span className="home-figure-label">{figure.label}</span>
              <span className="home-figure-value">{figure.value}</span>
              <span className="home-figure-detail">{figure.detail}</span>
            </Link>
          </li>)}
        </ul>
        <Text size="xs" c="dimmed">{digest.basis}</Text>
      </>}
  </Stack>;
}

function QueueRow({ item }: { item: AttentionItem }) {
  return <Table.Tr data-item-id={item.id} data-severity={item.severity}>
    <Table.Td className="harness-nowrap">
      <Badge variant="outline" color={SEVERITY_COLORS[item.severity]} tt="none" className="harness-badge">
        {SEVERITY_LABELS[item.severity]}
      </Badge>
    </Table.Td>
    <Table.Td>
      <Text fw={600} size="sm">{item.title}</Text>
      {item.detail && <Text size="sm" c="dimmed">{item.detail}</Text>}
      <Text size="xs" c="dimmed">{SOURCE_LABELS[item.source] ?? item.source}</Text>
    </Table.Td>
    <Table.Td className="harness-nowrap">
      <Button component={Link} to={item.action.href} size="compact-sm" variant="default">{item.action.label}</Button>
    </Table.Td>
  </Table.Tr>;
}

export function AttentionQueue({ queue }: { queue: Queue }) {
  const items = shownItems(queue.items);
  const failed = failedSources(queue.sources);
  const checking = pendingSources(queue.sources);
  return <Stack gap="xs" component="section" aria-labelledby="home-queue-title">
    <Group justify="space-between" align="baseline">
      <Title id="home-queue-title" order={2} size="h4">Needs you</Title>
      {queue.total > items.length && <Text size="xs" c="dimmed">The first {items.length} of {queue.total}, most pressing first</Text>}
    </Group>
    {items.length === 0 && checking.length > 0
      ? <EvidenceState kind="loading" title={`Still checking ${checking.join(", ")}`}>
        Nothing needs you so far. Their items appear here when the read finishes.
      </EvidenceState>
      : items.length === 0
      ? <EvidenceState kind="empty" title="Nothing needs you">
        No rule or hook has gone silent, no draft is waiting, no session ran past your context lines and nothing drifted.
      </EvidenceState>
      : <Box className="harness-panel" role="region" aria-label="Attention queue items" tabIndex={0} style={{ overflowX: "auto" }}>
        <Table className="harness-table home-queue" verticalSpacing="xs" aria-label="Attention queue, most pressing first">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Urgency</Table.Th>
              <Table.Th scope="col">What</Table.Th>
              <Table.Th scope="col">Action</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>{items.map((item) => <QueueRow key={item.id} item={item} />)}</Table.Tbody>
        </Table>
      </Box>}
    {items.length > 0 && checking.length > 0 && <Text size="sm" c="dimmed" role="status">
      Still checking {checking.join(", ")}; {checking.length === 1 ? "its items" : "their items"} will join the queue when the read finishes.
    </Text>}
    {failed.length > 0 && <Text size="sm" c="dimmed" role="status">
      Could not read {failed.join(", ")}; the queue may be missing {failed.length === 1 ? "its items" : "their items"}.
    </Text>}
  </Stack>;
}

export function DriftSection({ drift }: { drift: Drift }) {
  const label = drift.status === "drift" ? "Drifted" : drift.status === "current" ? "Current"
    : drift.status === "pending" ? "Still checking" : "Unavailable";
  const color = drift.status === "drift" ? "yellow" : drift.status === "current" || drift.status === "pending" ? "gray" : "red";
  return <Stack gap="xs" component="section" id="drift" aria-labelledby="home-drift-title">
    <Group gap="sm">
      <Title id="home-drift-title" order={2} size="h4">Projection drift</Title>
      <Badge variant="outline" color={color} tt="none">{label}</Badge>
    </Group>
    {drift.status === "failed" && <EvidenceState kind="error" title="Drift could not be read">{drift.message ?? "The overview did not answer."}</EvidenceState>}
    {drift.status === "pending" && <Text size="sm" c="dimmed" role="status">The doctor is still comparing the projected files with the selection.</Text>}
    {drift.status === "current" && <Text size="sm" c="dimmed">The projected files match the selection.</Text>}
    {drift.status === "drift" && <>
      <List size="sm" className="home-drift-list">{drift.items.map((line) => <List.Item key={line}>{line}</List.Item>)}</List>
      {drift.command && <CommandChip command={drift.command} label="Governed repair" />}
    </>}
  </Stack>;
}

// Kept free of hooks so tests can render it directly.
export function HomeView({ report }: { report: HomeReport }) {
  return <Stack gap="xl">
    <DigestStrip digest={report.digest} />
    <AttentionQueue queue={report.queue} />
    <DriftSection drift={report.drift} />
  </Stack>;
}

export function HomePage() {
  const home = useQuery(homeQuery);
  const location = useLocation();
  useEffect(() => {
    // A link to `/home#drift` lands on the section once it has rendered.
    if (location.hash && home.data) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [location.hash, home.data]);
  return <Stack gap="lg">
    <div className="page-heading">
      <Title order={1}>Home</Title>
      <Text c="dimmed" size="sm">Your last day in sessions, and what needs you first.</Text>
    </div>
    {home.isPending && <EvidenceState kind="loading" title="Reading your sessions, rules, hooks and drafts">
      Usually under a second; slower checks follow on the page.
    </EvidenceState>}
    {home.isError && <EvidenceState kind="error" title="Home could not be read"
      action={<Button onClick={() => { void home.refetch(); }} size="compact-sm" variant="default">Try again</Button>}>
      {home.error.message}
    </EvidenceState>}
    {home.data && <HomeView report={home.data} />}
  </Stack>;
}
