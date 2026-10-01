import { Alert, Button, Code, Group, Paper, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CommandChip } from "../components/StudioKit";
import { applyDraft, reviewDraftApply } from "./api";
import {
  applyBlocker, budgetDelta, canApply, coreFiles, personalFiles, resultHeadline, shown,
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
  const color = result.applied ? (result.doctor.status === "attention" ? "yellow" : "green") : "red";
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
    setBusy("applying");
    setMessage(`Applying ${draft} under the sync lock…`);
    try {
      const outcome = await applyDraft(draft, review.draft.revision, confirmation);
      setResult(outcome);
      setMessage(resultHeadline(outcome));
      setConfirmation("");
      if (outcome.applied) {
        setReview(null);
        onApplied?.();
      }
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "The apply did not report a result. Check Activity before retrying.");
    } finally {
      setBusy("");
    }
  }

  const blocker = applyBlocker(review, revision, draft, confirmation);
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
        {review && <ApplyReviewDetails review={review} />}
        {review?.can_apply && (
          <Stack gap="sm">
            <TextInput
              description={`Apply changes your live configuration and personal root, then runs citizen sync. Type ${draft} to confirm.`}
              label="Confirm the draft to apply"
              onChange={(event) => setConfirmation(event.currentTarget.value)}
              value={confirmation}
            />
            <Group>
              <Button color="red" disabled={!canApply(review, revision, draft, confirmation) || busy !== ""}
                loading={busy === "applying"} onClick={runApply} title={blocker || undefined}>Apply {draft}</Button>
              <CommandChip command={review.apply_command} label="Apply from the CLI" />
            </Group>
            {blocker ? <Text c="dimmed" size="sm">{blocker}</Text> : null}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
