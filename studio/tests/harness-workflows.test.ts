import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { periodsQuery } from "../src/harness/api.ts";
import { SettingsTable } from "../src/harness/SettingsTable.tsx";
import type { DraftChange, PeriodsResponse } from "../src/harness/model.ts";
import { draftHandoff, LIBRARY_QUERY_KEY } from "../src/pages/harness/librarySettings.ts";
import { WORKFLOW_FIELDS, WORKFLOW_TOOL_ROW, CHANGES_PATH, workflowSettings } from "../src/pages/harness/rolesWorkflowsModel.ts";
import { WorkflowsPage } from "../src/pages/harness/WorkflowsPage.tsx";
import { LIBRARY } from "./fixtures/harness-library.ts";

// Hand-typed in the shape `GET /api/harness/periods?surface=workflows` answers with.
const NOTE = "No setting change was recorded for these items in this window, so each shows one period.";
const PERIODS: PeriodsResponse = {
  surface: "workflows",
  note: NOTE,
  rows: [{
    item_id: "workflow-tool", item_label: "Workflow tool runs", setting_label: "Runtime default · unconfined",
    period: { start: "2026-10-08T10:00:00Z", end: null }, status: "measuring", measuring_day: 5, window_days: 7,
    metrics: { runs: 8, agents: 100, agents_per_run: 12.5, cost: "$114.64", cost_per_run: "$0.18 · $3.80", unpriced_runs: 0 },
  }],
};

function client(): QueryClient {
  const value = new QueryClient();
  value.setQueryData(periodsQuery("workflows").queryKey, PERIODS);
  value.setQueryData(LIBRARY_QUERY_KEY, LIBRARY);
  return value;
}

function render(path: string, queries = client()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client: queries },
    h(MemoryRouter, { initialEntries: [path] }, h(WorkflowsPage)))));
}

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...elements(element.props.children as ReactNode)];
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  return isValidElement(node) ? text((node.props as { children?: ReactNode }).children) : "";
}

test("the Workflows page renders runs, agents per run and cost for the period", () => {
  const html = render("/harness/workflows");
  assert.match(html, /<h1[^>]*>Workflows<\/h1>/);
  for (const heading of ["Runs", "Agents per run", "Cost per run p50 · p75", "Unpriced runs"]) assert.ok(html.includes(heading), heading);
  const body = /<tbody[^>]*data-item="workflow-tool"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  for (const value of ["Workflow tool runs", "Runtime default · unconfined", "12.5", "$114.64", "$0.18 · $3.80", "measuring, day 5 of 7"]) {
    assert.ok(body.includes(value), value);
  }
});

test("the periods note is rendered", () => {
  assert.ok(render("/harness/workflows").includes(NOTE));
});

test("Settings lists each workflow with its source read-only and its switch editable", () => {
  const rows = workflowSettings(LIBRARY);
  assert.deepEqual(rows.map((row) => [row.item_id, row.item_label]), [["build", "/build"], ["workflow-tool", "Workflow tool runs"]]);
  assert.deepEqual(rows[0].values, { source: "Core", enabled: "on" });
  assert.equal(rows[0].item_detail, "Implement an approved plan.");
  assert.equal(rows[1], WORKFLOW_TOOL_ROW);
  const shadowed = { ...LIBRARY, modules: [...LIBRARY.modules, { ...LIBRARY.modules[2], key: "personal:workflows:build" }] };
  assert.equal(workflowSettings(shadowed).filter((row) => row.item_id === "build").length, 1, "a name in two roots is one row");
  const html = render("/harness/workflows?tab=settings");
  assert.ok(html.includes("Workflows: settings"));
  assert.ok(html.includes("Source (read-only)"));
  assert.ok(html.includes('aria-label="Enabled: /build"'));
  assert.ok(!html.includes('aria-label="Enabled: Workflow tool runs"'), "the runtime's switch is not ours to draft");
});

test("saving a switch calls onDraft and hands the selection change to Changes", () => {
  const rows = workflowSettings(LIBRARY);
  const drafts: DraftChange[][] = [];
  const table = SettingsTable({
    caption: "Workflows: settings", rows, fields: WORKFLOW_FIELDS, edits: { build: { enabled: "off" } },
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes) => drafts.push(changes),
  });
  const send = elements(table).find((element) => element.type === Button && text(element) === "Send to Changes");
  assert.ok(send);
  (send.props.onClick as () => void)();
  assert.deepEqual(drafts, [[{ item_id: "build", field: "enabled", from: "on", to: "off" }]]);
  const visits: Array<[string, unknown]> = [];
  draftHandoff("workflows", drafts[0], (to, options) => visits.push([to, options]));
  assert.deepEqual(visits, [[CHANGES_PATH, { state: { harnessDraft: { surface: "workflows", selection: { "workflows.build": "off" } } } }]]);
});
