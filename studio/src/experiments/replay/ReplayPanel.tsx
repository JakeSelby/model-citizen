import {
  Alert, Button, Code, Group, MultiSelect, NumberInput, Paper, Select, Stack,
  Table, Text, TextInput, Title,
} from "@mantine/core";
import { useReducer, useRef, useState } from "react";

import { previewReplay, startReplay } from "./api";
import {
  analysisLines, comparisonLine, formatCost, formatPercent, initialPack, packKey, packOptions, packSelection, samplingLines, progressResult, readinessSummary,
  confirmableToken, failureMessage, launchKind, previewReducer, replayErrorMessage, tasksFor, validateReplay, type ReplayPack,
  SOURCE_ONLY_NOTE,
  ReplayRequestGate, type DraftComparison, type ReplayAnalysis, type ReplayLaunchInput, type ReplayMetricRow, type ReplayPreview,
  type ReplayProgressRow,
  type ReplayTargetKind,
} from "./model";

const targetKinds: ReplayTargetKind[] = ["installed", "release", "branch", "worktree", "draft"];

type Props = {
  tasks: string[];
  packs?: ReplayPack[];
  defaultPack?: string | null;
  defaultModel?: string;
  rows?: ReplayMetricRow[];
  analysis?: ReplayAnalysis[] | null;
  analysisError?: string | null;
  comparisons?: DraftComparison[];
  progress?: ReplayProgressRow[];
  runStatus?: string;
  onStarted?: (runId: string) => void;
};

/** The not-ready list without an assertive alert role: only the stable count is announced, and
 * politely, so screen readers do not re-read the list on every keystroke. */
export function ReplayReadiness({ errors }: { errors: string[] }) {
  return (
    <Alert color="yellow" role="group" aria-label="Replay readiness">
      <Text aria-live="polite" aria-atomic="true" fw={600} size="sm">{readinessSummary(errors)}</Text>
      <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>
    </Alert>
  );
}

export function ReplayPanel({ tasks, packs = [], defaultPack = null, defaultModel = "", rows = [], analysis = null, analysisError = null, comparisons = [], progress = [], runStatus, onStarted }: Props) {
  const startingPack = initialPack(packs, defaultPack);
  const [draft, setDraft] = useState<ReplayLaunchInput>({
    targets: [{ kind: "release", ref: "" }, { kind: "draft", ref: "" }],
    model: defaultModel, repetitions: 2, tasks: [], max_budget_usd: "2", spend_cap_usd: "20",
    pre_registration: "",
    pack: startingPack ? packSelection(startingPack) : null,
  });
  const choices = tasksFor(packs, draft.pack ? packKey(draft.pack) : null, tasks);
  const [slot, dispatch] = useReducer(previewReducer, { preview: null, generation: 0 });
  const preview: ReplayPreview | null = slot.preview;
  const token = confirmableToken(slot);
  const [message, setMessage] = useState("Choose two targets, tasks and one model.");
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const requestGate = useRef(new ReplayRequestGate());
  const errors = validateReplay(draft);

  function updateDraft(next: ReplayLaunchInput) {
    if (!requestGate.current.beginEdit()) return;
    setDraft(next);
    setTouched(true);
    dispatch({ type: "clear" });
    setBusy(false);
  }

  function target(index: 0 | 1, field: "kind" | "ref", value: string) {
    const targets = [...draft.targets] as ReplayLaunchInput["targets"];
    targets[index] = { ...targets[index], [field]: value };
    updateDraft({ ...draft, targets });
  }

  async function estimate(definitive = false) {
    if (errors.length || requestGate.current.paidBusy()) return;
    const generation = requestGate.current.next();
    dispatch({ type: "begin", generation });
    setBusy(true);
    try {
      // The definitive action is the only place the flag is set; the server holds such a launch
      // to the engine's registered budget, and the flag rides through to start in the request.
      const value = await previewReplay(definitive ? { ...draft, definitive: true } : draft);
      if (!requestGate.current.accepts(generation)) return;
      dispatch({ type: "loaded", generation, value });
      setMessage(value.valid ? "Estimate and caps are ready for confirmation." : "Nothing started.");
    } catch (error) {
      dispatch({ type: "failed", generation });
      if (!requestGate.current.accepts(generation)) return;
      setMessage(failureMessage(error, "Replay preview failed."));
    } finally {
      if (requestGate.current.accepts(generation)) setBusy(false);
    }
  }

  async function start() {
    if (!preview || !token) return;
    const generation = requestGate.current.beginPaid();
    if (generation === null) return;
    setBusy(true);
    try {
      const value = await startReplay(preview.request, token);
      if (!requestGate.current.acceptsPaid(generation)) return;
      setMessage("Replay queued. Its two target records and native rows will be kept.");
      onStarted?.(value.run_id);
    } catch (error) {
      if (!requestGate.current.acceptsPaid(generation)) return;
      setMessage(failureMessage(error, "Replay could not start."));
    } finally {
      if (requestGate.current.finishPaid(generation)) setBusy(false);
    }
  }

  return (
    <Stack gap="xl">
      <div>
        <Text className="eyebrow">Experiments / Live replay</Text>
        <Title order={2}>Measure two explicit targets.</Title>
        <Text c="dimmed">Each target is built in its own isolated profile. The installed harness is not an implicit fallback.</Text>
        <Text c="dimmed" size="sm">{SOURCE_ONLY_NOTE}</Text>
      </div>
      <Paper>
        <Stack>
          {([0, 1] as const).map((index) => (
            <Group align="flex-end" grow key={index}>
              <Select label={`Target ${index + 1} kind`} data={targetKinds} value={draft.targets[index].kind}
                disabled={busy}
                onChange={(value) => target(index, "kind", value ?? "draft")} />
              <TextInput label={`Target ${index + 1} reference`} value={draft.targets[index].ref}
                disabled={busy}
                onChange={(event) => target(index, "ref", event.currentTarget.value)} />
            </Group>
          ))}
          <Group align="flex-end" grow>
            <TextInput label="Model" value={draft.model}
              disabled={busy}
              onChange={(event) => updateDraft({ ...draft, model: event.currentTarget.value })} />
            <NumberInput label="Repetitions" min={1} max={20} value={draft.repetitions}
              disabled={busy}
              onChange={(value) => updateDraft({ ...draft, repetitions: Number(value) })} />
          </Group>
          {packs.length > 0 && <Select label="Evaluator pack" data={packOptions(packs)}
            value={draft.pack ? packKey(draft.pack) : null} allowDeselect={false} disabled={busy}
            onChange={(key) => {
              const pack = packs.find((item) => packKey(item) === key);
              if (pack) updateDraft({ ...draft, pack: packSelection(pack), tasks: [] });
            }} />}
          <MultiSelect label="Tasks" data={choices} value={draft.tasks}
            disabled={busy}
            onChange={(value) => updateDraft({ ...draft, tasks: value })} />
          <Group align="flex-end" grow>
            <TextInput label="Per-run budget (USD)" value={draft.max_budget_usd}
              disabled={busy}
              onChange={(event) => updateDraft({ ...draft, max_budget_usd: event.currentTarget.value })} />
            <TextInput label="Whole replay cap (USD)" value={draft.spend_cap_usd}
              disabled={busy}
              onChange={(event) => updateDraft({ ...draft, spend_cap_usd: event.currentTarget.value })} />
          </Group>
          <TextInput label="Pre-registration" description="Required when either target is a release; only a release target enters benchmark history."
            value={draft.pre_registration} disabled={busy}
            onChange={(event) => updateDraft({ ...draft, pre_registration: event.currentTarget.value })} />
          {touched && errors.length > 0 && <ReplayReadiness errors={errors} />}
          <Text aria-live="polite" c="dimmed" size="sm">{message}</Text>
          {preview
            ? <Stack gap={4}>
                <Text size="sm">Native commands, one per target. The Studio gives target two only what target one left of the cap.</Text>
                <Code block className="wrapped-command">{preview.command}</Code>
              </Stack>
            : <Text c="dimmed" size="sm">The native benchmark commands appear once the preview resolves both targets.</Text>}
          <Group justify="flex-end">
            <Button disabled={busy || errors.length > 0} loading={busy} variant="light" onClick={() => void estimate()}>Preview spend</Button>
            <Button disabled={busy || errors.length > 0} loading={busy} variant="subtle" onClick={() => void estimate(true)}>Preview definitive evaluation</Button>
            <Button disabled={busy || token === null} loading={busy} onClick={start}>Confirm and run</Button>
          </Group>
        </Stack>
      </Paper>
      {preview && (
        <Stack gap="sm">
          <Alert color="blue" title="Spend guard">
            {launchKind(preview)}. Estimate: {preview.estimate.amount_usd === null ? "No matching history" : `$${preview.estimate.amount_usd.toFixed(2)}`}. Cap: ${preview.caps.spend_cap_usd}.
          </Alert>
          {samplingLines(preview.sampling).length > 0 && <Alert color={preview.sampling?.evidence === "pre-registered" ? "teal" : "yellow"} title={preview.sampling?.evidence === "pre-registered" ? "Pre-registered sample" : "Exploratory run"}>
            {samplingLines(preview.sampling).map((line) => <Text key={line} size="sm">{line}</Text>)}
          </Alert>}
          <Paper>
            <Text fw={600}>Resolved target revisions</Text>
            {preview.request.targets.map((target) => (
              <Text key={`${target.kind}:${target.ref}`} size="sm">
                {target.kind} <Code>{target.ref}</Code> at <Code>{target.revision}</Code>
                {target.config_digest ? <> · config <Code>{target.config_digest}</Code></> : null}
              </Text>
            ))}
          </Paper>
        </Stack>
      )}
      {runStatus && <Alert color={runStatus === "succeeded" ? "teal" : "blue"} title="Replay status">
        {runStatus === "succeeded" ? "Replay complete. Native rows are indexed below." : runStatus}
      </Alert>}
      {progress.length > 0 && <Table.ScrollContainer minWidth={720} type="native" role="region" aria-label="Live replay progress" tabIndex={0}>
        <Table striped>
          <Table.Caption>Live progress by target, task, repetition and arm</Table.Caption>
          <Table.Thead><Table.Tr><Table.Th>Target</Table.Th><Table.Th>Task</Table.Th><Table.Th>Rep</Table.Th><Table.Th>Arm</Table.Th><Table.Th>Status</Table.Th><Table.Th>Result</Table.Th><Table.Th>Cost</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{progress.map((row) => <Table.Tr key={`${row.target.kind}:${row.target.ref}:${row.task}:${row.repetition}:${row.arm}`}>
            <Table.Td>{row.target.ref}</Table.Td><Table.Td>{row.task}</Table.Td><Table.Td>{row.repetition}</Table.Td><Table.Td>{row.arm}</Table.Td><Table.Td>{row.status}</Table.Td>
            <Table.Td>{progressResult(row)}</Table.Td><Table.Td>{formatCost(row.cost_usd)}</Table.Td>
          </Table.Tr>)}</Table.Tbody>
        </Table>
      </Table.ScrollContainer>}
      {rows.length > 0 && (
        <Table.ScrollContainer minWidth={720} type="native" role="region" aria-label="Cost and pass rate" tabIndex={0}>
          <Table striped highlightOnHover>
            <Table.Caption>Cost and pass rate by target, task and arm. Source only: target configuration was not applied.</Table.Caption>
            <Table.Thead><Table.Tr><Table.Th>Target</Table.Th><Table.Th>Task</Table.Th><Table.Th>Arm</Table.Th><Table.Th>Cost per passed task</Table.Th><Table.Th>Pass rate</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{rows.map((row) => <Table.Tr key={`${row.target.kind}:${row.target.ref}:${row.task}:${row.arm}`}>
              <Table.Td>{row.target.ref}</Table.Td><Table.Td>{row.task}</Table.Td><Table.Td>{row.arm}</Table.Td>
              <Table.Td>{formatCost(row.cost_per_passed)}</Table.Td><Table.Td>{formatPercent(row.pass_rate)}</Table.Td>
            </Table.Tr>)}</Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {analysisError && <Alert color="yellow" title="Engine analysis unknown">{analysisError}</Alert>}
      {analysis && analysis.map((entry) => <Paper key={entry.target}>
        <Text fw={600}>Engine analysis, target {entry.target} (cost_bench.py summarise)</Text>
        <Table className="leaf-table"><Table.Tbody>{analysisLines(entry).map(([label, value]) => <Table.Tr key={label}>
          <Table.Th scope="row">{label}</Table.Th><Table.Td><Code>{value}</Code></Table.Td></Table.Tr>)}</Table.Tbody></Table>
      </Paper>)}
      {comparisons.length > 0 && <Paper>
        <Text fw={600}>Matched draft comparisons</Text>
        {comparisons.map((item) => <Text key={item.key + item.draft} size="sm">{comparisonLine(item)}</Text>)}
      </Paper>}
    </Stack>
  );
}
