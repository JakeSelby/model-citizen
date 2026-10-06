import {
  Badge, Button, Group, NumberInput, Paper, ScrollArea, Select, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { EvidenceState, StatusBadge } from "../../components/StudioKit";
import { useLiveUpdates } from "../../live/LiveUpdates";
import { loadHistory } from "./api";
import { displayUnknown, emptyFilters, type HistoryFilters, type HistoryPage } from "./model";

export function RunHistoryPanel() {
  const [filters, setFilters] = useState<HistoryFilters>(emptyFilters);
  const [pages, setPages] = useState<HistoryPage[] | null>(null);
  const [error, setError] = useState("");
  const request = useRef(0);
  const pagesRef = useRef<HistoryPage[] | null>(null);
  const filtersRef = useRef(filters);
  const refreshing = useRef(false);
  const refreshPending = useRef(false);

  const replacePages = useCallback((value: HistoryPage[] | null) => {
    pagesRef.current = value;
    setPages(value);
  }, []);

  const refreshLoaded = useCallback(async () => {
    if (refreshing.current) {
      refreshPending.current = true;
      return;
    }
    refreshing.current = true;
    const serial = ++request.current;
    try {
      const count = Math.max(1, pagesRef.current?.length ?? 1);
      const loaded: HistoryPage[] = [];
      let cursor: string | null = null;
      for (let index = 0; index < count; index += 1) {
        const page = await loadHistory(filtersRef.current, cursor);
        loaded.push(page);
        cursor = page.next_cursor;
        if (!cursor) break;
      }
      if (serial !== request.current) return;
      replacePages(loaded);
      setError("");
    } catch (caught) {
      if (serial === request.current) setError(caught instanceof Error ? caught.message : "History unavailable.");
    } finally {
      refreshing.current = false;
      if (refreshPending.current) {
        refreshPending.current = false;
        void refreshLoaded();
      }
    }
  }, [replacePages]);

  const loadMore = useCallback(async () => {
    const current = pagesRef.current;
    const cursor = current?.at(-1)?.next_cursor;
    if (!current || !cursor) return;
    const serial = request.current;
    try {
      const loaded = await loadHistory(filtersRef.current, cursor);
      if (serial !== request.current) return;
      replacePages([...current, loaded]);
      setError("");
    } catch (caught) {
      if (serial === request.current) setError(caught instanceof Error ? caught.message : "History unavailable.");
    }
  }, [replacePages]);

  useEffect(() => {
    filtersRef.current = filters;
    request.current += 1;
    replacePages(null);
    void refreshLoaded();
  }, [filters, refreshLoaded, replacePages]);

  useLiveUpdates(["runs"], () => { void refreshLoaded(); });

  const page = pages && {
    items: pages.flatMap((item) => item.items),
    next_cursor: pages.at(-1)?.next_cursor ?? null,
  };

  function text(name: keyof HistoryFilters, value: string) {
    setFilters((current) => ({ ...current, [name]: value || null }));
  }

  function number(name: keyof HistoryFilters, value: string | number) {
    setFilters((current) => ({ ...current, [name]: typeof value === "number" ? value : null }));
  }

  return <Paper className="run-history">
    <Stack gap="md">
      <Group justify="space-between"><div><Text className="eyebrow">Every recorded run</Text><Title order={2}>Run history</Title></div>
        <Button variant="default" onClick={() => void refreshLoaded()}>Refresh</Button></Group>
      <div className="run-history-filters">
        <TextInput label="Suite" value={filters.suite_id ?? ""} onChange={(event) => text("suite_id", event.currentTarget.value)} />
        <TextInput label="Target" value={filters.target ?? ""} onChange={(event) => text("target", event.currentTarget.value)} />
        <Select clearable label="Status" value={filters.status} onChange={(value) => text("status", value ?? "")}
          data={["queued", "admitted", "starting", "running", "cancel_requested", "succeeded", "failed", "cancelled", "timed_out", "orphaned", "capped", "limited", "unknown"]} />
        <TextInput label="From" type="date" value={filters.created_from ?? ""} onChange={(event) => text("created_from", event.currentTarget.value)} />
        <TextInput label="To" type="date" value={filters.created_to ?? ""} onChange={(event) => text("created_to", event.currentTarget.value)} />
        <NumberInput label="Minimum cost ($)" min={0} value={filters.min_cost_usd ?? ""} onChange={(value) => number("min_cost_usd", value)} />
        <NumberInput label="Maximum cost ($)" min={0} value={filters.max_cost_usd ?? ""} onChange={(value) => number("max_cost_usd", value)} />
        <NumberInput label="Minimum duration (ms)" min={0} value={filters.min_duration_ms ?? ""} onChange={(value) => number("min_duration_ms", value)} />
        <NumberInput label="Maximum duration (ms)" min={0} value={filters.max_duration_ms ?? ""} onChange={(value) => number("max_duration_ms", value)} />
      </div>
      {error && <EvidenceState kind="error" title="History unavailable">{error}</EvidenceState>}
      {!page && !error && <EvidenceState kind="loading" title="Loading history">Reading the rebuildable index.</EvidenceState>}
      {page && <ScrollArea type="auto" viewportProps={{ role: "region", "aria-label": "Run history", tabIndex: 0 }}><Table className="data-table" highlightOnHover>
        <Table.Thead><Table.Tr><Table.Th>Run</Table.Th><Table.Th>Status</Table.Th><Table.Th>Target</Table.Th><Table.Th>Cost</Table.Th><Table.Th>Duration</Table.Th><Table.Th>Date</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{page.items.map((run) => <Table.Tr key={run.run_id}>
          <Table.Td><Text component={NavLink} to={`/experiments/runs/${run.run_id}`} fw={600}>{run.suite_id}</Text>
            {run.flaky_count > 0 && <Badge color="orange" ml="xs">{run.flaky_count} flaky</Badge>}</Table.Td>
          <Table.Td><StatusBadge>{run.status}</StatusBadge></Table.Td>
          <Table.Td>{displayUnknown(run.target.ref ?? run.target.kind)}</Table.Td>
          <Table.Td>{run.cost_usd === null ? "Unknown" : `$${run.cost_usd.toFixed(4)}`}</Table.Td>
          <Table.Td>{displayUnknown(run.duration_ms, " ms")}</Table.Td>
          <Table.Td>{displayUnknown(run.created_at)}</Table.Td>
        </Table.Tr>)}</Table.Tbody>
      </Table></ScrollArea>}
      {page?.next_cursor && <Button variant="default" onClick={() => void loadMore()}>Load more</Button>}
    </Stack>
  </Paper>;
}
