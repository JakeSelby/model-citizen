import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider, Switch } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { periodsQuery } from "../src/harness/api.ts";
import { EfficacyTable, statusText } from "../src/harness/EfficacyTable.tsx";
import { HarnessFrame, type PeriodsState, type SettingsState } from "../src/harness/HarnessPage.tsx";
import { isNumericColumn, parseTab, tabSearch, type HarnessTab, type PeriodRow, type SettingRow } from "../src/harness/model.ts";
import { isSwitch, SettingsTable } from "../src/harness/SettingsTable.tsx";
import { HooksPage, safetyQuery } from "../src/pages/harness/HooksPage.tsx";
import { ranAs } from "../src/pages/harness/rolesWorkflowsModel.ts";

// Hand-typed in the shape `/api/harness/periods` answers with; no generator exists.
const ROWS: PeriodRow[] = [
  {
    item_id: "secrets", item_label: "secrets", setting_label: "off",
    period: { start: "2026-10-08T12:00:00Z", end: null }, status: "measuring", measuring_day: 5, window_days: 7,
    metrics: { sessions: 13, fired_in: "0 (0%)", label: "plain words", tokens: "$3.80 · $7.01" },
  },
  {
    item_id: "working-style", item_label: "working-style", setting_label: "on",
    period: { start: "2026-09-12T12:00:00Z", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: 34, fired_in: "no detector", label: "other words", tokens: "not measured" },
  },
];

const COLUMNS = [
  { key: "sessions", heading: "Sessions" }, { key: "fired_in", heading: "Fired in" },
  { key: "label", heading: "Label" }, { key: "tokens", heading: "Tokens" },
];

const SETTINGS: SettingRow[] = [
  { item_id: "secrets", item_label: "secrets", values: { on: "on", scope: "" }, options: { on: ["on", "off"] }, last_changed: null },
];

const FIELDS = [{ key: "on", heading: "On" }, { key: "scope", heading: "Scope" }];

function wrap(node: ReactElement, path = "/", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...elements(element.props.children as ReactNode), ...elements(element.props.draftControls as ReactNode)];
}

function frame(overrides: Record<string, unknown> = {}) {
  return {
    title: "Rules", question: "", itemNoun: "Rule", fields: FIELDS, tab: "settings" as HarnessTab, onTabChange: () => undefined,
    periods: { state: "ready", rows: ROWS } as PeriodsState, settings: { state: "ready", rows: SETTINGS } as SettingsState,
    edits: {}, onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined, ...overrides,
  };
}

test("the efficacy table sits in a bordered panel with a hidden caption and right-aligned figures", () => {
  const html = wrap(h(EfficacyTable, { caption: "Rules: efficacy", rows: ROWS, columns: COLUMNS }));
  assert.match(html, /class="[^"]*harness-panel/);
  assert.match(html, /<table[^>]*class="[^"]*data-table harness-table/);
  assert.match(html, /<caption[^>]*class="[^"]*visually-hidden[^"]*"[^>]*>Rules: efficacy<\/caption>/);
  for (const heading of ["Sessions", "Fired in", "Tokens"]) assert.match(html, new RegExp(`<th[^>]*class="[^"]*harness-num"[^>]*>${heading}<`), heading);
  assert.match(html, /<th[^>]*scope="col"[^>]*>Label</);
  assert.doesNotMatch(html, /harness-num"[^>]*>Label</, "a text column stays left-aligned");
  assert.match(html, /harness-badge[^"]*" data-variant="dot"[^>]*>[\s\S]*?measuring, day 5 of 7/, "the status badge has an edge on the canvas");
});

test("a missing figure is a dash that still names itself to a screen reader", () => {
  const html = wrap(h(EfficacyTable, { caption: "Rules", rows: ROWS, columns: COLUMNS }));
  assert.match(html, /<span aria-hidden="true">–<\/span><span class="visually-hidden">no detector<\/span>/);
  assert.match(html, /<span class="visually-hidden">not measured<\/span>/);
});

test("Status shows only when a row is measuring or ran no session", () => {
  const measured = ROWS.filter((row) => row.status === "measured");
  assert.ok(!wrap(h(EfficacyTable, { caption: "R", rows: measured, columns: COLUMNS })).includes(">Status<"));
  assert.ok(wrap(h(EfficacyTable, { caption: "R", rows: ROWS, columns: COLUMNS })).includes(">Status<"));
  const idle = [{ ...measured[0], metrics: { sessions: 0 } }];
  assert.equal(statusText(idle[0]), "no sessions");
  assert.ok(wrap(h(EfficacyTable, { caption: "R", rows: idle, columns: COLUMNS })).includes("no sessions"));
});

test("figure columns are told apart from text columns", () => {
  assert.equal(isNumericColumn(ROWS, "sessions"), true);
  assert.equal(isNumericColumn(ROWS, "fired_in"), true, "a share in brackets and a no-detector row still read as figures");
  assert.equal(isNumericColumn(ROWS, "tokens"), true);
  assert.equal(isNumericColumn(ROWS, "label"), false);
  assert.equal(isNumericColumn(ROWS, "absent"), false, "a column with no figures is not right-aligned");
});

test("an on/off setting is a switch whose toggle reaches onEdit", () => {
  assert.equal(isSwitch(["off", "on"]), true);
  assert.equal(isSwitch(["on", "off", "ask"]), false);
  const seen: string[][] = [];
  const tree = SettingsTable({
    caption: "Rules: settings", rows: SETTINGS, fields: FIELDS, edits: {},
    onEdit: (...args: string[]) => seen.push(args), onDiscard: () => undefined, onDraft: () => undefined,
  });
  const toggle = elements(tree).find((element) => element.type === Switch);
  assert.ok(toggle);
  assert.equal(toggle.props["aria-label"], "On: secrets");
  assert.equal(toggle.props.checked, true);
  (toggle.props.onChange as (event: { currentTarget: { checked: boolean } }) => void)({ currentTarget: { checked: false } });
  assert.deepEqual(seen, [["secrets", "on", "off"]]);
  const html = wrap(h(SettingsTable, {
    caption: "Rules: settings", rows: SETTINGS, fields: FIELDS, edits: {},
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined,
  }));
  assert.match(html, /role="switch"/);
  const readOnly = wrap(h(SettingsTable, {
    caption: "Rules: settings", rows: [{ ...SETTINGS[0], options: undefined }], fields: FIELDS, edits: {},
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined,
  }));
  assert.ok(!readOnly.includes('aria-label="On: secrets"'), "a read-only value is no control");
  assert.match(readOnly, /harness-badge[^"]*" data-variant="dot"[^>]*>[\s\S]*?On</, "a read-only on/off is a status badge");
  assert.doesNotMatch(html, /<select/);
  assert.match(html, /<span aria-hidden="true">–<\/span><span class="visually-hidden">not set<\/span>/, "an empty value is a dash");
});

test("Settings says once that nothing applies until approved, and puts the picker in the footer", () => {
  const draft = { value: "", source: "rules", onChange: () => undefined };
  const html = wrap(h(HarnessFrame, frame({ draft })));
  assert.equal(html.split("Nothing applies until").length - 1, 1);
  assert.match(html, /class="harness-note[^>]*role="note"/, "the notice is informational, not an alert");
  assert.doesNotMatch(html, /--alert-bg:var\(--mantine-color-(blue|teal|green)/, "no coloured wash");
  assert.ok(html.indexOf("Rules: settings") < html.indexOf("Save into draft"), "the table comes before the picker");
  assert.match(html, /class="harness-footer"[\s\S]*Save into draft/);
  const idle = elements(HarnessFrame(frame({ draft })));
  const table = idle.find((element) => element.type === SettingsTable);
  assert.ok(table);
  assert.deepEqual(elements((table.type as (props: unknown) => ReactNode)(table.props)).filter((element) => element.type === Button)
    .map((element) => element.props.children), [], "no Discard or Send without an edit");
  const edited = SettingsTable({ ...(table.props as Parameters<typeof SettingsTable>[0]), edits: { secrets: { on: "off" } } });
  assert.deepEqual(elements(edited).filter((element) => element.type === Button).map((element) => element.props.children),
    ["Discard", "Send to Changes"]);
});

test("the log tab is ?tab=log, and leaving it drops the focused entry", () => {
  assert.equal(parseTab("?tab=log"), "log");
  assert.equal(tabSearch("?draft=d", "log").toString(), "draft=d&tab=log");
  assert.equal(tabSearch("?tab=log&apply=abc&draft=d", "efficacy").toString(), "draft=d");
  assert.equal(tabSearch("?tab=log&apply=abc", "log").toString(), "tab=log&apply=abc");
  const without = wrap(h(HarnessFrame, frame({ tab: "log" })));
  assert.equal((without.match(/role="tab"/g) ?? []).length, 2, "a page with no log has two tabs");
  assert.ok(without.includes("Rules: efficacy by setting period"), "?tab=log falls back to Efficacy there");
  const withLog = wrap(h(HarnessFrame, frame({ tab: "log", logTab: { name: "Safety log", content: h("p", {}, "log body") } })));
  assert.equal((withLog.match(/role="tab"/g) ?? []).length, 3);
  assert.ok(withLog.includes("log body"));
});

function hooksClient(): QueryClient {
  const client = new QueryClient();
  client.setQueryData(periodsQuery("hooks").queryKey, { surface: "hooks", rows: [{
    item_id: "grade-bash", item_label: "Command grader", setting_label: "on", period: { start: "2026-09-12T12:00:00Z", end: null },
    status: "measured", window_days: 7, metrics: { fires: 5, liveness: "erroring" },
  }] });
  client.setQueryData(safetyQuery.queryKey, []);
  return client;
}

test("Hooks: liveness leads Efficacy as badges, and the Activity timeline lives in the Safety log tab", () => {
  const efficacy = wrap(h(HooksPage), "/harness/hooks", hooksClient());
  assert.ok(efficacy.indexOf('aria-label="Hook liveness"') < efficacy.indexOf("Hooks: efficacy by setting period"), "the summary comes first");
  const body = /<tbody[^>]*data-item="grade-bash"[^>]*>([\s\S]*?)<\/tbody>/.exec(efficacy)?.[1] ?? "";
  assert.match(body, /mantine-Badge-root[^>]*>[\s\S]*?erroring/, "liveness is a badge in the table");
  assert.ok(!efficacy.includes("What the harness decided and changed."), "Efficacy no longer embeds Activity");
  assert.ok(efficacy.includes(">Safety log<"), "the third tab is named");
  const log = wrap(h(HooksPage), "/harness/hooks?tab=log", hooksClient());
  assert.ok(log.includes("What the harness decided and changed.") && log.includes("Nothing blocked or warned"));
  const applied = wrap(h(HooksPage), "/harness/hooks?apply=0123456789abcdef0123456789abcdef", hooksClient());
  assert.ok(applied.includes("What the harness decided and changed."), "an ?apply= link opens the log");
});

test("Roles shows the model and effort that ran in one cell", () => {
  const row = ROWS[0];
  assert.equal(ranAs({ ...row, metrics: { model: "opus", effort: "high" } }), "opus · high");
  assert.equal(ranAs({ ...row, metrics: { model: "opus" } }), "opus");
  assert.equal(ranAs({ ...row, metrics: {} }), "not measured");
});
