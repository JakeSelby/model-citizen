import { Anchor, Button, Group, NativeSelect, Paper, Stack, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { CommandChip, DataTable, EvidenceState, StatusBadge, type DataColumn } from "../../components/StudioKit";
import { loadTrends } from "./api";
import {
  cardValue, delegationText, evidenceText, extent, figureText, numberText, pointLabel, proofSummary, sm2Text,
  statusText, statusTone, type Bundle, type Card, type Line, type Measure, type Point, type StaticPoint, type Trends,
} from "./model";

const WIDTH = 640;
const HEIGHT = 200;
const PAD = 36;

/** The stored values joined in order, each stored interval drawn as a bar; hollow points are exploratory. */
export function TrendChart({ line, measure }: { line: Line; measure: Measure }) {
  const range = extent(line.points, measure.id);
  const label = `${measure.label}, ${line.id}: ` + line.points.map((point) =>
    `${pointLabel(point)} ${figureText(point.measures[measure.id])}, ${point.evidence.label}`).join("; ");
  if (!range) return <Text c="dimmed" size="sm">No point on this line records {measure.label.toLowerCase()}.</Text>;
  const [low, high] = range;
  const x = (index: number) => line.points.length === 1 ? WIDTH / 2 : PAD + index * (WIDTH - 2 * PAD) / (line.points.length - 1);
  const y = (value: number) => HEIGHT - PAD - (value - low) * (HEIGHT - 2 * PAD) / (high - low);
  const placed = line.points.map((point, index) => ({ point, index, figure: point.measures[measure.id] }))
    .filter((item) => item.figure && item.figure.value !== null);
  return <svg className="trend-chart" role="img" aria-label={label} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%">
    <text x={4} y={PAD - 8} fontSize="11" fill="currentColor">{numberText(high)}</text>
    <text x={4} y={HEIGHT - PAD + 14} fontSize="11" fill="currentColor">{numberText(low)}</text>
    <polyline fill="none" stroke="currentColor" strokeWidth={1.5}
      points={placed.map((item) => `${x(item.index)},${y(item.figure!.value!)}`).join(" ")} />
    {placed.map(({ point, index, figure }) => <g data-evidence={point.evidence.label} key={`${point.run_id}-${index}`}>
      {figure!.interval && <g className="trend-interval" data-low={figure!.interval[0]} data-high={figure!.interval[1]}>
        <line x1={x(index)} x2={x(index)} y1={y(figure!.interval[0])} y2={y(figure!.interval[1])} stroke="currentColor" />
        <line x1={x(index) - 5} x2={x(index) + 5} y1={y(figure!.interval[0])} y2={y(figure!.interval[0])} stroke="currentColor" />
        <line x1={x(index) - 5} x2={x(index) + 5} y1={y(figure!.interval[1])} y2={y(figure!.interval[1])} stroke="currentColor" />
      </g>}
      <circle cx={x(index)} cy={y(figure!.value!)} r={4} stroke="currentColor"
        fill={point.evidence.label === "exploratory" ? "none" : "currentColor"} />
      <text x={x(index)} y={HEIGHT - 8} fontSize="10" textAnchor="middle" fill="currentColor">{point.harness_version ?? "?"}</text>
    </g>)}
  </svg>;
}

function LinePanel({ line, measure }: { line: Line; measure: Measure }) {
  const columns: DataColumn<Point>[] = [
    { key: "version", heading: "Version and date", cell: (point) => <Text size="sm">{pointLabel(point)}</Text> },
    { key: "value", heading: measure.label, cell: (point) => <Text size="sm">{figureText(point.measures[measure.id])}</Text> },
    { key: "evidence", heading: "Evidence", cell: (point) => <Stack gap={2}>
      <StatusBadge tone={point.evidence.label === "exploratory" ? "warning" : "info"}>{evidenceText(point)}</StatusBadge>
      <Text c="dimmed" size="xs">{point.evidence.reason}</Text>
    </Stack> },
    { key: "note", heading: "Change note", cell: (point) => <Text size="sm">{point.change_note || "none recorded"}</Text> },
    { key: "sm2", heading: "SM-2 as stored", cell: (point) => <Text size="xs">{sm2Text(point)}</Text> },
    { key: "context", heading: "Run", cell: (point) => <Stack gap={2}>
      <Text size="xs">{point.model ?? "model not recorded"} · {point.reps ?? "?"} rep(s){point.cache_basis ? ` · cache ${point.cache_basis}` : ""}</Text>
      <Text c="dimmed" size="xs">{delegationText(point)}</Text>
      <Anchor component={NavLink} size="xs" to="/reports/rules" aria-label={`Rule health: current reading, adherence and precision (beside ${pointLabel(point)})`}>Rule health: current reading</Anchor>
    </Stack> },
  ];
  return <Paper p="lg" withBorder>
    <Stack gap="sm">
      <Group gap="sm">
        <Title order={2}>Series {line.series}{line.bucket ? `, bucket ${line.bucket}` : ""}</Title>
        <StatusBadge tone={line.evidence === "pre-registered" ? "info" : "warning"}>{line.evidence}</StatusBadge>
      </Group>
      <Text size="sm">{line.evidence_note}</Text>
      <TrendChart line={line} measure={measure} />
      <DataTable caption={`${measure.label} by version and date, series ${line.id}`} columns={columns} rows={line.points}
        rowKey={(point) => `${point.run_id}-${point.date}-${point.harness_version}`} />
    </Stack>
  </Paper>;
}

function BundlePanel({ bundle }: { bundle: Bundle }) {
  const columns: DataColumn<Card>[] = [
    { key: "claim", heading: "Claim", cell: (card) => <Text size="sm">{card.claim ?? card.id ?? "unnamed"}</Text> },
    { key: "estimand", heading: "Estimand", cell: (card) => <Text size="xs">{card.estimand ?? "not stated"}</Text> },
    { key: "figure", heading: "Figure", cell: (card) => <Text size="sm">{cardValue(card.figure)}</Text> },
    { key: "interval", heading: "Interval", cell: (card) => <Text size="sm">{cardValue(card.interval)}</Text> },
    { key: "status", heading: "Verify status", cell: (card) => <StatusBadge tone={card.verified ? "success" : "danger"}>{card.verified ? "verified" : card.verify_status ? "not verified (bundle failed)" : "not verified"}</StatusBadge> },
    { key: "published", heading: "Published", cell: (card) => <Text size="xs">{card.published ? `${card.published.field}: ${card.published.text}` : "in the bundle, not published"}</Text> },
  ];
  return <Stack gap="xs">
    <Group gap="sm">
      <Text fw={600}>{bundle.bundle}</Text>
      <StatusBadge tone={statusTone(bundle.status)}>{bundle.status === "not checked" ? statusText(bundle.status) : bundle.status}</StatusBadge>
      {bundle.bundle_id && <Text c="dimmed" size="xs">bundle {bundle.bundle_id}</Text>}
    </Group>
    {bundle.reason && <Text size="xs">{bundle.reason}</Text>}
    <CommandChip command={bundle.command} />
    {bundle.errors.map((error) => <Text key={error} size="xs">{error}</Text>)}
    {bundle.unknown.map((item) => <Text key={item} size="xs">unknown, not verified: {item}</Text>)}
    <DataTable caption={`Cards in ${bundle.bundle}`} columns={columns} rows={bundle.cards} rowKey={(card) => card.id ?? card.claim ?? ""} empty="The verifier reported no card" />
  </Stack>;
}

function SectionState({ name, section, maximum }: { name: "history" | "static"; section: Trends["sections"]["history"]; maximum: number }) {
  const label = name === "history" ? "Benchmark history" : "Static context";
  if (section.status === "unavailable") return <EvidenceState kind="error" title={`${label} could not be read; the rest of the page still shows`}>{section.reason}</EvidenceState>;
  return <>
    {section.truncated && <EvidenceState kind="error" title={`${label}: only the newest ${maximum} records are shown`}>Older records in the run index are left out.</EvidenceState>}
    {section.unreadable > 0 && <EvidenceState kind="error" title={`${label}: ${section.unreadable} record(s) could not be read and are left out`} />}
  </>;
}

export function ProofSet({ proof }: { proof: Trends["proof"] }) {
  return <Paper p="lg" withBorder>
    <Stack gap="sm">
      <Title order={2}>The project's proof set</Title>
      <Text>{proofSummary(proof)}</Text>
      {proof.claims.map((claim) => <Text key={claim.field} size="sm">
        <StatusBadge tone={statusTone(claim.status)}>{statusText(claim.status)}</StatusBadge> {claim.field}: {claim.text}{claim.reason ? ` (${claim.reason})` : ""}
      </Text>)}
      {proof.bundles.map((bundle) => <BundlePanel bundle={bundle} key={bundle.bundle} />)}
    </Stack>
  </Paper>;
}

export function TrendsReport({ trends, measure, onMeasure }: { trends: Trends; measure: string; onMeasure: (measure: string) => void }) {
  const chosen = trends.measures.find((item) => item.id === measure) ?? trends.measures[0];
  const staticColumns: DataColumn<StaticPoint>[] = [
    { key: "version", heading: "Version", cell: (point) => <Text size="sm">{point.harness_version}</Text> },
    { key: "tokens", heading: trends.static.measure.label, cell: (point) => <Text size="sm">{numberText(point.value)}</Text> },
  ];
  return <Stack gap="lg">
    <ProofSet proof={trends.proof} />
    <Paper p="lg" withBorder>
      <Stack gap="xs">
        <Text>{trends.ratio_note}</Text>
        <Text c="dimmed" size="sm">Each point and interval is the row's own, from benchmarks/history.jsonl through the run index. A hollow point is exploratory; the table beside each chart says so in words.</Text>
        {(["history", "static"] as const).map((name) => <SectionState key={name} name={name} section={trends.sections[name]} maximum={trends.max_records} />)}
        <NativeSelect aria-label="Measure" value={chosen?.id} onChange={(event) => onMeasure(event.currentTarget.value)}
          data={trends.measures.map((item) => ({ value: item.id, label: item.label }))} />
        {chosen && <Text c="dimmed" size="sm">{chosen.note}</Text>}
      </Stack>
    </Paper>
    {!trends.lines.length && trends.sections.history.status === "ready" && <EvidenceState kind="empty" title="No benchmark history is indexed">Run citizen runs reindex after a pre-registered replay writes benchmarks/history.jsonl.</EvidenceState>}
    {chosen && trends.lines.map((line) => <LinePanel key={line.id} line={line} measure={chosen} />)}
    <DataTable caption={trends.static.measure.label + " by version"} columns={staticColumns} rows={trends.static.points}
      rowKey={(point) => point.harness_version} empty="No static figure is indexed" />
    <Stack gap={4}>
      <Text fw={600} size="sm">Not tracked across versions</Text>
      {trends.not_tracked.map((item) => <Text key={item.measure} size="sm">{item.measure}: {item.reason} <Anchor component={NavLink} to={item.where}>Rule health</Anchor></Text>)}
    </Stack>
    <Stack gap={4}>
      <Text fw={600} size="sm">Sources</Text>
      {Object.entries(trends.commands).map(([name, command]) => <Text key={name} size="xs"><b>{name}</b>: <code>{command}</code></Text>)}
    </Stack>
  </Stack>;
}

export function TrendsPage() {
  const [trends, setTrends] = useState<Trends | null>(null);
  const [measure, setMeasure] = useState("ratio_sm2");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setTrends(await loadTrends());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Trends could not be read.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return <Stack gap="xl">
    <Group align="flex-end" className="page-heading" justify="space-between">
      <div>
        <Text className="eyebrow">Studio / Reports / Trends</Text>
        <Title order={1}>Each measure across versions, beside the proof set.</Title>
        <Text c="dimmed" mt="xs">Ratios, pass rates and the static figure by version and date, each as its engine stored it, with the proof set as citizen evidence verify reports it.</Text>
      </div>
      <Button variant="default" onClick={() => void refresh()} loading={loading}>Refresh</Button>
    </Group>
    {error && <EvidenceState kind="error" title="Trends could not be read">{error}</EvidenceState>}
    {!trends && loading && <EvidenceState kind="loading" title="Reading the run index and the proof set" />}
    {trends && <TrendsReport trends={trends} measure={measure} onMeasure={setMeasure} />}
  </Stack>;
}
