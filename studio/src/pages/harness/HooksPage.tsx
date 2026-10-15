import { Anchor, Badge, Box, Code, Group, Paper, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { ActivityPage } from "../../activity/ActivityPage";
import { loadActivity } from "../../activity/api";
import { EMPTY_FILTERS, type ActivityEntry } from "../../activity/model";
import { focusedEntry } from "../../activity/rollbackModel";
import { EvidenceState } from "../../components/StudioKit";
import { loadDraft, loadDraftSelection, loadSchema, saveDraft, saveDraftSelection } from "../../configure/api";
import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import { parseTab, type DraftChange, type MetricColumn, type PeriodRow } from "../../harness/model";
import { useLoadedDraft } from "../../harness/selectedDraft";
import {
  HOOK_METRICS, MODES_PATH, byLiveness, hookFields, SAFETY_OUTCOMES, SURFACE, livenessColor, livenessCounts,
  livenessOf, modeChoices, nextModes, safetyLabel, settingRows, splitChanges, utcMinute, worstFirst,
  type DraftState,
} from "./hooksModel";
import { hookHref } from "./HookDetailPage";

const SAFETY_LIMIT = 50;

export type HookDraftApi = {
  saveSelection: typeof saveDraftSelection;
  loadConfig: typeof loadDraft;
  saveConfig: typeof saveDraft;
};

const DRAFT_API: HookDraftApi = { saveSelection: saveDraftSelection, loadConfig: loadDraft, saveConfig: saveDraft };

/**
 * Save Settings edits into the loaded draft: switches through the selection save path, decision
 * modes through the configuration save path. Returns the draft's new revision; throws the refusal.
 */
export async function saveHookChanges(state: DraftState, changes: DraftChange[], api: HookDraftApi = DRAFT_API): Promise<string> {
  const { selection, modes } = splitChanges(changes);
  let revision = state.revision;
  if (Object.keys(selection).length) {
    const saved = await api.saveSelection(state.draft, revision, crypto.randomUUID(), selection);
    if (!saved.saved) throw new Error(saved.error || "The draft refused the switch change.");
    revision = saved.result?.revision ?? revision;
  }
  if (Object.keys(modes).length) {
    const current = await api.loadConfig(state.draft);
    if (current.status !== "ready") throw new Error(current.message);
    const saved = await api.saveConfig(state.draft, current.draft.revision ?? revision,
      { [MODES_PATH]: nextModes(current.values, modes) });
    if (!saved.saved) throw new Error(saved.errors.map((error) => `${error.path}: ${error.message}`).join("; ") || "The draft refused the mode change.");
    revision = saved.result?.revision ?? revision;
  }
  return revision;
}

export async function readHookDraft(draft: string): Promise<DraftState> {
  const [selection, config] = await Promise.all([loadDraftSelection(draft), loadDraft(draft)]);
  if (selection.status !== "ready") throw new Error(selection.message);
  if (config.status !== "ready") throw new Error(config.message);
  return { draft, revision: selection.draft.revision ?? config.draft.revision ?? "", controls: selection.controls, values: config.values };
}

export async function loadSafetyLog(signal?: AbortSignal): Promise<ActivityEntry[]> {
  const pages = await Promise.all(SAFETY_OUTCOMES.map((outcome) =>
    loadActivity({ ...EMPTY_FILTERS, outcome }, "", SAFETY_LIMIT, signal)));
  return worstFirst(pages.flatMap((page) => page.entries));
}

export const safetyQuery = {
  queryKey: ["hooks-safety-log"] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) => loadSafetyLog(signal),
  staleTime: 30_000,
};

/** Hooks' columns, with liveness as a badge coloured like the summary's. */
export const HOOK_COLUMNS: MetricColumn[] = HOOK_METRICS.map((column) => column.key === "liveness"
  ? { ...column, align: "left", render: (row: PeriodRow) => <Badge className="harness-badge" variant="light" tt="none" color={livenessColor(livenessOf(row))}>{livenessOf(row)}</Badge> }
  : column);

export function LivenessSummary({ rows }: { rows: PeriodRow[] }) {
  const counts = livenessCounts(rows);
  if (!counts.length) return null;
  return <Box component="section" aria-label="Hook liveness">
    <Group justify="space-between" wrap="wrap" gap="sm">
      <Text fw={600} size="sm">Liveness</Text>
      <Group gap="xs">
        {counts.map(({ liveness, count }) => <Badge key={liveness} color={livenessColor(liveness)} variant="light" tt="none">
          {count} {liveness}
        </Badge>)}
      </Group>
    </Group>
    <Text size="xs" c="dimmed" mt="xs">
      Silent means an enabled hook wrote no decision in seven days. A hook that never writes
      decisions is not observable, so its quiet says nothing.
    </Text>
  </Box>;
}

/** One link per hook to its detail page, with its liveness. */
export function HookLinks({ rows }: { rows: PeriodRow[] }) {
  const latest = new Map<string, PeriodRow>();
  for (const row of rows) {
    const seen = latest.get(row.item_id);
    if (!seen || row.period.start > seen.period.start) latest.set(row.item_id, row);
  }
  if (!latest.size) return null;
  return <Box component="nav" aria-label="Hook details">
    <Text fw={600} size="sm" mb="xs">Open a hook</Text>
    <Group gap="xs">
      {[...latest.values()].map((row) => <Anchor key={row.item_id} component={Link} to={hookHref(row.item_id)}
        size="sm" data-hook={row.item_id}>
        {row.item_label} <Badge size="xs" variant="light" tt="none" color={livenessColor(livenessOf(row))}>{livenessOf(row)}</Badge>
      </Anchor>)}
    </Group>
  </Box>;
}

export function SafetyLog({ entries }: { entries: ActivityEntry[] }) {
  const commands = entries.some((entry) => entry.command);
  return <Stack gap="xs" component="section" aria-label="Safety log">
    <Title order={2} size="h4">Safety log</Title>
    <Text size="sm" c="dimmed">Blocked, asked and warned actions from the decision log, worst first.</Text>
    {entries.length === 0
      ? <EvidenceState kind="empty" title="Nothing blocked or warned">No hook refused, asked about or warned on an action in the recent log.</EvidenceState>
      : <Paper withBorder radius="md" className="harness-panel"><Box role="region" aria-label="Safety log entries" tabIndex={0} style={{ overflowX: "auto" }}>
        <Table verticalSpacing="xs" aria-label="Safety log, worst first" miw={640} className="data-table harness-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Outcome</Table.Th>
              <Table.Th scope="col">When</Table.Th>
              <Table.Th scope="col">Hook</Table.Th>
              <Table.Th scope="col">Why</Table.Th>
              {commands && <Table.Th scope="col">Action</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {entries.map((entry) => <Table.Tr key={entry.id} data-outcome={entry.outcome}>
              <Table.Td>
                <Badge className="harness-badge" variant="light" tt="none" color={entry.outcome === "refused" ? "red" : "yellow"}>{safetyLabel(entry.outcome)}</Badge>
              </Table.Td>
              <Table.Td><Text size="sm"><time dateTime={entry.timestamp}>{utcMinute(entry.timestamp) || "Time unavailable"}</time></Text></Table.Td>
              <Table.Td>{entry.hook ? <Code>{entry.hook}</Code> : <Text size="sm" c="dimmed">built in</Text>}</Table.Td>
              <Table.Td><Text size="sm">{entry.reason}</Text></Table.Td>
              {commands && <Table.Td>{entry.command ? <Code block>{entry.command}</Code> : null}</Table.Td>}
            </Table.Tr>)}
          </Table.Tbody>
        </Table>
      </Box></Paper>}
  </Stack>;
}

/** The Hooks page: liveness and figures per hook on Efficacy, per-hook settings, and the Safety log. */
export function HooksPage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const focus = focusedEntry(location.search);
  const tab = !searchParams.get("tab") && focus ? "log" : parseTab(searchParams);
  const periods = usePeriods(SURFACE);
  const schema = useQuery({ queryKey: ["configure-schema"], queryFn: loadSchema, staleTime: 300_000 });
  const safety = useQuery({ ...safetyQuery, enabled: tab === "log" });
  const { draft, loading: draftLoading, error: draftError, reload } = useLoadedDraft(readHookDraft);
  const rows = periods.data?.rows ?? [];
  const choices = modeChoices(schema.data ?? null);

  const settings: SettingsState = periods.isPending
    ? { state: "loading" }
    : periods.isError
      ? { state: "error", message: periods.error.message }
      : { state: "ready", rows: settingRows(rows, draft, choices) };

  async function onDraft(changes: DraftChange[], name: string) {
    if (!draft || draft.draft !== name) throw new Error("Wait for the draft to load before saving.");
    await saveHookChanges(draft, changes);
    await reload();
  }

  const log = <Stack gap="xl">
    {safety.isPending
      ? <EvidenceState kind="loading" title="Reading the Safety log">Blocked and warned actions from the decision log.</EvidenceState>
      : safety.isError
        ? <EvidenceState kind="error" title="The Safety log could not be read">{safety.error.message}</EvidenceState>
        : <SafetyLog entries={safety.data} />}
    {/* The Activity timeline keeps Studio changes, their rollback and the `?apply=` links into
        them until that flow has a page of its own. */}
    <ActivityPage embedded />
  </Stack>;

  return <HarnessPage surface={SURFACE} title="Hooks" itemNoun="Hook"
    question="Guardrails that run outside the model: which are firing, failing or silent."
    metrics={HOOK_COLUMNS} fields={hookFields(settings.state === "ready" ? settings.rows : [])} settings={settings}
    order={byLiveness} onDraft={onDraft}
    draftLoading={draftLoading} draftError={draftError}
    efficacyLead={<LivenessSummary rows={rows} />}
    efficacyNote={<HookLinks rows={rows} />}
    logTab={{ name: "Safety log", summary: "Blocked, asked and warned", content: log }}
    openLog={Boolean(focus)} />;
}
