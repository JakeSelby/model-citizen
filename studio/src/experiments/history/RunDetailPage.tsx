import { Badge, Button, Code, Group, Paper, ScrollArea, Stack, Table, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { EvidenceState, StatusBadge } from "../../components/StudioKit";
import { useLiveUpdates } from "../../live/LiveUpdates";
import { loadCaseHistory, loadEvidence, loadRunDetail, rerun } from "./api";
import { displayUnknown, engineRowLines, evaluationLines, reportHref, reportLines, type CaseHistory, type RunDetail } from "./model";

/** One engine document as a hairline table of its leaves, in the engine's order. */
function EngineTable({ label, lines }: { label: string; lines: Array<[string, string]> }) {
  return <ScrollArea type="auto" viewportProps={{ role: "region", "aria-label": label, tabIndex: 0 }}>
    <Table className="data-table" mt="sm"><Table.Thead><Table.Tr><Table.Th scope="col">Field</Table.Th><Table.Th scope="col">Engine value</Table.Th></Table.Tr></Table.Thead>
      <Table.Tbody>{lines.map(([path, value], index) =>
        <Table.Tr key={`${path}-${index}`}><Table.Th scope="row"><Text component="span" ff="monospace" size="sm" style={{ overflowWrap: "anywhere" }}>{path}</Text></Table.Th>
          <Table.Td><Text component="span" size="sm" style={{ overflowWrap: "anywhere" }}>{value}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table>
  </ScrollArea>;
}

/** The engine row's fields and the definitive evaluation's reports, each as the engine gave it. */
export function EngineSections({ detail }: { detail: RunDetail }) {
  return <>
      {detail.engine_row && engineRowLines(detail.engine_row).length > 0 && <Paper><Title order={2}>Engine row fields</Title>
        <Text c="dimmed" size="sm">Stratum, arm, metrics, cache basis and session keys exactly as the engine wrote them on this row.</Text>
        <EngineTable label="Engine row fields" lines={engineRowLines(detail.engine_row)} /></Paper>}
      {detail.engine_reports && <>
        {detail.engine_reports.errors.map((item) => <EvidenceState key={item} kind="error" title="Engine report unavailable">{item}</EvidenceState>)}
        {detail.engine_reports.scorecard && <Paper><Title order={2}>Layer scorecard</Title>
          <Text c="dimmed" size="sm">As scripts/layer_scorecard.py wrote it; equivalence reads as the engine's verdict says.</Text>
          <EngineTable label="Layer scorecard" lines={reportLines(detail.engine_reports.scorecard)} /></Paper>}
        {detail.engine_reports.judge && <Paper><Title order={2}>Diff-quality judge</Title>
          <Text c="dimmed" size="sm">As scripts/replay_judge.py report --json printed it.</Text>
          <EngineTable label="Diff-quality judge" lines={reportLines(detail.engine_reports.judge)} /></Paper>}
      </>}
  </>;
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [caseHistory, setCaseHistory] = useState<CaseHistory | null>(null);
  const [caseError, setCaseError] = useState("");
  const [evidence, setEvidence] = useState("");
  const [evidenceError, setEvidenceError] = useState("");
  const [error, setError] = useState("");
  const [rerunPending, setRerunPending] = useState(false);
  const generation = useRef(0);
  const detailRequest = useRef(0);
  const caseRequest = useRef(0);
  const evidenceRequest = useRef(0);
  const lineageRequest = useRef(0);

  const refreshDetail = useCallback(async (selectedRun: string, selectedGeneration: number) => {
    const serial = ++detailRequest.current;
    try {
      const value = await loadRunDetail(selectedRun);
      if (selectedGeneration === generation.current && serial === detailRequest.current) {
        setDetail(value);
        setError("");
      }
    } catch (caught) {
      if (selectedGeneration === generation.current && serial === detailRequest.current) {
        setError(caught instanceof Error ? caught.message : "Run unavailable.");
      }
    }
  }, []);

  useEffect(() => {
    const selectedGeneration = ++generation.current;
    detailRequest.current += 1;
    caseRequest.current += 1;
    evidenceRequest.current += 1;
    lineageRequest.current += 1;
    setDetail(null);
    setCaseHistory(null);
    setCaseError("");
    setEvidence("");
    setEvidenceError("");
    setError("");
    setRerunPending(false);
    void refreshDetail(runId, selectedGeneration);
  }, [refreshDetail, runId]);

  useLiveUpdates(["runs"], () => { void refreshDetail(runId, generation.current); });

  const showCaseHistory = useCallback(async (caseId: string, cursor: string | null = null) => {
    const selectedGeneration = generation.current;
    const serial = ++caseRequest.current;
    try {
      const value = await loadCaseHistory(caseId, cursor);
      if (selectedGeneration !== generation.current || serial !== caseRequest.current) return;
      setCaseHistory((current) => cursor && current?.case_id === caseId
        ? { ...value, items: [...current.items, ...value.items] }
        : value);
      setCaseError("");
    } catch (caught) {
      if (selectedGeneration === generation.current && serial === caseRequest.current) {
        setCaseError(caught instanceof Error ? caught.message : "Case history unavailable.");
      }
    }
  }, []);

  const showEvidence = useCallback(async (artifact: string) => {
    const selectedGeneration = generation.current;
    const selectedRun = runId;
    const serial = ++evidenceRequest.current;
    setEvidence("");
    setEvidenceError("");
    try {
      const value = await loadEvidence(selectedRun, artifact);
      if (selectedGeneration === generation.current && serial === evidenceRequest.current) {
        setEvidence(value.content);
      }
    } catch (caught) {
      if (selectedGeneration === generation.current && serial === evidenceRequest.current) {
        setEvidenceError(caught instanceof Error ? caught.message : "Evidence unavailable.");
      }
    }
  }, [runId]);

  const loadMoreLineage = useCallback(async (cursor: string) => {
    const selectedGeneration = generation.current;
    const serial = ++lineageRequest.current;
    try {
      const value = await loadRunDetail(runId, cursor);
      if (selectedGeneration !== generation.current || serial !== lineageRequest.current) return;
      setDetail((current) => current?.run_id === value.run_id
        ? { ...current, reruns: { ...value.reruns,
          items: [...current.reruns.items, ...value.reruns.items] } }
        : current);
    } catch (caught) {
      if (selectedGeneration === generation.current && serial === lineageRequest.current) {
        setError(caught instanceof Error ? caught.message : "Run lineage unavailable.");
      }
    }
  }, [runId]);

  const startRerun = useCallback(async () => {
    if (!detail || rerunPending) return;
    const selectedGeneration = generation.current;
    setRerunPending(true);
    setError("");
    try {
      const run = await rerun(detail.run_id);
      if (selectedGeneration === generation.current) navigate(`/experiments/runs/${run.run_id}`);
    } catch (caught) {
      if (selectedGeneration === generation.current) {
        setError(caught instanceof Error ? caught.message : "Rerun unavailable.");
        setRerunPending(false);
      }
    }
  }, [detail, navigate, rerunPending]);

  return <Stack gap="xl">
    <div className="page-heading"><Text className="eyebrow">Studio / Experiments / Run</Text><Title order={1}>Run detail</Title>
      <Text component={NavLink} to="/experiments">Back to experiments</Text></div>
    {error && <EvidenceState kind="error" title="Run unavailable">{error}</EvidenceState>}
    {!detail && !error && <EvidenceState kind="loading" title="Loading run">Reading indexed evidence.</EvidenceState>}
    {detail && <>
      <Paper><Stack gap="sm">
        <Group justify="space-between"><Title order={2}>{detail.suite_id}</Title><StatusBadge>{detail.status}</StatusBadge></Group>
        <Text>Target: {displayUnknown(detail.target.ref ?? detail.target.kind)} · Commit: {displayUnknown(detail.target.commit)}</Text>
        <Text>Cost: {detail.cost_usd === null ? "Unknown" : `$${detail.cost_usd.toFixed(4)}`} · Duration: {displayUnknown(detail.duration_ms, " ms")}</Text>
        {detail.rerun_of && <Text>Rerun of <Text component={NavLink} to={`/experiments/runs/${detail.rerun_of}`}>{detail.rerun_of}</Text></Text>}
        {detail.reruns.items.map((id) => <Text key={id}>Rerun: <Text component={NavLink} to={`/experiments/runs/${id}`}>{id}</Text></Text>)}
        {detail.reruns.next_cursor && <Button variant="default" onClick={() => void loadMoreLineage(detail.reruns.next_cursor as string)}>Load more reruns</Button>}
        {detail.exact_command && <ScrollArea type="auto" viewportProps={{ role: "region", "aria-label": "Exact command", tabIndex: 0 }}><Code block>{detail.exact_command}</Code></ScrollArea>}
        <Button disabled={!detail.rerun.available || rerunPending} loading={rerunPending} onClick={() => void startRerun()}>Rerun</Button>
        {!detail.rerun.available && <Text c="dimmed" size="sm">{detail.rerun.reason}</Text>}
      </Stack></Paper>
      <Paper><Title order={2}>Cases</Title><ScrollArea type="auto" viewportProps={{ role: "region", "aria-label": "Cases", tabIndex: 0 }}><Table className="data-table">
        <Table.Thead><Table.Tr><Table.Th>Case</Table.Th><Table.Th>Outcome</Table.Th><Table.Th>Commit</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{detail.cases.map((item) => <Table.Tr key={item.id}><Table.Td>
          <Button variant="subtle" onClick={() => void showCaseHistory(item.id)}>{item.id}</Button>
          {item.flaky && <Badge color="orange" ml="xs">Flaky</Badge>}</Table.Td><Table.Td>{item.outcome}</Table.Td><Table.Td>{displayUnknown(item.commit)}</Table.Td></Table.Tr>)}</Table.Tbody>
      </Table></ScrollArea></Paper>
      {caseError && <EvidenceState kind="error" title="Case history unavailable">{caseError}</EvidenceState>}
      {caseHistory && <Paper><Title order={2}>Case history: {caseHistory.case_id}</Title>
        <Stack gap="xs">{caseHistory.items.map((item) => <Text key={item.run_id} component={NavLink} to={`/experiments/runs/${item.run_id}`}>{item.case?.outcome} · {displayUnknown(item.created_at)} {item.case?.flaky ? "· Flaky" : ""}</Text>)}</Stack>
        {caseHistory.next_cursor && <Button mt="sm" variant="default" onClick={() => void showCaseHistory(caseHistory.case_id, caseHistory.next_cursor)}>Load more</Button>}
      </Paper>}
      {detail.evaluation && <Paper><Title order={2}>Evaluation contract</Title>
        <Table className="data-table" mt="sm"><Table.Tbody>{evaluationLines(detail.evaluation).map(([label, value], index) =>
          <Table.Tr key={`${label}-${index}`}><Table.Th scope="row">{label}</Table.Th><Table.Td><Text style={{ overflowWrap: "anywhere" }}>{value}</Text></Table.Td></Table.Tr>)}</Table.Tbody></Table>
      </Paper>}
      <EngineSections detail={detail} />
      <Paper><Title order={2}>Evidence</Title><Group mt="sm">{detail.artifacts.map((item) => {
        const href = reportHref(item);
        return href
          ? <Button key={item.id} variant="default" component="a" href={href} target="_blank" rel="noopener noreferrer">{item.label}</Button>
          : <Button key={item.id} variant="default" disabled={item.available === false || item.kind === "html-report"}
            onClick={() => void showEvidence(item.id)}>{item.label}</Button>;
      })}</Group>
        {evidenceError && <EvidenceState kind="error" title="Evidence unavailable">{evidenceError}</EvidenceState>}
        {evidence && <ScrollArea className="run-log" h={300} mt="md" viewportProps={{ role: "region", "aria-label": "Case evidence", tabIndex: 0 }}><Code block>{evidence}</Code></ScrollArea>}</Paper>
    </>}
  </Stack>;
}
