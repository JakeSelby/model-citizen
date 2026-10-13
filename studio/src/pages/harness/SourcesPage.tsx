import { Anchor, Button, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { loadDraft } from "../../configure/api";
import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import { parseTab, type DraftChange, type PeriodRow } from "../../harness/model";
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

// Kept free of hooks so tests can call it directly and reach its handlers.
export function SourcesDraftPicker({ value, loaded, busy, error, onChange, onLoad }: {
  value: string;
  loaded: string | null;
  busy: boolean;
  error: string;
  onChange: (value: string) => void;
  onLoad: () => void;
}) {
  return <Paper withBorder p="md" radius="md" component="section" aria-labelledby="sources-draft-title">
    <Stack gap="xs">
      <Title order={2} size="h4" id="sources-draft-title">Switch a personal root off in a draft</Title>
      <Text size="sm" c="dimmed">
        {loaded
          ? `Editing draft ${loaded}. Switch a personal root off above, then send it to Changes.`
          : "Sources are read-only until you load an existing draft. Studio edits the draft's primitive roots, never the installed configuration."}
      </Text>
      <Group align="flex-end" gap="sm" wrap="wrap">
        <TextInput label="Draft name" value={value} error={error || undefined} w={280}
          onChange={(event) => onChange(event.currentTarget.value)} />
        <Button variant="default" disabled={!value.trim()} loading={busy} onClick={onLoad}>
          {loaded ? "Reload draft" : "Load draft"}
        </Button>
      </Group>
    </Stack>
  </Paper>;
}

export function ModuleBrowserLink() {
  return <Paper withBorder p="md" radius="md" component="section" aria-labelledby="sources-modules-title">
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
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePeriods(SOURCES_SURFACE);
  const [name, setName] = useState(searchParams.get("draft") ?? "");
  const [draft, setDraft] = useState<LoadedDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function readDraft(requested: string): Promise<LoadedDraft> {
    const result = await loadDraft(requested);
    if (result.status !== "ready") throw new Error(result.message || "The draft is unavailable.");
    return { name: result.draft.name ?? requested, revision: result.draft.revision ?? "", roots: draftRoots(result.values) };
  }

  async function openDraft(requested: string) {
    if (!requested.trim()) return;
    setBusy(true);
    setError("");
    try {
      const loaded = await readDraft(requested.trim());
      setDraft(loaded);
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("draft", loaded.name);
        return next;
      }, { replace: true });
    } catch (reason) {
      setDraft(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const initial = searchParams.get("draft");
    if (initial) void openDraft(initial);
    // Only the URL the page opened with; later loads come from the picker.
  }, []);

  async function onDraft(changes: DraftChange[]) {
    if (!draft) throw new Error("Load a draft before sending a change.");
    await saveSourcesDraft(draft.name, draft.revision, draft.roots, changes);
    setDraft(await readDraft(draft.name));
  }

  const settings = sourceSettings({
    isPending: query.isPending, isError: query.isError, error: query.error, rows: query.data?.rows,
  }, draft);

  return <Stack gap="lg">
    <HarnessPage surface={SOURCES_SURFACE} title="Sources" question={SOURCES_QUESTION} itemNoun="Source"
      metrics={SOURCES_METRICS} fields={SOURCES_FIELDS} settings={settings} onDraft={onDraft} />
    {parseTab(searchParams) === "settings" && <SourcesDraftPicker value={name} loaded={draft?.name ?? null}
      busy={busy} error={error} onChange={setName} onLoad={() => void openDraft(name)} />}
    <ModuleBrowserLink />
    <Text size="sm" c="dimmed">
      {TEAM_NOTE} Tokens per session are soft estimates (characters divided by four) of what each
      source keeps resident, credited to the first source holding a module; plugins are not in that
      record, so their sessions and tokens show as not recorded.
    </Text>
  </Stack>;
}
