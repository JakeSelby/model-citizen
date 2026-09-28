import {
  Alert, Badge, Button, Checkbox, Code, Group, Paper, Select, SimpleGrid, Stack, Switch, Text, Title,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";

import { CommandChip } from "../components/StudioKit";
import {
  loadDraftSelection, previewDraftSelection, saveDraftSelection,
  type DraftSelectionPreview, type SelectionControls, type SelectionSnapshot,
} from "./api";
import { remainingChanges } from "./model";
import {
  changedStances, changedSwitchUnits, cliCommands, controlValues, needsCoreAcknowledgement,
  selectionRequestSignature, selectionSaveFailureAction, selectionSwitchKinds,
  switchSelectionLines, SELECTION_AUTOSAVE_DELAY_MS,
} from "./selectionModel";
import "./selection-editing.css";

type Props = {
  draft: string;
  revision: string;
  onRevision: (revision: string) => void;
};

function SnapshotDiff({ preview }: { preview: DraftSelectionPreview }) {
  if (!preview.valid || !("selection" in preview.before) || !("selection" in preview.after)) return null;
  const before = preview.before as SelectionSnapshot;
  const after = preview.after as SelectionSnapshot;
  const stances = changedStances(before, after);
  const switchKinds = selectionSwitchKinds(before, after);
  const changedUnits = changedSwitchUnits(before, after);
  return (
    <Paper aria-label="Selection projection preview" className="selection-preview" p="lg" withBorder>
      <Group justify="space-between" wrap="wrap">
        <div>
          <Title order={3}>Projected selection preview</Title>
          <Text c="dimmed" size="sm">Draft evidence only. Nothing applied.</Text>
        </div>
        <Badge color="blue" variant="light">
          {before.budget.selected_lines} → {after.budget.selected_lines} always-loaded lines
        </Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg" spacing="md">
        <div>
          <Text fw={650} size="sm">Before</Text>
          <Code block>mode: {String(before.selection.mode ?? "none")}{"\n"}budget: {before.budget.selected_lines} / {before.budget.line_cap} lines</Code>
        </div>
        <div>
          <Text fw={650} size="sm">After</Text>
          <Code block>mode: {String(after.selection.mode ?? "none")}{"\n"}budget: {after.budget.selected_lines} / {after.budget.line_cap} lines</Code>
        </div>
      </SimpleGrid>
      <Text fw={650} mt="lg" size="sm">Changed projected units</Text>
      <Text c="dimmed" size="sm">{changedUnits.length ? changedUnits.join(", ") : "No switch units changed."}</Text>
      {switchKinds.map((kind) => (
        <SimpleGrid aria-label={`${kind} switch selection preview`} cols={{ base: 1, md: 2 }} key={kind} mt="md" spacing="md">
          <div><Text fw={650} size="sm">{kind} before</Text><Code block>{switchSelectionLines(before, kind)}</Code></div>
          <div><Text fw={650} size="sm">{kind} after</Text><Code block>{switchSelectionLines(after, kind)}</Code></div>
        </SimpleGrid>
      ))}
      {stances.map((name) => (
        <SimpleGrid aria-label={`${name} operative text preview`} cols={{ base: 1, md: 2 }} key={name} mt="md" spacing="md">
          <div><Text fw={650} size="sm">{name} before</Text><Code block className="selection-preview-code">{before.stance_text[name] ?? "No operative text"}</Code></div>
          <div><Text fw={650} size="sm">{name} after</Text><Code block className="selection-preview-code">{after.stance_text[name] ?? "No operative text"}</Code></div>
        </SimpleGrid>
      ))}
    </Paper>
  );
}

export function DraftSelectionEditor({ draft, revision, onRevision }: Props) {
  const [controls, setControls] = useState<SelectionControls | null>(null);
  const [current, setCurrent] = useState<SelectionSnapshot | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [changes, setChanges] = useState<Record<string, unknown>>({});
  const [preview, setPreview] = useState<DraftSelectionPreview | null>(null);
  const [message, setMessage] = useState("Loading draft selection…");
  const [error, setError] = useState("");
  const [lastCommands, setLastCommands] = useState<string[]>([]);
  const [retryAvailable, setRetryAvailable] = useState(false);
  const [reloadAvailable, setReloadAvailable] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const [reloadNonce, setReloadNonce] = useState(0);
  const generation = useRef(0);
  const activeDraft = useRef(draft);
  const saving = useRef<number | null>(null);
  const saveRequest = useRef<{ signature: string; idempotencyKey: string } | null>(null);
  const baselineValues = useRef<Record<string, unknown>>({});
  const changesRef = useRef<Record<string, unknown>>({});
  const recoverRevision = useRef(false);
  const onRevisionRef = useRef(onRevision);
  const errorSummary = useRef<HTMLDivElement>(null);
  onRevisionRef.current = onRevision;

  if (activeDraft.current !== draft) {
    activeDraft.current = draft;
    generation.current += 1;
    saveRequest.current = null;
    recoverRevision.current = false;
  }

  useEffect(() => () => {
    generation.current += 1;
    saving.current = null;
  }, []);

  useEffect(() => {
    const loadGeneration = generation.current;
    let active = true;
    setControls(null);
    setCurrent(null);
    setPreview(null);
    setLastCommands([]);
    changesRef.current = {};
    setChanges({});
    setError("");
    setRetryAvailable(false);
    setReloadAvailable(false);
    saveRequest.current = null;
    setMessage("Loading draft selection…");
    void loadDraftSelection(draft).then((result) => {
      if (!active || generation.current !== loadGeneration || activeDraft.current !== draft) return;
      if (result.status !== "ready") throw new Error(result.message);
      const loadedValues = controlValues(result.controls, result.current);
      setControls(result.controls);
      setCurrent(result.current);
      baselineValues.current = loadedValues;
      setValues(loadedValues);
      changesRef.current = {};
      setChanges({});
      setError("");
      setRetryAvailable(false);
      setReloadAvailable(false);
      setMessage("Selection controls are ready.");
      if (recoverRevision.current) {
        recoverRevision.current = false;
        if (result.draft.revision) onRevisionRef.current(result.draft.revision);
      }
    }).catch((reason: unknown) => {
      if (!active || generation.current !== loadGeneration || activeDraft.current !== draft) return;
      setError(reason instanceof Error ? reason.message : "Draft selection is unavailable.");
      setReloadAvailable(recoverRevision.current);
      setMessage("Nothing was saved.");
    });
    return () => { active = false; };
  }, [draft, reloadNonce]);

  useEffect(() => {
    const operationGeneration = generation.current;
    if (!Object.keys(changes).length || saving.current === operationGeneration) return;
    const snapshot = changes;
    let cancelled = false;
    const stale = () => cancelled || generation.current !== operationGeneration
      || activeDraft.current !== draft;
    const acceptCanonical = (result: DraftSelectionPreview) => {
      if (!("modes" in result.controls) || !("selection" in result.after)) return;
      const canonicalControls = result.controls as SelectionControls;
      const canonicalCurrent = result.after as SelectionSnapshot;
      const canonicalValues = controlValues(canonicalControls, canonicalCurrent);
      const remaining = remainingChanges(changesRef.current, snapshot);
      baselineValues.current = canonicalValues;
      changesRef.current = remaining;
      setControls(canonicalControls);
      setCurrent(canonicalCurrent);
      setValues({ ...canonicalValues, ...remaining });
      setChanges(remaining);
    };
    const timer = window.setTimeout(async () => {
      setMessage("Checking selection with citizen config set…");
      setError("");
      setLastCommands(cliCommands(snapshot));
      const signature = selectionRequestSignature(draft, revision, snapshot);
      const durableRetry = saveRequest.current?.signature === signature;
      try {
        if (!durableRetry) {
          const planned = await previewDraftSelection(draft, snapshot);
          if (stale()) return;
          setPreview(planned);
          setLastCommands(cliCommands(snapshot, planned.applied));
          if (!planned.valid) {
            setRetryAvailable(false);
            setReloadAvailable(false);
            setError(planned.error);
            setMessage("Nothing was saved. Correct the refused selection and try again.");
            window.setTimeout(() => errorSummary.current?.focus(), 0);
            return;
          }
          if (planned.unchanged) {
            saveRequest.current = null;
            acceptCanonical(planned);
            setRetryAvailable(false);
            setReloadAvailable(false);
            setMessage("No effective selection change to checkpoint. The draft revision is unchanged.");
            return;
          }
        }
        saving.current = operationGeneration;
        setMessage(durableRetry
          ? "Recovering the checked selection checkpoint…"
          : "Saving checked selection checkpoint…");
        if (saveRequest.current?.signature !== signature) {
          saveRequest.current = { signature, idempotencyKey: crypto.randomUUID() };
        }
        const saved = await saveDraftSelection(
          draft, revision, saveRequest.current.idempotencyKey, snapshot,
        );
        if (generation.current !== operationGeneration || activeDraft.current !== draft) return;
        if (cancelled && saved.saved) {
          saveRequest.current = null;
          acceptCanonical(saved);
          onRevision(saved.result?.replayed
            ? saved.base_revision : saved.result?.revision ?? revision);
          return;
        }
        if (cancelled) return;
        if (saved.unchanged) {
          saveRequest.current = null;
          acceptCanonical(saved);
          setPreview(saved);
          setRetryAvailable(false);
          setReloadAvailable(false);
          setMessage("No effective selection change to checkpoint. The draft revision is unchanged.");
          return;
        }
        if (!saved.saved) {
          const action = selectionSaveFailureAction(saved.error_code);
          if (action !== "retry") saveRequest.current = null;
          setPreview(saved);
          setRetryAvailable(action !== "reload");
          setReloadAvailable(action === "reload");
          setError(saved.error || "The checked selection was not saved.");
          setMessage(action === "reload"
            ? "The draft changed. Reload its canonical revision before editing again."
            : action === "new-identity"
              ? "Nothing was saved. Retry with a new checkpoint identity."
              : "Nothing was saved. The live configuration is unchanged.");
          window.setTimeout(() => errorSummary.current?.focus(), 0);
          return;
        }
        setPreview(saved);
        saveRequest.current = null;
        acceptCanonical(saved);
        setRetryAvailable(false);
        setReloadAvailable(false);
        setMessage("Draft checkpoint saved. Nothing was applied to the installed harness.");
        onRevision(saved.result?.replayed
          ? saved.base_revision : saved.result?.revision ?? revision);
      } catch (reason) {
        if (!stale()) {
          setRetryAvailable(true);
          setReloadAvailable(false);
          setError(reason instanceof Error ? reason.message : "Selection save failed.");
          setMessage("Nothing was saved. The live configuration is unchanged.");
          window.setTimeout(() => errorSummary.current?.focus(), 0);
        }
      } finally {
        if (saving.current === operationGeneration) {
          saving.current = null;
          if (cancelled && generation.current === operationGeneration
              && activeDraft.current === draft) {
            setRetryNonce((value) => value + 1);
          }
        }
      }
    }, SELECTION_AUTOSAVE_DELAY_MS);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [changes, draft, onRevision, retryNonce, revision]);

  function change(path: string, value: unknown) {
    saveRequest.current = null;
    setRetryAvailable(false);
    setReloadAvailable(false);
    setValues((existing) => ({ ...existing, [path]: value }));
    setChanges((existing) => {
      const next = { ...existing, [path]: value };
      const returnedToBaseline = JSON.stringify(value) === JSON.stringify(baselineValues.current[path]);
      if (returnedToBaseline && saving.current !== generation.current) delete next[path];
      if (!Object.keys(next).length) {
        setPreview(null);
        setLastCommands([]);
        setMessage("No effective selection change to checkpoint. The draft revision is unchanged.");
      }
      changesRef.current = next;
      return next;
    });
  }

  function reloadCanonicalDraft() {
    generation.current += 1;
    saving.current = null;
    saveRequest.current = null;
    changesRef.current = {};
    recoverRevision.current = true;
    setChanges({});
    setRetryAvailable(false);
    setReloadAvailable(false);
    setReloadNonce((value) => value + 1);
  }

  const showAcknowledgement = useMemo(
    () => Boolean(controls && values.core_switches_acknowledged !== true && (
      needsCoreAcknowledgement(error) || controls.switches.some((group) => group.kind === "hooks"
        && group.rows.some((row) => row.core && values[`hooks.${row.unit}`] === "off"))
    )),
    [controls, error, values],
  );

  if (!controls || !current) {
    return <Paper p="xl" withBorder><Text aria-live="polite" c={error ? "red" : "dimmed"}>{error || message}</Text></Paper>;
  }

  return (
    <Stack className="selection-editor" data-draft={draft} gap="lg">
      <Paper className="settings-section" p="xl" withBorder>
        <Group justify="space-between" wrap="wrap">
          <div>
            <Title order={2}>Change selection in this draft</Title>
            <Text c="dimmed" mt="xs" size="sm">Mode, stance and switch edits are checked by the same command as the CLI, then checkpointed. Nothing live changes.</Text>
          </div>
          <Badge color={error ? "red" : Object.keys(changes).length ? "yellow" : "teal"} variant="light">
            {error ? "refused" : Object.keys(changes).length ? "checking" : "checkpointed"}
          </Badge>
        </Group>
        <Text aria-live="polite" c={error ? "red" : "dimmed"} mt="md" size="sm">{message}</Text>
        {error && <Alert ref={errorSummary} tabIndex={-1} color="red" mt="md" title="Nothing was saved">
          <Text size="sm">{error}</Text>
          {(retryAvailable || reloadAvailable) && <Group mt="sm">
            {retryAvailable && <Button size="xs" variant="light" onClick={() => setRetryNonce((value) => value + 1)}>Retry save</Button>}
            {reloadAvailable && <Button size="xs" variant="light" onClick={reloadCanonicalDraft}>Reload draft</Button>}
          </Group>}
        </Alert>}
        {showAcknowledgement && (
          <Checkbox
            checked={values.core_switches_acknowledged === true}
            label="I acknowledge that disabling a core hook turns off harness enforcement."
            mt="md"
            onChange={(event) => change("core_switches_acknowledged", event.currentTarget.checked)}
          />
        )}
        <SimpleGrid cols={{ base: 1, md: 2 }} mt="xl" spacing="lg">
          <Select
            data={controls.modes}
            label="Mode"
            description="Sets the draft's mode layer."
            value={String(values.mode ?? "") || null}
            onChange={(value) => { if (value) change("mode", value); }}
          />
          <div>
            <Text fw={650} size="sm">Current preview budget</Text>
            <Text c="dimmed" size="sm">{current.budget.selected_lines} / {current.budget.line_cap} selected always-loaded lines</Text>
          </div>
        </SimpleGrid>
        <Title mt="xl" order={3}>Stance variants</Title>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} mt="md" spacing="md">
          {controls.stances.map((stance) => (
            <Select
              data={stance.options}
              key={stance.name}
              label={stance.name}
              value={String(values[`stances.${stance.name}`] ?? "") || null}
              onChange={(value) => { if (value) change(`stances.${stance.name}`, value); }}
            />
          ))}
        </SimpleGrid>
      </Paper>

      <Paper className="settings-section" p="xl" withBorder>
        <Title order={2}>Module switches</Title>
        <Text c="dimmed" mt="xs" size="sm">Dependencies and core-hook policy are checked before a checkpoint is written.</Text>
        <Stack gap="xs" mt="lg">
          {controls.switches.map((group) => (
            <details className="selection-switch-group" key={group.kind}>
              <summary><span>{group.kind}</span><Badge color="gray" variant="light">{group.rows.length}</Badge></summary>
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} p="md" spacing="md">
                {group.rows.map((row) => (
                  <Switch
                    checked={values[`${group.kind}.${row.unit}`] !== "off"}
                    key={row.unit}
                    label={`${row.unit}${row.core ? " · core" : ""}`}
                    onChange={(event) => change(`${group.kind}.${row.unit}`, event.currentTarget.checked ? "on" : "off")}
                  />
                ))}
              </SimpleGrid>
            </details>
          ))}
        </Stack>
      </Paper>

      {preview ? <SnapshotDiff preview={preview} /> : null}

      {lastCommands.length > 0 && (
        <Paper p="lg" withBorder>
          <Title order={3}>Matching CLI commands</Title>
          <Stack gap="xs" mt="md">{lastCommands.map((command) => <CommandChip command={command} key={command} label="Selection change" />)}</Stack>
        </Paper>
      )}
    </Stack>
  );
}
