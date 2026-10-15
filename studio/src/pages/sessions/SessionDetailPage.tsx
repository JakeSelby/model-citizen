// One session: context per call against the 160k and 400k lines with each reset marked, and cost
// per turn, from `GET /api/sessions/session` (`citizen sessions show ID --json`).
import { Anchor, Code, Group, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { EvidenceState } from "../../components/StudioKit";
import "../../harness/harness.css";
import { ContextPerCall } from "./ContextCharts";
import { BandBadge, Figure, Observed, ResetEvents } from "./SessionsPage";
import {
  OBSERVED, UnknownSession, count, dollars, sessionQuery, share, tokens, type SessionDetail, type TurnRow,
} from "./sessionsModel";

/** The costliest turns first, so the expensive part of a long session is on top. */
export function costliestTurns(turns: TurnRow[], limit = 10): TurnRow[] {
  return turns.filter((t) => t.calls > 0)
    .sort((a, b) => (b.cost ?? -1) - (a.cost ?? -1) || a.turn - b.turn).slice(0, limit);
}

export function TurnCosts({ turns }: { turns: TurnRow[] }) {
  const shown = costliestTurns(turns);
  const caption = `Costliest ${shown.length} of ${count(turns.filter((t) => t.calls > 0).length)} turns`;
  return <Stack gap="xs" component="section" aria-label="Cost per turn">
    <div>
      <Title order={2} size="h4">Cost per turn</Title>
      <Text size="sm" c="dimmed">{caption}, subagent calls included. {OBSERVED}.</Text>
    </div>
    {shown.length === 0
      ? <EvidenceState kind="empty" title="No turns with a call">This transcript has no priced call yet.</EvidenceState>
      : <div className="harness-panel">
        <Table.ScrollContainer minWidth={520}
          scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
          <Table verticalSpacing="xs" aria-label={caption} className="data-table harness-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Turn</Table.Th>
                <Table.Th scope="col">Started</Table.Th>
                <Table.Th scope="col" className="harness-num">Calls</Table.Th>
                <Table.Th scope="col" className="harness-num">Cost</Table.Th>
                <Table.Th scope="col" className="harness-num">Peak context</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((turn) => <Table.Tr key={turn.turn} data-turn={turn.turn}>
                <Table.Th scope="row" fw={400}>{turn.turn + 1}</Table.Th>
                <Table.Td className="harness-nowrap">{turn.ts ? <time dateTime={turn.ts}>{turn.ts}</time> : "unknown"}</Table.Td>
                <Table.Td className="harness-num">{count(turn.calls)}</Table.Td>
                <Table.Td className="harness-num">{dollars(turn.cost)}{turn.cost !== null && !turn.cost_complete ? " +" : ""}</Table.Td>
                <Table.Td className="harness-num">{tokens(turn.max_context)}</Table.Td>
              </Table.Tr>)}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>}
  </Stack>;
}

/** The page body for a loaded session. Kept free of hooks so tests render it directly. */
export function SessionDetailView({ detail }: { detail: SessionDetail }) {
  const { session, lines, calls } = detail;
  return <Stack gap="lg">
    <Text size="sm" component="nav" aria-label="Breadcrumb">
      <Anchor component={Link} to="/sessions">Sessions</Anchor> / {session.session_id.slice(0, 8)}
    </Text>
    <div>
      <Group gap="sm" align="center">
        <Title order={1}>{session.title ?? session.session_id.slice(0, 8)}</Title>
        <BandBadge context={session.max_context} lines={lines} />
      </Group>
      <Text c="dimmed" size="sm">
        {session.repository} · <Code>{session.session_id}</Code>{session.models.length ? ` · ${session.models.join(", ")}` : ""}
      </Text>
    </div>
    <SimpleGrid cols={{ base: 2, sm: 5 }} component="section" aria-label="Session figures" className="sessions-figures">
      <Figure label="Cost" value={dollars(session.cost)} detail={session.cost_complete ? "every call priced" : "priced calls only"} />
      <Figure label="Cost per turn" value={dollars(session.cost_per_turn)} detail={`${count(session.turns)} turns`} />
      <Figure label="Peak context" value={tokens(session.max_context)} detail={`median ${tokens(session.median_context)}`} />
      <Figure label={`Calls past ${tokens(lines.warn)}`} value={share(session.over_warn, session.calls)}
        detail={`${count(session.over_warn)} of ${count(session.calls)} main-thread calls`} />
      <Figure label="Resets" value={count(session.compacts + session.reseeds)}
        detail={`${count(session.compacts)} compactions, ${count(session.reseeds)} reseeds`} />
    </SimpleGrid>
    <Stack gap="xs" component="section" aria-label="Context per call">
      <div>
        <Title order={2} size="h4">Context per call</Title>
        <Text size="sm" c="dimmed">
          Each main-thread call in order against your stance's {tokens(lines.warn)} and {tokens(lines.limit)} lines; a marker is a compaction or a reseed. {OBSERVED}.
        </Text>
      </div>
      {calls.length === 0
        ? <EvidenceState kind="empty" title="No calls in this session">The transcript has no assistant reply yet.</EvidenceState>
        : <div className="sessions-chart-scroll" role="region" aria-label="Context per call, scrollable" tabIndex={0}><ContextPerCall calls={calls} lines={lines} /></div>}
    </Stack>
    <ResetEvents events={detail.events} />
    <TurnCosts turns={detail.turns} />
    <Observed basis={detail.basis} />
  </Stack>;
}

export function SessionDetailPage() {
  const { session = "" } = useParams();
  const query = useQuery(sessionQuery(session));
  if (query.isPending) return <EvidenceState kind="loading" title={`Reading session ${session.slice(0, 8)}`}>From its transcript, through the index.</EvidenceState>;
  if (query.isError) {
    return query.error instanceof UnknownSession
      ? <EvidenceState kind="empty" title="No such session">
        No transcript is named <Code>{session}</Code>. <Anchor component={Link} to="/sessions">Back to Sessions</Anchor>
      </EvidenceState>
      : <EvidenceState kind="error" title="The session could not be read">{query.error.message}</EvidenceState>;
  }
  return <SessionDetailView detail={query.data} />;
}
