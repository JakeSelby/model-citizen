import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { ActivityPage } from "../src/activity/ActivityPage.tsx";
import { periodsQuery } from "../src/harness/api.ts";
import { EfficacyTable } from "../src/harness/EfficacyTable.tsx";
import { isShareMetric, metricText, type PeriodRow, type SettingRow } from "../src/harness/model.ts";
import { SettingsTable } from "../src/harness/SettingsTable.tsx";
import { HooksPage, safetyQuery } from "../src/pages/harness/HooksPage.tsx";

// Hand-typed rows in the shape `/api/harness/periods` answers with; no generator exists.
const ROWS: PeriodRow[] = [{
  item_id: "session", item_label: "Session usage", setting_label: "Default",
  period: { start: "2026-09-24", end: null }, status: "measured", window_days: 7,
  metrics: { input_tokens: 1234567, cache_read_share: 0.8125, pass_rate: 1 },
}];

const SETTINGS: SettingRow[] = [{
  item_id: "session", item_label: "Session usage", values: { scope: "All repos" }, last_changed: null,
}];

function render(element: ReactElement, path = "/"): string {
  const client = new QueryClient();
  client.setQueryData(periodsQuery("hooks").queryKey, { surface: "hooks", rows: [] });
  client.setQueryData(safetyQuery.queryKey, []);
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, element))));
}

function regions(html: string): string[] {
  return [...html.matchAll(/<div[^>]*role="region"[^>]*>/g)].map((match) => match[0]);
}

test("the efficacy table's scroll region is focusable and named", () => {
  const html = render(h(EfficacyTable, { caption: "Rules: efficacy", rows: ROWS, columns: [] }));
  const [region] = regions(html);
  assert.ok(region, "a role=region scroll viewport");
  assert.match(region, /tabindex="0"/);
  assert.match(region, /aria-label="Rules: efficacy"/);
});

test("the settings table's scroll region is focusable and named", () => {
  const html = render(h(SettingsTable, {
    caption: "Rules: settings", rows: SETTINGS, fields: [{ key: "scope", heading: "Scope" }], edits: {},
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined,
  }));
  const [region] = regions(html);
  assert.ok(region, "a role=region scroll viewport");
  assert.match(region, /tabindex="0"/);
  assert.match(region, /aria-label="Rules: settings"/);
});

test("shares and rates read as percentages, integers get separators", () => {
  assert.equal(isShareMetric("cache_read_share"), true);
  assert.equal(isShareMetric("pass_rate"), true);
  assert.equal(isShareMetric("sessions"), false);
  assert.equal(metricText(0.8125, "cache_read_share"), "81.3%");
  assert.equal(metricText(1, "pass_rate"), "100.0%");
  assert.equal(metricText(0, "cache_read_share"), "0.0%");
  assert.equal(metricText(1234567, "input_tokens"), "1,234,567");
  assert.equal(metricText("23% · 3", "broken_in_rate"), "23% · 3");
  assert.equal(metricText(undefined, "cache_read_share"), "not measured");
  const html = render(h(EfficacyTable, { caption: "Usage", rows: ROWS, columns: [
    { key: "input_tokens", heading: "Input tokens" },
    { key: "cache_read_share", heading: "Cache read share" },
    { key: "pass_rate", heading: "Pass rate" },
  ] }));
  for (const shown of ["1,234,567", "81.3%", "100.0%"]) assert.ok(html.includes(shown), shown);
  assert.ok(!html.includes("0.8125"));
});

test("Activity keeps its h1 alone and an h2 when embedded", () => {
  const count = (html: string, tag: string) => (html.match(new RegExp(`<${tag}[\\s>]`, "g")) ?? []).length;
  const standalone = render(h(ActivityPage));
  assert.equal(count(standalone, "h1"), 1);
  const embedded = render(h(ActivityPage, { embedded: true }));
  assert.equal(count(embedded, "h1"), 0);
  assert.match(embedded, /<h2[^>]*>What the harness decided and changed\.<\/h2>/);
});

test("the Hooks page renders exactly one h1", () => {
  const html = render(h(HooksPage), "/harness/hooks?tab=log");
  assert.equal((html.match(/<h1[\s>]/g) ?? []).length, 1);
  assert.ok(html.includes("What the harness decided and changed."), "the Activity timeline is embedded in the Safety log tab");
});
