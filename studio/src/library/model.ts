export type ModuleProjection = { runtime: string; path: string };

export type LibraryModule = {
  key: string;
  name: string;
  kind: string;
  root: { id: string; label: string; path: string; core: boolean };
  state: { value: string; layer: string; switchable: boolean };
  collision: boolean;
  manifest: Record<string, unknown> | null;
  source: { path: string; text: string };
  rendered: { text: string };
  projections: ModuleProjection[];
  context_cost: { tokens: number; estimate: string; method: string };
};

export type LibraryPayload = {
  schema_version: number;
  modules: LibraryModule[];
  summary: { modules: number; collisions: number; roots: number; generated_ms: number };
};

export type LibraryFilters = {
  query: string;
  kind: string;
  root: string;
  state: string;
  cost: string;
};

export function filterLibrary(modules: LibraryModule[], filters: LibraryFilters): LibraryModule[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return modules.filter((module) => {
    const haystack = `${module.name} ${module.kind} ${module.root.label}`.toLocaleLowerCase();
    const tokens = module.context_cost.tokens;
    const costMatches = filters.cost === ""
      || (filters.cost === "none" && tokens === 0)
      || (filters.cost === "low" && tokens > 0 && tokens < 100)
      || (filters.cost === "medium" && tokens >= 100 && tokens < 1000)
      || (filters.cost === "high" && tokens >= 1000);
    return (!query || haystack.includes(query))
      && (!filters.kind || module.kind === filters.kind)
      && (!filters.root || module.root.id === filters.root)
      && (!filters.state || module.state.value === filters.state)
      && costMatches;
  });
}
