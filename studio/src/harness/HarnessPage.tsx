import { Anchor, Code, Stack, Tabs, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { DraftPicker } from "../changes/DraftPicker";
import { EvidenceState } from "../components/StudioKit";
import { usePeriods } from "./api";
import { EfficacyTable } from "./EfficacyTable";
import {
  efficacySummary, metricColumns, parseTab, settingsSummary, tabSearch, withEdit,
  type DraftChange, type HarnessTab, type Loadable, type MetricColumn, type PeriodRow,
  type SettingEdits, type SettingField, type SettingRow,
} from "./model";
import { changesHref, selectedDraft, withDraft } from "./selectedDraft";
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

/** The Settings tab's draft selection: the picker's value, and the selected draft's read state. */
export type DraftSelection = {
  value: string;
  /** The page creating a draft, recorded as its source in Changes: "stances", "hooks". */
  source: string;
  onChange: (name: string) => void;
  loading?: boolean;
  error?: string;
  /** Saving needs no pick: the page makes its own draft per change then. */
  optional?: boolean;
};

function DraftStatus({ draft }: { draft: DraftSelection }) {
  if (draft.error) return <Text size="sm" c="red" role="alert">{draft.error}</Text>;
  if (draft.loading) return <Text size="sm" c="dimmed" role="status">Reading draft {draft.value}…</Text>;
  return <Text size="sm" c="dimmed">
    {draft.value
      ? <>Edits save into draft <Code>{draft.value}</Code>, never the installed configuration.</>
      : draft.optional
        ? "Pick a draft to save edits into it, or save with none and each change becomes its own draft."
        : "Pick or create a draft to save edits into; nothing here changes the installed configuration."}
  </Text>;
}

/** After a save: where the draft waits for review. */
export function SavedDraft({ name }: { name: string }) {
  return <Text size="sm" role="status">
    Saved to draft <Code>{name}</Code>. Nothing is live until you apply it.{" "}
    <Anchor component={Link} to={changesHref(name)}>Review {name} in Changes</Anchor>
  </Text>;
}

// Kept free of hooks so tests can call it directly and reach its handlers.
export function HarnessFrame({ title, question, itemNoun = "Item", metrics, fields, tab, onTabChange, periods, settings, edits, onEdit, onDiscard, onDraft, saving, saveError, draft, savedDraft }: Content & {
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
  /** Shows the draft picker above the settings; a page without one saves some other way. */
  draft?: DraftSelection;
  /** The draft the last save went into, linked to its card in Changes. */
  savedDraft?: string | null;
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
        <Stack gap="md">
          {draft && <Stack gap="xs" component="section" aria-label={`${title}: draft to save into`}>
            <DraftPicker value={draft.value} onChange={draft.onChange} source={draft.source} label="Save into draft" />
            <DraftStatus draft={draft} />
          </Stack>}
          {settings.state !== "ready"
            ? <StateView state={settings} noun={itemNoun} kind="settings" />
            : settings.rows.length === 0
              ? <EvidenceState kind="empty" title="Nothing to configure yet">
                No {noun} is installed for this harness.
              </EvidenceState>
              : <SettingsTable caption={`${title}: settings`} rows={settings.rows} fields={fields} edits={edits}
                onEdit={onEdit} onDiscard={onDiscard} onDraft={onDraft} saving={saving} saveError={saveError}
                itemHeading={itemNoun} />}
          {savedDraft && <SavedDraft name={savedDraft} />}
        </Stack>
      </Tabs.Panel>
    </Tabs>
  </Stack>;
}

export type DraftSave = (changes: DraftChange[], draft: string) => void | Promise<void>;

/**
 * Hand `changes` to `onDraft` with the selected draft. Resolves to the draft saved into, or ""
 * when a page that accepts no selection saved some other way; throws when one is required.
 */
export async function saveToDraft(changes: DraftChange[], draft: string, onDraft: DraftSave, draftOptional = false): Promise<string> {
  if (!draft && !draftOptional) throw new Error("Pick or create a draft above before saving.");
  await onDraft(changes, draft);
  return draft;
}

/**
 * A Harness page: Efficacy (the default) reads the surface's setting periods; Settings shows the
 * page's configuration, a picker for the draft to save into (`?draft=`), and hands edits to
 * `onDraft` with that draft, which records them for review in Changes.
 */
export function HarnessPage({ surface, settings, onDraft, draftOptional = false, draftLoading, draftError, ...content }: Content & {
  surface: string;
  settings: SettingsState;
  onDraft: DraftSave;
  /** The page can save with no draft selected (Rules makes one per rule then). */
  draftOptional?: boolean;
  draftLoading?: boolean;
  draftError?: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePeriods(surface);
  const [edits, setEdits] = useState<SettingEdits>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedDraft, setSavedDraft] = useState<string | null>(null);
  const draft = selectedDraft(searchParams);
  const periods: PeriodsState = query.isPending
    ? { state: "loading" }
    : query.isError
      ? { state: "error", message: query.error.message }
      : { state: "ready", rows: query.data.rows, note: query.data.note };

  async function saveDraft(changes: DraftChange[]) {
    if (!changes.length) return;
    setSaving(true);
    setSaveError(null);
    setSavedDraft(null);
    try {
      setSavedDraft(await saveToDraft(changes, draft, onDraft, draftOptional) || null);
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
    saveError={saveError}
    draft={{
      value: draft, source: surface, loading: draftLoading, error: draftError, optional: draftOptional,
      onChange: (name) => setSearchParams(withDraft(searchParams, name), { replace: true }),
    }}
    savedDraft={savedDraft} />;
}
