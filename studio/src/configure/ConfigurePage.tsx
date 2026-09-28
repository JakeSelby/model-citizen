import {
  Alert, Badge, Button, Checkbox, Code, Group, MultiSelect, NumberInput,
  Paper, Select, Stack, Text, Textarea, TextInput, Title,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";

import { loadDraft, loadSchema, previewDraft, saveDraft, type Preview } from "./api";
import { CommandChip } from "../components/StudioKit";
import {
  AUTOSAVE_DELAY_MS, errorMap, fieldEnabled, hydrateValues, parseJsonObject,
  remainingChanges, commandFor, type ConfigureSchema, type FieldDescriptor, type ReferenceValue,
} from "./model";

type FieldProps = {
  field: FieldDescriptor;
  value: unknown;
  error?: string;
  disabled?: boolean;
  onChange: (value: unknown) => void;
};

export function GovernedSyncReview() {
  const [reviewed, setReviewed] = useState(false);
  return (
    <Paper aria-labelledby="governed-sync-title" id="governed-sync" className="settings-section" p="xl" withBorder>
      <Title id="governed-sync-title" order={2}>Review projection sync</Title>
      <Text c="dimmed" mt="xs" size="sm">
        Preview every projection change first. Studio will not run either command or write to the installed harness.
      </Text>
      <Stack gap="lg" mt="lg">
        <CommandChip command="citizen sync --dry-run" label="Preview sync" />
        <Checkbox
          checked={reviewed}
          label="I reviewed the dry-run output and want the explicit apply command."
          onChange={(event) => setReviewed(event.currentTarget.checked)}
        />
        {reviewed && (
          <Alert color="yellow" title="Applying changes is explicit">
            <Text mb="sm" size="sm">Copying does not run sync. Run the command in your terminal when you are ready.</Text>
            <CommandChip command="citizen sync" label="Apply reviewed sync" />
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

function Field({ field, value, error, disabled, onChange }: FieldProps) {
  const common = { description: field.help, disabled, error, label: field.label, required: field.required };
  if (field.kind === "boolean") {
    return <Checkbox {...common} checked={Boolean(value)} onChange={(event) => onChange(event.currentTarget.checked)} />;
  }
  if (field.kind === "select") {
    return <Select {...common} data={field.options} value={String(value ?? "")} onChange={(next) => onChange(next ?? "")} />;
  }
  if (field.kind === "multi-select") {
    return <MultiSelect {...common} data={field.options} value={Array.isArray(value) ? value as string[] : []} onChange={onChange} />;
  }
  if (field.kind === "integer" || field.kind === "number") {
    return <NumberInput {...common} allowDecimal={field.kind === "number"} value={Number(value ?? 0)} onChange={onChange} />;
  }
  if (field.kind === "string-list") {
    return (
      <Textarea
        {...common}
        autosize
        minRows={2}
        placeholder="One absolute path per line"
        value={Array.isArray(value) ? value.join("\n") : ""}
        onChange={(event) => onChange(event.currentTarget.value.split("\n").map((item) => item.trim()).filter(Boolean))}
      />
    );
  }
  if (field.kind === "json-object") {
    return (
      <Textarea
        {...common}
        autosize
        className="json-field"
        minRows={3}
        value={typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2)}
        onChange={(event) => {
          try { onChange(parseJsonObject(event.currentTarget.value)); } catch { onChange(event.currentTarget.value); }
        }}
      />
    );
  }
  if (field.kind === "reference") {
    const reference = (value && typeof value === "object" ? value : {}) as Partial<ReferenceValue>;
    const source = reference.source ?? field.reference_sources[0] ?? "environment";
    const redacted = (reference as { configured?: boolean }).configured === true;
    return (
      <Stack gap="xs">
        <TextInput
          {...common}
          autoComplete="off"
          label={`${field.label} (${source} reference)`}
          placeholder={redacted ? "Malformed stored reference" : source === "environment" ? "HARNESS_OTLP_HEADERS" : "~/.config/agent-harness/otlp-headers"}
          value={reference.name ?? ""}
          onChange={(event) => onChange({ source, name: event.currentTarget.value })}
        />
        {redacted && (
          <Button
            aria-label={`Clear malformed ${field.label}`}
            size="xs"
            variant="subtle"
            onClick={() => onChange({ source, name: "" })}
          >Clear malformed reference</Button>
        )}
      </Stack>
    );
  }
  return (
    <TextInput
      {...common}
      type={field.kind === "url" ? "url" : "text"}
      value={String(value ?? "")}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  );
}

export function ConfigurePage() {
  const [schema, setSchema] = useState<ConfigureSchema | null>(null);
  const [draft, setDraft] = useState("");
  const [loadedDraft, setLoadedDraft] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [changes, setChanges] = useState<Record<string, unknown>>({});
  const [baseRevision, setBaseRevision] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const saving = useRef(false);
  const [retryAvailable, setRetryAvailable] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "unavailable" | "error">("loading");
  const [message, setMessage] = useState("Loading configuration schema…");

  useEffect(() => {
    loadSchema().then((next) => {
      setSchema(next);
      setStatus("idle");
      setMessage("Enter an existing draft name to load its configuration.");
    }).catch((error: unknown) => {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Configuration schema is unavailable.");
    });
  }, []);

  const errors = useMemo(() => errorMap(preview?.errors ?? []), [preview]);

  useEffect(() => {
    if (!baseRevision || !loadedDraft || !Object.keys(changes).length || saving.current) return;
    const snapshot = changes;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setStatus("loading");
      setRetryAvailable(false);
      setMessage("Validating draft changes…");
      try {
        const planned = await previewDraft(loadedDraft, snapshot);
        if (cancelled) return;
        setPreview(planned);
        setWarnings(planned.warnings);
        if (!planned.valid) {
          setStatus("error");
          setMessage("Fix the validation errors. Nothing was saved.");
          return;
        }
        if (!planned.changed.length) {
          setChanges((current) => remainingChanges(current, snapshot));
          setStatus("ready");
          setMessage("No effective configuration change to checkpoint.");
          return;
        }
        saving.current = true;
        setMessage("Saving a validated draft checkpoint…");
        const saved = await saveDraft(loadedDraft, baseRevision, snapshot);
        setPreview(saved);
        setWarnings(saved.warnings);
        if (!saved.saved) {
          setStatus("error");
          setRetryAvailable(true);
          setMessage("Nothing was saved. Retry when the draft is available.");
          return;
        }
        saving.current = false;
        setBaseRevision(saved.result?.revision ?? baseRevision);
        setChanges((current) => remainingChanges(current, snapshot));
        setRetryNonce((current) => current + 1);
        setStatus("ready");
        setMessage("Draft checkpoint saved. Nothing was applied to the installed harness.");
      } catch (error) {
        if (!cancelled || saving.current) {
          setStatus("error");
          setRetryAvailable(true);
          setMessage(error instanceof Error ? error.message : "Autosave failed.");
        }
      } finally {
        saving.current = false;
      }
    }, AUTOSAVE_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [baseRevision, changes, loadedDraft, retryNonce]);

  async function openDraft() {
    if (!schema || !draft.trim()) return;
    setStatus("loading");
    setMessage("Loading draft…");
    try {
      const result = await loadDraft(draft.trim());
      setStatus(result.status === "ready" ? "ready" : result.status);
      setMessage(result.message);
      setValues(hydrateValues(schema, result.values));
      setChanges({});
      setPreview(null);
      setWarnings(result.warnings);
      setBaseRevision(result.draft.revision ?? "");
      setLoadedDraft(result.draft.name ?? draft.trim());
      setRetryAvailable(false);
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Draft could not be loaded.");
    }
  }

  function change(path: string, value: unknown) {
    setValues((current) => ({ ...current, [path]: value }));
    setChanges((current) => ({ ...current, [path]: value }));
    setPreview(null);
  }

  async function validate() {
    setStatus("loading");
    setMessage("Validating draft changes…");
    try {
      const result = await previewDraft(loadedDraft, changes);
      setPreview(result);
      setWarnings(result.warnings);
      setStatus(result.valid ? "ready" : "error");
      setMessage(result.valid ? `${result.changed.length} field change${result.changed.length === 1 ? "" : "s"} ready to save.` : "Fix the validation errors before saving.");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Preview failed.");
    }
  }

  const unsaved = Object.keys(changes).length;
  return (
    <Stack gap="xl">
      <Group align="flex-end" justify="space-between">
        <div>
          <Text className="eyebrow">Studio / Configure</Text>
          <Title order={1}>Configure inside a draft.</Title>
          <Text c="dimmed" mt="xs">Preview and validate every setting before a checkpoint is written.</Text>
        </div>
        <Badge color={status === "ready" ? "teal" : status === "error" ? "red" : "gray"} variant="light">{status}</Badge>
      </Group>

      <Paper className="draft-loader" p="lg" withBorder>
        <Group align="flex-end">
          <TextInput
            className="draft-name"
            description="Studio edits an existing managed draft and never the installed configuration."
            label="Draft name"
            placeholder="my-experiment"
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
          />
          <Button disabled={!schema || !draft.trim()} loading={status === "loading"} onClick={openDraft}>Load draft</Button>
        </Group>
        <Text aria-live="polite" c={status === "error" || status === "unavailable" ? "red" : "dimmed"} mt="md" size="sm">{message}</Text>
      </Paper>

      {warnings.length > 0 && (
        <Alert color="yellow" title="Configuration is valid with limitations">
          <ul className="message-list">{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </Alert>
      )}

      <GovernedSyncReview />

      {preview && preview.errors.length > 0 && (
        <Alert color="red" title="Nothing was saved">
          <ul className="message-list">
            {preview.errors.map((error) => <li key={`${error.path}:${error.message}`}><Code>{error.path}</Code> {error.message}</li>)}
          </ul>
        </Alert>
      )}

      {schema && baseRevision && schema.sections.map((section) => (
        <Paper className="settings-section" key={section.id} p="xl" withBorder>
          <Title order={2}>{section.label}</Title>
          <Text c="dimmed" mt="xs" size="sm">{section.description}</Text>
          {section.id === "telemetry" && (
            <Alert color="yellow" mt="lg" title="Read before enabling export">
              Native export can include runtime identifiers that the local ledger does not. Header inputs store references only; Studio never reads or displays credential values.
            </Alert>
          )}
          <div className="settings-grid">
            {section.fields.map((field) => (
              <div className="settings-field" key={field.path}>
                <Field
                  disabled={!fieldEnabled(field, values)}
                  field={field}
                  value={values[field.path]}
                  error={errors[field.path]}
                  onChange={(value) => change(field.path, value)}
                />
                <Code className="field-provenance">{field.path}</Code>
              </div>
            ))}
          </div>
        </Paper>
      ))}

      {schema && baseRevision && (
        <Paper aria-labelledby="configure-cli-title" className="settings-section" p="xl" withBorder>
          <Title id="configure-cli-title" order={2}>Exact CLI equivalents</Title>
          <Text c="dimmed" mt="xs" size="sm">
            Studio calls the same settings core as these headless commands. Put path-keyed changes in changes.json.
          </Text>
          <Stack className="cli-equivalents" gap="xs" mt="lg">
            {Object.entries(schema.commands).map(([operation, command]) => (
              <div key={operation}>
                <CommandChip command={commandFor(command, loadedDraft, baseRevision)} label={operation} />
              </div>
            ))}
          </Stack>
        </Paper>
      )}

      {baseRevision && (
        <Paper className="save-bar" p="lg" shadow="sm" withBorder>
          <div>
            <Text fw={650}>{unsaved} unsaved field{unsaved === 1 ? "" : "s"}</Text>
            <Text c="dimmed" size="sm">Forms validate and checkpoint 750 ms after the last change; applying remains a separate governed action.</Text>
          </div>
          <Group>
            <Button disabled={!unsaved} variant="default" onClick={validate}>Preview</Button>
            {retryAvailable && (
              <Button disabled={!unsaved} onClick={() => setRetryNonce((current) => current + 1)}>Retry save</Button>
            )}
            <Badge color="teal" size="lg" variant="light">Autosave on</Badge>
          </Group>
        </Paper>
      )}
    </Stack>
  );
}
