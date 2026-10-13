import type { LibraryModule, LibraryPayload } from "../../src/library/model.ts";

export function module(kind: string, name: string, source: string, rendered: string, value = "on", layer = "default"): LibraryModule {
  return {
    key: `core:${kind}:${name}`, name, kind,
    root: { id: "core", label: "Core", path: "/repo/primitives", core: true },
    state: { value, layer, switchable: true }, collision: false, manifest: null,
    source: { path: `/repo/primitives/${kind}/${name}.md`, text: source }, rendered: { text: rendered },
    projections: [], context_cost: { tokens: 0, estimate: "", method: "" }, fork: null,
  };
}

// Hand-typed in the shape `GET /api/library` answers with; the texts are cut to their headers.
export const LIBRARY: LibraryPayload = {
  schema_version: 1, repository: "/repo",
  summary: { modules: 4, collisions: 0, roots: 1, generated_ms: 1 },
  modules: [
    module("roles", "worker-a", "---\nname: worker-a\ndescription: Band A work. More.\ntier: standard\n---\n\nbody",
      "---\nname: worker-a\nmodel: sonnet\neffort: low\n---\n\nbody", "off", "user"),
    module("roles", "builder", "---\nname: builder\ndescription: Implement one issue. Never pushes.\ntier: strong\n---\n\nbody",
      "---\nname: builder\ndescription: Implement one issue.\nmodel: opus\ntools: Read\neffort: medium\n---\n\nbody"),
    module("workflows", "build", "---\ndescription: Implement an approved plan.\n---\n\nbody", "body"),
    module("skills", "debugging", "---\nname: debugging\n---\n", ""),
  ],
};
