import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider, Tabs } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { loadPeriods, periodsQuery } from "../src/harness/api.ts";
import { EfficacyTable } from "../src/harness/EfficacyTable.tsx";
import { HarnessFrame, HarnessPage, type PeriodsState, type SettingsState } from "../src/harness/HarnessPage.tsx";
import {
  draftChanges, groupRows, measuringText, parseTab, periodRole, periodText, settingsSummary, tabSearch,
  type DraftChange, type HarnessTab, type PeriodRow, type SettingRow,
} from "../src/harness/model.ts";
import { NEW_PERIOD_NOTE, SettingsTable } from "../src/harness/SettingsTable.tsx";

// Hand-typed rows in the shape `/api/harness/periods` answers with; no generator exists yet.
const ROWS: PeriodRow[] = [
  {
    item_id: "decisions-and-plans", item_label: "Offer alternatives on decisions", setting_label: "New wording",
    period: { start: "2026-10-09", end: null }, status: "measuring", measuring_day: 4, window_days: 7,
    metrics: { sessions: 13, broken_in: "23% · 3" }, evals_href: "/evals?item=decisions-and-plans",
  },
  {
    item_id: "transcript-hygiene", item_label: "Read narrowly", setting_label: "Guidance only · all repos",
    period: { start: "2026-09-24", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: 34, broken_in: "71% · 24" },
  },
  {
    item_id: "decisions-and-plans", item_label: "Offer alternatives on decisions", setting_label: "Old wording",
    period: { start: "2026-09-24", end: "2026-10-08" }, status: "measured", window_days: 7,
    metrics: { sessions: 21, broken_in: "43% · 9" },
  },
];

const SETTINGS: SettingRow[] = [
  {
    item_id: "transcript-hygiene", item_label: "Read narrowly", item_detail: "transcript-hygiene",
    values: { enforced_by: "Guidance only", scope: "All repos" },
    options: { enforced_by: ["Guidance only", "Hook: read-guard"], scope: ["All repos", "Code repos"] },
    last_changed: "2026-09-24", last_change_note: "v0.13.0 release",
  },
  {
    item_id: "conciseness", item_label: "Comment and doc conciseness",
    values: { enforced_by: "Guidance only", scope: "Code repos" },
    last_changed: null,
  },
];

const FIELDS = [{ key: "enforced_by", heading: "Enforced by" }, { key: "scope", heading: "Scope" }];
const READY_PERIODS: PeriodsState = { state: "ready", rows: ROWS };
const READY_SETTINGS: SettingsState = { state: "ready", rows: SETTINGS };

function frameProps(overrides: Record<string, unknown> = {}) {
  return {
    title: "Rules", question: "Is each rule holding under the setting in effect?", itemNoun: "Rule", fields: FIELDS,
    tab: "efficacy" as HarnessTab, onTabChange: () => undefined,
    periods: READY_PERIODS, settings: READY_SETTINGS, edits: {},
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined,
    ...overrides,
  };
}

function render(node: ReactNode, path = "/harness/rules", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

/** Every element in a hook-free component's returned tree, without rendering it. */
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

function selected(html: string): string[] {
  return [...html.matchAll(/<button[^>]*role="tab"[^>]*>/g)]
    .filter((match) => match[0].includes('aria-selected="true"'))
    .map((match) => /id="[^"]*-(efficacy|settings)"/.exec(match[0])?.[1] ?? match[0]);
}

test("the tab lives in the URL query and Efficacy is the default", () => {
  assert.equal(parseTab(""), "efficacy");
  assert.equal(parseTab("?tab=settings"), "settings");
  assert.equal(parseTab("?tab=unknown"), "efficacy");
  assert.equal(tabSearch("?range=30&tab=settings", "efficacy").toString(), "range=30");
  assert.equal(tabSearch("?range=30", "settings").toString(), "range=30&tab=settings");
});

test("the page opens on the tab the URL names", () => {
  const client = new QueryClient();
  client.setQueryData(periodsQuery("rules").queryKey, { surface: "rules", rows: ROWS });
  const page = h(HarnessPage, {
    surface: "rules", title: "Rules", question: "Is each rule holding?", itemNoun: "Rule",
    fields: FIELDS, settings: READY_SETTINGS, onDraft: () => undefined,
  });
  const efficacy = render(page, "/harness/rules", client);
  assert.deepEqual(selected(efficacy), ["efficacy"]);
  assert.ok(efficacy.includes("Rules: efficacy by setting period"));
  assert.ok(!efficacy.includes("Rules: settings"));
  const settings = render(page, "/harness/rules?tab=settings", client);
  assert.deepEqual(selected(settings), ["settings"]);
  assert.ok(settings.includes("Rules: settings"));
  assert.ok(!settings.includes("Rules: efficacy by setting period"));
});

test("choosing a tab reports it to the page, which writes it to the URL", () => {
  const chosen: HarnessTab[] = [];
  const tree = HarnessFrame(frameProps({ onTabChange: (tab: HarnessTab) => chosen.push(tab) }));
  const tabs = elements(tree).find((element) => element.type === Tabs);
  assert.ok(tabs);
  const onChange = tabs.props.onChange as (value: string | null) => void;
  onChange("settings");
  onChange("efficacy");
  onChange(null);
  assert.deepEqual(chosen, ["settings", "efficacy", "efficacy"]);
});

test("a measuring row says its day of seven and links to Evals", () => {
  const measuring = ROWS[0];
  assert.equal(measuringText(measuring), "measuring, day 4 of 7");
  assert.equal(measuringText({ ...measuring, measuring_day: undefined }), "measuring, 7-day window");
  assert.equal(periodText(measuring.period), "since Oct 9");
  const html = render(h(EfficacyTable, { caption: "Rules", rows: ROWS, columns: [{ key: "sessions", heading: "Sessions" }] }));
  assert.ok(html.includes("measuring, day 4 of 7"));
  const link = /<a[^>]*aria-label="Before and after in Evals: Offer alternatives on decisions"[^>]*>/.exec(html)?.[0] ?? "";
  assert.ok(link.includes('href="/evals?item=decisions-and-plans"'), link);
  const fallback = render(h(EfficacyTable, { caption: "Rules", rows: [{ ...measuring, evals_href: undefined }], columns: [] }));
  assert.match(fallback, /href="\/evals"/);
  assert.equal((html.match(/Before and after in Evals</g) ?? []).length, 1, "only the measuring row links");
});

test("a changed item's before row and measuring row are adjacent", () => {
  const groups = groupRows(ROWS);
  assert.deepEqual(groups.map((group) => group.item_id), ["decisions-and-plans", "transcript-hygiene"]);
  const changed = groups[0].rows;
  assert.deepEqual(changed.map((row) => row.setting_label), ["Old wording", "New wording"]);
  assert.deepEqual([periodRole(changed, 0), periodRole(changed, 1)], ["Before", "After"]);
  assert.equal(periodRole(groups[1].rows, 0), null);
  const html = render(h(EfficacyTable, { caption: "Rules", rows: ROWS, columns: [{ key: "sessions", heading: "Sessions" }] }));
  const body = /<tbody[^>]*data-item="decisions-and-plans"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  const periods = [...body.matchAll(/<tr[^>]*data-period="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(periods, ["Before", "After"]);
  assert.ok(body.indexOf("Old wording") < body.indexOf("New wording"));
  assert.ok(body.includes("Sep 24 to Oct 8"));
  assert.match(body, /rowspan="2"[^>]*>Offer alternatives on decisions/i);
});

test("each setting shows its last changed date", () => {
  assert.equal(settingsSummary(SETTINGS), "2 items · last change Sep 24");
  const html = render(h(HarnessFrame, frameProps({ tab: "settings" })));
  assert.ok(html.includes("Last changed"));
  assert.ok(html.includes("Sep 24"));
  assert.ok(html.includes("v0.13.0 release"));
  assert.ok(html.includes("not recorded"), "a missing date is never invented");
  assert.ok(html.includes(NEW_PERIOD_NOTE));
});

test("saving calls onDraft with only the changed values", () => {
  const edits = { "transcript-hygiene": { scope: "Code repos", enforced_by: "Guidance only" } };
  const expected: DraftChange[] = [{ item_id: "transcript-hygiene", field: "scope", from: "All repos", to: "Code repos" }];
  assert.deepEqual(draftChanges(SETTINGS, edits), expected);
  const drafts: DraftChange[][] = [];
  const props = {
    caption: "Rules: settings", rows: SETTINGS, fields: FIELDS, edits,
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes: DraftChange[]) => drafts.push(changes),
  };
  const send = elements(SettingsTable(props)).find((element) => element.type === Button && text(element) === "Send to Changes");
  assert.ok(send);
  assert.ok(!send.props.disabled);
  (send.props.onClick as () => void)();
  assert.deepEqual(drafts, [expected]);
  const html = render(h(SettingsTable, props));
  assert.ok(html.includes("was: All repos"));
  assert.ok(html.includes("Draft, not applied"));
  assert.ok(html.includes("starts a new measurement period"));
  const idle = elements(SettingsTable({ ...props, edits: {} })).filter((element) => element.type === Button);
  assert.deepEqual(idle, [], "with nothing to save there is no Discard or Send to Changes");
});

test("an edit reaches the page through onEdit", () => {
  const seen: string[][] = [];
  const tree = SettingsTable({
    caption: "Rules: settings", rows: SETTINGS, fields: FIELDS, edits: {},
    onEdit: (...args: string[]) => seen.push(args), onDiscard: () => undefined, onDraft: () => undefined,
  });
  const select = elements(tree).find((element) => element.props["aria-label"] === "Scope: Read narrowly");
  assert.ok(select);
  (select.props.onChange as (event: { currentTarget: { value: string } }) => void)({ currentTarget: { value: "Code repos" } });
  assert.deepEqual(seen, [["transcript-hygiene", "scope", "Code repos"]]);
});

test("empty, loading and error states are explicit", () => {
  const empty = render(h(HarnessFrame, frameProps({ periods: { state: "ready", rows: [] } })));
  assert.ok(empty.includes("No measurement periods yet"));
  assert.match(empty, /role="status"/);
  const loading = render(h(HarnessFrame, frameProps({ periods: { state: "loading" } })));
  assert.ok(loading.includes("Reading measurement periods"));
  assert.match(loading, /aria-busy="true"/);
  const failed = render(h(HarnessFrame, frameProps({ periods: { state: "error", message: "usage log unreadable" } })));
  assert.ok(failed.includes("Rule measurement periods could not be read"));
  assert.ok(failed.includes("usage log unreadable"));
  assert.match(failed, /role="alert"/);
  const noSettings = render(h(HarnessFrame, frameProps({ tab: "settings", settings: { state: "ready", rows: [] } })));
  assert.ok(noSettings.includes("Nothing to configure yet"));
  const settingsError = render(h(HarnessFrame, frameProps({ tab: "settings", settings: { state: "error", message: "config unreadable" } })));
  assert.ok(settingsError.includes("Rule settings could not be read"));
  const pending = render(h(HarnessPage, {
    surface: "rules", title: "Rules", question: "Is each rule holding?", fields: FIELDS,
    settings: READY_SETTINGS, onDraft: () => undefined,
  }));
  assert.ok(pending.includes("Reading measurement periods"), "the page shows loading before the periods arrive");
});

test("tabs, tables and controls carry roles and labels", () => {
  const html = render(h(HarnessFrame, frameProps()));
  assert.match(html, /role="tablist"[^>]*aria-label="Rules views"|aria-label="Rules views"[^>]*role="tablist"/);
  assert.equal((html.match(/role="tab"/g) ?? []).length, 2);
  assert.match(html, /role="tabpanel"/);
  assert.match(html, /<h1[^>]*>Rules<\/h1>/);
  assert.ok(html.includes('aria-label="Rules: efficacy by setting period"'));
  assert.match(html, /<caption[^>]*>Rules: efficacy by setting period<\/caption>/);
  assert.match(html, /<th[^>]*scope="col"[^>]*>Setting in effect<\/th>/);
  const settings = render(h(HarnessFrame, frameProps({ tab: "settings" })));
  assert.ok(settings.includes('aria-label="Enforced by: Read narrowly"'));
  assert.ok(settings.includes('aria-label="Scope: Read narrowly"'));
});

test("usePeriods reads the surface's periods with a plain same-origin GET", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input).includes("surface=broken")) return new Response(JSON.stringify({ error: "usage log unreadable" }), { status: 500 });
    if (String(input).includes("surface=odd")) return new Response(JSON.stringify({ surface: "odd" }), { status: 200 });
    return new Response(JSON.stringify({ surface: "rules & hooks", rows: ROWS }), { status: 200 });
  }) as typeof fetch;
  try {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const data = await client.fetchQuery(periodsQuery("rules & hooks"));
    assert.equal(data.rows.length, 3);
    assert.deepEqual(client.getQueryData(["harness-periods", "rules & hooks"]), data);
    await assert.rejects(loadPeriods("broken"), /usage log unreadable/);
    await assert.rejects(loadPeriods("odd"), /no rows/);
  } finally { globalThis.fetch = original; }
  assert.equal(calls[0].input, "/api/harness/periods?surface=rules%20%26%20hooks");
  assert.equal(calls[0].init?.method, "GET");
  assert.equal(calls[0].init?.credentials, "same-origin");
  assert.equal(calls[0].init?.body, undefined);
});
