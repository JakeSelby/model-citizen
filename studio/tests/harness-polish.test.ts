import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { EfficacyTable } from "../src/harness/EfficacyTable.tsx";
import { HarnessFrame, type PeriodsState, type SettingsState } from "../src/harness/HarnessPage.tsx";
import type { HarnessTab, PeriodRow, SettingRow } from "../src/harness/model.ts";
import { SettingsTable } from "../src/harness/SettingsTable.tsx";

const CSS = readFileSync(new URL("../src/harness/harness.css", import.meta.url), "utf8");

// Hand-typed in the shape `/api/harness/periods` answers with; no generator exists.
const ROW: PeriodRow = {
  item_id: "decisions-and-plans", item_label: "decisions-and-plans", setting_label: "on",
  period: { start: "2026-09-12T12:00:00Z", end: null }, status: "measured", window_days: 7, metrics: { sessions: 3 },
};
const SETTING: SettingRow = {
  item_id: "decisions-and-plans", item_label: "Decisions and plans", item_detail: "primitives/rules/decisions-and-plans.md",
  values: { on: "on" }, options: { on: ["on", "off"] }, last_changed: null,
};
const FIELDS = [{ key: "on", heading: "On" }];

function wrap(node: ReactElement): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client: new QueryClient() },
    h(MemoryRouter, {}, node))));
}

function rule(selector: string): string {
  const start = CSS.indexOf(selector + " {");
  assert.ok(start >= 0, `harness.css has a rule for ${selector}`);
  return CSS.slice(start, CSS.indexOf("}", start));
}

function settings(overrides: Record<string, unknown> = {}): string {
  return wrap(h(SettingsTable, {
    caption: "Rules: settings", rows: [SETTING], fields: FIELDS, edits: {}, onEdit: () => undefined,
    onDiscard: () => undefined, onDraft: () => undefined, draftControls: h("p", {}, "PICKER"), ...overrides,
  }));
}

test("a name never wraps, so an id never breaks at a hyphen", () => {
  assert.match(rule(".harness-table .harness-name"), /white-space: nowrap/);
});

test("both tables put the name in a capped block in the sticky column", () => {
  const efficacy = wrap(h(EfficacyTable, { caption: "Rules", rows: [ROW], columns: [{ key: "sessions", heading: "Sessions" }] }));
  assert.match(efficacy, /<th[^>]*class="[^"]*harness-sticky[^"]*"[^>]*><span class="harness-name" title="decisions-and-plans">decisions-and-plans</);
  const html = settings();
  assert.match(html, /harness-sticky[\s\S]*?class="[^"]*harness-name[^"]*"[^>]*title="Decisions and plans"[^>]*>Decisions and plans</);
  assert.match(html, /class="[^"]*harness-detail[^"]*"[^>]*title="primitives\/rules\/decisions-and-plans.md"/, "a truncated path keeps its whole text as a title");
  const phone = CSS.slice(CSS.indexOf("@media (max-width: 48em)"));
  assert.match(phone, /\.harness-name \{ max-width: \d+vw; overflow: hidden; text-overflow: ellipsis; \}/, "a phone caps the sticky column");
  assert.match(phone, /\.harness-item \{ width: auto; \}/, "the desktop share of the width does not hold on a phone");
  assert.match(rule(".harness-table .harness-detail"), /contain: inline-size/, "a detail line never widens the column");
});

test("a table wider than its panel shows a shade on its clipped edge", () => {
  assert.match(rule(".harness-panel .mantine-ScrollArea-viewport"), /no-repeat local,[\s\S]*no-repeat scroll/);
});

test("a dot status badge is an outline in both themes", () => {
  const badge = rule('.harness-table .harness-badge[data-variant="dot"]');
  assert.match(badge, /background: transparent/);
  assert.match(badge, /border: var\(--studio-border-width\) solid var\(--studio-border-default\)/);
});

test("the Settings notice is a lead line over a hairline, not a bordered card", () => {
  const html = settings();
  assert.match(html, /<div class="harness-note" role="note">/);
  assert.doesNotMatch(html, /mantine-Alert/);
  const note = rule(".harness-note");
  assert.match(note, /border-bottom: var\(--studio-border-width\) solid var\(--studio-border-default\)/);
  assert.doesNotMatch(note, /border(-left)?:/, "no box and no accent bar");
});

test("the footer's labels share a baseline once the picker's description is hidden", () => {
  assert.match(rule(".harness-footer .draft-picker .mantine-Input-wrapper"), /margin-top: 0/);
});

test("a page that saves with no draft shows the picker only once a row is edited", () => {
  assert.match(settings(), /class="harness-footer"[\s\S]*PICKER/, "a page that needs a draft shows it at once");
  assert.doesNotMatch(settings({ draftOnChange: true }), /harness-footer/, "no form with nothing to save");
  const edited = settings({ draftOnChange: true, edits: { "decisions-and-plans": { on: "off" } } });
  assert.match(edited, /class="harness-footer"[\s\S]*PICKER[\s\S]*1 change in draft/);
});

function frame(overrides: Record<string, unknown>) {
  return {
    title: "Rules", question: "", itemNoun: "Rule", fields: FIELDS, tab: "settings" as HarnessTab, onTabChange: () => undefined,
    periods: { state: "ready", rows: [] } as PeriodsState, settings: { state: "ready", rows: [SETTING] } as SettingsState,
    edits: {}, onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined, ...overrides,
  };
}

test("the frame defers the picker only for an optional draft not yet picked", () => {
  const draft = { value: "", source: "rules", onChange: () => undefined };
  assert.doesNotMatch(wrap(h(HarnessFrame, frame({ draft: { ...draft, optional: true } }))), /Save into draft/);
  assert.match(wrap(h(HarnessFrame, frame({ draft: { ...draft, optional: true, value: "mine" } }))), /Save into draft/);
  assert.match(wrap(h(HarnessFrame, frame({ draft }))), /Save into draft/);
});

test("an empty state in the frame is its words alone, with no symbol tile", () => {
  const efficacy = wrap(h(HarnessFrame, frame({ tab: "efficacy" })));
  assert.match(efficacy, /class="harness-panel harness-empty"[\s\S]*No measurement periods yet/);
  const none = wrap(h(HarnessFrame, frame({ settings: { state: "ready", rows: [] } })));
  assert.match(none, /class="harness-empty"[\s\S]*Nothing to configure yet/);
  assert.match(rule(".harness-empty .state-symbol"), /display: none/);
});
