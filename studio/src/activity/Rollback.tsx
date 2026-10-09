import { Alert, Button, Code, Group, Stack, Text, TextInput } from "@mantine/core";
import { type Ref, useRef, useState } from "react";

import { CommandChip, StatusBadge } from "../components/StudioKit";
import { previewRollback, rollBack } from "./api";
import {
  noticeHeadline, noticeTone, restoredChanges, rollbackBlocker, rollbackNotice, shownValue,
  type RollbackNotice, type RollbackPreview,
} from "./rollbackModel";

/** The reverse diff: each key and file the apply wrote, and the value it returns to. */
export function RollbackDetails({ preview }: { preview: RollbackPreview }) {
  const keys = preview.config.filter((row) => row.action !== "none");
  const files = preview.files.filter((row) => row.action !== "none");
  return (
    <Stack gap="sm">
      {preview.refusals.length > 0 && (
        <Alert color="red" title="Rollback is refused; nothing will change">
          <ul className="message-list">{preview.refusals.map((item) =>
            <li key={`${item.code}:${item.message}`}><Code>{item.code}</Code> {item.message}</li>)}</ul>
        </Alert>
      )}
      {keys.length > 0 && <div>
        <Text fw={650} size="sm">Configuration keys restored</Text>
        <ul className="message-list">{keys.map((row) => <li key={row.key}>
          <Code>{row.key}</Code> {shownValue(row.current_present, row.current)} → {shownValue(row.restored_present, row.restored)}
        </li>)}</ul>
      </div>}
      {files.length > 0 && <div>
        <Text fw={650} size="sm">Files in your personal root (<Code>{preview.destination}</Code>)</Text>
        <ul className="message-list">{files.map((row) => <li key={row.path}>
          <Code>{row.path}</Code> {row.action === "delete" ? "removed" : "restored to its earlier content"}
        </li>)}</ul>
      </div>}
      {preview.commands.length > 0 && <Stack gap="xs">
        {preview.commands.map((item) => <CommandChip command={item.command} key={item.command} label={`Step: ${item.step}`} />)}
      </Stack>}
    </Stack>
  );
}

type ControlsProps = {
  preview: RollbackPreview;
  confirmation: string;
  busy: boolean;
  describedBy: string;
  onConfirm: (value: string) => void;
  onRollBack: () => void;
};

/** The confirmation and Roll back button. A blocked button stays focusable and reads out why. */
export function RollbackControls({ preview, confirmation, busy, describedBy, onConfirm, onRollBack }: ControlsProps) {
  const blocker = rollbackBlocker(preview, confirmation);
  const draft = preview.apply.draft ?? "";
  return (
    <Stack gap="sm">
      <TextInput
        description={`Rollback restores your live configuration and personal root, then runs citizen sync. Type ${draft} to confirm.`}
        label="Confirm the applied draft to roll back"
        onChange={(event) => onConfirm(event.currentTarget.value)}
        value={confirmation}
      />
      <Group>
        <Button aria-describedby={blocker ? describedBy : undefined} aria-disabled={blocker !== "" || undefined}
          color="red" data-disabled={blocker !== "" || undefined} disabled={busy} loading={busy}
          onClick={() => { if (!blocker) onRollBack(); }}>Roll back {draft}</Button>
        <CommandChip command={preview.rollback_command} label="Roll back from the CLI" />
      </Group>
      {blocker ? <Text c="dimmed" id={describedBy} size="sm">{blocker}</Text> : null}
    </Stack>
  );
}

type ResultProps = { notice: RollbackNotice | null; onDismiss: () => void; dismissRef?: Ref<HTMLButtonElement> };

/**
 * The finished rollback, kept above the Activity list until it is dismissed. The status region is
 * always mounted, so the result is announced once when it arrives and not again when the list
 * behind it refreshes; the dismiss control sits outside it, so it is not read as part of the news.
 */
export function RollbackResultRegion({ notice, onDismiss, dismissRef }: ResultProps) {
  const { keys, files } = notice ? restoredChanges(notice) : { keys: [], files: [] };
  return (
    <div className={notice ? "activity-rollback-result" : undefined} data-tone={notice ? noticeTone(notice) : undefined}>
      <div aria-atomic="true" aria-live="polite" role="status">
        {notice ? <Stack gap="xs">
          <Group gap="sm" wrap="wrap">
            <Text component="h2" fw={650} size="md">Rollback of {notice.draft || notice.applyId.slice(0, 12)}</Text>
            <StatusBadge tone={noticeTone(notice)}>{notice.result?.status ?? "no result"}</StatusBadge>
          </Group>
          <Text size="sm">{noticeHeadline(notice)}</Text>
          {notice.result?.message ? <Text c="dimmed" size="sm">{notice.result.message}</Text> : null}
          {keys.length > 0 && <div>
            <Text fw={650} size="sm">Configuration keys restored</Text>
            <ul className="message-list">{keys.map((row) => <li key={row.key}>
              <Code>{row.key}</Code> {shownValue(row.restored_present, row.restored)}
            </li>)}</ul>
          </div>}
          {files.length > 0 && <div>
            <Text fw={650} size="sm">Files in your personal root</Text>
            <ul className="message-list">{files.map((row) => <li key={row.path}>
              <Code>{row.path}</Code> {row.action === "delete" ? "removed" : "restored to its earlier content"}
            </li>)}</ul>
          </div>}
        </Stack> : null}
      </div>
      {notice ? <Group mt="sm">
        <Button onClick={onDismiss} ref={dismissRef} size="xs" variant="default">Dismiss rollback result</Button>
      </Group> : null}
    </div>
  );
}

type Props = { applyId: string; onChanged?: () => void; onResult: (notice: RollbackNotice) => void };

/**
 * One-step rollback of the apply an Activity entry records: preview, confirm, roll back. The
 * finished rollback goes to `onResult`, and the page keeps it on screen; the panel's own live region
 * carries only progress, so the result is not announced twice.
 */
export function RollbackPanel({ applyId, onChanged, onResult }: Props) {
  const [preview, setPreview] = useState<RollbackPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState<"" | "previewing" | "rolling-back">("");
  const [message, setMessage] = useState("");
  const pending = useRef(false);

  async function runPreview() {
    setBusy("previewing");
    setMessage("Reading the apply journal and the live values…");
    try {
      const loaded = await previewRollback(applyId);
      setPreview(loaded);
      setMessage(loaded.can_rollback ? "Preview ready. Nothing has changed." : "Rollback is refused. Nothing has changed.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "The preview did not complete.");
    } finally {
      setBusy("");
    }
  }

  function finish(notice: RollbackNotice) {
    setMessage("");
    onResult(notice);
  }

  async function runRollback() {
    const draft = preview?.apply.draft;
    if (pending.current || !draft) return;
    pending.current = true;
    setBusy("rolling-back");
    setMessage("Rolling back under the sync lock…");
    try {
      const outcome = await rollBack(applyId, confirmation);
      setConfirmation("");
      if (outcome.status === "rolled-back") setPreview(null);
      finish(rollbackNotice(applyId, draft, outcome));
      if (outcome.status === "rolled-back") onChanged?.();
    } catch (reason) {
      finish(rollbackNotice(applyId, draft, null, reason instanceof Error ? reason.message : ""));
    } finally {
      pending.current = false;
      setBusy("");
    }
  }

  return (
    <Stack className="activity-rollback" gap="sm" mt="md">
      <Group>
        <Button disabled={busy !== ""} loading={busy === "previewing"} onClick={() => void runPreview()} size="xs" variant="default">
          Preview rollback
        </Button>
        <CommandChip command={`citizen draft rollback ${applyId} --preview --json`} label="Preview from the CLI" />
      </Group>
      <div aria-live="polite" role="status">
        {message ? <Text size="sm">{message}</Text> : null}
      </div>
      {preview && <RollbackDetails preview={preview} />}
      {preview?.can_rollback && (
        <RollbackControls busy={busy === "rolling-back"} confirmation={confirmation}
          describedBy={`rollback-blocker-${applyId}`} onConfirm={setConfirmation}
          onRollBack={() => void runRollback()} preview={preview} />
      )}
    </Stack>
  );
}
