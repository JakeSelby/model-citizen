import {
  Badge,
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
import { useEffect, useMemo, useState } from "react";

import { EvidenceState, StatusBadge } from "../components/StudioKit";
import { loadLibrary } from "./api";
import { filterLibrary, type LibraryFilters, type LibraryModule, type LibraryPayload } from "./model";

const EMPTY_FILTERS: LibraryFilters = { query: "", kind: "", root: "", state: "", cost: "" };

function ModuleDetail({ module }: { module: LibraryModule }) {
  return (
    <Paper className="library-module" p="lg" withBorder>
      <Group align="flex-start" justify="space-between" wrap="nowrap">
        <div>
          <Group gap="xs">
            <Title order={3}>{module.name}</Title>
            {module.collision ? <Badge color="orange">Collision</Badge> : null}
          </Group>
          <Text c="dimmed" size="sm">{module.kind} · {module.root.label}</Text>
        </div>
        <StatusBadge>{module.state.value}</StatusBadge>
      </Group>
      <Group gap="xs" mt="md">
        <Badge variant="light">{module.context_cost.tokens.toLocaleString()} tokens</Badge>
        <Text c="dimmed" size="xs">{module.context_cost.estimate} · {module.context_cost.method}</Text>
      </Group>
      <Text mt="md" size="sm"><strong>State provenance:</strong> {module.state.layer} · {module.state.switchable ? "Selectable" : "Informational"}</Text>
      <details className="library-detail">
        <summary>Manifest, projections, and source</summary>
        <Stack gap="md" mt="md">
          <div>
            <Text fw={650} size="sm">Manifest</Text>
            <Code block>{JSON.stringify(module.manifest, null, 2)}</Code>
          </div>
          <div>
            <Text fw={650} size="sm">Runtime projections</Text>
            {module.projections.length ? module.projections.map((projection) => (
              <Text key={`${projection.runtime}:${projection.path}`} size="sm">
                {projection.runtime}: <Code>{projection.path}</Code>
              </Text>
            )) : <Text c="dimmed" size="sm">No direct runtime file; this module changes selection only.</Text>}
          </div>
          <div>
            <Text fw={650} size="sm">Source · <Code>{module.source.path}</Code></Text>
            <Code block className="library-code">{module.source.text}</Code>
          </div>
          <div>
            <Text fw={650} size="sm">Rendered view</Text>
            <Code block className="library-code">{module.rendered.text}</Code>
          </div>
        </Stack>
      </details>
    </Paper>
  );
}

export function LibraryPage() {
  const [payload, setPayload] = useState<LibraryPayload | null>(null);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState<LibraryFilters>(EMPTY_FILTERS);

  useEffect(() => {
    let active = true;
    void loadLibrary().then((value) => { if (active) setPayload(value); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Library unavailable."); });
    return () => { active = false; };
  }, []);

  const filtered = useMemo(
    () => filterLibrary(payload?.modules ?? [], filters),
    [payload, filters],
  );
  const kinds = [...new Set(payload?.modules.map((module) => module.kind) ?? [])].sort();
  const roots = [...new Map(payload?.modules.map((module) => [module.root.id, module.root]) ?? []).values()];
  const update = (key: keyof LibraryFilters) => (value: string | null) => {
    setFilters((current) => ({ ...current, [key]: value ?? "" }));
  };

  return (
    <Stack gap="xl">
      <Group align="flex-end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Studio / Library</Text>
          <Title order={1}>Every module, from source to runtime.</Title>
          <Text c="dimmed" mt="xs">Inspect ownership, selection provenance, projections, and static context cost.</Text>
        </div>
        {payload ? <Text c="dimmed" size="sm">{payload.summary.modules} modules · {payload.summary.roots} roots</Text> : null}
      </Group>

      <Paper p="lg" withBorder>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }} spacing="sm">
          <TextInput aria-label="Search modules" onChange={(event) => update("query")(event.currentTarget.value)} placeholder="Search modules" value={filters.query} />
          <Select aria-label="Filter by kind" clearable data={kinds} onChange={update("kind")} placeholder="All kinds" value={filters.kind || null} />
          <Select aria-label="Filter by root" clearable data={roots.map((root) => ({ value: root.id, label: root.label }))} onChange={update("root")} placeholder="All roots" value={filters.root || null} />
          <Select aria-label="Filter by state" clearable data={["on", "off"]} onChange={update("state")} placeholder="Any state" value={filters.state || null} />
          <Select aria-label="Filter by cost" clearable data={[{ value: "none", label: "0 tokens" }, { value: "low", label: "1–99" }, { value: "medium", label: "100–999" }, { value: "high", label: "1,000+" }]} onChange={update("cost")} placeholder="Any cost" value={filters.cost || null} />
        </SimpleGrid>
      </Paper>

      {error ? <EvidenceState kind="error" title="Library unavailable">{error}</EvidenceState> : null}
      {!payload && !error ? <EvidenceState kind="loading" title="Loading module library">Reading the local harness inventory.</EvidenceState> : null}
      {payload && !filtered.length ? <EvidenceState kind="empty" title="No modules match">Clear a filter to widen the result.</EvidenceState> : null}
      <Stack gap="md" aria-live="polite">
        {payload ? <Text c="dimmed" size="sm">Showing {filtered.length} of {payload.summary.modules} modules</Text> : null}
        {filtered.map((module) => <ModuleDetail key={module.key} module={module} />)}
      </Stack>
    </Stack>
  );
}
