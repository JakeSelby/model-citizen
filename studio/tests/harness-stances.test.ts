import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import type { DraftSelectionSave, SelectionControls } from "../src/configure/api.ts";
import { periodsQuery } from "../src/harness/api.ts";
import { HarnessFrame } from "../src/harness/HarnessPage.tsx";
import type { DraftChange, PeriodRow, PeriodsResponse } from "../src/harness/model.ts";
import { SettingsTable } from "../src/harness/SettingsTable.tsx";
import { StancesPage, STANCES_QUESTION, stanceSettings } from "../src/pages/harness/StancesPage.tsx";
import {
  lastChanges, saveStanceDraft, selectionChanges, settingRowsFromControls, settingRowsFromPeriods,
  stanceLabel, STANCE_FIELDS, STANCE_METRICS,
} from "../src/pages/harness/stancesModel.ts";

// Hand-typed in the shape `/api/harness/periods?surface=stances` answers with, mirroring the
// provider test's voice switch from answer-card to concise.
const ROWS: PeriodRow[] = [
  {
    item_id: "cost", item_label: "Cost", setting_label: "balanced",
    period: { start: "2026-09-12T12:00:00Z", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: 5, agents: 1, output_tokens: 1200, cache_read_share: 0.9 },
  },
  {
    item_id: "voice", item_label: "Voice", setting_label: "concise",
    period: { start: "2026-10-09T12:00:00Z", end: null }, status: "measuring", measuring_day: 4, window_days: 7,
    metrics: { sessions: 3, agents: 0, output_tokens: 300, cache_read_share: 0.9 },
    evals_href: "/evals?surface=stances&item=voice",
  },
  {
    item_id: "voice", item_label: "Voice", setting_label: "answer-card",
    period: { start: "2026-09-12T12:00:00Z", end: "2026-10-09T12:00:00Z" }, status: "measured", window_days: 7,
    metrics: { sessions: 2, agents: 0, output_tokens: 7400, cache_read_share: 0.75 },
  },
];

const CONTROLS: SelectionControls = {
  modes: ["default"],
  stances: [
    { name: "voice", value: "concise", options: ["concise", "scannable", "answer-card"] },
    { name: "plan-ceremony", value: null, options: ["light", "review-card"] },
  ],
  switches: [],
  core_acknowledged: false,
};

function render(path: string, response: PeriodsResponse | null): string {
  const client = new QueryClient();
  if (response) client.setQueryData(periodsQuery("stances").queryKey, response);
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, h(StancesPage)))));
}

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...elements(element.props.children as ReactNode)];
}

test("Efficacy renders one row per stance per variant period with the usage columns", () => {
  const html = render("/harness/stances", { surface: "stances", rows: ROWS });
  assert.match(html, /<h1[^>]*>Stances<\/h1>/);
  assert.ok(html.includes(STANCES_QUESTION));
  assert.ok(html.includes("Stances: efficacy by setting period"));
  for (const heading of ["Stance", "Sessions", "Output tokens", "Cache-read share"]) {
    assert.match(html, new RegExp(`<th[^>]*scope="col"[^>]*>${heading}</th>`), heading);
  }
  assert.equal((html.match(/data-period="/g) ?? []).length, 3);
  const voice = /<tbody[^>]*data-item="voice"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(voice.indexOf("answer-card") < voice.indexOf("concise"), "the earlier variant sits above");
  assert.ok(voice.includes("7,400") && voice.includes("0.75"));
  assert.ok(voice.includes("measuring, day 4 of 7"));
  assert.ok(html.includes('href="/evals?surface=stances&amp;item=voice"'));
  assert.ok(!html.includes("Change a variant in a draft"), "the draft picker is Settings-only");
});

test("an unattributable answer shows the API's note and no invented split", () => {
  const note = "No setting change was found in this window; each item shows one period.";
  const html = render("/harness/stances", { surface: "stances", rows: [ROWS[0]], note });
  assert.ok(html.includes(note));
  assert.equal((html.match(/data-period="/g) ?? []).length, 1);
});

test("before the periods arrive the page says so", () => {
  assert.ok(render("/harness/stances", null).includes("Reading measurement periods"));
});

test("Settings shows each stance's variant read-only until a draft is loaded", () => {
  const rows = settingRowsFromPeriods(ROWS);
  assert.deepEqual(rows.map((row) => [row.item_id, row.values.variant, row.options]),
    [["cost", "balanced", undefined], ["voice", "concise", undefined]]);
  assert.deepEqual([rows[0].last_changed, rows[1].last_changed], [null, "2026-10-09T12:00:00Z"]);
  assert.equal(rows[1].last_change_note, "variant · measuring, day 4 of 7");
  const html = render("/harness/stances?tab=settings", { surface: "stances", rows: ROWS });
  assert.ok(html.includes("Stances: settings"));
  assert.ok(html.includes("Save into draft"));
  assert.ok(html.includes("Pick or create a draft"));
  assert.ok(!html.includes('aria-label="Variant: Voice"'), "no select before a draft");
});

test("a loaded draft offers each stance's alternatives, keeping an unset one blank", () => {
  const rows = settingRowsFromControls(CONTROLS, ROWS);
  assert.deepEqual(rows.map((row) => [row.item_label, row.values.variant, row.options?.variant]), [
    ["Voice", "concise", ["concise", "scannable", "answer-card"]],
    ["Plan ceremony", "", ["", "light", "review-card"]],
  ]);
  assert.equal(rows[0].last_changed, "2026-10-09T12:00:00Z");
  assert.equal(stanceLabel("build-vs-buy"), "Build vs buy");
  const state = stanceSettings({ isPending: true, isError: false }, { name: "d1", revision: "r1", controls: CONTROLS });
  assert.equal(state.state, "ready");
  assert.deepEqual(stanceSettings({ isPending: true, isError: false }, null), { state: "loading" });
  assert.deepEqual(stanceSettings({ isPending: false, isError: true, error: new Error("unreadable") }, null),
    { state: "error", message: "unreadable" });
  assert.deepEqual(lastChanges([ROWS[0]]), {});
});

test("sending a changed variant calls onDraft with the change", () => {
  const rows = settingRowsFromControls(CONTROLS, ROWS);
  const sent: DraftChange[][] = [];
  const edits = { voice: { variant: "answer-card" } };
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, h(HarnessFrame, {
    title: "Stances", question: STANCES_QUESTION, itemNoun: "Stance", metrics: STANCE_METRICS, fields: STANCE_FIELDS,
    tab: "settings", onTabChange: () => undefined, periods: { state: "ready", rows: ROWS },
    settings: { state: "ready", rows }, edits, onEdit: () => undefined, onDiscard: () => undefined,
    onDraft: () => undefined,
  }))));
  assert.ok(html.includes('aria-label="Variant: Voice"'));
  assert.ok(html.includes("was: concise"));
  const table = SettingsTable({
    caption: "Stances: settings", rows, fields: STANCE_FIELDS, edits,
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes) => sent.push(changes),
  });
  const send = elements(table).find((element) => element.type === Button && element.props.children === "Send to Changes");
  assert.ok(send);
  (send.props.onClick as () => void)();
  assert.deepEqual(sent, [[{ item_id: "voice", field: "variant", from: "concise", to: "answer-card" }]]);
});

function saved(overrides: Partial<DraftSelectionSave>): DraftSelectionSave {
  return {
    valid: true, error: "", error_code: "", changed: [], unchanged: false, base_revision: "r1",
    before: {}, after: {}, controls: {}, applied: [], saved: true, result: { revision: "r2" }, ...overrides,
  };
}

test("the draft save goes through selection editing as stances.<name> changes", async () => {
  const change: DraftChange = { item_id: "voice", field: "variant", from: "concise", to: "answer-card" };
  assert.deepEqual(selectionChanges([change, { ...change, field: "scope" }]), { "stances.voice": "answer-card" });
  const calls: unknown[][] = [];
  const save = async (...args: unknown[]) => { calls.push(args); return saved({}); };
  assert.equal(await saveStanceDraft("d1", "r1", [change], save, () => "key-1"), "r2");
  assert.deepEqual(calls, [["d1", "r1", "key-1", { "stances.voice": "answer-card" }]]);
  assert.equal(await saveStanceDraft("d1", "r1", [], save), "r1", "nothing to send makes no request");
  assert.equal(calls.length, 1);
  assert.equal(await saveStanceDraft("d1", "r1", [change], async () => saved({ unchanged: true, saved: false, result: null })), "r1");
  await assert.rejects(
    saveStanceDraft("d1", "r1", [change], async () => saved({ saved: false, error: "unknown variant", result: null })),
    /unknown variant/,
  );
});

test("the Settings tab picks its draft in the frame and keeps the selection in the URL", () => {
  const html = render("/harness/stances?tab=settings&draft=d1", { surface: "stances", rows: ROWS });
  assert.ok(html.includes("Save into draft"));
  assert.ok(!html.includes("Draft name"), "the typed draft field is gone");
  assert.match(html, /Edits save into draft <code[^>]*>d1<\/code>/);
});
