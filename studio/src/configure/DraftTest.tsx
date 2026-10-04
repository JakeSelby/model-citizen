import {
  Alert, Badge, Button, Code, Group, MultiSelect, NumberInput, Paper, Select, Stack, Text, TextInput, Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CommandChip } from "../components/StudioKit";
import { CompareReport } from "../experiments/compare/ComparePanel";
import { compareRuns } from "../experiments/compare/api";
import type { CompareResult } from "../experiments/compare/model";
import { loadReplayCatalog } from "../experiments/replay/api";
import { initialPack, packOptions, tasksFor, type ReplayCatalog } from "../experiments/replay/model";
import { loadDraftVerdicts, planDraftTest, startDraftTest } from "./draftTestApi";
import {
  draftTestErrorMessage, initialForm, planBody, readingLines, spendLine, validateDraftTest, verdictBadge,
  withNeededTrials, type DraftTestForm, type DraftTestPlan, type DraftTestVerdict, type DraftTestVerdicts,
} from "./draftTestModel";

type Props = { draft: string; revision: string };

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? draftTestErrorMessage(error.message) : fallback;
}

/** One checkpoint's latest verdict: badge, headline, staleness, readings, spend and the comparison. */
export function VerdictCard({ test, current, count }: { test: DraftTestVerdict; current: boolean; count: number }) {
  const badge = verdictBadge(test.verdict);
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
    <Paper p="md" withBorder>
      <Stack gap="xs">
        <Group gap="xs">
          <Text fw={650} size="sm">Checkpoint <Code>{test.revision.slice(0, 12)}</Code>{current ? " (current)" : ""}</Text>
          <Badge color={badge.color} variant={badge.variant}>{badge.label}</Badge>
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
  const errors = validateDraftTest(form);

  useEffect(() => {
    let active = true;
    void loadReplayCatalog().then((value) => {
      if (!active) return;
      setCatalog(value);
      const pack = initialPack(value.packs, value.default_pack);
      setForm((prior) => ({ ...prior, model: prior.model || value.default_model,
        pack: prior.pack ?? (pack ? { name: pack.name, digest: pack.digest } : null) }));
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
      const value = await planDraftTest(planBody(draft, form));
      setPlan(value);
      setStatus("Power and spend are ready. Nothing has started.");
    } catch (error) {
      setStatus(message(error, "The plan could not be made."));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!plan?.preview.confirmation_token) return;
    setBusy(true);
    try {
      const body = planBody(draft, form);
      const value = await startDraftTest(draft, plan.preview.request, plan.preview.confirmation_token, body.effect, body.cv);
      setPlan(null);
      setStatus(`Test started as run ${value.run_id}. Refresh to follow its verdict.`);
      await refresh();
    } catch (error) {
      setStatus(message(error, "The test could not start."));
    } finally {
      setBusy(false);
    }
  }

  const tasks = catalog ? tasksFor(catalog.packs, form.pack?.digest ?? null, catalog.tasks.map((task) => task.id)) : [];
  return (
    <Paper className="settings-section" p="xl" withBorder>
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
          value={form.pack?.digest ?? null} allowDeselect={false} disabled={busy}
          onChange={(digest) => {
            const pack = catalog.packs.find((item) => item.digest === digest);
            if (pack) update({ ...form, pack: { name: pack.name, digest: pack.digest }, tasks: [] });
          }} />}
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
        <Group justify="flex-end">
          <Button variant="light" disabled={busy || errors.length > 0} loading={busy} onClick={check}>Check power and spend</Button>
          <Button disabled={busy || !plan?.preview.valid || !plan.preview.confirmation_token} loading={busy} onClick={start}>Confirm and test</Button>
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
