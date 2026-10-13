// The draft a Harness page's Settings tab saves into. It lives in the URL as `?draft=NAME`, so the
// shared frame's picker and the page that reads the draft's contents agree on one selection.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { loadDraftSelection, saveDraftSelection } from "../configure/api";

export const DRAFT_PARAM = "draft";

export function selectedDraft(search: string | URLSearchParams): string {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  return params.get(DRAFT_PARAM)?.trim() ?? "";
}

/** The query string with `name` selected, keeping every other parameter; "" clears it. */
export function withDraft(search: string | URLSearchParams, name: string): URLSearchParams {
  const next = new URLSearchParams(search);
  if (name.trim()) next.set(DRAFT_PARAM, name.trim());
  else next.delete(DRAFT_PARAM);
  return next;
}

/** The draft's card in the Changes inbox. */
export function changesHref(name: string): string {
  return `/changes?${DRAFT_PARAM}=${encodeURIComponent(name)}`;
}

export function useSelectedDraft(): string {
  const [params] = useSearchParams();
  return selectedDraft(params);
}

export type LoadedDraftState<T> = {
  draft: T | null;
  loading: boolean;
  error: string;
  /** Read the selected draft again, as after a save. */
  reload: () => Promise<void>;
};

/**
 * The selected draft's contents, read through `read` whenever the selection changes. A failed
 * read clears the contents and keeps the refusal to show beside the picker.
 */
export function useLoadedDraft<T>(read: (name: string) => Promise<T>): LoadedDraftState<T> {
  const name = useSelectedDraft();
  const [draft, setDraft] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // A newly selected draft drops the last one's contents at once; a reload keeps them meanwhile.
  async function load(requested: string, live: () => boolean = () => true, fresh = false) {
    if (fresh || !requested) setDraft(null);
    if (!requested) {
      setError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const loaded = await read(requested);
      if (live()) setDraft(loaded);
    } catch (reason) {
      if (!live()) return;
      setDraft(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (live()) setLoading(false);
    }
  }

  useEffect(() => {
    let live = true;
    void load(name, () => live, true);
    return () => { live = false; };
    // `read` is a module-level function on every page; only the selection triggers a read.
  }, [name]);

  return { draft, loading, error, reload: () => load(name) };
}

export type SelectionDraftApi = {
  read: typeof loadDraftSelection;
  save: typeof saveDraftSelection;
};

const SELECTION_API: SelectionDraftApi = { read: loadDraftSelection, save: saveDraftSelection };

/**
 * Checkpoint `<kind>.<unit>` switch changes into `draft` through the selection save route, against
 * the draft's current revision. An unchanged save is success; a refusal throws its message.
 */
export async function saveSelectionToDraft(
  draft: string, changes: Record<string, string>,
  api: SelectionDraftApi = SELECTION_API, key: () => string = () => crypto.randomUUID(),
): Promise<void> {
  if (!Object.keys(changes).length) return;
  const current = await api.read(draft);
  if (current.status !== "ready") throw new Error(current.message || "The draft is unavailable.");
  const saved = await api.save(draft, current.draft.revision ?? "", key(), changes);
  if (saved.unchanged) return;
  if (!saved.saved) throw new Error(saved.error || "The draft refused the change.");
}
