import { Badge, Button, Code, Group, Paper, ScrollArea, Stack, Table, Text, Title } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { EvidenceState, StatusBadge } from "../../components/StudioKit";
import { useLiveUpdates } from "../../live/LiveUpdates";
import { loadCaseHistory, loadEvidence, loadRunDetail, rerun } from "./api";
import { displayUnknown, type CaseHistory, type RunDetail } from "./model";

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
    <div><Text className="eyebrow">Studio / Experiments / Run</Text><Title order={1}>Run detail</Title>
      <Text component={NavLink} to="/experiments">Back to experiments</Text></div>
    {error && <EvidenceState kind="error" title="Run unavailable">{error}</EvidenceState>}
    {!detail && !error && <EvidenceState kind="loading" title="Loading run">Reading indexed evidence.</EvidenceState>}
    {detail && <>
      <Paper p="xl" withBorder><Stack gap="sm">
        <Group justify="space-between"><Title order={2}>{detail.suite_id}</Title><StatusBadge>{detail.status}</StatusBadge></Group>
        <Text>Target: {displayUnknown(detail.target.ref ?? detail.target.kind)} · Commit: {displayUnknown(detail.target.commit)}</Text>
        <Text>Cost: {detail.cost_usd === null ? "Unknown" : `$${detail.cost_usd.toFixed(4)}`} · Duration: {displayUnknown(detail.duration_ms, " ms")}</Text>
        {detail.rerun_of && <Text>Rerun of <Text component={NavLink} to={`/experiments/runs/${detail.rerun_of}`}>{detail.rerun_of}</Text></Text>}
        {detail.reruns.items.map((id) => <Text key={id}>Rerun: <Text component={NavLink} to={`/experiments/runs/${id}`}>{id}</Text></Text>)}
        {detail.reruns.next_cursor && <Button variant="default" onClick={() => void loadMoreLineage(detail.reruns.next_cursor as string)}>Load more reruns</Button>}
        {detail.exact_command && <ScrollArea type="auto"><Code block>{detail.exact_command}</Code></ScrollArea>}
        <Button disabled={!detail.rerun.available || rerunPending} loading={rerunPending} onClick={() => void startRerun()}>Rerun</Button>
        {!detail.rerun.available && <Text c="dimmed" size="sm">{detail.rerun.reason}</Text>}
      </Stack></Paper>
      <Paper p="xl" withBorder><Title order={2}>Cases</Title><ScrollArea type="auto"><Table className="data-table">
        <Table.Thead><Table.Tr><Table.Th>Case</Table.Th><Table.Th>Outcome</Table.Th><Table.Th>Commit</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{detail.cases.map((item) => <Table.Tr key={item.id}><Table.Td>
          <Button variant="subtle" onClick={() => void showCaseHistory(item.id)}>{item.id}</Button>
          {item.flaky && <Badge color="orange" ml="xs">Flaky</Badge>}</Table.Td><Table.Td>{item.outcome}</Table.Td><Table.Td>{displayUnknown(item.commit)}</Table.Td></Table.Tr>)}</Table.Tbody>
      </Table></ScrollArea></Paper>
      {caseError && <EvidenceState kind="error" title="Case history unavailable">{caseError}</EvidenceState>}
      {caseHistory && <Paper p="xl" withBorder><Title order={2}>Case history: {caseHistory.case_id}</Title>
        <Stack gap="xs">{caseHistory.items.map((item) => <Text key={item.run_id} component={NavLink} to={`/experiments/runs/${item.run_id}`}>{item.case?.outcome} · {displayUnknown(item.created_at)} {item.case?.flaky ? "· Flaky" : ""}</Text>)}</Stack>
        {caseHistory.next_cursor && <Button mt="sm" variant="default" onClick={() => void showCaseHistory(caseHistory.case_id, caseHistory.next_cursor)}>Load more</Button>}
      </Paper>}
      <Paper p="xl" withBorder><Title order={2}>Evidence</Title><Group mt="sm">{detail.artifacts.map((item) => <Button key={item.id} variant="default" disabled={item.available === false}
        onClick={() => void showEvidence(item.id)}>{item.label}</Button>)}</Group>
        {evidenceError && <EvidenceState kind="error" title="Evidence unavailable">{evidenceError}</EvidenceState>}
        {evidence && <ScrollArea className="run-log" h={300} mt="md"><Code block>{evidence}</Code></ScrollArea>}</Paper>
    </>}
  </Stack>;
}
