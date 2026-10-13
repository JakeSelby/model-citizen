import { Anchor, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

import { loadDraft } from "../../configure/api";
import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import type { DraftChange, PeriodRow } from "../../harness/model";
import { useLoadedDraft } from "../../harness/selectedDraft";
import {
  draftRoots, saveSourcesDraft, settingRowsFromDraft, settingRowsFromPeriods, SOURCES_FIELDS,
  SOURCES_METRICS, SOURCES_QUESTION, SOURCES_SURFACE, TEAM_NOTE,
} from "./sourcesModel";

export const MODULES_PATH = "/harness/sources/modules";

type LoadedDraft = { name: string; revision: string; roots: string[] };

/** The Settings tab's state: read-only from the periods until a draft is loaded. */
export function sourceSettings(
  periods: { isPending: boolean; isError: boolean; error?: Error | null; rows?: PeriodRow[] },
  draft: LoadedDraft | null,
): SettingsState {
  if (periods.isPending) return { state: "loading" };
  if (periods.isError) return { state: "error", message: periods.error?.message ?? "The sources could not be read." };
  const rows = periods.rows ?? [];
  return { state: "ready", rows: draft ? settingRowsFromDraft(rows, draft.roots) : settingRowsFromPeriods(rows) };
}

/** The draft's primitive roots, which the Settings tab switches. */
export async function readSourcesDraft(requested: string): Promise<LoadedDraft> {
  const result = await loadDraft(requested);
  if (result.status !== "ready") throw new Error(result.message || "The draft is unavailable.");
  return { name: result.draft.name ?? requested, revision: result.draft.revision ?? "", roots: draftRoots(result.values) };
}

export function ModuleBrowserLink() {
  return <Paper p="md" radius="md" component="section" aria-labelledby="sources-modules-title">
    <Group justify="space-between" wrap="wrap" gap="sm">
      <div>
        <Title order={2} size="h4" id="sources-modules-title">Modules in each source</Title>
        <Text size="sm" c="dimmed">Every module a source holds, with its text, projections and context cost.</Text>
      </div>
      <Anchor component={Link} to={MODULES_PATH}>Open the module browser</Anchor>
    </Group>
  </Paper>;
}

/** Sources: each source's load per harness-version period on Efficacy; its root on Settings. */
export function SourcesPage() {
  const query = usePeriods(SOURCES_SURFACE);
  const { draft, loading, error, reload } = useLoadedDraft(readSourcesDraft);

  async function onDraft(changes: DraftChange[], name: string) {
    if (!draft || draft.name !== name) throw new Error("Wait for the draft to load before saving.");
    await saveSourcesDraft(draft.name, draft.revision, draft.roots, changes);
    await reload();
  }

  const settings = sourceSettings({
    isPending: query.isPending, isError: query.isError, error: query.error, rows: query.data?.rows,
  }, draft);

  return <Stack gap="lg">
    <HarnessPage surface={SOURCES_SURFACE} title="Sources" question={SOURCES_QUESTION} itemNoun="Source"
      metrics={SOURCES_METRICS} fields={SOURCES_FIELDS} settings={settings} onDraft={onDraft}
      draftLoading={loading} draftError={error} />
    <ModuleBrowserLink />
    <Text size="sm" c="dimmed">
      {TEAM_NOTE} Tokens per session are soft estimates (characters divided by four) of what each
      source keeps resident, credited to the first source holding a module; plugins are not in that
      record, so their sessions and tokens show as not recorded.
    </Text>
  </Stack>;
}
