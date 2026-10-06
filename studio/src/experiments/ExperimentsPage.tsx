import {
  Anchor, Badge, Button, Code, Group, Paper, Progress, ScrollArea, Select, Stack, Text, Title,
} from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { EvidenceState, StatusBadge } from "../components/StudioKit";
import { cancelRun, loadCatalog, startRun, streamRun } from "./api";
import { NativeAcceptancePanel } from "./native-acceptance/NativeAcceptancePanel";
import {
  loadNativeCatalog, loadNativeProgress, retryFailedCase,
} from "./native-acceptance/api";
import type {
  NativeCatalog, NativeRun, NativeSelection, NativeSnapshot,
} from "./native-acceptance/model";
import { stateAfterStart } from "./native-acceptance/model";
import { ComparePanel } from "./compare/ComparePanel";
import { EvalTiersPanel } from "./evals/EvalTiersPanel";
import { loadEvalCatalog } from "./evals/api";
import type { EvalCatalog } from "./evals/model";
import type { CompareInput } from "./compare/model";
import { ReplayPanel } from "./replay/ReplayPanel";
import { RunHistoryPanel } from "./history/RunHistoryPanel";
import { loadReplayCatalog, loadReplayResult } from "./replay/api";
import type { ReplayCatalog, ReplayRunResult } from "./replay/model";
import {
  commandFor, mergeUpdate, scopeOptions, streamComplete, terminal, type FreeSuite, type RunCatalog, type RunUpdate,
} from "./model";

const POLL_MS = 350;

export function ExperimentsPage() {
  const [catalog, setCatalog] = useState<RunCatalog | null>(null);
  const [nativeCatalog, setNativeCatalog] = useState<NativeCatalog | null>(null);
  const [nativeSelection, setNativeSelection] = useState<NativeSelection | null>(null);
  const [nativeSnapshot, setNativeSnapshot] = useState<NativeSnapshot | null>(null);
  const [nativeRun, setNativeRun] = useState<NativeRun | null>(null);
  const [replayCatalog, setReplayCatalog] = useState<ReplayCatalog | null>(null);
  const [evalCatalog, setEvalCatalog] = useState<EvalCatalog | null>(null);
  const [replayRunId, setReplayRunId] = useState("");
  const [replayResult, setReplayResult] = useState<ReplayRunResult | null>(null);
  const firstComparison = replayResult?.result?.comparisons?.[0];
  const compareInitial: CompareInput | null = replayRunId && firstComparison
    ? { base: { run_id: replayRunId, target: firstComparison.base_target },
        candidate: { run_id: replayRunId, target: firstComparison.target } }
    : null;
  const [error, setError] = useState("");
  const [suiteId, setSuiteId] = useState("unit-tests");
  const [selectedCase, setSelectedCase] = useState("all");
  const [update, setUpdate] = useState<RunUpdate | null>(null);
  const [stdout, setStdout] = useState("");
  const [stderr, setStderr] = useState("");
  const [paused, setPaused] = useState(false);
  const [queuedUpdates, setQueuedUpdates] = useState(0);
  const pending = useRef<RunUpdate | null>(null);
  const pausedRef = useRef(false);

  useEffect(() => {
    let active = true;
    void loadCatalog().then((value) => { if (active) setCatalog(value); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "Catalog unavailable."); });
    void loadNativeCatalog().then((value) => {
      if (!active) return;
      setNativeCatalog(value);
      setNativeSelection(value.initial);
    }).catch(() => {});
    void loadReplayCatalog().then((value) => { if (active) setReplayCatalog(value); })
      .catch(() => {});
    void loadEvalCatalog().then((value) => { if (active) setEvalCatalog(value); })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!nativeRun) return;
    let active = true;
    async function followNative() {
      while (active) {
        try {
          const snapshot = await loadNativeProgress(nativeRun!.selection);
          if (!active) return;
          setNativeSnapshot(snapshot);
          if (terminal(snapshot.run_status ?? "")) return;
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "Native evidence unavailable.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    }
    void followNative();
    return () => { active = false; };
  }, [nativeRun?.run_id]);

  useEffect(() => {
    if (!replayRunId) return;
    let active = true;
    async function followReplay() {
      while (active) {
        try {
          const result = await loadReplayResult(replayRunId);
          if (!active) return;
          setReplayResult(result);
          if (terminal(result.run.status)) return;
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "Replay result unavailable.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    }
    void followReplay();
    return () => { active = false; };
  }, [replayRunId]);

  useEffect(() => {
    if (!update || terminal(update.run.status)) return;
    let active = true;
    let outCursor = update.stdout.cursor;
    let errCursor = update.stderr.cursor;
    async function follow() {
      while (active) {
        try {
          const events = await streamRun(update!.run.run_id, outCursor, errCursor);
          for (const next of events) {
            outCursor = next.stdout.cursor;
            errCursor = next.stderr.cursor;
            if (pausedRef.current) {
              const prior = pending.current;
              const merged = prior ? mergeUpdate(prior, next) : next;
              pending.current = prior ? {
                ...merged,
                stdout: { ...merged.stdout, chunk: prior.stdout.chunk + next.stdout.chunk },
                stderr: { ...merged.stderr, chunk: prior.stderr.chunk + next.stderr.chunk },
              } : next;
              setQueuedUpdates((count) => count + 1);
            } else {
              setStdout((value) => value + next.stdout.chunk);
              setStderr((value) => value + next.stderr.chunk);
              setUpdate((current) => current ? mergeUpdate(current, next) : next);
            }
            if (streamComplete(next)) return;
          }
        } catch (caught) {
          if (active) setError(caught instanceof Error ? caught.message : "Run stream unavailable.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
    }
    void follow();
    return () => { active = false; };
  }, [update?.run.run_id]);

  const suite = catalog?.suites.find((item) => item.id === suiteId) ?? null;
  const cases = useMemo(() => catalog ? scopeOptions(catalog) : [], [catalog]);
  const command = suite ? commandFor(suite, suite.id === "unit-tests" ? selectedCase : "all") : "";

  async function launch(selected: FreeSuite) {
    setError(""); setStdout(""); setStderr(""); setQueuedUpdates(0); pending.current = null;
    pausedRef.current = false; setPaused(false);
    try {
      const run = await startRun(selected.id, selected.parameters.root, selectedCase);
      setUpdate({ run, stdout: { chunk: "", cursor: 0, eof: false },
        stderr: { chunk: "", cursor: 0, eof: false },
        progress: { completed: 0, eligible: run.case_identities.length, cases: [], lint_findings: [] } });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Run could not start.");
    }
  }

  function resume() {
    const next = pending.current;
    if (next) {
      setStdout((value) => value + next.stdout.chunk);
      setStderr((value) => value + next.stderr.chunk);
      setUpdate((current) => current ? mergeUpdate(current, next) : next);
    }
    pending.current = null;
    pausedRef.current = false;
    setPaused(false);
    setQueuedUpdates(0);
  }

  function pause() {
    pausedRef.current = true;
    setPaused(true);
  }

  async function stop() {
    if (!update) return;
    try {
      const run = await cancelRun(update.run.run_id);
      setUpdate((current) => current ? { ...current, run } : current);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Run could not be cancelled.");
    }
  }

  return (
    <Stack gap="xl">
      <div>
        <Text className="eyebrow">Studio / Experiments</Text>
        <Title order={1}>Run checks, native acceptance or a live replay.</Title>
        <Text c="dimmed" mt="xs">Free suites stay local. Paid experiments show their estimate, resolved targets and hard caps before any model process starts.</Text>
      </div>

      {error && <EvidenceState kind="error" title="Run unavailable">{error}</EvidenceState>}
      {!catalog && !error && <EvidenceState kind="loading" title="Discovering suites">Reading the installed checkout.</EvidenceState>}
      {catalog && suite && <Paper className="experiment-launch" p="xl" withBorder>
        <Stack gap="md">
          <Group justify="space-between"><Title order={2}>Free local suites</Title><Badge color="teal" variant="light">No model usage</Badge></Group>
          <Select label="Suite" value={suiteId} onChange={(value) => { setSuiteId(value ?? "unit-tests"); setSelectedCase("all"); }}
            data={catalog.suites.map((item) => ({ value: item.id, label: item.label }))} allowDeselect={false} />
          {suite.id === "unit-tests" && <Select searchable limit={50} label="Test scope" value={selectedCase}
            onChange={(value) => setSelectedCase(value ?? "all")} data={cases} allowDeselect={false}
            description={`${suite.case_count} discovered tests; search by module, class, or test.`} />}
          <div><Text fw={600} size="sm">Exact command</Text><ScrollArea type="auto" viewportProps={{ role: "region", "aria-label": "Exact command", tabIndex: 0 }}><Code block>{command}</Code></ScrollArea></div>
          <Group><Button onClick={() => void launch(suite)} disabled={Boolean(update && !terminal(update.run.status))}>Run {suite.label}</Button>
            <Text c="dimmed" size="sm">Installed checkout · isolated profile · about {suite.expected_duration_seconds}s</Text></Group>
        </Stack>
      </Paper>}

      {nativeCatalog && nativeSelection && <NativeAcceptancePanel
        key={nativeSelection.progress_id}
        catalog={nativeCatalog}
        initial={nativeSelection}
        snapshot={nativeSnapshot}
        busy={Boolean(nativeRun && !terminal(nativeSnapshot?.run_status ?? nativeRun.status))}
        onStarting={() => setNativeSnapshot(null)}
        onStarted={(run) => {
          const next = stateAfterStart(run);
          setNativeSnapshot(next.snapshot); setNativeRun(next.run); setNativeSelection(next.selection);
        }}
        onResume={(selection) => { setNativeSelection(selection); setNativeRun(null); }}
        onRetryFailed={(selection, caseId) => {
          void retryFailedCase(selection, caseId).then((retry) => {
            setNativeSelection(retry); setNativeSnapshot(null); setNativeRun(null);
          }).catch((caught: unknown) => {
            setError(caught instanceof Error ? caught.message : "Retry could not be prepared.");
          });
        }}
      />}

      {replayCatalog && <ReplayPanel
        tasks={replayCatalog.tasks.map((task) => task.id)}
        packs={replayCatalog.packs}
        defaultPack={replayCatalog.default_pack}
        defaultModel={replayCatalog.default_model}
        rows={replayResult?.result?.table ?? []}
        analysis={replayResult?.result?.analysis ?? null}
        analysisError={replayResult?.result?.analysis_error ?? null}
        comparisons={replayResult?.result?.comparisons ?? []}
        progress={replayResult?.progress ?? []}
        runStatus={replayResult?.run.status}
        onStarted={(runId) => { setReplayRunId(runId); setReplayResult(null); }}
      />}

      {evalCatalog && <EvalTiersPanel catalog={evalCatalog} />}

      <ComparePanel key={compareInitial ? JSON.stringify(compareInitial) : "empty"} initial={compareInitial} />

      {update && <Paper className="run-console" p="xl" withBorder>
        <Stack gap="md">
          <Group justify="space-between"><div><Text className="eyebrow">Run detail</Text><Title order={2}>{update.run.suite_id}</Title></div><StatusBadge>{update.run.status}</StatusBadge></Group>
          <Text size="sm"><Code>{update.run.exact_command}</Code></Text>
          <Progress aria-label="Run progress" value={update.progress.eligible ? update.progress.completed / update.progress.eligible * 100 : 0} />
          <Text aria-live="polite" size="sm">{update.progress.completed} of {update.progress.eligible} completed</Text>
          <Group>
            {!terminal(update.run.status) && <Button color="red" variant="light" onClick={() => void stop()}>Cancel</Button>}
            {!paused && <Button variant="default" onClick={pause}>Pause updates</Button>}
            {paused && <Button variant="default" onClick={resume}>Resume ({queuedUpdates} queued)</Button>}
          </Group>
          {update.progress.cases.length > 0 && <Stack gap="xs">{update.progress.cases.map((item) => <Paper key={item.id} p="sm" withBorder>
            <Group justify="space-between"><Text size="sm">{item.id}</Text><StatusBadge>{item.status}</StatusBadge></Group>
            {item.detail && <Code block mt="xs">{item.detail}</Code>}
          </Paper>)}</Stack>}
          {update.progress.lint_findings.map((finding) => finding.library_href
            ? <Anchor component={NavLink} key={`${finding.path}:${finding.line}`} to={finding.library_href}>
                {finding.path}:{finding.line} — {finding.message}
              </Anchor>
            : <Text key={`${finding.path}:${finding.line}`} size="sm">
                {finding.path}:{finding.line} — {finding.message}
              </Text>)}
          <div><Text fw={600} size="sm">Live log</Text><ScrollArea className="run-log" h={260} viewportProps={{ role: "region", "aria-label": "Live log", tabIndex: 0 }}><Code block>{stdout}{stderr}</Code></ScrollArea></div>
        </Stack>
      </Paper>}
      <RunHistoryPanel />
    </Stack>
  );
}
