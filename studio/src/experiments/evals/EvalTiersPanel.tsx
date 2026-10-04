import { Alert, Badge, Button, Code, Group, Paper, Select, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";

import { StatusBadge } from "../../components/StudioKit";
import { pollEvalRun, previewPaidTier, startFreeTier, startPaidTier, type EvalRunState } from "./api";
import {
  isHookMatrix, isPaidAnalysis, paidAnalysisLines, paidInput, tierById,
  type EvalCatalog, type EvalPreview, type EvalRunResult, type HookMatrixResult, type PaidAnalysis, type PaidTierInput,
} from "./model";

export type { EvalRunState } from "./api";

/** The run's state for a component: `pollEvalRun`, started and stopped with the run id. */
export function useEvalRun(runId: string): EvalRunState {
  const [state, setState] = useState<EvalRunState>({ result: null, error: "" });
  useEffect(() => (runId ? pollEvalRun(runId, setState) : undefined), [runId]);
  return state;
}

export function HookMatrixGrid({ result, initialRow }: { result: HookMatrixResult; initialRow?: string }) {
  const [row, setRow] = useState(initialRow ?? result.rows[0] ?? "");
  const grid = result.grid[row] ?? [];
  return (
    <Stack gap="sm">
      {result.moved.length > 0
        ? <Alert color="orange" title={`${result.moved.length} cell(s) moved from the committed matrix`}>
            <Code block>{result.moved.join("\n")}</Code>
          </Alert>
        : <Text c="dimmed" size="sm">Every cell matches the committed matrix.</Text>}
      <Select data={result.rows} label="Hook" onChange={(value) => setRow(value ?? "")} value={row || null} />
      <Table.ScrollContainer minWidth={900} scrollAreaProps={{ viewportProps: { role: "region", "aria-label": "Hook matrix", tabIndex: 0 } }}>
        <Table aria-label={`Hook matrix for ${row}`} striped withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Recorded call</Table.Th>{result.variants.map((variant) => <Table.Th key={variant}>{variant}</Table.Th>)}</Table.Tr></Table.Thead>
          <Table.Tbody>{grid.map((line) => <Table.Tr key={line.call}>
            <Table.Td><Code>{line.call}</Code></Table.Td>
            {line.cells.map((cell, index) => <Table.Td data-cell={`${row}|${line.call}|${result.variants[index]}`} key={result.variants[index]}>{cell}</Table.Td>)}
          </Table.Tr>)}</Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}

export function PaidAnalysisView({ analysis, title }: { analysis: PaidAnalysis; title: string }) {
  return (
    <Paper p="md" withBorder>
      <Text fw={600}>{title} (cost_bench.py summarise)</Text>
      <Text c="dimmed" size="xs">Exploratory: writes no history row and is never cited as evidence. Spent {analysis.spend_usd} USD{analysis.stopped_at_cap ? ", stopped at the cap" : ""}.</Text>
      <Code block>{analysis.command.join(" ")}</Code>
      <Table><Table.Tbody>{paidAnalysisLines(analysis).map(([label, value]) => <Table.Tr key={label}>
        <Table.Td><Code>{label}</Code></Table.Td><Table.Td>{value}</Table.Td>
      </Table.Tr>)}</Table.Tbody></Table>
    </Paper>
  );
}

export function EvalResultView({ result, error = "", initialRow }: { result: EvalRunResult | null; error?: string; initialRow?: string }) {
  const value = result?.result ?? null;
  return (
    <Stack gap="sm">
      {result && <Group gap="xs"><Text size="sm">Run {result.run.run_id}</Text><StatusBadge>{result.run.status}</StatusBadge></Group>}
      {error && <Alert color="red" title="Result unavailable">{error}</Alert>}
      {result?.analysis_error && <Alert color="yellow" title="Engine result unknown">{result.analysis_error}</Alert>}
      {isHookMatrix(value) && <HookMatrixGrid initialRow={initialRow} result={value} />}
      {isPaidAnalysis(value) && <PaidAnalysisView analysis={value} title={result?.run.suite === "unit-eval" ? "Unit eval" : "Micro tier"} />}
      {value && !isHookMatrix(value) && !isPaidAnalysis(value) && <Code block>{JSON.stringify(value, null, 2)}</Code>}
    </Stack>
  );
}

/** Target, caps, a spend preview and an explicit confirmation, for the micro tier or a unit eval. */
export function PaidTierForm({ suite, unit, catalog, onStarted }: {
  suite: PaidTierInput["suite"]; unit?: string; catalog: EvalCatalog; onStarted: (runId: string) => void;
}) {
  const tier = tierById(catalog, suite);
  const [kind, setKind] = useState("installed");
  const [ref, setRef] = useState("");
  const [maxBudget, setMaxBudget] = useState("");
  const [spendCap, setSpendCap] = useState("");
  const [preview, setPreview] = useState<EvalPreview | null>(null);
  const [error, setError] = useState("");
  if (!tier) return null;
  const input = paidInput(suite, { kind, ref, unit, maxBudget, spendCap });
  const reset = () => { setPreview(null); setError(""); };
  return (
    <Stack gap="sm">
      <Group grow>
        <Select data={tier.target_kinds} label="Target kind" onChange={(value) => { setKind(value ?? "installed"); reset(); }} value={kind} />
        <TextInput label="Target reference" onChange={(event) => { setRef(event.currentTarget.value); reset(); }} placeholder="Branch, tag, path or draft" value={ref} />
      </Group>
      {suite === "unit-eval" && <Text c="dimmed" size="xs">Model {catalog.unit_model}, with the engine's default tasks and trials.</Text>}
      <Group grow>
        <TextInput description="--max-budget-usd, each run" label="Per-run cap (USD)" onChange={(event) => { setMaxBudget(event.currentTarget.value); reset(); }} value={maxBudget} />
        <TextInput description="--spend-cap, the whole run" label="Spend cap (USD)" onChange={(event) => { setSpendCap(event.currentTarget.value); reset(); }} value={spendCap} />
      </Group>
      <Group>
        <Button disabled={!ref.trim() || !maxBudget.trim() || !spendCap.trim()} variant="light" onClick={() => {
          setError("");
          void previewPaidTier(input).then(setPreview).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Preview failed."));
        }}>Preview spend</Button>
        {preview && <Button color="orange" onClick={() => {
          void startPaidTier(preview.request, preview.confirmation_token).then((started) => { setPreview(null); onStarted(started.run_id); })
            .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Start failed."));
        }}>Confirm and spend up to {preview.caps.spend_cap_usd} USD</Button>}
      </Group>
      {preview && <Paper p="sm" withBorder>
        <Text size="sm">Revision <Code>{preview.request.revision.slice(0, 12)}</Code> · estimate {preview.estimate.amount_usd === null ? "unknown (no history)" : `${preview.estimate.amount_usd} USD`} · {preview.pricing.source}</Text>
        <Text c="dimmed" size="xs">Exploratory: writes no history row.</Text>
        <Code block>{preview.command}</Code>
      </Paper>}
      {error && <Alert color="red" title="Refused">{error}</Alert>}
    </Stack>
  );
}

export function EvalTiersPanel({ catalog }: { catalog: EvalCatalog }) {
  const [runId, setRunId] = useState("");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");
  const run = useEvalRun(runId);
  const startFree = (suite: string, rawDirectory?: string) => {
    setError("");
    void startFreeTier(suite, rawDirectory).then((started) => setRunId(started.run_id))
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Start failed."));
  };
  return (
    <Paper p="xl" withBorder>
      <Stack gap="md">
        <div><Text className="eyebrow">Evaluation tiers</Text><Title order={2}>Run an engine, read its own result.</Title></div>
        {catalog.tiers.length === 0 && <Text c="dimmed" size="sm">No evaluation engine is in this checkout.</Text>}
        {catalog.tiers.map((tier) => <Paper key={tier.id} p="md" withBorder>
          <Stack gap="xs">
            <Group justify="space-between"><Text fw={600}>{tier.label}</Text><Badge color={tier.cost_class === "free" ? "green" : "orange"}>{tier.cost_class === "free" ? "Free" : "Spends usage"}</Badge></Group>
            <Text c="dimmed" size="sm">{tier.description}</Text>
            <Code>{tier.command}</Code>
            {tier.id === "hook-matrix" && <Group><Button variant="light" onClick={() => startFree("hook-matrix")}>Run the hook matrix</Button></Group>}
            {tier.id === "rule-detection" && <Group align="flex-end" grow>
              <TextInput description="Its top-level <task>-<arm>-<rep>.json files are copied and read" label="Saved raw directory" onChange={(event) => setRaw(event.currentTarget.value)} placeholder="/absolute/path/to/raw" value={raw} />
              <Button disabled={!raw.startsWith("/")} variant="light" onClick={() => startFree("rule-detection", raw)}>Detect offline</Button>
            </Group>}
            {tier.id === "micro-tier" && <PaidTierForm catalog={catalog} suite="micro-tier" onStarted={setRunId} />}
            {tier.id === "unit-eval" && <Text size="sm">Start a unit eval from a rule's page in the Library: "Test this rule".</Text>}
          </Stack>
        </Paper>)}
        {error && <Alert color="red" title="Refused">{error}</Alert>}
        {(run.result || run.error) && <EvalResultView error={run.error} result={run.result} />}
      </Stack>
    </Paper>
  );
}

/** What "Test this rule" shows on the rule's page: the launch form when open, then the run's result. */
export function TestThisRuleView({ unit, catalog, open, onToggle, onStarted, run }: {
  unit: string; catalog: EvalCatalog; open: boolean; onToggle: () => void;
  onStarted: (runId: string) => void; run: EvalRunState;
}) {
  return (
    <Stack gap="sm">
      <Group><Button variant="light" onClick={onToggle}>Test this rule</Button><Text c="dimmed" size="xs">Unit eval of <Code>{unit}</Code>, alone and with the economy concern.</Text></Group>
      {open && <PaidTierForm catalog={catalog} suite="unit-eval" unit={unit} onStarted={onStarted} />}
      {(run.result || run.error) && <EvalResultView error={run.error} result={run.result} />}
    </Stack>
  );
}

/** The library's "Test this rule": the unit's two-by-two against a chosen target, shown on the rule's page. */
export function TestThisRule({ unit, catalog }: { unit: string; catalog: EvalCatalog }) {
  const [open, setOpen] = useState(false);
  const [runId, setRunId] = useState("");
  const run = useEvalRun(runId);
  return <TestThisRuleView catalog={catalog} onStarted={setRunId} onToggle={() => setOpen(!open)} open={open} run={run} unit={unit} />;
}
