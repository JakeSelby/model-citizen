import { Alert, Badge, Button, Group, Paper, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { CommandChip, StatusBadge } from "../components/StudioKit";
import { loadDraft, loadSchema } from "../configure/api";
import { DraftApply } from "../configure/DraftApply";
import { DraftSelectionEditor } from "../configure/DraftSelectionEditor";
import type { FieldDescriptor } from "../configure/model";
import { loadCatalog, startRun, streamRun } from "../experiments/api";
import { commandFor, streamComplete, terminal, type FreeSuite, type RunUpdate } from "../experiments/model";
import { loadOverview } from "../overview/api";
import { attentionCount, type Overview } from "../overview/model";
import { afterApply, beginSetup, loadStatus, saveIdentity } from "./flow";
import {
  doctorPassed, errorMessage, FIRST_RUN_DRAFT, liveChangeNotice, nextStep, resumeStep, stepReachable,
  type FirstRunStatus, type StepId,
} from "./model";
import "./firstrun.css";

const CHECK_SUITE = "lint";
const POLL_MS = 750;

export function GuideProgress({ status, active, onSelect }: {
  status: FirstRunStatus; active: StepId; onSelect: (step: StepId) => void;
}) {
  return (
    <nav aria-label="Setup steps">
      <ol className="first-run-steps">
        {status.steps.map((step, index) => {
          const reachable = stepReachable(status, step.id);
          return (
            <li aria-current={step.id === active ? "step" : undefined} key={step.id}>
              <Button disabled={!reachable} onClick={() => onSelect(step.id)} size="compact-sm"
                variant={step.id === active ? "filled" : "subtle"}>
                {index + 1}. {step.label}
              </Button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Always mounted, so a screen reader announces each new message; errors interrupt as alerts. */
export function LiveMessages({ message, error }: { message: string; error: string }) {
  return (
    <>
      <Text aria-live="polite" component="div" role="status" size="sm">{message}</Text>
      <Text c="red" component="div" role="alert" size="sm">{error}</Text>
    </>
  );
}

export function HealthSummary({ overview }: { overview: Overview | null }) {
  if (!overview) return <Text c="dimmed" size="sm">Reading the install's health…</Text>;
  const attention = attentionCount(overview);
  return (
    <Stack gap="sm">
      <Text size="sm">
        Installed {overview.installed.version ? `v${overview.installed.version}` : "version unavailable"}
        {overview.mode.value ? `, mode ${overview.mode.value}` : ""}.
      </Text>
      <StatusBadge tone={attention ? "warning" : "success"}>
        {attention ? `${attention} doctor item${attention === 1 ? "" : "s"} to review` : "Doctor checks clear"}
      </StatusBadge>
      <ul className="message-list">
        {overview.doctor.checks.map((check) => <li key={check.id}>{check.message}</li>)}
      </ul>
    </Stack>
  );
}

export function ReproduceCommands({ status }: { status: FirstRunStatus }) {
  return (
    <Paper aria-labelledby="first-run-cli-title" className="settings-section" p="xl" withBorder>
      <Title id="first-run-cli-title" order={2}>The same setup from the CLI</Title>
      <Text c="dimmed" mt="xs" size="sm">
        Run these in order on another machine to reach the same configuration without a draft.
      </Text>
      <Stack gap="xs" mt="lg">
        {status.commands.headless.map((command, index) =>
          <CommandChip command={command} key={`${index}:${command}`} label={`Step ${index + 1}`} />)}
      </Stack>
      <Text c="dimmed" mt="lg" size="sm">An agent runs the draft loop headless with these:</Text>
      <Stack gap="xs" mt="xs">
        {status.commands.agent.map((command) => <CommandChip command={command} key={command} label="Agent" />)}
        <CommandChip command={status.commands.status} label="Where setup stands" />
      </Stack>
    </Paper>
  );
}

export function DoneSummary({ status, overview }: { status: FirstRunStatus; overview: Overview | null }) {
  const passed = doctorPassed(status);
  return (
    <Stack gap="md">
      <Alert color={passed ? "teal" : "yellow"} title={passed ? "Setup applied and the doctor checks passed" : "Setup applied; the doctor has items to review"}>
        Draft {status.draft_name} was applied
        {status.applied.revision ? ` at ${status.applied.revision.slice(0, 12)}` : ""}. Activity records the governed decision.
      </Alert>
      <HealthSummary overview={overview} />
      <Group>
        <Button component={NavLink} to="/">Go to the Hub</Button>
        <Button component={NavLink} to="/activity" variant="default">Open Activity</Button>
      </Group>
    </Stack>
  );
}

function IdentityStep({ draft, revision, onRevision, onDone }: {
  draft: string; revision: string; onRevision: (revision: string) => void; onDone: () => void;
}) {
  const [fields, setFields] = useState<FieldDescriptor[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [changes, setChanges] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("Loading your identity…");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([loadSchema(), loadDraft(draft)]).then(([schema, read]) => {
      if (!active) return;
      setFields(schema.sections.find((section) => section.id === "identity")?.fields ?? []);
      setValues(read.values);
      setMessage(read.status === "ready" ? "" : read.message);
    }).catch((reason: unknown) => {
      if (active) { setMessage(""); setError(errorMessage(reason)); }
    });
    return () => { active = false; };
  }, [draft]);

  function change(path: string, value: unknown) {
    setValues((current) => ({ ...current, [path]: value }));
    setChanges((current) => ({ ...current, [path]: value }));
  }

  async function save() {
    if (!Object.keys(changes).length) { onDone(); return; }
    setBusy(true);
    setError("");
    setMessage("Checking and saving a draft checkpoint…");
    try {
      const outcome = await saveIdentity(draft, revision, changes);
      if (outcome.revision !== revision) onRevision(outcome.revision);
      if (!outcome.saved) {
        setErrors(outcome.errors);
        setMessage("");
        setError(outcome.message);
        return;
      }
      setErrors({});
      setChanges({});
      setMessage(outcome.message);
      onDone();
    } catch (reason) {
      setMessage("");
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="md">
      {fields.map((field) => field.kind === "select" ? (
        <Select data={field.options} description={field.help} error={errors[field.path]} key={field.path}
          label={field.label} onChange={(next) => change(field.path, next ?? "")} required={field.required}
          value={String(values[field.path] ?? "")} />
      ) : (
        <TextInput description={field.help} error={errors[field.path]} key={field.path} label={field.label}
          onChange={(event) => change(field.path, event.currentTarget.value)} required={field.required}
          value={String(values[field.path] ?? "")} />
      ))}
      <LiveMessages error={error} message={message} />
      <Group>
        <Button loading={busy} onClick={save}>Save and continue</Button>
      </Group>
    </Stack>
  );
}

function CheckStep({ onDone }: { onDone: () => void }) {
  const [suite, setSuite] = useState<FreeSuite | null>(null);
  const [update, setUpdate] = useState<RunUpdate | null>(null);
  const [message, setMessage] = useState("");
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    loadCatalog().then((catalog) => {
      if (active.current) setSuite(catalog.suites.find((item) => item.id === CHECK_SUITE) ?? null);
    }).catch((reason: unknown) => {
      if (active.current) setMessage(reason instanceof Error ? reason.message : "Free suites are unavailable.");
    });
    return () => { active.current = false; };
  }, []);

  async function run() {
    if (!suite) return;
    setMessage("Running the free check. It uses no model and costs nothing.");
    try {
      const record = await startRun(suite.id, suite.parameters.root, "all");
      let current: RunUpdate = { run: record, stdout: { chunk: "", cursor: 0, eof: false },
        stderr: { chunk: "", cursor: 0, eof: false }, progress: { completed: 0, eligible: 0, cases: [], lint_findings: [] } };
      setUpdate(current);
      while (active.current) {
        const events = await streamRun(record.run_id, current.stdout.cursor, current.stderr.cursor);
        for (const next of events) current = next;
        setUpdate(current);
        if (events.some(streamComplete) || terminal(current.run.status)) break;
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
      setMessage(current.run.status === "succeeded" ? "The free check passed." : `The free check ended ${current.run.status}. Read its findings in Experiments.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "The check could not start.");
    }
  }

  const finished = update !== null && terminal(update.run.status);
  return (
    <Stack gap="md">
      <Text size="sm">Lint the installed harness with the free local suite before you apply. The apply review then runs the draft's own checks.</Text>
      {suite && <CommandChip command={commandFor(suite, "all")} label="Run it from the CLI" />}
      <Group>
        <Button disabled={!suite || (update !== null && !finished)} onClick={() => { void run(); }} variant="default">Run the free check</Button>
        <Button disabled={!finished} onClick={onDone}>Continue to review</Button>
        {update && <Badge color={update.run.status === "succeeded" ? "teal" : finished ? "red" : "gray"} variant="light">{update.run.status}</Badge>}
      </Group>
      {update && update.progress.lint_findings.length > 0 && (
        <ul className="message-list">{update.progress.lint_findings.slice(0, 20).map((item) =>
          <li key={`${item.path}:${item.line}:${item.message}`}>{item.path}:{item.line} {item.message}</li>)}</ul>
      )}
      <LiveMessages error="" message={message} />
    </Stack>
  );
}

export function FirstRunPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<FirstRunStatus | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [step, setStep] = useState<StepId>("health");
  const [revision, setRevision] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);

  function adopt(next: FirstRunStatus, moveTo?: StepId) {
    setStatus(next);
    setRevision(next.draft.revision ?? "");
    setStep(moveTo ?? resumeStep(next));
  }

  function go(next: StepId) {
    moved.current = true;
    setStep(next);
  }

  useEffect(() => {
    // The control that moved the guide on is gone, so focus the new step's heading.
    if (moved.current) heading.current?.focus();
  }, [step]);

  useEffect(() => {
    let active = true;
    loadStatus(FIRST_RUN_DRAFT).then((next) => { if (active) adopt(next); })
      .catch((reason: unknown) => { if (active) setError(errorMessage(reason)); });
    loadOverview().then((next) => { if (active) setOverview(next); }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    // The headless commands follow the draft's choices, so re-read them after each checkpoint.
    if (!revision) return;
    let active = true;
    loadStatus(FIRST_RUN_DRAFT).then((next) => { if (active) setStatus(next); }).catch(() => undefined);
    return () => { active = false; };
  }, [revision]);

  async function start() {
    setBusy(true);
    setError("");
    setMessage(`Creating draft ${FIRST_RUN_DRAFT} from the installed version…`);
    try {
      const next = await beginSetup(FIRST_RUN_DRAFT);
      moved.current = true;
      adopt(next, "identity");
      setMessage(`Draft ${next.draft_name} is ready.`);
    } catch (reason) {
      setMessage("");
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function applied() {
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    try {
      const next = await afterApply(FIRST_RUN_DRAFT);
      setOverview(await loadOverview());
      moved.current = true;
      adopt(next.status, next.step);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  if (!status) {
    return (
      <Stack gap="md">
        <Title order={1}>Set up Model Citizen</Title>
        <LiveMessages error={error} message={error ? "" : "Reading where setup stands…"} />
      </Stack>
    );
  }
  const draft = status.draft_name;
  const label = status.steps.find((item) => item.id === step)?.label ?? "";
  return (
    <Stack gap="xl">
      <Group align="flex-end" justify="space-between">
        <div>
          <Text className="eyebrow">Studio / First run</Text>
          <Title order={1}>From this install to an applied setup.</Title>
          <Text c="dimmed" mt="xs">{liveChangeNotice(status)}</Text>
        </div>
        <Button component={NavLink} to="/" variant="default">Leave setup</Button>
      </Group>
      <GuideProgress active={step} onSelect={go} status={status} />
      <LiveMessages error={error} message={message} />

      <Paper className="settings-section first-run-step" p="xl" withBorder>
        <Title className="first-run-heading" order={2} ref={heading} tabIndex={-1}>{label}</Title>
        {step === "health" && (
          <Stack gap="md" mt="md">
            <Text c="dimmed" size="sm">The runtimes and links the doctor found. Afterwards, Hub shows health, Configure holds drafts, Library lists modules, Experiments runs suites, Reports traces evidence and Activity records decisions.</Text>
            <HealthSummary overview={overview} />
            <CommandChip command="citizen doctor" label="The same check from the CLI" />
            <Group><Button onClick={() => go(nextStep("health"))}>Continue</Button></Group>
          </Stack>
        )}
        {step === "draft" && (
          <Stack gap="md" mt="md">
            <Text c="dimmed" size="sm">Every choice goes into draft {draft}. Leaving keeps the draft and changes nothing live.</Text>
            <CommandChip command={status.steps.find((item) => item.id === "draft")?.command ?? ""} label="The same step from the CLI" />
            <Group>
              <Button loading={busy} onClick={() => { void start(); }}>
                {status.draft.revision ? "Continue with the kept draft" : "Create the draft"}
              </Button>
            </Group>
          </Stack>
        )}
        {step === "identity" && revision && (
          <Stack gap="md" mt="md">
            <IdentityStep draft={draft} onDone={() => go("preferences")} onRevision={setRevision} revision={revision} />
          </Stack>
        )}
        {step === "preferences" && revision && (
          <Stack gap="md" mt="md">
            <Text c="dimmed" size="sm">Each pick previews its operative text and saves to the draft.</Text>
            <DraftSelectionEditor draft={draft} onRevision={setRevision} revision={revision} />
            <Group><Button onClick={() => go("check")}>Continue</Button></Group>
          </Stack>
        )}
        {step === "check" && (
          <Stack gap="md" mt="md">
            <CheckStep onDone={() => go("apply")} />
          </Stack>
        )}
        {step === "apply" && revision && (
          <Stack gap="md" mt="md">
            {status.blocked_by.draft && (
              <Alert color="yellow" title="Another apply is open">
                An apply of draft {status.blocked_by.draft} was interrupted. The review below offers to restore or abandon it first.
              </Alert>
            )}
            <DraftApply draft={draft} onApplied={() => { void applied(); }} revision={revision} />
          </Stack>
        )}
        {step === "done" && <Stack mt="md"><DoneSummary overview={overview} status={status} /></Stack>}
      </Paper>

      {status.state !== "not-started" && <ReproduceCommands status={status} />}
    </Stack>
  );
}
