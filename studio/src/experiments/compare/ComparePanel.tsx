import {
  Alert, Badge, Button, Code, Group, Paper, Select, SimpleGrid, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { useState } from "react";

import { analysisLines, engineLeaves } from "../replay/model";
import { compareRuns } from "./api";
import {
  compareErrorMessage, measureHeadlines, sideLine, validateCompare,
  type CompareInput, type CompareResult, type CompareSide,
} from "./model";

const EMPTY: CompareInput = { base: { run_id: "", target: 1 }, candidate: { run_id: "", target: 2 } };

function readingColor(reading: string): string {
  return reading === "inconclusive" || reading === "unavailable" ? "gray" : "blue";
}

function SideColumn({ title, side }: { title: string; side: CompareSide }) {
  return (
    <Paper p="md" withBorder>
      <Stack gap="xs">
        <Text fw={600}>{title}</Text>
        <Text size="sm">{sideLine(side)}</Text>
        {side.stale && <Alert color="yellow" title="Stale">Stale: {side.stale_reason ?? "the draft changed"} after this run.</Alert>}
        {side.analysis
          ? <Table><Table.Caption>This target against its own bare arm (cost_bench.py summarise)</Table.Caption>
              <Table.Tbody>{analysisLines(side.analysis).map(([label, value]) => <Table.Tr key={label}>
                <Table.Th scope="row">{label}</Table.Th><Table.Td><Code>{value}</Code></Table.Td></Table.Tr>)}</Table.Tbody></Table>
          : <Text c="dimmed" size="sm">No engine analysis was recorded for this target.</Text>}
      </Stack>
    </Paper>
  );
}

/** The comparison as the engine reported it: refusals, readings with their intervals and trial
 * counts, both sides' identities and recorded analyses, and every field of the engine's output. */
export function CompareReport({ result }: { result: CompareResult }) {
  const headlines = measureHeadlines(result.result);
  return (
    <Stack gap="md">
      {!result.comparable && <Alert color="red" title="Not compared">
        <ul>{result.refusals.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </Alert>}
      {result.error !== null && <Alert color="yellow" title="Engine refused">{result.error}</Alert>}
      {headlines.length > 0 && <Table.ScrollContainer minWidth={720} type="native">
        <Table striped>
          <Table.Caption>{`${headlines[0].arm} against ${result.control}, paired by task (${result.engine}). Each reading is the engine's; an interval spanning no effect reads inconclusive.`}</Table.Caption>
          <Table.Tbody>{headlines.map((item) => <Table.Tr key={`${item.arm}:${item.key}`}>
            <Table.Td><Badge color={readingColor(item.reading)} variant="light">{item.reading}</Badge></Table.Td>
            <Table.Td><Text size="sm">{item.line}</Text></Table.Td>
          </Table.Tr>)}</Table.Tbody>
        </Table>
      </Table.ScrollContainer>}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <SideColumn title="Base" side={result.base} />
        <SideColumn title="Candidate" side={result.candidate} />
      </SimpleGrid>
      {result.result !== null && <Paper p="md" withBorder>
        <Text fw={600}>Engine output ({result.engine})</Text>
        <Table><Table.Tbody>{engineLeaves(result.result).map(([label, value]) => <Table.Tr key={label}>
          <Table.Th scope="row">{label}</Table.Th><Table.Td><Code>{value}</Code></Table.Td></Table.Tr>)}</Table.Tbody></Table>
      </Paper>}
    </Stack>
  );
}

export function ComparePanel({ initial = null }: { initial?: CompareInput | null }) {
  const [input, setInput] = useState<CompareInput>(initial ?? EMPTY);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [message, setMessage] = useState("Choose two finished replay targets that ran the same tasks, model and trials.");
  const [busy, setBusy] = useState(false);
  const errors = validateCompare(input);

  function side(name: "base" | "candidate", field: "run_id" | "target", value: string) {
    const next = { ...input[name], [field]: field === "target" ? (value === "2" ? 2 : 1) : value };
    setInput({ ...input, [name]: next });
    setResult(null);
  }

  async function run() {
    if (errors.length) return;
    setBusy(true);
    try {
      const value = await compareRuns(input);
      setResult(value);
      setMessage(value.comparable ? "Compared by the engine." : "Not compared; the differences are named below.");
    } catch (error) {
      setResult(null);
      setMessage(error instanceof Error ? compareErrorMessage(error.message) : "The comparison failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="md">
      <div>
        <Text className="eyebrow">Experiments / Compare</Text>
        <Title order={2}>Compare two runs, paired by task.</Title>
      </div>
      <Paper p="lg" withBorder>
        <Stack>
          {(["base", "candidate"] as const).map((name) => (
            <Group align="flex-end" grow key={name}>
              <TextInput label={`${name === "base" ? "Base" : "Candidate"} run id`} value={input[name].run_id}
                disabled={busy} onChange={(event) => side(name, "run_id", event.currentTarget.value)} />
              <Select label={`${name === "base" ? "Base" : "Candidate"} target`} data={["1", "2"]}
                value={String(input[name].target)} allowDeselect={false} disabled={busy}
                onChange={(value) => side(name, "target", value ?? "1")} />
            </Group>
          ))}
          <Text aria-live="polite" c="dimmed" size="sm">{message}</Text>
          <Group justify="flex-end">
            <Button disabled={busy || errors.length > 0} loading={busy} onClick={run}>Compare</Button>
          </Group>
        </Stack>
      </Paper>
      {result && <CompareReport result={result} />}
    </Stack>
  );
}
