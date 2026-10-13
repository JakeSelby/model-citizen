import { loadDraftSelection, type SelectionControls } from "../../configure/api";
import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import type { DraftChange, PeriodRow } from "../../harness/model";
import { useLoadedDraft } from "../../harness/selectedDraft";
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

/** The draft's selection controls, which the Settings tab edits. */
export async function readStanceDraft(requested: string): Promise<LoadedDraft> {
  const result = await loadDraftSelection(requested);
  if (result.status !== "ready") throw new Error(result.message || "The draft is unavailable.");
  return { name: result.draft.name ?? requested, revision: result.draft.revision ?? "", controls: result.controls };
}

/** Stances: each stance's variant periods on Efficacy; its selected variant on Settings. */
export function StancesPage() {
  const query = usePeriods(STANCES_SURFACE);
  const { draft, loading, error, reload } = useLoadedDraft(readStanceDraft);

  async function onDraft(changes: DraftChange[], name: string) {
    if (!draft || draft.name !== name) throw new Error("Wait for the draft to load before saving.");
    await saveStanceDraft(draft.name, draft.revision, changes);
    await reload();
  }

  const settings = stanceSettings({
    isPending: query.isPending, isError: query.isError, error: query.error, rows: query.data?.rows,
  }, draft);

  return <HarnessPage surface={STANCES_SURFACE} title="Stances" question={STANCES_QUESTION} itemNoun="Stance"
    metrics={STANCE_METRICS} fields={STANCE_FIELDS} settings={settings} onDraft={onDraft}
    draftLoading={loading} draftError={error} />;
}
