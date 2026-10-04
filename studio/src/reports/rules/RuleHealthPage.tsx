import { Anchor, Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { CommandChip, DataTable, EvidenceState, StatusBadge, type DataColumn } from "../../components/StudioKit";
import { DraftTest } from "../../configure/DraftTest";
import { loadRuleHealth, tryWithout } from "./api";
import {
  adviceText, effectText, lastFiredText, precisionText, reliability, sortRows, stateTone, summaryLine,
  tokensText, tryWithoutErrorMessage, unavailableSources, windowText,
  type RuleHealth, type RuleRow, type TryWithoutResult,
} from "./model";

type TryState = { rule: string; busy: boolean; error: string; result: TryWithoutResult | null };

function HitsCell({ row, days }: { row: RuleRow; days: number }) {
  const check = reliability(row.hits);
  return <Stack gap={2}>
    <Text size="sm">{windowText(row.hits, days)}</Text>
    {row.hits.status === "measured" && <Text c="dimmed" size="xs">{row.hits.label}</Text>}
    {check?.reason && <StatusBadge tone={check.tone}>{check.label}</StatusBadge>}
  </Stack>;
}

function PrecisionCell({ row, floor }: { row: RuleRow; floor: number | null }) {
  const check = reliability(row.hits);
  if (!row.detectors.length) return <Text size="sm">no detector</Text>;
  return <Stack gap={2}>
    {check && <StatusBadge tone={check.tone}>{check.label}</StatusBadge>}
    {check?.reason && <Text size="xs">Hit figures unreliable: {check.reason}</Text>}
    {row.detectors.map((detector) => <Text key={detector.id} size="xs">{precisionText(detector, floor)}</Text>)}
  </Stack>;
}

/** The table as the engines reported it; `onTry` asks for a draft with the rule switched off. */
export function RuleHealthReport({ health, onTry, trying }: {
  health: RuleHealth; onTry: (rule: string) => void; trying: TryState | null;
}) {
  const [shortWindow, longWindow] = [health.windows[0] ?? 7, health.windows[1] ?? 30];
  const missing = unavailableSources(health);
  const columns: DataColumn<RuleRow>[] = [
    { key: "rule", heading: "Rule", cell: (row) => <Stack gap={2}><Text fw={600} size="sm">{row.rule}</Text><Text c="dimmed" size="xs">{row.module} · {row.path}</Text></Stack> },
    { key: "status", heading: "Status", cell: (row) => <Stack gap={2}><StatusBadge tone={stateTone(row.state)}>{row.state}</StatusBadge>{row.reason && <Text size="xs">{row.reason}</Text>}</Stack> },
    { key: "short", heading: `Detector hits, ${shortWindow} days`, cell: (row) => <HitsCell row={row} days={shortWindow} /> },
    { key: "long", heading: `Detector hits, ${longWindow} days`, cell: (row) => <HitsCell row={row} days={longWindow} /> },
    { key: "last", heading: "Last fired", cell: (row) => <Text size="sm">{lastFiredText(row.hits, health.windows)}</Text> },
    { key: "precision", heading: "Detector precision", cell: (row) => <PrecisionCell row={row} floor={health.precision_floor} /> },
    { key: "advice", heading: "Advice followed", cell: (row) => <Text size="xs">{adviceText(row.advice)}</Text> },
    { key: "tokens", heading: "Context cost", cell: (row) => <Text size="xs">{tokensText(row.tokens)}</Text> },
    { key: "effect", heading: "Measured effect", cell: (row) => <Text size="xs">{effectText(row.effect)}</Text> },
    {
      key: "try", heading: "Try without it", cell: (row) => row.try_without.available
        ? <Button size="compact-sm" variant="light" aria-label={`Try without ${row.rule}`}
            loading={trying?.rule === row.unit && trying.busy} disabled={Boolean(trying?.busy)}
            onClick={() => onTry(row.unit)}>Try without it</Button>
        : <Text c="dimmed" size="xs">{row.try_without.reason}</Text>,
    },
  ];
  return <Stack gap="lg">
    <Paper p="lg" withBorder>
      <Stack gap="xs">
        <Text fw={600}>rules: {summaryLine(health)}</Text>
        <Text c="dimmed" size="sm">{health.exploratory_note}</Text>
        {health.precision_floor !== null && <Text c="dimmed" size="sm">Precision floor in force: {health.precision_floor}. A rule whose detector is under it, or has no measured precision, has its hit figures marked unreliable.</Text>}
        <Text c="dimmed" size="sm">A detector fires on what its rule names, which can be the breach rather than the compliance, so hits are shown as fired and never as followed.</Text>
        {missing.length > 0 && <EvidenceState kind="error" title="Some sources could not be read; their columns read not measured.">{missing.join(" · ")}</EvidenceState>}
      </Stack>
    </Paper>
    <DataTable caption={`Every loaded rule, generated ${health.generated_at}`} columns={columns} rows={sortRows(health.rows)} rowKey={(row) => row.module} empty="No rule is loaded" />
    {health.findings.length > 0 && <Paper p="md" withBorder><Stack gap={4}>
      <Text fw={600}>findings: {health.findings.length} (everything else still loaded)</Text>
      {health.findings.map((finding) => <Text key={`${finding.path}:${finding.line}`} size="sm">{finding.path}:{finding.line} {finding.reason}</Text>)}
    </Stack></Paper>}
    <Stack gap={4}>
      <Text fw={600} size="sm">Sources</Text>
      {Object.entries(health.commands).map(([name, command]) => <Text key={name} size="xs"><b>{name}</b>: <code>{command}</code></Text>)}
    </Stack>
  </Stack>;
}

/** What "Try without it" leaves: the draft, its CLI steps and its test, ready to run. */
export function TriedDraft({ result }: { result: TryWithoutResult }) {
  return <Paper p="lg" withBorder>
    <Stack gap="sm">
      <Title order={2}>Draft {result.draft.name}</Title>
      <Text>{result.message} Test it against its base below, then decide in Configure whether to apply it.</Text>
      {result.commands.map((command, index) => <CommandChip command={command} key={command} label={`CLI step ${index + 1}`} />)}
      <Anchor component={NavLink} to="/configure#drafts">Open drafts in Configure</Anchor>
      <DraftTest draft={result.draft.name} revision={result.draft.revision} />
    </Stack>
  </Paper>;
}

export function RuleHealthPage() {
  const [health, setHealth] = useState<RuleHealth | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [trying, setTrying] = useState<TryState | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setHealth(await loadRuleHealth());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Rule health could not be read.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function onTry(rule: string) {
    setTrying({ rule, busy: true, error: "", result: null });
    try {
      setTrying({ rule, busy: false, error: "", result: await tryWithout(rule) });
    } catch (caught) {
      setTrying({ rule, busy: false, error: tryWithoutErrorMessage(caught instanceof Error ? caught.message : ""), result: null });
    }
  }

  return <Stack gap="xl">
    <Group align="flex-end" className="page-heading" justify="space-between">
      <div>
        <Text className="eyebrow">Studio / Reports / Rule health</Text>
        <Title order={1}>Every rule, measured or not.</Title>
        <Text c="dimmed" mt="xs">Status, detector hits, precision and context cost, each as its engine reports it. Nothing is switched off for you.</Text>
      </div>
      <Button variant="default" onClick={() => void refresh()} loading={loading}>Refresh</Button>
    </Group>
    {trying?.error && <EvidenceState kind="refused" title="No draft was made">{trying.error}</EvidenceState>}
    {trying?.result && <TriedDraft result={trying.result} />}
    {error && <EvidenceState kind="error" title="Rule health could not be read">{error}</EvidenceState>}
    {!health && loading && <EvidenceState kind="loading" title="Reading every rule's evidence" />}
    {health && <RuleHealthReport health={health} onTry={(rule) => void onTry(rule)} trying={trying} />}
  </Stack>;
}
