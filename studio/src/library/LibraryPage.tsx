import {
  Anchor,
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import { CodeView, EvidenceState, StatusBadge } from "../components/StudioKit";
import { useLiveUpdates } from "../live/LiveUpdates";
import { updateTouchesPaths } from "../live/model";
import { TestThisRule } from "../experiments/evals/EvalTiersPanel";
import { loadEvalCatalog } from "../experiments/evals/api";
import { unitFor, type EvalCatalog } from "../experiments/evals/model";
import { loadLibrary } from "./api";
import { filterLibrary, LibraryRequestGate, repositoryRelativePath, type LibraryFilters, type LibraryModule, type LibraryPayload, type ModuleFork } from "./model";
import "./library.css";

const EMPTY_FILTERS: LibraryFilters = { query: "", kind: "", root: "", state: "", cost: "" };

export function sourceLineId(moduleKey: string, line: number): string {
  return `library-source-${encodeURIComponent(moduleKey)}-${line}`;
}

export function ForkProvenance({ fork }: { fork: ModuleFork }) {
  const from = fork.version ? ` at ${fork.version}` : "";
  const revision = fork.revision ? ` (${fork.revision.slice(0, 12)})` : "";
  return (
    <div className="library-fork">
      <Text fw={650} size="sm">Forked from core <Code>{fork.source}</Code>{from}{revision}</Text>
      {fork.upstream.missing ? (
        <Text c="orange" size="sm">The core original is no longer installed{fork.upstream.diff
          ? "; the diff shows what it was."
          : ". The original as forked is not in this checkout's history, so no diff is shown."}</Text>
      ) : fork.upstream.changed && !fork.upstream.original_available ? (
        <Text c="orange" size="sm">The core original changed since this fork. The original as forked is not in this checkout's history, so no diff is shown.</Text>
      ) : fork.upstream.changed ? (
        <Text c="orange" size="sm">The core original changed since this fork. Upstream diff, from the original as forked to core now:</Text>
      ) : (
        <Text c="dimmed" size="sm">The core original is unchanged since this fork.</Text>
      )}
      {fork.upstream.diff ? <CodeView label={`Upstream diff for ${fork.source}`}>{fork.upstream.diff}</CodeView> : null}
    </div>
  );
}

function ModuleDetail({ module, sharedRoot, focusedPath, focusedLine, repository, evals }: {
  module: LibraryModule; sharedRoot: string; focusedPath: string; focusedLine: number | null;
  repository: string; evals: EvalCatalog | null;
}) {
  const unit = unitFor(evals, module.kind, module.name);
  const sourcePath = repositoryRelativePath(repository, module.source.path);
  const focused = sourcePath === focusedPath && focusedLine !== null;
  return (
    <details className="library-module" data-source-path={sourcePath} open={focused || undefined}>
      <summary className="library-module-summary">
        <span className="library-module-name">{module.name}</span>
        {module.root.id !== sharedRoot && <span className="library-module-root">{module.root.label}</span>}
        <span className="library-module-state">
          {module.fork ? <Badge variant="light">Fork of {module.fork.source}</Badge> : null}
          {module.fork?.upstream.changed ? <Badge color="orange">Upstream changed</Badge> : null}
          {module.collision ? <Badge color="orange">Collision</Badge> : null}
          <StatusBadge>{module.state.value}</StatusBadge>
        </span>
      </summary>
      <Stack className="library-module-body" gap="md">
      <Text c="dimmed" size="sm">Ownership: {module.root.label}</Text>
      {module.fork ? <ForkProvenance fork={module.fork} /> : null}
      {unit && evals ? <TestThisRule catalog={evals} unit={unit} /> : null}
      <Group gap="xs">
        <Badge variant="light">{module.context_cost.tokens.toLocaleString()} tokens</Badge>
        <Text c="dimmed" size="xs">{module.context_cost.estimate} · {module.context_cost.method}</Text>
      </Group>
      <Text size="sm"><strong>State provenance:</strong> {module.state.layer} · {module.state.switchable ? "Selectable" : "Informational"}</Text>
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
            <Code block className="library-code">{focused ? module.source.text.split("\n").map((line, index) => {
              const lineNumber = index + 1;
              return <span className={lineNumber === focusedLine ? "library-source-line focused" : "library-source-line"}
                id={sourceLineId(module.key, lineNumber)} key={lineNumber}
                tabIndex={lineNumber === focusedLine ? -1 : undefined}>{line || " "}{"\n"}</span>;
            }) : module.source.text}</Code>
          </div>
          <div>
            <Text fw={650} size="sm">Rendered view</Text>
            <Code block className="library-code">{module.rendered.text}</Code>
          </div>
      </Stack>
    </details>
  );
}

export function LibraryGroups({ modules, focusedPath = "", focusedLine = null, repository = "", evals = null }: {
  modules: LibraryModule[]; focusedPath?: string; focusedLine?: number | null; repository?: string;
  evals?: EvalCatalog | null;
}) {
  const kinds = [...new Set(modules.map((module) => module.kind))].sort();
  return <Stack gap="lg">{kinds.map((kind) => {
    const items = modules.filter((module) => module.kind === kind);
    const roots = new Map<string, { label: string; count: number }>();
    for (const item of items) {
      roots.set(item.root.id, { label: item.root.label, count: (roots.get(item.root.id)?.count ?? 0) + 1 });
    }
    const [sharedRoot, ownership] = [...roots.entries()].sort((a, b) => b[1].count - a[1].count)[0];
    return <Paper className="library-group" component="section" key={kind} withBorder aria-label={`${kind} modules`}>
      <Group className="library-group-heading" justify="space-between">
        <Group gap="xs"><Title order={2}>{kind}</Title><Text c="dimmed" size="sm">{ownership.label}{roots.size > 1 ? " + others" : ""}</Text></Group>
        <Badge variant="light">{items.length}</Badge>
      </Group>
      {items.map((module) => <ModuleDetail evals={evals} focusedLine={focusedLine} focusedPath={focusedPath}
        key={module.key} module={module} repository={repository} sharedRoot={sharedRoot} />)}
    </Paper>;
  })}</Stack>;
}

export function LibraryPage() {
  const [searchParams] = useSearchParams();
  const focusedPath = searchParams.get("path") ?? "";
  const requestedLine = Number(searchParams.get("line"));
  const focusedLine = Number.isSafeInteger(requestedLine) && requestedLine > 0 ? requestedLine : null;
  const [payload, setPayload] = useState<LibraryPayload | null>(null);
  const [error, setError] = useState("");
  const [evals, setEvals] = useState<EvalCatalog | null>(null);
  const [filters, setFilters] = useState<LibraryFilters>(EMPTY_FILTERS);
  const requestGate = useRef(new LibraryRequestGate());
  const filtered = useMemo(
    () => filterLibrary(payload?.modules ?? [], filters),
    [payload, filters],
  );
  const visiblePaths = useMemo(() => new Set(filtered.map((module) => module.source.path)), [filtered]);
  const reload = useCallback(async () => {
    const generation = requestGate.current.next();
    try {
      const value = await loadLibrary();
      if (!requestGate.current.accepts(generation)) return;
      setPayload(value);
      setError("");
    } catch (reason) {
      if (!requestGate.current.accepts(generation)) return;
      setError(reason instanceof Error ? reason.message : "Library unavailable.");
    }
  }, []);

  useEffect(() => {
    void reload();
    return () => { requestGate.current.invalidate(); };
  }, [reload]);
  useEffect(() => {
    let active = true;
    // No unit eval is offered while the catalog is unread or its engine is absent.
    void loadEvalCatalog().then((value) => { if (active) setEvals(value); }).catch(() => {});
    return () => { active = false; };
  }, []);
  useLiveUpdates(["library", "library-index"], () => { void reload(); },
    (event) => event.topics.includes("library-index") || updateTouchesPaths(event, visiblePaths));

  useEffect(() => {
    if (!focusedPath) return;
    setFilters((current) => ({ ...current, query: focusedPath }));
  }, [focusedPath]);

  useEffect(() => {
    if (!payload || !focusedPath || focusedLine === null) return;
    const module = payload.modules.find((item) =>
      repositoryRelativePath(payload.repository, item.source.path) === focusedPath);
    const line = module ? document.getElementById(sourceLineId(module.key, focusedLine)) : null;
    line?.focus({ preventScroll: true });
    line?.scrollIntoView({ block: "center" });
  }, [payload, focusedPath, focusedLine]);

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
        <Group gap="sm">
          {payload ? <Text c="dimmed" size="sm">{payload.summary.modules} modules · {payload.summary.roots} roots</Text> : null}
          <Anchor component={NavLink} size="sm" to="/reports/rules">Rule health</Anchor>
        </Group>
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
      {payload && focusedPath && filtered.length > 0 ? <Text aria-live="polite" size="sm">
        Opened <Code>{focusedPath}</Code>{focusedLine === null ? "" : ` at line ${focusedLine}`}.
      </Text> : null}
      <Stack gap="md" aria-live="polite">
        {payload ? <Text c="dimmed" size="sm">Showing {filtered.length} of {payload.summary.modules} modules</Text> : null}
        <LibraryGroups evals={evals} focusedLine={focusedLine} focusedPath={focusedPath} modules={filtered}
          repository={payload?.repository ?? ""} />
      </Stack>
    </Stack>
  );
}
