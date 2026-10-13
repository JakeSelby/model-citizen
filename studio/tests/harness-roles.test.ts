import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { periodsQuery } from "../src/harness/api.ts";
import { HarnessFrame } from "../src/harness/HarnessPage.tsx";
import type { DraftChange, HarnessTab, PeriodsResponse } from "../src/harness/model.ts";
import { draftHandoff, LIBRARY_QUERY_KEY } from "../src/pages/harness/librarySettings.ts";
import { LIBRARY } from "./fixtures/harness-library.ts";
import { ROLES_CONTENT, RolesPage } from "../src/pages/harness/RolesPage.tsx";
import {
  BUILT_IN_ROW, CHANGES_PATH, headerField, roleSettings, selectionChanges,
} from "../src/pages/harness/rolesWorkflowsModel.ts";

// Hand-typed in the shape `GET /api/harness/periods?surface=roles` answers with.
const NOTE = "No setting change was recorded for these items in this window, so each shows one period.";
const PERIODS: PeriodsResponse = {
  surface: "roles",
  note: NOTE,
  rows: [
    {
      item_id: "builder", item_label: "builder", setting_label: "strong · medium effort",
      period: { start: "2026-09-12T10:00:00Z", end: null }, status: "measured", window_days: 7,
      metrics: { spawns: 570, model: "claude-opus-5-5 +3", effort: "medium", cost: "$3525.73",
        cost_per_spawn: "$2.82 · $7.01", unpriced: 0, over_budget: "12% · 67 of 545", tier_rewrites: 4 },
    },
    {
      item_id: "built-in", item_label: "Built-in and unnamed agents", setting_label: "Session model · runtime default",
      period: { start: "2026-09-13T10:00:00Z", end: null }, status: "measured", window_days: 7,
      metrics: { spawns: 378, unpriced: 2, over_budget: "no budget", tier_rewrites: 0 },
    },
  ],
};

function render(node: ReactNode, path: string, client: QueryClient): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function client(): QueryClient {
  const value = new QueryClient();
  value.setQueryData(periodsQuery("roles").queryKey, PERIODS);
  value.setQueryData(LIBRARY_QUERY_KEY, LIBRARY);
  return value;
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

test("the Roles page renders each role's period with spawns, tier, effort, cost and budget", () => {
  const html = render(h(RolesPage), "/harness/roles", client());
  assert.match(html, /<h1[^>]*>Roles<\/h1>/);
  assert.ok(html.includes("Roles: efficacy by setting period"));
  for (const heading of ["Spawns", "Model that ran", "Effort that ran", "Cost p50 · p75", "Over budget", "Tier rewrites"]) {
    assert.ok(html.includes(heading), heading);
  }
  const builder = /<tbody[^>]*data-item="builder"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  for (const value of ["strong · medium effort", "570", "claude-opus-5-5 +3", "$3525.73", "$2.82 · $7.01", "12% · 67 of 545", "since Sep 12"]) {
    assert.ok(builder.includes(value), value);
  }
  const builtIn = /<tbody[^>]*data-item="built-in"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(builtIn.includes("Session model · runtime default"));
  assert.ok(builtIn.includes("not measured"), "a cost the API did not send is never shown as zero");
  assert.ok(builtIn.includes("no budget"));
});

test("the periods note is rendered under the table", () => {
  const html = render(h(RolesPage), "/harness/roles", client());
  assert.ok(html.includes(NOTE));
  const bare = client();
  bare.setQueryData(periodsQuery("roles").queryKey, { ...PERIODS, note: undefined });
  assert.ok(!render(h(RolesPage), "/harness/roles", bare).includes(NOTE));
});

test("Settings shows each role's class, model and effort read-only and its switch editable", () => {
  const rows = roleSettings(LIBRARY);
  assert.deepEqual(rows.map((row) => row.item_id), ["builder", "worker-a", "built-in"]);
  assert.deepEqual(rows[0].values, { tier: "strong", model: "opus", effort: "medium", enabled: "on" });
  assert.deepEqual(rows[0].options, { enabled: ["on", "off"] });
  assert.equal(rows[0].item_detail, "Implement one issue");
  assert.deepEqual(rows[1].values, { tier: "standard", model: "sonnet", effort: "low", enabled: "off" });
  assert.match(rows[1].last_change_note ?? "", /^set by user/);
  assert.equal(rows[2], BUILT_IN_ROW);
  assert.equal(headerField("no header", "tier"), "");

  const html = render(h(RolesPage), "/harness/roles?tab=settings", client());
  assert.ok(html.includes("Roles: settings"));
  assert.ok(html.includes("Tier (read-only)"));
  assert.ok(html.includes("Effort (read-only)"));
  assert.ok(html.includes('aria-label="Enabled: builder"'), "the switch is a control");
  assert.ok(!html.includes('aria-label="Tier: builder"'), "the class has no editing route, so no control");
  assert.ok(html.includes("Built-in and unnamed agents"));
  assert.ok(html.includes("Set by the runtime"));
});

test("saving a switch calls onDraft, which hands a selection change to Changes", () => {
  const rows = roleSettings(LIBRARY);
  const drafts: DraftChange[][] = [];
  const tree = HarnessFrame({
    ...ROLES_CONTENT, tab: "settings" as HarnessTab, onTabChange: () => undefined,
    periods: { state: "ready", rows: PERIODS.rows, note: PERIODS.note },
    settings: { state: "ready", rows },
    edits: { builder: { enabled: "off" } },
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes) => drafts.push(changes),
  });
  const panel = elements(tree).find((element) => typeof element.props.onDraft === "function" && element.props.rows === rows);
  assert.ok(panel, "the settings table receives the page's rows");
  const table = (panel.type as (props: Record<string, unknown>) => ReactNode)(panel.props);
  const send = elements(table).find((element) => element.type === Button && text(element) === "Send to Changes");
  assert.ok(send);
  (send.props.onClick as () => void)();
  const expected: DraftChange[] = [{ item_id: "builder", field: "enabled", from: "on", to: "off" }];
  assert.deepEqual(drafts, [expected]);

  const visits: Array<[string, unknown]> = [];
  draftHandoff("roles", expected, (to, options) => visits.push([to, options]));
  assert.deepEqual(visits, [[CHANGES_PATH, { state: { harnessDraft: { surface: "roles", selection: { "roles.builder": "off" } } } }]]);
  assert.deepEqual(selectionChanges("roles", [{ item_id: "builder", field: "tier", from: "strong", to: "light" }]), {},
    "a read-only field never becomes a selection change");
});

test("Settings show a loading state until the library arrives", () => {
  const failing = new QueryClient();
  failing.setQueryData(periodsQuery("roles").queryKey, PERIODS);
  const html = render(h(RolesPage), "/harness/roles?tab=settings", failing);
  assert.ok(html.includes("Reading settings"), "settings load before the library arrives");
});
