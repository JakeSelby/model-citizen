import { Alert, Button, Code, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CommandChip } from "../components/StudioKit";
import { applyDraft, recoverApply, reviewDraftApply } from "./api";
import {
  applyBlocker, budgetDelta, canApply, canRecover, changedLive, coreFiles, outcomeAction, personalFiles,
  resultHeadline, shown,
  type ApplyResult, type ApplyReview,
} from "./applyModel";

type Props = { draft: string; revision: string; onApplied?: () => void };

/** Everything the draft changes against its base, its checks, budget delta and exact commands. */
export function ApplyReviewDetails({ review }: { review: ApplyReview }) {
  const personal = personalFiles(review);
  const core = coreFiles(review);
  return (
    <Stack gap="md">
      {review.refusals.length > 0 && (
        <Alert color="red" title="Apply is refused; nothing will change">
          <ul className="message-list">{review.refusals.map((item) =>
            <li key={`${item.code}:${item.message}`}><Code>{item.code}</Code> {item.message}</li>)}</ul>
        </Alert>
      )}
      <div>
        <Text fw={650} size="sm">Checks: {review.checks.status}</Text>
        {review.checks.findings.length > 0 && (
          <ul className="message-list">{review.checks.findings.map((line) => <li key={line}>{line}</li>)}</ul>
        )}
      </div>
      <Text size="sm">Context budget: {budgetDelta(review.budget)}</Text>
      <div>
        <Text fw={650} size="sm">Files for your personal root (<Code>{review.destination}</Code>)</Text>
        {personal.length === 0 ? <Text c="dimmed" size="sm">None.</Text> : (
          <ul className="message-list">{personal.map((file) =>
            <li key={file.path}><Code>{file.path}</Code> {file.status}</li>)}</ul>
        )}
      </div>
      <div>
        <Text fw={650} size="sm">Configuration keys</Text>
        {review.config.length === 0 ? <Text c="dimmed" size="sm">None.</Text> : (
          <ul className="message-list">{review.config.map((row) =>
            <li key={row.key}><Code>{row.key}</Code> {shown(row.before)} → {shown(row.after)}
              {row.action === "none" ? " (already live)" : row.action === "conflict" ? ` (live value is ${shown(row.live)})` : ""}</li>)}</ul>
        )}
      </div>
      {review.core && (
        <Alert color="yellow" title={`This draft edits ${core.length} core file(s) in place`}>
          <ul className="message-list">{core.map((file) => <li key={file.path}><Code>{file.path}</Code> {file.status}</li>)}</ul>
          {review.core.fork.modules.length > 0 && (
            <Stack gap="xs" mt="sm">
              <Text size="sm">Fork instead: {review.core.fork.modules.map((item) => item.source).join(", ")}. {review.core.fork.note}</Text>
              <CommandChip command={review.core.fork.command} label="Plan a fork" />
            </Stack>
          )}
          <Stack gap="xs" mt="sm">
            <Text size="sm">Or contribute it: branch <Code>{review.core.branch.name}</Code> holds the draft's commits.</Text>
            {review.core.branch.commands.map((command) => <CommandChip command={command} key={command} label="Open a pull request" />)}
          </Stack>
        </Alert>
      )}
      <div>
        <Text fw={650} size="sm">What apply runs, in order</Text>
        <Stack gap="xs" mt="xs">
          {review.commands.map((item) => <CommandChip command={item.command} key={item.command} label={`Step: ${item.step}`} />)}
        </Stack>
      </div>
    </Stack>
  );
}

export function ApplyOutcome({ result }: { result: ApplyResult }) {
  const color = result.applied ? (result.doctor.status === "attention" ? "yellow" : "green")
    : changedLive(result) || result.status === "abandoned" ? "blue" : "red";
  return (
    <Alert color={color} title={resultHeadline(result)}>
      <Text size="sm">{result.message}</Text>
      {result.doctor.checks.filter((check) => check.status === "attention").length > 0 && (
        <ul className="message-list">{result.doctor.checks.filter((check) => check.status === "attention")
          .map((check) => <li key={check.id}>{check.message}</li>)}</ul>
      )}
    </Alert>
  );
}

type ControlsProps = {
  draft: string;
  revision: string;
  review: ApplyReview;
  confirmation: string;
  busy: "" | "reviewing" | "applying";
  onConfirm: (value: string) => void;
  onApply: () => void;
};

/** The confirmation and Apply button. A blocked button stays focusable and reads out why. */
export function ApplyControls({ draft, revision, review, confirmation, busy, onConfirm, onApply }: ControlsProps) {
  const blocker = applyBlocker(review, revision, draft, confirmation);
  const blocked = blocker !== "";
  return (
    <Stack gap="sm">
      <TextInput
        description={`Apply changes your live configuration and personal root, then runs citizen sync. Type ${draft} to confirm.`}
        label="Confirm the draft to apply"
        onChange={(event) => onConfirm(event.currentTarget.value)}
        value={confirmation}
      />
      <Group>
        <Button aria-describedby={blocked ? "draft-apply-blocker" : undefined} aria-disabled={blocked || undefined}
          color="red" data-disabled={blocked || undefined} disabled={busy !== ""} loading={busy === "applying"}
          onClick={() => { if (!blocked && canApply(review, revision, draft, confirmation)) onApply(); }}>Apply {draft}</Button>
        <CommandChip command={review.apply_command} label="Apply from the CLI" />
      </Group>
      {blocked ? <Text c="dimmed" id="draft-apply-blocker" size="sm">{blocker}</Text> : null}
    </Stack>
  );
}

type RecoverProps = {
  review: ApplyReview;
  confirmation: string;
  busy: "" | "reviewing" | "applying";
  onConfirm: (value: string) => void;
  onRecover: (action: "restore" | "abandon") => void;
};

/** An interrupted apply blocks every apply until it is restored or explicitly abandoned. */
export function RecoverControls({ review, confirmation, busy, onConfirm, onRecover }: RecoverProps) {
  const interrupted = review.interrupted;
  if (!interrupted) return null;
  const ready = canRecover(review, confirmation);
  return (
    <Alert color="yellow" title={interrupted.kind === "rollback"
      ? `A rollback of ${interrupted.draft}'s apply was interrupted`
      : `An apply of ${interrupted.draft} was interrupted`}>
      <Stack gap="sm">
        <Text size="sm">Restore puts back only the keys and files it wrote, then syncs. Abandon keeps them as they are now.</Text>
        <TextInput label="Confirm the interrupted draft" description={`Type ${interrupted.draft} to confirm.`}
          onChange={(event) => onConfirm(event.currentTarget.value)} value={confirmation} />
        <Group>
          <Button aria-describedby={ready ? undefined : "draft-recover-blocker"} aria-disabled={!ready || undefined}
            data-disabled={!ready || undefined} disabled={busy !== ""} loading={busy === "applying"}
            onClick={() => { if (ready) onRecover("restore"); }}>Restore</Button>
          <Button aria-describedby={ready ? undefined : "draft-recover-blocker"} aria-disabled={!ready || undefined}
            data-disabled={!ready || undefined} disabled={busy !== ""} variant="default"
            onClick={() => { if (ready) onRecover("abandon"); }}>Abandon</Button>
          <CommandChip command={interrupted.recover_command} label="Restore from the CLI" />
        </Group>
        {ready ? null : <Text c="dimmed" id="draft-recover-blocker" size="sm">Type {interrupted.draft} to confirm.</Text>}
      </Stack>
    </Alert>
  );
}

export function DraftApply({ draft, revision, onApplied }: Props) {
  const [review, setReview] = useState<ApplyReview | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState<"" | "reviewing" | "applying">("");
  const [message, setMessage] = useState("");
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    setReview(null);
    setResult(null);
    setMessage("");
    setConfirmation("");
  }, [draft, revision]);

  async function runReview() {
    const current = ++generation.current;
    setBusy("reviewing");
    setResult(null);
    setMessage("Reviewing the draft against its base and running its checks…");
    try {
      const loaded = await reviewDraftApply(draft);
      if (current !== generation.current) return;
      setReview(loaded);
      setMessage(loaded.can_apply ? "Review complete. Nothing has been applied." : "Apply is refused. Nothing has been applied.");
    } catch (reason) {
      if (current !== generation.current) return;
      setMessage(reason instanceof Error ? reason.message : "The review did not complete.");
    } finally {
      setBusy("");
    }
  }

  async function runApply() {
    if (!review?.draft.revision) return;
    const current = generation.current;
    setBusy("applying");
    setMessage(`Applying ${draft} under the sync lock…`);
    try {
      const outcome = await applyDraft(draft, review.draft.revision, confirmation);
      const action = outcomeAction(changedLive(outcome), current, generation.current);
      if (action.refreshOverview) onApplied?.();
      if (!action.show) return;
      setResult(outcome);
      setMessage(resultHeadline(outcome));
      setConfirmation("");
      if (changedLive(outcome)) setReview(null);
    } catch (reason) {
      if (current !== generation.current) return;
      setMessage(reason instanceof Error ? reason.message : "The apply did not report a result. Check Activity before retrying.");
    } finally {
      setBusy("");
    }
  }

  async function runRecover(action: "restore" | "abandon") {
    const confirm = review?.interrupted?.draft ?? "";
    const current = generation.current;
    setBusy("applying");
    setMessage(action === "restore" ? "Restoring the interrupted apply under the sync lock…" : "Abandoning the interrupted apply…");
    try {
      const outcome = await recoverApply(action, confirm);
      const next = outcomeAction(changedLive(outcome), current, generation.current);
      if (next.refreshOverview) onApplied?.();
      if (!next.show) return;
      setResult(outcome);
      setMessage(resultHeadline(outcome));
      setConfirmation("");
      if (outcome.status === "recovered" || outcome.status === "abandoned") setReview(null);
    } catch (reason) {
      if (current !== generation.current) return;
      setMessage(reason instanceof Error ? reason.message : "The recovery did not report a result.");
    } finally {
      setBusy("");
    }
  }

  return (
    <Paper aria-labelledby="draft-apply-title" className="settings-section" id="draft-apply" p="xl" withBorder>
      <Title id="draft-apply-title" order={2}>Review and apply this draft</Title>
      <Text c="dimmed" mt="xs" size="sm">
        Apply runs the same locks, checks and commands as the CLI. A lock or check failure stops it before anything changes.
      </Text>
      <Stack gap="lg" mt="lg">
        <Group>
          <Button loading={busy === "reviewing"} disabled={busy !== ""} onClick={runReview} variant="default">Review draft</Button>
          <CommandChip command={`citizen draft review ${draft} --json`} label="Review from the CLI" />
        </Group>
        <div aria-live="polite" role="status">
          {message ? <Text size="sm">{message}</Text> : null}
          {result ? <ApplyOutcome result={result} /> : null}
        </div>
        {review?.interrupted && (
          <RecoverControls busy={busy} confirmation={confirmation} onConfirm={setConfirmation}
            onRecover={(action) => { void runRecover(action); }} review={review} />
        )}
        {review && <ApplyReviewDetails review={review} />}
        {review?.can_apply && (
          <ApplyControls busy={busy} confirmation={confirmation} draft={draft} onApply={runApply}
            onConfirm={setConfirmation} review={review} revision={revision} />
        )}
      </Stack>
    </Paper>
  );
}
