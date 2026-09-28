import {
  Alert,
  Badge,
  Button,
  Code,
  Group,
  Paper,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import { CommandChip, EvidenceState } from "../components/StudioKit";
import { loadSelection } from "./api";
import {
  budgetPercent,
  SelectionRequestGate,
  selectedCount,
  sourceLabel,
  type Provenance,
  type SelectionReport,
  type SelectionRow,
} from "./model";
import "./selection.css";

function Origin({ item }: { item: Provenance }) {
  return (
    <div className="selection-origin">
      <Group gap="xs" wrap="wrap">
        <Badge color={item.saved ? "gray" : "yellow"} variant="light">{sourceLabel(item)}</Badge>
        {!item.saved && <Badge color="yellow" variant="outline">Not saved</Badge>}
        <Code>{String(item.value ?? "none")}</Code>
      </Group>
      <Code className="selection-source">{item.source_file || "No source file"}</Code>
    </div>
  );
}

function SelectionValue({ kind, row }: { kind: string; row: SelectionRow }) {
  return (
    <details className="selection-row">
      <summary>
        <span className="selection-key">{kind}.{row.unit}</span>
        <Code>{String(row.value)}</Code>
        <Badge color={row.saved ? "teal" : "yellow"} variant="light">{sourceLabel(row)}</Badge>
      </summary>
      <div className="selection-provenance">
        <Text fw={650} size="sm">Effective source</Text>
        <Origin item={row} />
        {row.overridden.length > 0 && (
          <>
            <Text fw={650} mt="md" size="sm">Overridden values</Text>
            <Stack gap="xs">{[...row.overridden].reverse().map((item, index) => (
              <Origin item={item} key={`${item.source}:${item.source_file}:${index}`} />
            ))}</Stack>
          </>
        )}
      </div>
    </details>
  );
}

export function SelectionPanel() {
  const [repository, setRepository] = useState("");
  const [projectFile, setProjectFile] = useState("");
  const [report, setReport] = useState<SelectionReport | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("Resolving the launcher selection…");
  const requestGate = useRef(new SelectionRequestGate());

  async function refresh(nextRepository = repository, nextProjectFile = projectFile) {
    const generation = requestGate.current.next();
    setStatus("loading");
    setMessage("Resolving the effective selection…");
    try {
      const next = await loadSelection(nextRepository, nextProjectFile);
      if (!requestGate.current.accepts(generation)) return;
      setReport(next);
      setRepository(next.repository);
      setProjectFile(next.project_file);
      setStatus("ready");
      setMessage(`${selectedCount(next)} values resolved through the policy kernel.`);
    } catch (error) {
      if (!requestGate.current.accepts(generation)) return;
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "The effective selection is unavailable.");
    }
  }

  useEffect(() => { void refresh("", ""); }, []);

  return (
    <Stack gap="lg">
      <Paper className="selection-controls" p="lg" withBorder>
        <Group align="flex-end" grow>
          <TextInput
            label="Repository"
            description="Used to identify the project whose selection you are inspecting."
            placeholder="Current working directory"
            value={repository}
            onChange={(event) => setRepository(event.currentTarget.value)}
          />
          <TextInput
            label="Project selection file"
            description="Optional file used as HARNESS_PROJECT_CONFIG."
            placeholder="No project override"
            value={projectFile}
            onChange={(event) => setProjectFile(event.currentTarget.value)}
          />
          <Button loading={status === "loading"} onClick={() => void refresh()}>Resolve selection</Button>
        </Group>
        <Text aria-live="polite" c={status === "error" ? "red" : "dimmed"} mt="md" size="sm">{message}</Text>
      </Paper>

      {status === "error" && !report && (
        <EvidenceState kind="error" title="Selection unavailable">Check the repository and project selection paths, then try again.</EvidenceState>
      )}

      {report && (
        <>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
            <Paper p="xl" withBorder>
              <Group justify="space-between">
                <Title order={2}>Effective mode</Title>
                <Badge color="teal" variant="light">{sourceLabel(report.mode)}</Badge>
              </Group>
              <Text className="selection-mode" fw={650} mt="md">{String(report.mode.value ?? "No mode")}</Text>
              <Origin item={report.mode} />
              {report.mode.overridden.length > 0 && (
                <Text c="dimmed" mt="sm" size="sm">{report.mode.overridden.length} lower-precedence mode value{report.mode.overridden.length === 1 ? "" : "s"} retained in provenance.</Text>
              )}
            </Paper>
            <Paper p="xl" withBorder>
              <Title order={2}>CLI parity</Title>
              <Text c="dimmed" mt="xs" size="sm">Studio resolves through the same policy kernel as these commands.</Text>
              <Stack gap="sm" mt="lg">
                <CommandChip command={report.commands.selection} label="Effective selection" />
                <CommandChip command={report.commands.budget} label="Budget enforcement" />
              </Stack>
            </Paper>
          </SimpleGrid>

          <Paper p="xl" withBorder>
            <Title order={2}>Always-loaded budget</Title>
            <Text c="dimmed" mt="xs" size="sm">The lint-enforced worst case is shown against both caps. The selected line total reflects this selection.</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }} mt="lg" spacing="lg">
              {report.budgets.map((budget) => (
                <div className="budget-meter" key={budget.runtime}>
                  <Group justify="space-between"><Text fw={650}>{budget.label}</Text><Badge color={budget.managed ? "teal" : "gray"} variant="light">{budget.managed ? "Managed" : "Not managed"}</Badge></Group>
                  <Progress aria-label={`${budget.label} always-loaded token budget`} mt="sm" value={budgetPercent(budget)} />
                  <Text mt="xs" size="sm">~{budget.used_tokens.toLocaleString()} / {budget.token_cap.toLocaleString()} tokens</Text>
                  <Text c="dimmed" size="xs">{budget.used_lines} / {budget.line_cap} worst-case lines · {budget.selected_lines} selected lines</Text>
                </div>
              ))}
            </SimpleGrid>
          </Paper>

          {status === "error" && <Alert color="red" title="Refresh failed">{message} The last resolved selection remains visible.</Alert>}

          <Paper className="selection-report" p="xl" withBorder>
            <Title order={2}>Resolved values and source layers</Title>
            <Text c="dimmed" mt="xs" size="sm">Open any row to see its source and every lower-precedence value it replaced.</Text>
            <Stack gap="lg" mt="lg">
              {report.groups.map((group) => (
                <section aria-labelledby={`selection-${group.kind}`} key={group.kind}>
                  <Group className="selection-group-heading" justify="space-between">
                    <Title id={`selection-${group.kind}`} order={3}>{group.kind}</Title>
                    <Badge color="gray" variant="light">{group.rows.length}</Badge>
                  </Group>
                  <div className="selection-rows">{group.rows.map((row) => (
                    <SelectionValue kind={group.kind} key={row.unit} row={row} />
                  ))}</div>
                </section>
              ))}
            </Stack>
          </Paper>
        </>
      )}
    </Stack>
  );
}
