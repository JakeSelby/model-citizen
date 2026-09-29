import { Compartment, EditorState } from "@codemirror/state";
import { Decoration, EditorView, keymap, type DecorationSet } from "@codemirror/view";
import {
  Alert, Badge, Button, Code, Group, Paper, Select, SimpleGrid, Stack, Text, Title,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";

import { CodeView, CommandChip } from "../components/StudioKit";
import {
  loadDraftModule, previewDraftModule, saveDraftModule,
  type EditableModule, type ModulePreview,
} from "./api";
import "./module-editing.css";

const PREVIEW_DELAY_MS = 350;

type Props = {
  draft: string;
  revision: string;
  onRevision: (revision: string) => void;
};

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value}`;
}

export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function diagnosticDecorations(state: EditorState, lines: number[]): DecorationSet {
  const marks = lines.filter((line) => line > 0 && line <= state.doc.lines).map((line) =>
    Decoration.line({ class: "cm-module-diagnostic-line" }).range(state.doc.line(line).from));
  return Decoration.set(marks, true);
}

export function ModuleEditor({ draft, revision, onRevision }: Props) {
  const [modules, setModules] = useState<EditableModule[]>([]);
  const [moduleKey, setModuleKey] = useState("");
  const [module, setModule] = useState<EditableModule | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [sourceDigest, setSourceDigest] = useState("");
  const [loadedRevision, setLoadedRevision] = useState("");
  const [preview, setPreview] = useState<ModulePreview | null>(null);
  const [message, setMessage] = useState("Loading editable modules…");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [reloadAvailable, setReloadAvailable] = useState(false);
  const [retryAvailable, setRetryAvailable] = useState(false);
  const [loadNonce, setLoadNonce] = useState(0);
  const [previewRetry, setPreviewRetry] = useState(0);
  const mount = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const listGeneration = useRef(0);
  const moduleGeneration = useRef(0);
  const editorGeneration = useRef(0);
  const saveGeneration = useRef(0);
  const previewGeneration = useRef(0);
  const previewedContent = useRef("");
  const previewRetryKey = useRef("");
  const previewRetryCount = useRef(0);
  const inventoryDraft = useRef("");
  const currentRevision = useRef(revision);
  const currentModuleKey = useRef(moduleKey);
  const saveRequest = useRef<{ signature: string; key: string } | null>(null);
  // One save at a time: typing or switching modules never releases it before the request settles.
  const saveInFlight = useRef(0);
  const errorSummary = useRef<HTMLDivElement>(null);
  const saveAction = useRef<() => void>(() => {});
  const diagnostics = useRef(new Compartment());
  currentRevision.current = revision;
  currentModuleKey.current = moduleKey;

  useEffect(() => {
    const current = ++listGeneration.current;
    const draftChanged = inventoryDraft.current !== draft;
    inventoryDraft.current = draft;
    if (draftChanged) {
      editorGeneration.current += 1;
      setModules([]);
      setModuleKey("");
      setModule(null);
      setLoadedRevision("");
      setPreview(null);
      setError("");
      setMessage("Loading editable modules…");
    }
    void loadDraftModule(draft).then((result) => {
      if (listGeneration.current !== current) return;
      if (result.status !== "ready") throw new Error(result.message);
      setModules(result.modules);
      const selectedKey = currentModuleKey.current;
      if (selectedKey && !result.modules.some((item) => item.key === selectedKey)) {
        setReloadAvailable(true);
        setError("draft module changed; reload before saving");
        setMessage("The module inventory changed. Your buffer is retained; reload is explicit.");
        return;
      }
      // A revision-only refresh keeps the save or load outcome the user is reading.
      if (!draftChanged) return;
      setMessage(result.modules.length
        ? "Choose a draft-owned rule, skill, or stance. Nothing applied."
        : "This draft has no editable developer-root modules. Core modules stay read-only.");
    }).catch((reason: unknown) => {
      if (listGeneration.current !== current) return;
      setError(reason instanceof Error ? reason.message : "Editable modules are unavailable.");
      setMessage("Nothing was changed.");
    });
    // The editor generation advances only on a draft change (above): a revision-only refresh
    // must not orphan an in-flight save or keyed load.
    return () => {
      listGeneration.current += 1;
    };
  }, [draft, loadNonce, revision]);

  useEffect(() => {
    if (!moduleKey) {
      moduleGeneration.current += 1;
      editorGeneration.current += 1;
      saveGeneration.current += 1;
      setModule(null);
      setLoadedRevision("");
      setPreview(null);
      return;
    }
    const current = ++moduleGeneration.current;
    editorGeneration.current += 1;
    setModule(null);
    setLoadedRevision("");
    setPreview(null);
    setError("");
    setReloadAvailable(false);
    setRetryAvailable(false);
    saveRequest.current = null;
    setMessage("Loading draft module…");
    void loadDraftModule(draft, moduleKey).then((result) => {
      if (moduleGeneration.current !== current || moduleKey !== result.module?.key) return;
      if (result.status !== "ready" || !result.module) throw new Error(result.message);
      setModule(result.module);
      setContent(result.content);
      setSavedContent(result.content);
      setSourceDigest(result.source_digest);
      const canonicalRevision = result.draft.revision ?? "";
      setLoadedRevision(canonicalRevision);
      if (canonicalRevision) onRevision(canonicalRevision);
      setMessage("Module editor is ready. Nothing applied.");
    }).catch((reason: unknown) => {
      if (moduleGeneration.current !== current) return;
      setError(reason instanceof Error ? reason.message : "Draft module is unavailable.");
      setMessage("Nothing was changed.");
    });
  }, [draft, moduleKey, loadNonce, onRevision]);

  useEffect(() => {
    if (!module || !mount.current) return;
    const editor = new EditorView({
      parent: mount.current,
      state: EditorState.create({
        doc: content,
        extensions: [
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({
            "aria-label": `Edit ${module.kind.slice(0, -1)} ${module.name}`,
            "aria-describedby": "module-editor-help",
            spellcheck: "false",
          }),
          keymap.of([
            { key: "Mod-a", preventDefault: true, run: (target) => {
              target.dispatch({ selection: { anchor: 0, head: target.state.doc.length } });
              return true;
            } },
            { key: "Mod-s", preventDefault: true, run: () => {
              saveAction.current();
              return true;
            } },
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) setContent(update.state.doc.toString());
          }),
          diagnostics.current.of(EditorView.decorations.of(Decoration.none)),
        ],
      }),
    });
    view.current = editor;
    return () => { editor.destroy(); view.current = null; };
  // The view owns content changes after one canonical module load.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module]);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const lines = (preview?.diagnostics ?? []).flatMap((item) => item.line === null ? [] : [item.line]);
    editor.dispatch({ effects: diagnostics.current.reconfigure(
      EditorView.decorations.of((candidate) => diagnosticDecorations(candidate.state, lines)),
    ) });
  }, [preview]);

  useEffect(() => {
    if (error || preview && !preview.valid) errorSummary.current?.focus();
  }, [error, preview]);

  useEffect(() => {
    if (!module || content === savedContent) {
      previewGeneration.current += 1;
      setPreview(null);
      setError("");
      setReloadAvailable(false);
      setRetryAvailable(false);
      saveRequest.current = null;
      previewedContent.current = content;
      return;
    }
    const current = ++previewGeneration.current;
    const candidate = content;
    const retryKey = JSON.stringify([draft, module.key, candidate]);
    if (previewRetryKey.current !== retryKey) {
      previewRetryKey.current = retryKey;
      previewRetryCount.current = 0;
    }
    const retryTransient = () => {
      if (previewGeneration.current !== current || previewRetryCount.current >= 2) return;
      previewRetryCount.current += 1;
      window.setTimeout(() => {
        if (previewGeneration.current === current) setPreviewRetry((value) => value + 1);
      }, PREVIEW_DELAY_MS);
    };
    const timer = window.setTimeout(() => {
      setMessage("Checking lint, budgets, and projections…");
      setError("");
      void previewDraftModule(draft, module.key, candidate).then((result) => {
        if (previewGeneration.current !== current) return;
        setPreview(result);
        previewedContent.current = candidate;
        const stale = result.error_code === "stale-revision" || result.error_code === "stale-source";
        const transient = ["busy", "preview-timeout", "preview-unavailable"]
          .includes(result.error_code);
        setReloadAvailable(stale);
        setRetryAvailable(false);
        setError(result.valid ? "" : result.error);
        setMessage(result.valid
          ? "Preview is clean. Save explicitly to create one checkpoint. Nothing applied."
          : stale
            ? "The draft changed externally. Your buffer is retained; reload is explicit."
            : result.error_code === "lint-refused"
              ? "Fix the findings before saving. Nothing was saved."
              : "Preview was refused. Your buffer is retained; nothing was saved.");
        if (!result.valid) window.setTimeout(() => errorSummary.current?.focus(), 0);
        if (transient) retryTransient();
      }).catch((reason: unknown) => {
        if (previewGeneration.current !== current) return;
        setError(reason instanceof Error ? reason.message : "Module preview failed.");
        setMessage("Nothing was saved.");
        window.setTimeout(() => errorSummary.current?.focus(), 0);
        retryTransient();
      });
    }, PREVIEW_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      if (previewGeneration.current === current) previewGeneration.current += 1;
    };
  }, [content, draft, module, previewRetry, savedContent]);

  async function save() {
    if (!module || !loadedRevision || !preview?.valid || previewedContent.current !== content
        || saving || saveInFlight.current) return;
    const selectedModule = module;
    const candidate = content;
    const signature = JSON.stringify([draft, selectedModule.key, loadedRevision, sourceDigest, candidate]);
    const durableRetry = retryAvailable && saveRequest.current?.signature === signature;
    if (revision !== loadedRevision && !durableRetry) {
      setReloadAvailable(true);
      setRetryAvailable(false);
      setError("draft revision changed; reload before saving");
      setMessage("The draft changed externally. Your buffer is retained; reload is explicit.");
      window.setTimeout(() => errorSummary.current?.focus(), 0);
      return;
    }
    const current = editorGeneration.current;
    const currentSave = ++saveGeneration.current;
    saveInFlight.current = currentSave;
    if (saveRequest.current?.signature !== signature) {
      saveRequest.current = { signature, key: crypto.randomUUID() };
    }
    setSaving(true);
    setError("");
    setMessage("Saving the checked draft checkpoint…");
    try {
      const result = await saveDraftModule(
        draft, selectedModule.key, loadedRevision, sourceDigest, saveRequest.current.key, candidate,
      );
      if (editorGeneration.current !== current) return;
      const liveContent = view.current?.state.doc.toString() ?? candidate;
      const newerBuffer = liveContent !== candidate;
      if (!result.saved) {
        if (saveGeneration.current !== currentSave || newerBuffer) return;
        const stale = result.error_code === "stale-revision" || result.error_code === "stale-source";
        if (result.error_code === "lint-refused") setPreview(result);
        setReloadAvailable(stale);
        setRetryAvailable(["busy", "check-timeout", "preview-timeout", "preview-unavailable"]
          .includes(result.error_code));
        if (result.error_code === "idempotency-conflict") saveRequest.current = null;
        setError(result.error || "The module was not saved.");
        setMessage(stale
          ? "The draft changed externally. Your buffer is retained; reload is explicit."
          : "Nothing was saved. Your buffer is retained.");
        window.setTimeout(() => errorSummary.current?.focus(), 0);
        return;
      }
      const resultRevision = result.result?.revision ?? loadedRevision;
      if (currentRevision.current !== loadedRevision
          && currentRevision.current !== resultRevision) {
        const canonical = await loadDraftModule(draft, selectedModule.key);
        if (editorGeneration.current !== current) return;
        const reconciledContent = view.current?.state.doc.toString() ?? candidate;
        const reconciledNewerBuffer = reconciledContent !== candidate;
        if (canonical.status !== "ready" || !canonical.module) {
          setReloadAvailable(true);
          setError(canonical.message || "Canonical module reconciliation failed.");
          setMessage("The draft advanced after this save. Your buffer is retained; reload is explicit.");
          return;
        }
        const canonicalRevision = canonical.draft.revision ?? currentRevision.current;
        if (canonicalRevision !== currentRevision.current) {
          setReloadAvailable(true);
          setError("draft revision changed again; reload before saving");
          setMessage("The draft advanced during reconciliation. Your buffer is retained; reload is explicit.");
          return;
        }
        const editor = view.current;
        if (editor && !reconciledNewerBuffer) {
          editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: canonical.content } });
        }
        setModule(canonical.module);
        if (!reconciledNewerBuffer) setContent(canonical.content);
        setSavedContent(canonical.content);
        setSourceDigest(canonical.source_digest);
        setLoadedRevision(canonicalRevision);
        setPreview(null);
        setReloadAvailable(false);
        setRetryAvailable(false);
        saveRequest.current = null;
        setMessage(reconciledNewerBuffer
          ? "Draft checkpoint saved; newer unsaved text is retained. Nothing applied."
          : "Saved response reconciled with the newer canonical checkpoint. Nothing applied.");
        onRevision(canonicalRevision);
        return;
      }
      saveRequest.current = null;
      setSavedContent(candidate);
      setSourceDigest(result.content_digest);
      const nextRevision = resultRevision;
      setLoadedRevision(nextRevision);
      setPreview(newerBuffer ? null : result);
      setReloadAvailable(false);
      setRetryAvailable(false);
      setMessage(newerBuffer
        ? "Draft checkpoint saved; newer unsaved text is retained. Nothing applied."
        : "Draft checkpoint saved. Nothing applied to the installed harness.");
      onRevision(nextRevision);
      view.current?.focus();
    } catch (reason) {
      if (editorGeneration.current !== current || saveGeneration.current !== currentSave) return;
      setRetryAvailable((view.current?.state.doc.toString() ?? candidate) === candidate);
      setError(reason instanceof Error ? reason.message : "Module save failed.");
      setMessage("Nothing was saved. Your buffer is retained.");
      window.setTimeout(() => errorSummary.current?.focus(), 0);
    } finally {
      // The request owns the busy state until it settles, whatever the editor did meanwhile.
      if (saveInFlight.current === currentSave) {
        saveInFlight.current = 0;
        setSaving(false);
      }
    }
  }
  saveAction.current = () => { void save(); };

  async function reload() {
    if (!module) return;
    const selected = module.key;
    editorGeneration.current += 1;
    saveGeneration.current += 1;
    previewGeneration.current += 1;
    const current = ++moduleGeneration.current;
    setError("");
    setReloadAvailable(false);
    setRetryAvailable(false);
    saveRequest.current = null;
    setMessage("Reloading canonical draft module…");
    try {
      const result = await loadDraftModule(draft, selected);
      if (moduleGeneration.current !== current || result.module?.key !== selected) return;
      if (result.status !== "ready" || !result.module) throw new Error(result.message);
      const editor = view.current;
      if (editor) {
        editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: result.content } });
      }
      setContent(result.content);
      setSavedContent(result.content);
      setSourceDigest(result.source_digest);
      const canonicalRevision = result.draft.revision ?? currentRevision.current;
      setLoadedRevision(canonicalRevision);
      setPreview(null);
      setMessage("Canonical draft module reloaded. Nothing applied.");
      onRevision(canonicalRevision);
    } catch (reason) {
      if (moduleGeneration.current !== current) return;
      setReloadAvailable(true);
      setError(reason instanceof Error ? reason.message : "Draft module reload failed.");
      setMessage("Reload failed. Your buffer is retained.");
    }
  }

  function focusLine(line: number) {
    const editor = view.current;
    if (!editor || line > editor.state.doc.lines) return;
    const position = editor.state.doc.line(line).from;
    editor.dispatch({ selection: { anchor: position }, scrollIntoView: true });
    editor.focus();
  }

  const dirty = Boolean(module && content !== savedContent);
  const saveReady = dirty && preview?.valid && previewedContent.current === content;
  const options = useMemo(() => modules.map((item) => ({
    value: item.key, label: `${item.kind.slice(0, -1)} · ${item.name} · ${item.root.label}`,
  })), [modules]);

  return (
    <Paper aria-labelledby="module-editor-title" className="settings-section module-editor" p="xl" withBorder>
      <Group justify="space-between" wrap="wrap">
        <div>
          <Title id="module-editor-title" order={2}>Edit module text</Title>
          <Text id="module-editor-help" c="dimmed" mt="xs" size="sm">
            Live checks use the draft candidate. Save or Ctrl/Cmd-S creates a checkpoint. Nothing applied.
          </Text>
        </div>
        <Badge color={error || preview && !preview.valid ? "red" : dirty ? "yellow" : "teal"}>
          {error || preview && !preview.valid ? "refused" : dirty ? "unsaved" : "checkpointed"}
        </Badge>
      </Group>
      <Text aria-live="polite" c={error ? "red" : "dimmed"} mt="md" size="sm">{message}</Text>
      <Select
        data={options}
        disabled={!modules.length}
        label="Draft-owned module"
        mt="lg"
        placeholder="Choose a rule, skill, or stance"
        searchable
        value={moduleKey || null}
        onChange={(value) => setModuleKey(value ?? "")}
      />

      {module && <Stack gap="lg" mt="lg">
        <div className="module-editor-surface" ref={mount} />
        {(error || preview && !preview.valid) && (
          <div ref={errorSummary} tabIndex={-1}>
          <Alert color="red" title="Nothing was saved">
            {error && <Text size="sm">{error}</Text>}
            {preview?.diagnostics.length ? <ul className="message-list">{preview.diagnostics.map((item, index) => (
              <li key={`${item.line}:${item.message}:${index}`}>
                {item.line === null ? item.message : <Button size="compact-xs" variant="subtle" onClick={() => focusLine(item.line!)}>
                  Line {item.line}: {item.message}
                </Button>}
              </li>
            ))}</ul> : null}
            <Group mt="sm">
              {retryAvailable && <Button size="xs" variant="light" onClick={() => void save()}>Retry save</Button>}
              {reloadAvailable && <Button size="xs" variant="light" onClick={() => void reload()}>Reload canonical draft</Button>}
            </Group>
          </Alert>
          </div>
        )}

        {preview && <>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {preview.budgets.map((budget) => <Paper key={budget.runtime} p="md" withBorder>
              <Group justify="space-between"><Text fw={650}>{budget.label} context</Text>
                <Badge color={budget.over_cap ? "red" : "teal"}>{budget.over_cap ? "over cap" : "within cap"}</Badge></Group>
              <Text size="sm">{budget.tokens} tokens ({signed(budget.token_delta)}) / {budget.token_cap}</Text>
              <Text c="dimmed" size="sm">{budget.lines} lines ({signed(budget.line_delta)}) / {budget.line_cap}</Text>
            </Paper>)}
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {preview.projections.map((projection) => <div key={projection.runtime}>
              <Text fw={650} size="sm">{projection.runtime} projection</Text>
              <Text c="dimmed" size="xs">{projection.path}{projection.truncated ? " · preview truncated" : ""}</Text>
              <CodeView label={`${projection.runtime} projected text`}>{projection.text}</CodeView>
            </div>)}
          </SimpleGrid>
        </>}

        <Paper className="module-save-bar" p="md" withBorder>
          <Group justify="space-between" wrap="wrap">
            <div><Text fw={650}>{dirty ? "Unsaved module text" : "Checkpointed module text"}</Text>
              <Text c="dimmed" size="sm">Draft {draft} · checkpoint {loadedRevision.slice(0, 12)} · Nothing applied</Text></div>
            <Group>
              <Button disabled={!saveReady || saving} loading={saving} onClick={() => void save()}>Save checkpoint</Button>
            </Group>
          </Group>
        </Paper>
        <CommandChip
          label="Module save"
          command={[
            "citizen", "draft", "module", "save", draft, module.key,
            "--base-revision", loadedRevision, "--source-digest", sourceDigest,
            "--idempotency-key", "KEY", "--content", "source.md", "--json",
          ].map(shellQuote).join(" ")}
        />
      </Stack>}
    </Paper>
  );
}
