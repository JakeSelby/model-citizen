import {
  Anchor,
  Button,
  Code,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { CodeView, CommandChip, EvidenceState, StatusBadge } from "../components/StudioKit";
import { loadActivity } from "./api";
import {
  activityTone,
  ActivityRequestGate,
  continuationLabel,
  EMPTY_FILTERS,
  mergeEntries,
  mergeSources,
  type ActivityEntry,
  type ActivityFilters,
  type ActivityPage as ActivityPayload,
} from "./model";
import "./activity.css";

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  return (
    <Paper className="activity-row" component="article" p={{ base: "md", sm: "lg" }} withBorder>
      <Group align="flex-start" justify="space-between" wrap="wrap">
        <div>
          <Text className="activity-meta" c="dimmed" size="xs">
            <time dateTime={entry.timestamp}>{entry.timestamp || "Time unavailable"}</time>
            {entry.actor ? ` · ${entry.actor}` : ""}
          </Text>
          <Title mt="xs" order={2}>{entry.title}</Title>
        </div>
        <StatusBadge tone={activityTone(entry.outcome)}>{entry.outcome}</StatusBadge>
      </Group>
      <Text mt="sm">{entry.reason}</Text>
      <dl className="activity-facts">
        {entry.session ? <><dt>Session</dt><dd><Code>{entry.session}</Code></dd></> : null}
        {entry.repository ? <><dt>Repository</dt><dd>{entry.repository}</dd></> : null}
        {entry.hook ? <><dt>Hook</dt><dd><Code>{entry.hook}</Code></dd></> : null}
        {entry.kind === "decision" ? <><dt>Grade</dt><dd>{entry.grade}</dd></> : null}
        {entry.draft ? <><dt>Draft</dt><dd><Code>{entry.draft}</Code></dd></> : null}
        <dt>Source</dt><dd>{entry.source}</dd>
      </dl>
      {entry.command ? <CodeView label={entry.kind === "decision" ? "Command text" : "CLI equivalent"}>{entry.command}</CodeView> : null}
      {entry.files.length ? <div className="activity-files">
        <Text fw={650} size="sm">Changed files</Text>
        <ul>{entry.files.map((file) => <li key={file}><Code>{file}</Code></li>)}</ul>
      </div> : null}
      {entry.evidence_href ? <Anchor component={Link} mt="md" to={entry.evidence_href}>
        {entry.evidence_label || "Open source evidence"}
      </Anchor> : null}
    </Paper>
  );
}

export function ActivityTimeline({ payload }: { payload: ActivityPayload }) {
  return <Stack gap="md">
    <Paper className="activity-sources" p="md" withBorder>
      <Text fw={650} size="sm">Evidence sources</Text>
      <Stack gap="xs" mt="xs">
        {payload.sources.map((source) => <Group justify="space-between" key={source.id} wrap="wrap">
          <Text size="sm"><Code>{source.id}</Code> · {source.message}</Text>
          <StatusBadge tone={activityTone(source.status)}>{source.status}</StatusBadge>
        </Group>)}
      </Stack>
    </Paper>
    {payload.entries.length ? payload.entries.map((entry) => <ActivityRow entry={entry} key={entry.id} />)
      : payload.next_cursor
        ? <EvidenceState kind="empty" title="No matches in this page">More rows remain. Search older activity to continue.</EvidenceState>
        : <EvidenceState kind="empty" title="No activity matches">Change a filter or create the first local decision.</EvidenceState>}
  </Stack>;
}

export function ActivityPage() {
  const [filters, setFilters] = useState<ActivityFilters>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<ActivityFilters>(EMPTY_FILTERS);
  const [payload, setPayload] = useState<ActivityPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const requestGate = useRef(new ActivityRequestGate());

  useEffect(() => {
    const request = requestGate.current.next();
    setLoading(true);
    setLoadingMore(false);
    setError("");
    setPayload(null);
    void loadActivity(filters, "", 25, request.signal)
      .then((next) => { if (requestGate.current.accepts(request.generation)) setPayload(next); })
      .catch((reason: unknown) => {
        if (requestGate.current.accepts(request.generation)
            && (!(reason instanceof Error) || reason.name !== "AbortError")) {
          setError(reason instanceof Error ? reason.message : "Activity unavailable.");
        }
      })
      .finally(() => {
        if (requestGate.current.accepts(request.generation)) setLoading(false);
      });
    return () => { requestGate.current.cancel(request.generation); };
  }, [filters]);

  function update(name: keyof ActivityFilters, value: string | null) {
    setDraftFilters((current) => ({ ...current, [name]: value ?? "" }));
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    requestGate.current.invalidate();
    setPayload(null);
    setError("");
    setLoadingMore(false);
    setFilters({ ...draftFilters });
  }

  async function loadOlder() {
    if (!payload?.next_cursor) return;
    const current = payload;
    const request = requestGate.current.next();
    setLoadingMore(true);
    setError("");
    try {
      const next = await loadActivity(filters, current.next_cursor, 25, request.signal);
      if (requestGate.current.accepts(request.generation)) {
        setPayload({
          ...next,
          entries: mergeEntries(current.entries, next.entries),
          sources: mergeSources(current.sources, next.sources),
        });
      }
    } catch (reason) {
      if (requestGate.current.accepts(request.generation)
          && (!(reason instanceof Error) || reason.name !== "AbortError")) {
        setError(reason instanceof Error ? reason.message : "Older activity is unavailable.");
      }
    } finally {
      if (requestGate.current.accepts(request.generation)) setLoadingMore(false);
    }
  }

  return (
    <Stack gap="xl">
      <Group align="flex-end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Studio / Activity</Text>
          <Title order={1}>What the harness decided and changed.</Title>
          <Text c="dimmed" mt="xs">Trace guardrails, governed Studio actions, and ownership evidence without rewriting history.</Text>
        </div>
        {payload ? <Text c="dimmed" size="sm">{payload.entries.length} loaded</Text> : null}
      </Group>

      <Paper component="form" onSubmit={applyFilters} p="lg" withBorder>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
          <TextInput aria-label="Filter by session" onChange={(event) => update("session", event.currentTarget.value)} placeholder="Any session" value={draftFilters.session} />
          <TextInput aria-label="Filter by repository" onChange={(event) => update("repository", event.currentTarget.value)} placeholder="Any repository" value={draftFilters.repository} />
          <TextInput aria-label="Filter by hook" onChange={(event) => update("hook", event.currentTarget.value)} placeholder="Any hook" value={draftFilters.hook} />
          <Select aria-label="Filter by outcome" clearable data={[
            { value: "refused", label: "Refused" },
            { value: "confirmation-required", label: "Confirmation required" },
            { value: "allowed", label: "Allowed" },
            { value: "completed", label: "Completed" },
            { value: "recorded", label: "Recorded" },
          ]} onChange={(value) => update("outcome", value)} placeholder="Any outcome" value={draftFilters.outcome || null} />
        </SimpleGrid>
        <Group mt="md">
          <Button type="submit">Apply filters</Button>
          <Button onClick={() => {
            requestGate.current.invalidate();
            setPayload(null);
            setError("");
            setLoadingMore(false);
            setDraftFilters(EMPTY_FILTERS);
            setFilters({ ...EMPTY_FILTERS });
          }} type="button" variant="default">Clear</Button>
        </Group>
      </Paper>

      {error ? <EvidenceState kind="error" title="Activity unavailable">{error}</EvidenceState> : null}
      {loading && !payload ? <EvidenceState kind="loading" title="Loading local activity">Reading bounded pages from local evidence.</EvidenceState> : null}
      {payload ? <>
        <ActivityTimeline payload={payload} />
        {payload.next_cursor ? <Button loading={loadingMore} onClick={() => void loadOlder()} variant="default">
          {continuationLabel(payload.entries)}
        </Button> : null}
        <CommandChip command={payload.command} />
      </> : null}
    </Stack>
  );
}
