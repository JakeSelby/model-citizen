import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import test from "node:test";

import { filterLibrary, type LibraryModule } from "../src/library/model.ts";

function moduleAt(index: number): LibraryModule {
  return {
    key: `core:rules:rule-${index}`,
    name: `rule-${index}`,
    kind: "rules",
    root: { id: `root-${index % 5}`, label: `Root ${index % 5}`, path: "/root", core: false },
    state: { value: index % 3 === 0 ? "off" : "on", layer: "user", switchable: true },
    collision: false,
    manifest: null,
    source: { path: `/rules/rule-${index}.md`, text: "source" },
    rendered: { text: "rendered" },
    projections: [],
    context_cost: { tokens: index * 5, estimate: "soft estimate", method: "chars/4" },
  };
}

test("search and every library filter compose over 500 modules within 100 ms", () => {
  const modules = Array.from({ length: 500 }, (_, index) => moduleAt(index));
  const started = performance.now();
  const result = filterLibrary(modules, {
    query: "rule-4",
    kind: "rules",
    root: "root-4",
    state: "on",
    cost: "high",
  });
  const elapsed = performance.now() - started;
  assert.ok(elapsed < 100, `filter took ${elapsed.toFixed(3)} ms`);
  assert.ok(result.length > 0);
  assert.ok(result.every((item) => item.root.id === "root-4"
    && item.state.value === "on" && item.context_cost.tokens >= 1000));
});
