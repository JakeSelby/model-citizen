import { Stack, Tabs, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { EvidenceState } from "../components/StudioKit";
import { usePeriods } from "./api";
import { EfficacyTable } from "./EfficacyTable";
import {
  efficacySummary, metricColumns, parseTab, settingsSummary, tabSearch, withEdit,
  type DraftChange, type HarnessTab, type Loadable, type MetricColumn, type PeriodRow,
  type SettingEdits, type SettingField, type SettingRow,
} from "./model";
import { SettingsTable } from "./SettingsTable";

export type PeriodsState = Loadable<{ rows: PeriodRow[]; note?: string }>;
export type SettingsState = Loadable<{ rows: SettingRow[] }>;

type Content = {
  title: string;
  /** The one question the page answers, shown under the title. */
  question: string;
  /** Singular noun for the page's items, used in table headings and states: "Rule", "Hook". */
  itemNoun?: string;
  metrics?: MetricColumn[];
  fields: SettingField[];
};

function TabLabel({ name, summary }: { name: string; summary?: string }) {
  return <Stack gap={0} align="flex-start">
    <Text fw={600} size="sm">{name}</Text>
    {summary && <Text size="xs" c="dimmed">{summary}</Text>}
  </Stack>;
}

function StateView({ state, noun, kind }: { state: Exclude<PeriodsState | SettingsState, { state: "ready" }>; noun: string; kind: string }) {
  return state.state === "loading"
    ? <EvidenceState kind="loading" title={`Reading ${kind}`}>Usually under five seconds.</EvidenceState>
    : <EvidenceState kind="error" title={`${noun} ${kind} could not be read`}>{state.message}</EvidenceState>;
}

// Kept free of hooks so tests can call it directly and reach its handlers.
export function HarnessFrame({ title, question, itemNoun = "Item", metrics, fields, tab, onTabChange, periods, settings, edits, onEdit, onDiscard, onDraft, saving, saveError }: Content & {
  tab: HarnessTab;
  onTabChange: (tab: HarnessTab) => void;
  periods: PeriodsState;
  settings: SettingsState;
  edits: SettingEdits;
  onEdit: (itemId: string, field: string, value: string) => void;
  onDiscard: () => void;
  onDraft: (changes: DraftChange[]) => void;
  saving?: boolean;
  saveError?: string | null;
}) {
  const noun = itemNoun.toLowerCase();
  return <Stack gap="lg">
    <div>
      <Title order={1}>{title}</Title>
      <Text c="dimmed">{question}</Text>
    </div>
    <Tabs value={tab} onChange={(value) => onTabChange(value === "settings" ? "settings" : "efficacy")} keepMounted={false}>
      <Tabs.List aria-label={`${title} views`}>
        <Tabs.Tab value="efficacy">
          <TabLabel name="Efficacy" summary={periods.state === "ready" ? efficacySummary(periods.rows) : undefined} />
        </Tabs.Tab>
        <Tabs.Tab value="settings">
          <TabLabel name="Settings" summary={settings.state === "ready" ? settingsSummary(settings.rows) : undefined} />
        </Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="efficacy" pt="lg">
        {periods.state !== "ready"
          ? <StateView state={periods} noun={itemNoun} kind="measurement periods" />
          : periods.rows.length === 0
            ? <EvidenceState kind="empty" title="No measurement periods yet">
              Each {noun} gets a row once a session has run under its setting.
            </EvidenceState>
            : <Stack gap="sm">
              <EfficacyTable caption={`${title}: efficacy by setting period`} rows={periods.rows}
                columns={metrics ?? metricColumns(periods.rows)} itemHeading={itemNoun} />
              {periods.note && <Text size="sm" c="dimmed">{periods.note}</Text>}
              <Text size="sm" c="dimmed">
                One row per {noun} per setting period. A change splits the {noun} into a before row and
                a row that measures for seven days; the comparison is in Evals.
              </Text>
            </Stack>}
      </Tabs.Panel>
      <Tabs.Panel value="settings" pt="lg">
        {settings.state !== "ready"
          ? <StateView state={settings} noun={itemNoun} kind="settings" />
          : settings.rows.length === 0
            ? <EvidenceState kind="empty" title="Nothing to configure yet">
              No {noun} is installed for this harness.
            </EvidenceState>
            : <SettingsTable caption={`${title}: settings`} rows={settings.rows} fields={fields} edits={edits}
              onEdit={onEdit} onDiscard={onDiscard} onDraft={onDraft} saving={saving} saveError={saveError}
              itemHeading={itemNoun} />}
      </Tabs.Panel>
    </Tabs>
  </Stack>;
}

/**
 * A Harness page: Efficacy (the default) reads the surface's setting periods; Settings shows the
 * page's configuration and hands edits to `onDraft`, which saves them as a draft in Changes.
 */
export function HarnessPage({ surface, settings, onDraft, ...content }: Content & {
  surface: string;
  settings: SettingsState;
  onDraft: (changes: DraftChange[]) => void | Promise<void>;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePeriods(surface);
  const [edits, setEdits] = useState<SettingEdits>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const periods: PeriodsState = query.isPending
    ? { state: "loading" }
    : query.isError
      ? { state: "error", message: query.error.message }
      : { state: "ready", rows: query.data.rows, note: query.data.note };

  async function saveDraft(changes: DraftChange[]) {
    if (!changes.length) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onDraft(changes);
      setEdits({});
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return <HarnessFrame {...content}
    tab={parseTab(searchParams)}
    onTabChange={(tab) => setSearchParams(tabSearch(searchParams, tab))}
    periods={periods}
    settings={settings}
    edits={edits}
    onEdit={(itemId, field, value) => setEdits((current) => withEdit(current, itemId, field, value))}
    onDiscard={() => setEdits({})}
    onDraft={(changes) => void saveDraft(changes)}
    saving={saving}
    saveError={saveError} />;
}
