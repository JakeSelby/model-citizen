import {
  Alert, Badge, Button, Checkbox, Code, Group, MultiSelect, NumberInput, Paper, Select, Stack, Text, TextInput, Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CommandChip } from "../components/StudioKit";
import { CompareReport } from "../experiments/compare/ComparePanel";
import { compareRuns } from "../experiments/compare/api";
import type { CompareResult } from "../experiments/compare/model";
import { loadReplayCatalog } from "../experiments/replay/api";
import { packKey, packOptions, withEngineReason, type ReplayCatalog } from "../experiments/replay/model";
import { loadDraftVerdicts, planDraftTest, registerDraftTest, startDraftTest } from "./draftTestApi";
import {
  currentRegistration, draftTestErrorMessage, draftTestTasks, evidenceBadge, initialForm, planBody, readingLines, registerBlocked, registerBody,
  spendLine, startBlocked, startedMessage, startingPack, validateDraftTest, verdictBadge, withNeededTrials, withPack,
  type DraftTestForm, type DraftTestPlan, type DraftTestRegistration, type DraftTestVerdict, type DraftTestVerdicts,
} from "./draftTestModel";

type Props = { draft: string; revision: string };

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? withEngineReason(draftTestErrorMessage(error.message), error) : fallback;
}

/** One checkpoint's latest verdict: badge, headline, staleness, readings, spend and the comparison. */
export function VerdictCard({ test, current, count }: { test: DraftTestVerdict; current: boolean; count: number }) {
  const badge = verdictBadge(test.verdict);
  const evidence = evidenceBadge(test);
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [error, setError] = useState("");
  async function open() {
    setError("");
    try {
      setComparison(await compareRuns(test.comparison));
    } catch (failure) {
      setError(failure instanceof Error ? `The comparison could not be read (${failure.message}).` : "The comparison could not be read.");
    }
  }
  return (
    <Paper>
      <Stack gap="xs">
        <Group gap="xs">
          <Text fw={650} size="sm">Checkpoint <Code>{test.revision.slice(0, 12)}</Code>{current ? " (current)" : ""}</Text>
          <Badge color={badge.color} variant={badge.variant}>{badge.label}</Badge>
          <Badge color="gray" variant={evidence.variant}>{evidence.label}</Badge>
          {test.stale && <Badge color="yellow" variant="outline">Verdict stale</Badge>}
        </Group>
        <Text size="sm">{test.headline}</Text>
        {test.stale_copy && <Alert color="yellow" title="Stale">{test.stale_copy} {test.stale_reason ? `(${test.stale_reason})` : ""}</Alert>}
        {test.reasons.length > 0 && <ul className="message-list">{test.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
        {readingLines(test).map((line) => <Text key={line} size="sm"><Code>{line}</Code></Text>)}
        <Text c="dimmed" size="sm">{spendLine(test)} {test.power_line}</Text>
        <Text c="dimmed" size="xs">{count} test(s) of this checkpoint; the latest is shown.</Text>
        <Group gap="xs">
          <Button size="xs" variant="light" disabled={test.verdict === "running"} onClick={open}>Open comparison</Button>
          <CommandChip command={test.comparison.command} label="Comparison" />
        </Group>
        <Text aria-live="polite" c="red" size="sm">{error}</Text>
        {comparison && <CompareReport result={comparison} />}
      </Stack>
    </Paper>
  );
}

/** The power check before a run: enough, or too few with the number needed and a way to use it. */
export function PowerNotice({ plan, onUseTrials }: { plan: DraftTestPlan; onUseTrials: () => void }) {
  return (
    <Alert color={plan.power.enough ? "blue" : "yellow"} title={plan.power.enough ? "Enough trials" : "Too few trials"}>
      <Text size="sm">{plan.power_line}</Text>
      <Text c="dimmed" size="xs">Planning assumption: coefficient of variation {plan.power.cv} ({plan.power.cv_source}).</Text>
      {!plan.power.enough && plan.power.needed_trials !== null && (
        <Button mt="xs" size="xs" variant="light" onClick={onUseTrials}>
          Use {plan.power.needed_trials} trials per task
        </Button>
      )}
    </Alert>
  );
}

/** Every registration of the draft, newest first: a stale or altered one stays listed, marked. */
export function RegistrationList({ registrations }: { registrations: DraftTestRegistration[] }) {
  if (registrations.length === 0) return <Text c="dimmed" size="sm">No registrations of this draft yet.</Text>;
  return (
    <Stack gap="xs">
      {registrations.map((item) => (
        <Paper key={item.registration_id}>
          <Group gap="xs">
            <Text size="sm">Registered checkpoint <Code>{item.revision.slice(0, 12)}</Code>: {item.tasks.length} task(s), {item.repetitions} trial(s) per task, {item.model}, a {(item.effect * 100).toFixed(1)}% change</Text>
            {item.stale && <Badge color="yellow" variant="outline">Stale</Badge>}
            {item.problems.length > 0 && <Badge color="red" variant="outline">Not intact</Badge>}
            {item.used_by && <Badge color="gray" variant="light">Used</Badge>}
          </Group>
          {item.stale_reason && <Text c="dimmed" size="xs">Stale: {item.stale_reason}; register the test again.</Text>}
          {item.problems.map((problem) => <Text c="red" key={problem} size="xs">{problem}</Text>)}
          {item.used_by && <Text c="dimmed" size="xs">Backs run <Code>{item.used_by}</Code>; a registration backs one run.</Text>}
          <Text c="dimmed" size="xs">Plan <Code>{item.plan}</Code> at <Code>{item.plan_commit.slice(0, 12)}</Code></Text>
        </Paper>
      ))}
    </Stack>
  );
}

/** The latest verdict of every tested checkpoint, newest first. */
export function CheckpointList({ verdicts }: { verdicts: DraftTestVerdicts | null }) {
  if (!verdicts) return null;
  if (verdicts.checkpoints.length === 0) return <Text c="dimmed" size="sm">No tests of this draft yet.</Text>;
  return (
    <Stack gap="sm">
      {verdicts.checkpoints.map((entry) => (
        <VerdictCard count={entry.tests} current={entry.current} key={entry.latest.run_id} test={entry.latest} />
      ))}
    </Stack>
  );
}

/** Test the draft against the commit it was based on, as one matched pair, and keep a verdict for
 * each checkpoint. Power and spend are shown before anything starts. */
export function DraftTest({ draft, revision }: Props) {
  const [catalog, setCatalog] = useState<ReplayCatalog | null>(null);
  const [form, setForm] = useState<DraftTestForm>(initialForm("", null));
  const [plan, setPlan] = useState<DraftTestPlan | null>(null);
  const [verdicts, setVerdicts] = useState<DraftTestVerdicts | null>(null);
  const [status, setStatus] = useState("Choose tasks, trials and the change worth detecting.");
  const [busy, setBusy] = useState(false);
  const [preRegister, setPreRegister] = useState(false);
  const errors = validateDraftTest(form);
  const registration = currentRegistration(verdicts);
  const blocked = startBlocked(preRegister, registration);
  const unregistrable = registerBlocked(form);
  // The server's deviations, for the registration this plan named; never recomputed here.
  const deviations = plan?.registration && plan.registration.registration_id === registration?.registration_id
    ? plan.registration.deviations : null;

  useEffect(() => {
    let active = true;
    void loadReplayCatalog().then((value) => {
      if (!active) return;
      setCatalog(value);
      setForm((prior) => ({ ...prior, model: prior.model || value.default_model, pack: startingPack(value, prior.pack) }));
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  // Only the newest refresh may land: a slower, older answer would show an old checkpoint current.
  const sequence = useRef(0);
  async function refresh() {
    const mine = ++sequence.current;
    try {
      const value = await loadDraftVerdicts(draft);
      if (mine === sequence.current) setVerdicts(value);
    } catch (error) {
      if (mine === sequence.current) setStatus(message(error, "Verdicts could not be read."));
    }
  }

  useEffect(() => {
    void refresh();
    return () => { sequence.current += 1; };
  }, [draft, revision]);

  function update(next: DraftTestForm) {
    setForm(next);
    setPlan(null);
  }

  async function check() {
    if (errors.length) return;
    setBusy(true);
    try {
      const under = preRegister && registration ? { registration: registration.registration_id } : {};
      const value = await planDraftTest({ ...planBody(draft, form), ...under });
      setPlan(value);
      setStatus("Power and spend are ready. Nothing has started.");
    } catch (error) {
      setStatus(message(error, "The plan could not be made."));
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    if (!plan?.power.enough) return;
    setBusy(true);
    try {
      await registerDraftTest(registerBody(draft, form));
      setPlan(null);
      setStatus("Registered. It backs one run and is fixed; any edit to the draft makes it stale. Check power and spend again.");
      await refresh();
    } catch (error) {
      setStatus(message(error, "The test could not be registered."));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!plan?.preview.confirmation_token) return;
    setBusy(true);
    try {
      const body = planBody(draft, form);
      const under = preRegister && registration ? registration.registration_id : null;
      const value = await startDraftTest(draft, plan.preview.request, plan.preview.confirmation_token, body.effect, body.cv, under);
      setPlan(null);
      setStatus(startedMessage(value));
      await refresh();
    } catch (error) {
      setStatus(message(error, "The test could not start."));
    } finally {
      setBusy(false);
    }
  }

  const tasks = draftTestTasks(catalog, form.pack);
  return (
    <Paper className="settings-section">
      <Stack gap="md">
        <div>
          <Title order={2}>Test this draft</Title>
          <Text c="dimmed" size="sm">The draft and the commit it was based on run as one pair: the same tasks, model, trials, pack and caps.</Text>
          {verdicts && <Text c="dimmed" size="sm">{verdicts.evidence_note}</Text>}
        </div>
        <Group align="flex-end" grow>
          <TextInput label="Model" value={form.model} disabled={busy}
            onChange={(event) => update({ ...form, model: event.currentTarget.value })} />
          <NumberInput label="Trials per task" min={1} max={20} value={form.repetitions} disabled={busy}
            onChange={(value) => update({ ...form, repetitions: Number(value) })} />
        </Group>
        {catalog && catalog.packs.length > 0 && <Select label="Evaluator pack" data={packOptions(catalog.packs)}
          value={form.pack ? packKey(form.pack) : null} allowDeselect={false} disabled={busy}
          onChange={(key) => update(withPack(form, catalog, key))} />}
        <MultiSelect label="Tasks" data={tasks} value={form.tasks} disabled={busy}
          onChange={(value) => update({ ...form, tasks: value })} />
        <Group align="flex-end" grow>
          <NumberInput label="Change worth detecting (%)" min={1} max={99} value={form.effect_percent} disabled={busy}
            onChange={(value) => update({ ...form, effect_percent: Number(value) })} />
          <TextInput label="Coefficient of variation (optional)" description="Empty uses the repository's declared planning assumption."
            value={form.cv} disabled={busy} onChange={(event) => update({ ...form, cv: event.currentTarget.value })} />
        </Group>
        <Group align="flex-end" grow>
          <TextInput label="Per-run budget (USD)" value={form.max_budget_usd} disabled={busy}
            onChange={(event) => update({ ...form, max_budget_usd: event.currentTarget.value })} />
          <TextInput label="Whole test cap (USD)" value={form.spend_cap_usd} disabled={busy}
            onChange={(event) => update({ ...form, spend_cap_usd: event.currentTarget.value })} />
        </Group>
        <div aria-live="polite" role="group" aria-label="What the test still needs">
          {errors.length > 0 && <ul className="message-list">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
        </div>
        <Text aria-live="polite" c="dimmed" size="sm">{status}</Text>
        {plan && (
          <Stack gap="xs">
            <PowerNotice plan={plan} onUseTrials={() => update(withNeededTrials(form, plan.power))} />
            <Alert color="blue" title="Spend guard">
              Estimate: {plan.preview.estimate.amount_usd === null ? "No matching history" : `$${plan.preview.estimate.amount_usd.toFixed(2)}`}. Cap: ${plan.preview.caps.spend_cap_usd}.
            </Alert>
            <Code block>{plan.preview.command}</Code>
          </Stack>
        )}
        <Checkbox checked={preRegister} disabled={busy} label="Pre-register this test"
          description="Optional. A run that matches its registration exactly may read helped or worse; any other run stays exploratory."
          onChange={(event) => { setPreRegister(event.currentTarget.checked); setPlan(null); }} />
        {preRegister && (
          <Stack gap="xs">
            {registration && deviations === null && <Text c="dimmed" size="sm">Registered. Check power and spend to see whether this test matches it.</Text>}
            {registration && deviations !== null && (
              <Alert color={deviations.length ? "yellow" : "blue"} title={deviations.length ? "Differs from the registration" : "Matches the registration"}>
                {deviations.length
                  ? <ul className="message-list">{deviations.map((item) => <li key={item}>{item}</li>)}</ul>
                  : "This test matches the current registration exactly."}
                {deviations.length > 0 && <Text size="sm">It would run exploratory.</Text>}
              </Alert>
            )}
            {!registration && <Text c="dimmed" size="sm">No current registration. Check power, then register before you start.</Text>}
            {form.cv.trim() !== "" && <Text c="dimmed" size="sm">A registration plans from the repository's declared variance; clear the coefficient of variation to register.</Text>}
            {unregistrable && <Text c="dimmed" size="sm">{unregistrable}</Text>}
            <Group justify="flex-end">
              <Button variant="light" disabled={busy || !plan?.power.enough || form.cv.trim() !== "" || unregistrable !== null} loading={busy} onClick={register}>Register this test</Button>
            </Group>
            {verdicts && <RegistrationList registrations={verdicts.registrations} />}
          </Stack>
        )}
        {/* Always mounted, so a screen reader announces the text when it changes. */}
        <Text aria-live="polite" c="dimmed" size="sm">{blocked ?? ""}</Text>
        <Group justify="flex-end">
          <Button variant="light" disabled={busy || errors.length > 0} loading={busy} onClick={check}>Check power and spend</Button>
          <Button disabled={busy || blocked !== null || !plan?.preview.valid || !plan.preview.confirmation_token} loading={busy} onClick={start}>Confirm and test</Button>
        </Group>
        <Group justify="space-between">
          <Title order={3}>Verdicts by checkpoint</Title>
          <Button size="xs" variant="subtle" onClick={() => { void refresh(); }}>Refresh</Button>
        </Group>
        <CheckpointList verdicts={verdicts} />
      </Stack>
    </Paper>
  );
}
