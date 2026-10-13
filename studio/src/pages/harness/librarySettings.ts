import { useQuery } from "@tanstack/react-query";

import type { DraftChange, SettingRow } from "../../harness/model";
import type { SettingsState } from "../../harness/HarnessPage";
import { loadLibrary } from "../../library/api";
import type { LibraryPayload } from "../../library/model";
import { saveSelectionToDraft } from "../../harness/selectedDraft";
import { selectionChanges } from "./rolesWorkflowsModel";

export const LIBRARY_QUERY_KEY = ["harness-library"] as const;

/** A Settings tab's rows, derived from the module library the Sources page also reads. */
export function useLibrarySettings(derive: (library: LibraryPayload) => SettingRow[]): SettingsState {
  const query = useQuery({ queryKey: LIBRARY_QUERY_KEY, queryFn: loadLibrary, staleTime: 30_000 });
  if (query.isPending) return { state: "loading" };
  if (query.isError) return { state: "error", message: query.error.message };
  return { state: "ready", rows: derive(query.data) };
}

/**
 * Save switch edits into the selected draft as `<kind>.<unit>` selection changes, through the
 * same selection save route the Stances page uses.
 */
export async function saveLibrarySwitches(
  kind: "roles" | "workflows", changes: DraftChange[], draft: string,
  save: (draft: string, changes: Record<string, string>) => Promise<void> = saveSelectionToDraft,
): Promise<void> {
  await save(draft, selectionChanges(kind, changes));
}
