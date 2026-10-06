import {
  Alert, Button, Checkbox, Code, Group, Paper, Select, SimpleGrid, Stack, Text, TextInput, Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CodeView, CommandChip } from "../components/StudioKit";
import { LibraryGroups } from "../library/LibraryPage";
import type { LibraryModule } from "../library/model";
import { loadAuthoring, loadDraftLibrary, previewAuthoring, saveAuthoring } from "./api";
import {
  authoringCommand, canPreview, draftOwnedModules, EMPTY_FORM, nameProblem, needsRoot, toRequest,
  type AuthoringForm, type AuthoringKind, type AuthoringPreview, type AuthoringRead,
} from "./authoringModel";

type Props = { draft: string; revision: string; onRevision: (revision: string) => void };

/** Only the verdict and findings: this is what the live region announces after a check or save. */
export function AuthoringVerdict({ preview }: { preview: AuthoringPreview }) {
  if (preview.findings.length > 0) {
    return (
      <Alert color="red" title="The manifest checks refused this module; nothing was saved">
        <ul className="message-list">{preview.findings.map((line) => <li key={line}>{line}</li>)}</ul>
      </Alert>
    );
  }
  if (!preview.valid) return preview.error ? <Alert color="red" title="Nothing was saved">{preview.error}</Alert> : null;
  return (
    <Text size="sm">Manifest checks pass. Saving writes {preview.files.length} file(s)
      {preview.root?.created ? <> and registers the new personal root <Code>{preview.root.label}</Code></> : null}.
      Nothing is applied.</Text>
  );
}

/** The planned configuration and files, outside the live region so a check never reads them out. */
export function AuthoringFiles({ preview }: { preview: AuthoringPreview }) {
  return (
    <Stack gap="sm">
      {preview.fork && (
        <Text size="sm">Fork of core <Code>{preview.fork.source}</Code> at {preview.fork.version || "this checkout"}.</Text>
      )}
      {preview.config_changes.length > 0 && (
        <div>
          <Text fw={650} size="sm">Draft configuration</Text>
          <ul className="message-list">{preview.config_changes.map((change) =>
            <li key={change.path}><Code>{change.path}</Code> {change.value}</li>)}</ul>
        </div>
      )}
      {preview.files.map((file) => (
        <div key={file.path}>
          <Text fw={650} size="sm"><Code>{preview.root?.label ?? ""}/{file.path}</Code></Text>
          <CodeView label={`Planned ${file.path}`}>{file.text}</CodeView>
        </div>
      ))}
    </Stack>
  );
}

/** The always-mounted status region: its text changes, the region itself never mounts late. */
export function AuthoringStatus({ message, preview }: { message: string; preview: AuthoringPreview | null }) {
  return (
    <div aria-live="polite" className="authoring-status" role="status">
      {message ? <Text size="sm">{message}</Text> : null}
      {preview ? <AuthoringVerdict preview={preview} /> : null}
    </div>
  );
}

export function RootOffer({ read, checked, onChange }: {
  read: AuthoringRead | null; checked: boolean; onChange: (checked: boolean) => void;
}) {
  if (!needsRoot(read)) return null;
  return (
    <Alert color="blue" title="This draft has no personal root">
      <Checkbox checked={checked} onChange={(event) => onChange(event.currentTarget.checked)}
        label={<>Create <Code>{read?.offer.label}</Code> in this draft and register <Code>{read?.offer.registers}</Code> in <Code>primitive_roots</Code></>} />
    </Alert>
  );
}

export function ModuleAuthoring({ draft, revision, onRevision }: Props) {
  const [read, setRead] = useState<AuthoringRead | null>(null);
  const [modules, setModules] = useState<LibraryModule[]>([]);
  const [form, setForm] = useState<AuthoringForm>(EMPTY_FORM);
  const [preview, setPreview] = useState<AuthoringPreview | null>(null);
  const [previewed, setPreviewed] = useState("");
  const [busy, setBusy] = useState<"" | "checking" | "saving">("");
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const saveKey = useRef("");

  useEffect(() => {
    const current = ++generation.current;
    void (async () => {
      try {
        const [loaded, library] = await Promise.all([loadAuthoring(draft), loadDraftLibrary(draft)]);
        if (current !== generation.current) return;
        setRead(loaded);
        setModules(library.modules);
      } catch (reason) {
        if (current !== generation.current) return;
        setMessage(reason instanceof Error ? reason.message : "Module templates are unavailable.");
      }
    })();
    return () => { generation.current += 1; };
  }, [draft, revision]);

  const update = (patch: Partial<AuthoringForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setPreview(null);
    setPreviewed("");
  };
  const request = toRequest(form);
  const requestText = JSON.stringify(request);
  const problem = form.name ? nameProblem(form) : "";
  const template = read?.templates.find((item) => item.kind === form.kind);

  async function check() {
    setBusy("checking");
    setMessage("Checking the manifest and the draft's selection…");
    try {
      const result = await previewAuthoring(draft, request);
      setPreview(result);
      setPreviewed(requestText);
      saveKey.current = crypto.randomUUID();
      setMessage(result.valid ? "" : "Nothing was saved.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "The check did not complete.");
    } finally {
      setBusy("");
    }
  }

  async function save() {
    setBusy("saving");
    setMessage("Saving a checked draft checkpoint…");
    try {
      const result = await saveAuthoring(draft, revision, saveKey.current, request);
      setPreview(result);
      if (result.saved && result.result?.revision) {
        setMessage(`Saved ${result.module?.kind}/${result.module?.name} to the draft. Nothing applied.`);
        setForm(EMPTY_FORM);
        setPreviewed("");
        onRevision(result.result.revision);
      } else {
        setMessage("Nothing was saved.");
      }
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Nothing was saved. Retry when the draft is available.");
    } finally {
      setBusy("");
    }
  }

  const owned = draftOwnedModules(modules);
  return (
    <Paper className="module-authoring" component="section" aria-label="Add or fork a module">
      <Stack gap="md">
        <div>
          <Title order={2}>Add or fork a module</Title>
          <Text c="dimmed" size="sm">New modules and forks go to your personal root in this draft; a core module is never edited in place.</Text>
        </div>
        {read && read.status !== "ready" && <Alert color="red" title="Unavailable">{read.message}</Alert>}
        <RootOffer read={read} checked={form.createRoot} onChange={(createRoot) => update({ createRoot })} />
        {read?.root && <Text size="sm">Personal root: <Code>{read.root.label}</Code></Text>}
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <Select label="Start from" allowDeselect={false} value={form.action}
            data={[{ value: "add", label: "A template" }, { value: "fork", label: "A core module (fork)" }]}
            onChange={(value) => update({ action: value === "fork" ? "fork" : "add", name: "" })} />
          {form.action === "add" ? (
            <Select label="Template" allowDeselect={false} value={form.kind}
              data={(read?.templates ?? []).map((item) => ({ value: item.kind, label: item.label }))}
              description={template?.detail}
              onChange={(value) => update({ kind: (value ?? "rules") as AuthoringKind })} />
          ) : (
            <Select label="Core module" searchable value={form.source || null} placeholder="Choose a rule or skill"
              data={(read?.forkable ?? []).map((item) => ({ value: item.key, label: `${item.kind}/${item.name}` }))}
              description="The fork is switched on and the core module off, in this draft."
              onChange={(value) => update({ source: value ?? "" })} />
          )}
          <TextInput label="Module name" value={form.name} error={problem || undefined}
            placeholder={form.action === "fork" ? `${form.source.split(":")[2] ?? "module"}-fork` : template?.name_hint}
            onChange={(event) => update({ name: event.currentTarget.value })} />
          <TextInput label={form.action === "fork" ? "What the fork is for (optional)" : "What it is for"}
            description="Becomes the manifest claim." value={form.description}
            onChange={(event) => update({ description: event.currentTarget.value })} />
        </SimpleGrid>
        <Group>
          <Button variant="default" disabled={!canPreview(form, read) || busy !== ""} loading={busy === "checking"} onClick={check}>Check</Button>
          <Button disabled={!preview?.valid || previewed !== requestText || busy !== ""} loading={busy === "saving"} onClick={save}>Save to draft</Button>
        </Group>
        <AuthoringStatus message={message} preview={preview} />
        {preview && <AuthoringFiles preview={preview} />}
        <CommandChip command={authoringCommand(draft, revision, true)} />
        <div>
          <Title order={3}>Your modules in this draft</Title>
          {owned.length ? <LibraryGroups modules={owned} /> : <Text c="dimmed" size="sm">None yet.</Text>}
        </div>
      </Stack>
    </Paper>
  );
}
