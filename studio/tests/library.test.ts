import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";

import { filterLibrary, LibraryRequestGate, repositoryRelativePath, type LibraryModule } from "../src/library/model.ts";
import { LibraryGroups, sourceLineId } from "../src/library/LibraryPage.tsx";

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

test("modules are grouped by kind with details collapsed and all evidence retained", () => {
  const modules = [moduleAt(1), { ...moduleAt(2), kind: "hooks" }, moduleAt(3)];
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(LibraryGroups, { modules })));
  assert.equal((html.match(/class="library-module"/g) ?? []).length, 3);
  assert.equal((html.match(/aria-label="rules modules"/g) ?? []).length, 1);
  assert.equal((html.match(/aria-label="hooks modules"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /<details[^>]*open/);
  assert.match(html, /State provenance/);
  assert.match(html, /Runtime projections/);
  assert.match(html, /Rendered view/);
  assert.ok(html.indexOf('aria-label="hooks modules"') < html.indexOf('aria-label="rules modules"'));
});

test("shared ownership appears in the group and only exceptions appear on collapsed rows", () => {
  const first = moduleAt(1);
  const modules = [first, { ...moduleAt(2), root: first.root }, moduleAt(3)];
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(LibraryGroups, { modules })));
  assert.equal((html.match(/class="library-module-root"/g) ?? []).length, 1);
  assert.match(html, /Root 1 \+ others/);
  assert.match(html, /class="library-module-root">Root 3/);
});

test("file-line links filter by source path, expand the module, and mark the exact line", () => {
  const repository = "/checkout";
  const module = { ...moduleAt(1), source: { path: `${repository}/rules/rule-1.md`, text: "first\nsecond\nthird" } };
  const relative = "rules/rule-1.md";
  assert.equal(repositoryRelativePath(repository, module.source.path), relative);
  assert.deepEqual(filterLibrary([module], { ...({ query: relative, kind: "", root: "", state: "", cost: "" }) }), [module]);
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(LibraryGroups, {
    modules: [module], focusedPath: relative, focusedLine: 2, repository,
  })));
  assert.match(html, /<details class="library-module"[^>]* open=""/);
  assert.match(html, new RegExp(`id="${sourceLineId(module.key, 2)}"[^>]*tabindex="-1"`));
  assert.match(html, /library-source-line focused/);
});

test("request generations prevent a stale library response from replacing newer inventory", () => {
  const gate = new LibraryRequestGate();
  const slower = gate.next();
  const newer = gate.next();
  assert.equal(gate.accepts(newer), true);
  assert.equal(gate.accepts(slower), false);
  gate.invalidate();
  assert.equal(gate.accepts(newer), false);
});
