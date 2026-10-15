// One hook's page: its liveness, decisions by day and outcome, recent decisions and settings,
// read from `GET /api/harness/hook` (`citizen harness hook NAME --json`).
import { Anchor, Badge, Box, Code, Group, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { EvidenceState } from "../../components/StudioKit";
import { livenessColor } from "./hooksModel";

export const OUTCOMES = ["block", "warn", "allow", "error"] as const;
export type Outcome = typeof OUTCOMES[number];

export type HookDay = { day: string } & Record<Outcome, number>;

export type HookDecision = {
  ts: string;
  point: string;
  answer: string;
  outcome: Outcome;
  session: string;
  input_hash: string;
  reason: string;
};

export type HookDetail = {
  hook: string;
  label: string;
  built_in: boolean;
  core: boolean;
  window_days: number;
  summary: { fires: number; blocks: number; warns: number; errors: number; points: string; last_fired: string; liveness: string };
  by_day: HookDay[];
  recent: HookDecision[];
  settings: { enabled: string; modes: Record<string, string>; recording: boolean; source: string };
};

export const OUTCOME_LABELS: Record<Outcome, string> = { block: "Blocked", warn: "Warned", allow: "Allowed", error: "Errored" };
const OUTCOME_COLORS: Record<Outcome, string> = { block: "red", warn: "yellow", allow: "gray", error: "grape" };

/** The detail page of one hook; a built-in point's id carries its `point:` prefix. */
export function hookHref(id: string): string {
  return `/harness/hooks/${encodeURIComponent(id)}`;
}

export class UnknownHook extends Error {}

export async function loadHookDetail(name: string, signal?: AbortSignal): Promise<HookDetail> {
  const response = await fetch(`/api/harness/hook?name=${encodeURIComponent(name)}`, {
    method: "GET", credentials: "same-origin", signal,
  });
  let body: (HookDetail & { error?: string }) | null = null;
  try {
    body = await response.json() as HookDetail & { error?: string };
  } catch { /* A non-JSON body is reported by status below. */ }
  if (response.status === 404) throw new UnknownHook(name);
  if (!response.ok) throw new Error(body?.error ?? `Request failed (${response.status}).`);
  if (!body || !Array.isArray(body.by_day) || !Array.isArray(body.recent)) throw new Error("The hook response is incomplete.");
  return body;
}

export function hookDetailQuery(name: string) {
  return {
    queryKey: ["harness-hook", name] as const,
    queryFn: ({ signal }: { signal?: AbortSignal }) => loadHookDetail(name, signal),
    staleTime: 30_000,
    retry: (count: number, error: Error) => !(error instanceof UnknownHook) && count < 2,
  };
}

/** Each outcome's total over the window. */
export function outcomeTotals(days: HookDay[]): Record<Outcome, number> {
  const out = { block: 0, warn: 0, allow: 0, error: 0 };
  for (const day of days) for (const name of OUTCOMES) out[name] += day[name];
  return out;
}

function percent(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 1000) / 10}% of fires` : "no fires";
}

function Figure({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <Box>
    <Text size="xs" c="dimmed">{label}</Text>
    <Text fw={700} size="xl">{value}</Text>
    <Text size="xs" c="dimmed">{detail}</Text>
  </Box>;
}

/** Stacked bars, one per day, with the counts in a table a screen reader can read. */
export function DecisionsByDay({ days }: { days: HookDay[] }) {
  const peak = Math.max(1, ...days.map((day) => OUTCOMES.reduce((sum, name) => sum + day[name], 0)));
  return <Stack gap="xs" component="section" aria-label="Decisions by day">
    <Title order={2} size="h4">Decisions by day</Title>
    <Group gap="xs">
      {OUTCOMES.map((name) => <Badge key={name} variant="light" color={OUTCOME_COLORS[name]} tt="none">{OUTCOME_LABELS[name]}</Badge>)}
    </Group>
    <Group gap={4} align="flex-end" h={96} aria-hidden="true" wrap="nowrap">
      {days.map((day) => <Stack key={day.day} gap={0} justify="flex-end" h="100%" style={{ flex: 1 }}>
        {OUTCOMES.map((name) => day[name] ? <Box key={name} bg={`var(--mantine-color-${OUTCOME_COLORS[name]}-5)`}
          h={`${Math.max(2, (day[name] / peak) * 96)}px`} /> : null)}
      </Stack>)}
    </Group>
    <Box role="region" aria-label="Decisions by day, counts" tabIndex={0} style={{ overflowX: "auto" }}>
      <Table verticalSpacing={2} aria-label="Decisions by day and outcome">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Day</Table.Th>
            {OUTCOMES.map((name) => <Table.Th key={name} scope="col">{OUTCOME_LABELS[name]}</Table.Th>)}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {days.map((day) => <Table.Tr key={day.day} data-day={day.day}>
            <Table.Th scope="row" fw={400}>{day.day}</Table.Th>
            {OUTCOMES.map((name) => <Table.Td key={name}>{day[name]}</Table.Td>)}
          </Table.Tr>)}
        </Table.Tbody>
      </Table>
    </Box>
  </Stack>;
}

export function RecentDecisions({ rows }: { rows: HookDecision[] }) {
  return <Stack gap="xs" component="section" aria-label="Recent decisions">
    <Group justify="space-between">
      <Title order={2} size="h4">Recent decisions</Title>
      <Anchor component={Link} to="/harness/hooks" size="sm">All blocks in the Safety log</Anchor>
    </Group>
    {rows.length === 0
      ? <EvidenceState kind="empty" title="No decisions in this window">This hook wrote nothing to the decision log in the window.</EvidenceState>
      : <Box role="region" aria-label="Recent decision entries" tabIndex={0} style={{ overflowX: "auto" }}>
        <Table verticalSpacing="xs" aria-label="Recent decisions, newest first" miw={640}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">When</Table.Th>
              <Table.Th scope="col">Outcome</Table.Th>
              <Table.Th scope="col">Point</Table.Th>
              <Table.Th scope="col">Session</Table.Th>
              <Table.Th scope="col">Input hash</Table.Th>
              <Table.Th scope="col">Why</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, index) => <Table.Tr key={`${row.ts}-${index}`} data-outcome={row.outcome}>
              <Table.Td><Text size="sm"><time dateTime={row.ts}>{row.ts}</time></Text></Table.Td>
              <Table.Td><Badge variant="light" tt="none" color={OUTCOME_COLORS[row.outcome]}>{OUTCOME_LABELS[row.outcome]}</Badge></Table.Td>
              <Table.Td><Code>{row.point}</Code></Table.Td>
              <Table.Td>{row.session ? <Code>{row.session}</Code> : <Text size="sm" c="dimmed">none</Text>}</Table.Td>
              <Table.Td>{row.input_hash ? <Code title={row.input_hash}>{row.input_hash.slice(0, 12)}</Code> : <Text size="sm" c="dimmed">none</Text>}</Table.Td>
              <Table.Td><Text size="sm">{row.reason || row.answer}</Text></Table.Td>
            </Table.Tr>)}
          </Table.Tbody>
        </Table>
      </Box>}
  </Stack>;
}

export function HookSettings({ detail }: { detail: HookDetail }) {
  const modes = Object.entries(detail.settings.modes);
  return <Stack gap="xs" component="section" aria-label="Hook settings">
    <Group justify="space-between">
      <Title order={2} size="h4">Settings</Title>
      {!detail.built_in && <Anchor component={Link} to="/harness/hooks?tab=settings" size="sm">Change in a draft</Anchor>}
    </Group>
    <Table verticalSpacing="xs" aria-label={`Settings: ${detail.label}`}>
      <Table.Tbody>
        <Table.Tr><Table.Th scope="row">On</Table.Th><Table.Td>{detail.settings.enabled}</Table.Td></Table.Tr>
        <Table.Tr>
          <Table.Th scope="row">Decision mode</Table.Th>
          <Table.Td>{modes.length
            ? modes.map(([point, mode]) => <Text key={point} size="sm"><Code>{point}</Code> {mode}</Text>)
            : <Text size="sm" c="dimmed">no mode</Text>}</Table.Td>
        </Table.Tr>
        <Table.Tr><Table.Th scope="row">Decision points</Table.Th><Table.Td>{detail.summary.points}</Table.Td></Table.Tr>
        <Table.Tr>
          <Table.Th scope="row">Decision log</Table.Th>
          <Table.Td>{detail.settings.recording ? "recording" : "off, so this hook's silence says nothing"}</Table.Td>
        </Table.Tr>
        <Table.Tr>
          <Table.Th scope="row">Source</Table.Th>
          <Table.Td>{detail.settings.source ? <Code>{detail.settings.source}</Code> : <Text size="sm" c="dimmed">built into the decision log</Text>}</Table.Td>
        </Table.Tr>
      </Table.Tbody>
    </Table>
  </Stack>;
}

/** The page body for a loaded hook. Kept free of hooks so tests render it directly. */
export function HookDetailView({ detail }: { detail: HookDetail }) {
  const { summary } = detail;
  const totals = outcomeTotals(detail.by_day);
  return <Stack gap="lg">
    <Text size="sm" component="nav" aria-label="Breadcrumb">
      <Anchor component={Link} to="/harness/hooks">Hooks</Anchor> / {detail.label}
    </Text>
    <div>
      <Group gap="sm" align="center">
        <Title order={1}>{detail.label}</Title>
        <Badge color={livenessColor(summary.liveness)} variant="light" tt="none" data-liveness={summary.liveness}>{summary.liveness}</Badge>
      </Group>
      <Text c="dimmed" size="sm">
        {detail.built_in ? "Built in" : detail.core ? "Core" : "Optional"} · <Code>{detail.hook}</Code> · last {detail.window_days} days
      </Text>
    </div>
    <SimpleGrid cols={{ base: 2, sm: 5 }} component="section" aria-label="Hook figures">
      <Figure label="Last ran" value={summary.last_fired} detail={summary.points} />
      <Figure label="Decisions" value={summary.fires} detail={`in ${detail.window_days} days`} />
      <Figure label="Blocked" value={totals.block} detail={percent(totals.block, summary.fires)} />
      <Figure label="Warned or asked" value={totals.warn} detail={percent(totals.warn, summary.fires)} />
      <Figure label="Errors" value={totals.error} detail={totals.error ? "the hook failed or timed out" : "never failed in the window"} />
    </SimpleGrid>
    <DecisionsByDay days={detail.by_day} />
    <RecentDecisions rows={detail.recent} />
    <HookSettings detail={detail} />
  </Stack>;
}

export function HookDetailPage() {
  const { hook = "" } = useParams();
  const query = useQuery(hookDetailQuery(hook));
  if (query.isPending) return <EvidenceState kind="loading" title={`Reading ${hook}`}>Decisions come from the local decision log.</EvidenceState>;
  if (query.isError) {
    return query.error instanceof UnknownHook
      ? <EvidenceState kind="empty" title="No such hook">
        No hook is named <Code>{hook}</Code>. <Anchor component={Link} to="/harness/hooks">Back to Hooks</Anchor>
      </EvidenceState>
      : <EvidenceState kind="error" title={`${hook} could not be read`}>{query.error.message}</EvidenceState>;
  }
  return <HookDetailView detail={query.data} />;
}
