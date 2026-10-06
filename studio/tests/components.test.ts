import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { CodeView, CommandChip, DataTable, DiffView, EvidenceState, IntervalDisplay, StatusBadge, ToastProvider } from "../src/components/StudioKit.tsx";
import { compareLines, intervalSummary } from "../src/components/evidence.ts";

const render = (element) => renderToStaticMarkup(h(MantineProvider, {}, element));

test("the browser fixture is rendered from the current React component kit", () => {
  const current = execFileSync(process.execPath, ["--import", "./scripts/register-tests.mjs", "--experimental-strip-types", "tests/render-kit.mjs"], { encoding: "utf8" });
  assert.equal(current, readFileSync(new URL("./fixtures/kit.html", import.meta.url), "utf8"));
});

test("intervals keep the uncertainty and reject malformed evidence", () => {
  assert.equal(intervalSummary(2.1, -1.4, 5.6, "pts"), "+2.1 pts; 95% interval -1.4 to +5.6; inconclusive, interval crosses zero");
  assert.equal(intervalSummary(4, 2, 8, "ms", 90), "+4 ms; 90% interval +2 to +8");
  for (const input of [[NaN, 0, 1], [2, 4, 1], [9, 0, 2]]) assert.equal(intervalSummary(...input, "pts"), "Interval unavailable");
  assert.equal(intervalSummary(1, 0, 2, "pts", 101), "Interval unavailable");
  assert.match(render(h(IntervalDisplay, { estimate: 2.1, low: -1.4, high: 5.6, unit: "pts" })), /inconclusive/);
});

test("line comparison preserves common edges and shows addition and removal explicitly", () => {
  assert.deepEqual(compareLines("first\nold\nlast", "first\nnew\nlast"), [
    { kind: "context", text: "first" }, { kind: "removed", text: "old" },
    { kind: "added", text: "new" }, { kind: "context", text: "last" },
  ]);
  assert.deepEqual(compareLines("same", "same"), [{ kind: "context", text: "same" }]);
  assert.deepEqual(compareLines("first", "first\nnew"), [{ kind: "context", text: "first" }, { kind: "added", text: "new" }]);
  const html = render(h(DiffView, { before: "<script>old</script>", after: "<script>new</script>" }));
  assert.match(html, /removed: /); assert.match(html, /added: /);
  assert.ok(!html.includes("<script>"));
});

test("evidence states expose urgency and loading without relying on color", () => {
  for (const kind of ["empty", "loading", "error", "refused"]) {
    const html = render(h(EvidenceState, { kind, title: `${kind} evidence` }, "Reason and next action"));
    assert.match(html, new RegExp(`role="${["error", "refused"].includes(kind) ? "alert" : "status"}"`));
    assert.match(html, new RegExp(`aria-busy="${kind === "loading"}"`));
    assert.ok(html.includes(`${kind} evidence`) && html.includes("Reason and next action"));
  }
});

test("table keeps its accessible caption, column headings, empty and many-row states", () => {
  const columns = [{ key: "name", heading: "Name", cell: (row) => row.name }];
  const rows = Array.from({ length: 120 }, (_, id) => ({ id: String(id), name: `Run ${id}` }));
  const html = render(h(DataTable, { caption: "Recent runs", columns, rows, rowKey: (row) => row.id }));
  assert.match(html, /<caption id=/); assert.match(html, /scope="col"/);
  assert.equal((html.match(/<td>/g) ?? []).length, 120);
  assert.match(render(h(DataTable, { caption: "Runs", columns, rows: [], rowKey: (row) => row.id })), /No records yet/);
});

test("commands render the caller's exact string, and code remains inert and focusable", () => {
  const command = "citizen configure save --draft 'review first' --changes changes.json --base-revision abc";
  const html = render(h(ToastProvider, {}, h(CommandChip, { command, label: "Save checkpoint" })));
  assert.match(html, /Copy command: Save checkpoint/); assert.match(html, /citizen configure save/);
  assert.match(html, /&#x27;review first&#x27;/);
  const code = render(h(CodeView, {}, "<img onerror=bad()>"));
  assert.match(code, /tabindex="0"/); assert.ok(!code.includes("<img"));
  assert.match(render(h(StatusBadge, { tone: "warning" }, "Needs attention")), /Needs attention/);
});
