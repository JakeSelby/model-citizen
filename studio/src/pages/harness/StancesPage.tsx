import { Button, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { loadDraftSelection, type SelectionControls } from "../../configure/api";
import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import { parseTab, type DraftChange, type PeriodRow } from "../../harness/model";
import {
  saveStanceDraft, settingRowsFromControls, settingRowsFromPeriods, STANCE_FIELDS, STANCE_METRICS,
  STANCES_SURFACE,
} from "./stancesModel";

export const STANCES_QUESTION = "One variant per stance decides how the agent works for you. Efficacy is always shown against the variant that was in effect.";

type LoadedDraft = { name: string; revision: string; controls: SelectionControls };

/** The Settings tab's draft state: none loaded shows variants read-only; a loaded one edits them. */
export function stanceSettings(
  periods: { isPending: boolean; isError: boolean; error?: Error | null; rows?: PeriodRow[] },
  draft: LoadedDraft | null,
): SettingsState {
  if (draft) return { state: "ready", rows: settingRowsFromControls(draft.controls, periods.rows ?? []) };
  if (periods.isPending) return { state: "loading" };
  if (periods.isError) return { state: "error", message: periods.error?.message ?? "The stance periods could not be read." };
  return { state: "ready", rows: settingRowsFromPeriods(periods.rows ?? []) };
}

// Kept free of hooks so tests can call it directly and reach its handlers.
export function DraftPicker({ value, loaded, busy, error, onChange, onLoad }: {
  value: string;
  loaded: string | null;
  busy: boolean;
  error: string;
  onChange: (value: string) => void;
  onLoad: () => void;
}) {
  return <Paper withBorder p="md" radius="md" component="section" aria-labelledby="stances-draft-title">
    <Stack gap="xs">
      <Title order={2} size="h4" id="stances-draft-title">Change a variant in a draft</Title>
      <Text size="sm" c="dimmed">
        {loaded
          ? `Editing draft ${loaded}. Pick a variant above, then send it to Changes.`
          : "Variants are read-only until you load an existing draft. Studio edits the draft, never the installed configuration."}
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

/** Stances: each stance's variant periods on Efficacy; its selected variant on Settings. */
export function StancesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = usePeriods(STANCES_SURFACE);
  const [name, setName] = useState(searchParams.get("draft") ?? "");
  const [draft, setDraft] = useState<LoadedDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function readDraft(requested: string): Promise<LoadedDraft> {
    const result = await loadDraftSelection(requested);
    if (result.status !== "ready") throw new Error(result.message || "The draft is unavailable.");
    return { name: result.draft.name ?? requested, revision: result.draft.revision ?? "", controls: result.controls };
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
    await saveStanceDraft(draft.name, draft.revision, changes);
    setDraft(await readDraft(draft.name));
  }

  const settings = stanceSettings({
    isPending: query.isPending, isError: query.isError, error: query.error, rows: query.data?.rows,
  }, draft);

  return <Stack gap="lg">
    <HarnessPage surface={STANCES_SURFACE} title="Stances" question={STANCES_QUESTION} itemNoun="Stance"
      metrics={STANCE_METRICS} fields={STANCE_FIELDS} settings={settings} onDraft={onDraft} />
    {parseTab(searchParams) === "settings" && <DraftPicker value={name} loaded={draft?.name ?? null}
      busy={busy} error={error} onChange={setName} onLoad={() => void openDraft(name)} />}
  </Stack>;
}
