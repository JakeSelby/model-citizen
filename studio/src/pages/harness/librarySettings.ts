import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import type { DraftChange, SettingRow } from "../../harness/model";
import type { SettingsState } from "../../harness/HarnessPage";
import { loadLibrary } from "../../library/api";
import type { LibraryPayload } from "../../library/model";
import { CHANGES_PATH, selectionChanges, type DraftHandoff } from "./rolesWorkflowsModel";

export const LIBRARY_QUERY_KEY = ["harness-library"] as const;

/** A Settings tab's rows, derived from the module library the Sources page also reads. */
export function useLibrarySettings(derive: (library: LibraryPayload) => SettingRow[]): SettingsState {
  const query = useQuery({ queryKey: LIBRARY_QUERY_KEY, queryFn: loadLibrary, staleTime: 30_000 });
  if (query.isPending) return { state: "loading" };
  if (query.isError) return { state: "error", message: query.error.message };
  return { state: "ready", rows: derive(query.data) };
}

/**
 * Hands switch edits to Changes, where a named draft records them; nothing is written here. The
 * selection travels in the navigation state as `<kind>.<unit>` changes.
 */
export function draftHandoff(surface: DraftHandoff["surface"], changes: DraftChange[],
  navigate: (to: string, options: { state: { harnessDraft: DraftHandoff } }) => unknown): void {
  const handoff: DraftHandoff = { surface, selection: selectionChanges(surface, changes) };
  void navigate(CHANGES_PATH, { state: { harnessDraft: handoff } });
}

export function useDraftHandoff(surface: DraftHandoff["surface"]): (changes: DraftChange[]) => void {
  const navigate = useNavigate();
  return (changes) => draftHandoff(surface, changes, navigate);
}
