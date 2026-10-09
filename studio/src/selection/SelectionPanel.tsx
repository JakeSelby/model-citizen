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
import { useLiveUpdates } from "../live/LiveUpdates";
import { loadSelection } from "./api";
import {
  budgetPercent,
  lintBudgetText,
  SelectionRequestGate,
  selectedCount,
  sourceLabel,
  type Provenance,
  type SelectionReport,
  type SelectionRow,
  type SelectionGroup,
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

export function SelectionCategory({ group }: { group: SelectionGroup }) {
  const overrides = group.rows.filter((row) => row.source !== "default");
  const layers = [...new Set(overrides.map(sourceLabel))];
  return <details className="selection-category" open={overrides.length > 0}>
    <summary className="selection-group-heading">
      <span className="selection-category-name">{group.kind}</span>
      <Badge color="gray" variant="light">{group.rows.length}</Badge>
      <span className="selection-category-summary">{overrides.length
        ? `${overrides.length} override${overrides.length === 1 ? "" : "s"} · ${layers.join(", ")}`
        : "All defaults"}</span>
    </summary>
    <div className="selection-rows">{group.rows.map((row) => (
      <SelectionValue kind={group.kind} key={row.unit} row={row} />
    ))}</div>
  </details>;
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
  useLiveUpdates(["selection"], () => { void refresh(); });

  return (
    <Stack gap="lg">
      <Paper className="selection-controls">
        <Group className="selection-fields" align="flex-end" grow>
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
            <Paper>
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
            <Paper>
              <Title order={2}>CLI parity</Title>
              <Text c="dimmed" mt="xs" size="sm">Studio resolves through the same policy kernel as these commands.</Text>
              <Stack gap="sm" mt="lg">
                <CommandChip command={report.commands.selection} label="Effective selection" />
                <CommandChip command={report.commands.budget} label="Budget enforcement" />
              </Stack>
            </Paper>
          </SimpleGrid>

          <Paper>
            <Title order={2}>Always-loaded budget</Title>
            <Text c="dimmed" mt="xs" size="sm">The worst case is shown against both caps, with the figure citizen lint enforces once switched-off rules are left out. The selected line total reflects this selection.</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }} mt="lg" spacing="lg">
              {report.budgets.map((budget) => (
                <div className="budget-meter" key={budget.runtime}>
                  <Group justify="space-between"><Text fw={650}>{budget.label}</Text><Badge color={budget.managed ? "teal" : "gray"} variant="light">{budget.managed ? "Managed" : "Not managed"}</Badge></Group>
                  <Progress aria-label={`${budget.label} always-loaded token budget`} mt="sm" value={budgetPercent(budget)} />
                  <Text mt="xs" size="sm">~{budget.used_tokens.toLocaleString()} / {budget.token_cap.toLocaleString()} tokens</Text>
                  <Text c="dimmed" size="xs">{budget.used_lines} / {budget.line_cap} worst-case lines · {budget.selected_lines} selected lines</Text>
                  <Text size="xs">{lintBudgetText(budget)}</Text>
                </div>
              ))}
            </SimpleGrid>
          </Paper>

          {status === "error" && <Alert color="red" title="Refresh failed">{message} The last resolved selection remains visible.</Alert>}

          <Paper className="selection-report">
            <Title order={2}>Resolved values and source layers</Title>
            <Text c="dimmed" mt="xs" size="sm">Categories with overrides are expanded. Open a value to inspect its source layers.</Text>
            <Stack gap="xs" mt="lg">
              {report.groups.map((group) => (
                <SelectionCategory group={group} key={`${group.kind}:${report.repository}:${report.project_file}`} />
              ))}
            </Stack>
          </Paper>
        </>
      )}
    </Stack>
  );
}
