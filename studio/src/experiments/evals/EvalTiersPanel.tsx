import { Alert, Badge, Button, Code, Group, NumberInput, Paper, Select, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";

import { StatusBadge } from "../../components/StudioKit";
import { loadEvalResult, previewPaidTier, startFreeTier, startPaidTier } from "./api";
import {
  hookGrid, isHookMatrix, isPaidAnalysis, isTerminal, paidAnalysisLines, paidInput, tierById,
  type EvalCatalog, type EvalPreview, type EvalRunResult, type HookMatrixResult, type PaidAnalysis, type PaidTierInput,
} from "./model";

const POLL_MS = 1500;

/** Poll one tier run until it ends; the engine's result arrives with the terminal status. */
export function useEvalRun(runId: string): EvalRunResult | null {
  const [result, setResult] = useState<EvalRunResult | null>(null);
  useEffect(() => {
    if (!runId) return undefined;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      void loadEvalResult(runId).then((value) => {
        if (stopped) return;
        setResult(value);
        if (!isTerminal(value.run.status)) timer = setTimeout(tick, POLL_MS);
      }).catch(() => { if (!stopped) timer = setTimeout(tick, POLL_MS * 2); });
    };
    setResult(null);
    tick();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [runId]);
  return result;
}

export function HookMatrixGrid({ result }: { result: HookMatrixResult }) {
  const [row, setRow] = useState(result.matrix.rows[0] ?? "");
  const grid = hookGrid(result.matrix, row, result.base, result.same);
  return (
    <Stack gap="sm">
      {result.moved.length > 0
        ? <Alert color="orange" title={`${result.moved.length} cell(s) moved from the committed matrix`}>
            <Code block>{result.moved.join("\n")}</Code>
          </Alert>
        : <Text c="dimmed" size="sm">Every cell matches the committed matrix.</Text>}
      <Select aria-label="Hook" data={result.matrix.rows} onChange={(value) => setRow(value ?? "")} value={row || null} />
      <Table.ScrollContainer minWidth={900}>
        <Table aria-label={`Hook matrix for ${row}`} striped withTableBorder>
          <Table.Thead><Table.Tr><Table.Th>Recorded call</Table.Th>{result.matrix.variants.map((variant) => <Table.Th key={variant}>{variant}</Table.Th>)}</Table.Tr></Table.Thead>
          <Table.Tbody>{grid.map((line) => <Table.Tr key={line.call}>
            <Table.Td><Code>{line.call}</Code></Table.Td>
            {line.cells.map((cell, index) => <Table.Td key={result.matrix.variants[index]}>{cell}</Table.Td>)}
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

export function EvalResultView({ result }: { result: EvalRunResult }) {
  const value = result.result;
  return (
    <Stack gap="sm">
      <Group gap="xs"><Text size="sm">Run {result.run.run_id}</Text><StatusBadge>{result.run.status}</StatusBadge></Group>
      {result.analysis_error && <Alert color="yellow" title="Engine result unknown">{result.analysis_error}</Alert>}
      {isHookMatrix(value) && <HookMatrixGrid result={value} />}
      {isPaidAnalysis(value) && <PaidAnalysisView analysis={value} title={result.run.suite === "unit-eval" ? "Unit eval" : "Micro tier"} />}
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
  const [model, setModel] = useState(catalog.default_model);
  const [repetitions, setRepetitions] = useState(catalog.default_repetitions);
  const [maxBudget, setMaxBudget] = useState("");
  const [spendCap, setSpendCap] = useState("");
  const [preview, setPreview] = useState<EvalPreview | null>(null);
  const [error, setError] = useState("");
  if (!tier) return null;
  const input = paidInput(suite, { kind, ref, unit, model, repetitions, maxBudget, spendCap });
  const reset = () => { setPreview(null); setError(""); };
  return (
    <Stack gap="sm">
      <Group grow>
        <Select aria-label="Target kind" data={tier.target_kinds} onChange={(value) => { setKind(value ?? "installed"); reset(); }} value={kind} />
        <TextInput aria-label="Target reference" onChange={(event) => { setRef(event.currentTarget.value); reset(); }} placeholder="Branch, tag, path or draft" value={ref} />
      </Group>
      {suite === "unit-eval" && <Group grow>
        <TextInput aria-label="Model" onChange={(event) => { setModel(event.currentTarget.value); reset(); }} value={model} />
        <NumberInput aria-label="Trials per task and arm" max={20} min={1} onChange={(value) => { setRepetitions(Number(value) || 1); reset(); }} value={repetitions} />
      </Group>}
      <Group grow>
        <TextInput aria-label="Per-run cap (USD)" onChange={(event) => { setMaxBudget(event.currentTarget.value); reset(); }} placeholder="Per-run cap, USD" value={maxBudget} />
        <TextInput aria-label="Spend cap (USD)" onChange={(event) => { setSpendCap(event.currentTarget.value); reset(); }} placeholder="Whole-run spend cap, USD" value={spendCap} />
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
  const result = useEvalRun(runId);
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
            {tier.id === "rule-detection" && <Group grow>
              <TextInput aria-label="Saved raw directory" onChange={(event) => setRaw(event.currentTarget.value)} placeholder="/absolute/path/to/raw" value={raw} />
              <Button disabled={!raw.startsWith("/")} variant="light" onClick={() => startFree("rule-detection", raw)}>Detect offline</Button>
            </Group>}
            {tier.id === "micro-tier" && <PaidTierForm catalog={catalog} suite="micro-tier" onStarted={setRunId} />}
            {tier.id === "unit-eval" && <Text size="sm">Start a unit eval from a rule's page in the Library: "Test this rule".</Text>}
          </Stack>
        </Paper>)}
        {error && <Alert color="red" title="Refused">{error}</Alert>}
        {result && <EvalResultView result={result} />}
      </Stack>
    </Paper>
  );
}

/** The library's "Test this rule": the unit's two-by-two against a chosen target, shown on the rule's page. */
export function TestThisRule({ unit, catalog }: { unit: string; catalog: EvalCatalog }) {
  const [open, setOpen] = useState(false);
  const [runId, setRunId] = useState("");
  const result = useEvalRun(runId);
  return (
    <Stack gap="sm">
      <Group><Button variant="light" onClick={() => setOpen(!open)}>Test this rule</Button><Text c="dimmed" size="xs">Unit eval of <Code>{unit}</Code>, alone and with the economy concern.</Text></Group>
      {open && <PaidTierForm catalog={catalog} suite="unit-eval" unit={unit} onStarted={setRunId} />}
      {result && <EvalResultView result={result} />}
    </Stack>
  );
}
