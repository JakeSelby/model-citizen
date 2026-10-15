import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import type { Save } from "../src/configure/api.ts";
import { periodsQuery } from "../src/harness/api.ts";
import type { DraftChange, PeriodRow, PeriodsResponse } from "../src/harness/model.ts";
import { SettingsTable } from "../src/harness/SettingsTable.tsx";
import { MODULES_PATH, sourceSettings, SourcesPage } from "../src/pages/harness/SourcesPage.tsx";
import {
  draftRoots, READ_ONLY, rootsAfter, saveSourcesDraft, settingRowsFromDraft, settingRowsFromPeriods, shortPath,
  SOURCES_FIELDS, SOURCES_QUESTION, TEAM_NOTE,
} from "../src/pages/harness/sourcesModel.ts";
import { OLD_PATHS } from "../src/navigation.ts";

// Hand-typed in the shape `/api/harness/periods?surface=sources` answers with, mirroring the
// provider test's 0.14.2 to 0.15.0 split.
const ROOT = "/srv/primitives/my-primitives";
const ROWS: PeriodRow[] = [
  {
    item_id: "core", item_label: "Core", setting_label: "0.14.2",
    period: { start: "2026-09-22T12:00:00Z", end: "2026-10-09T12:00:00Z" }, status: "measured", window_days: 7,
    metrics: { sessions: 30, tokens_per_session: 6449, modules: 96, source: "Core", root: "/opt/mc/primitives", enabled: "on" },
  },
  {
    item_id: "core", item_label: "Core", setting_label: "0.15.0",
    period: { start: "2026-10-09T12:00:00Z", end: null }, status: "measuring", measuring_day: 4, window_days: 7,
    metrics: { sessions: 4, tokens_per_session: 6501, modules: 96, source: "Core", root: "/opt/mc/primitives", enabled: "on" },
    evals_href: "/evals?surface=sources&item=core",
  },
  {
    item_id: `personal:${ROOT}`, item_label: "Personal: my-primitives", setting_label: "0.15.0",
    period: { start: "2026-09-22T12:00:00Z", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: 34, tokens_per_session: 5850, modules: 14, source: "Personal", root: ROOT, enabled: "on" },
  },
  {
    item_id: "third-party:bmad-method@bmad", item_label: "Third-party: bmad-method", setting_label: "0.15.0",
    period: { start: "2026-09-22T12:00:00Z", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: "not recorded", tokens_per_session: "not recorded", modules: 50, source: "Third-party", root: "/c/bmad", enabled: "on" },
  },
];

function render(path: string, response: PeriodsResponse | null): string {
  const client = new QueryClient();
  if (response) client.setQueryData(periodsQuery("sources").queryKey, response);
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, h(SourcesPage)))));
}

test("Efficacy renders one row per source per harness-version period", () => {
  const html = render("/harness/sources", { surface: "sources", rows: ROWS });
  assert.match(html, /<h1[^>]*>Sources<\/h1>/);
  assert.ok(html.includes(SOURCES_QUESTION));
  for (const heading of ["Source", "Sessions", "Tokens per session", "Modules"]) {
    assert.match(html, new RegExp(`<th[^>]*scope="col"[^>]*>${heading}</th>`), heading);
  }
  assert.equal((html.match(/data-period="/g) ?? []).length, 4);
  const core = /<tbody[^>]*data-item="core"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(core.indexOf("0.14.2") < core.indexOf("0.15.0"), "the earlier version sits above");
  assert.ok(core.includes("6,449") && core.includes("6,501"));
  assert.ok(core.includes("measuring, day 4 of 7"));
  for (const label of ["Personal: my-primitives", "Third-party: bmad-method", "not recorded"]) assert.ok(html.includes(label), label);
  assert.ok(!html.includes(`>${ROOT}<`), "the root path is a Settings column, not an Efficacy one");
  assert.ok(html.includes(TEAM_NOTE.split(".")[0]), "the Team label is explained, never guessed");
});

test("the module browser stays reachable and the old Library route lands on it", () => {
  const html = render("/harness/sources", { surface: "sources", rows: ROWS });
  assert.ok(html.includes(`href="${MODULES_PATH}"`));
  assert.ok(html.includes("Open the module browser"));
  assert.equal(OLD_PATHS["/library"], MODULES_PATH);
});

test("an unattributable answer shows the API's note and no invented split", () => {
  const note = "No setting change was recorded for these items in this window, so each shows one period.";
  const html = render("/harness/sources", { surface: "sources", rows: [ROWS[2]], note });
  assert.ok(html.includes(note));
  assert.equal((html.match(/data-period="/g) ?? []).length, 1);
});

test("Settings shows each source's root and enabled state, read-only where no route edits it", () => {
  const rows = settingRowsFromPeriods(ROWS);
  assert.deepEqual(rows.map((row) => [row.item_id, row.item_detail, row.values.source, row.values.on, row.values.editing, row.options]), [
    ["core", "…/mc/primitives", "Core", "on", READ_ONLY.Core, undefined],
    [`personal:${ROOT}`, "…/primitives/my-primitives", "Personal", "on", "pick a draft to switch it off", undefined],
    ["third-party:bmad-method@bmad", "/c/bmad", "Third-party", "on", READ_ONLY["Third-party"], undefined],
  ]);
  assert.equal(rows[0].item_detail_title, "/opt/mc/primitives", "the full root is the row's title");
  assert.equal(shortPath("/c/bmad"), "/c/bmad", "a short path is kept whole");
  assert.equal(rows[0].last_changed, "2026-10-09T12:00:00Z");
  assert.equal(rows[0].last_change_note, "harness 0.15.0 · measuring, day 4 of 7");
  assert.equal(rows[1].last_changed, null);
  const html = render("/harness/sources?tab=settings", { surface: "sources", rows: ROWS });
  assert.ok(html.includes("Sources: settings"));
  assert.ok(html.includes("Save into draft"));
  assert.ok(!html.includes("Draft name"), "the frame's picker replaces the typed field");
  assert.ok(html.includes("Pick or create a draft"));
  assert.ok(html.includes(READ_ONLY.Core));
  assert.ok(html.includes(">Kind<"), "the field naming Core, Personal or Third-party is Kind, not a second Source");
  assert.ok(html.includes('title="/opt/mc/primitives"'), "the full root is one hover away");
  assert.ok(!html.includes('aria-label="On: Personal: my-primitives"'), "no control before a draft");
});

test("a loaded draft lists its own roots as switchable and keeps Core and plugins read-only", () => {
  const rows = settingRowsFromDraft(ROWS, [ROOT, "/srv/primitives/other"]);
  assert.deepEqual(rows.map((row) => [row.item_label, row.options?.on]), [
    ["Core", undefined],
    ["Personal: my-primitives", ["on", "off"]],
    ["Personal: other", ["on", "off"]],
    ["Third-party: bmad-method", undefined],
  ]);
  assert.deepEqual(draftRoots({ primitive_roots: [ROOT, 3] }), [ROOT]);
  assert.deepEqual(draftRoots({}), []);
  assert.deepEqual(sourceSettings({ isPending: true, isError: false }, null), { state: "loading" });
  assert.deepEqual(sourceSettings({ isPending: false, isError: true, error: new Error("unreadable") }, null),
    { state: "error", message: "unreadable" });
});

test("sending a root switched off calls onDraft, which saves the shorter list to the draft", async () => {
  const rows = settingRowsFromDraft(ROWS, [ROOT, "/srv/primitives/other"]);
  const drafted: DraftChange[][] = [];
  const table = SettingsTable({
    caption: "Sources: settings", rows, fields: SOURCES_FIELDS, edits: { [`personal:${ROOT}`]: { on: "off" } },
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes) => drafted.push(changes),
  });
  const html = renderToStaticMarkup(h(MantineProvider, {}, table));
  assert.ok(html.includes("1 change in draft"));
  const send = findButton(table, "Send to Changes");
  send.props.onClick();
  assert.deepEqual(drafted, [[{ item_id: `personal:${ROOT}`, field: "on", from: "on", to: "off" }]]);

  assert.deepEqual(rootsAfter([ROOT, "/srv/primitives/other"], drafted[0]), ["/srv/primitives/other"]);
  const calls: Array<[string, string, Record<string, unknown>]> = [];
  const save = async (draft: string, revision: string, changes: Record<string, unknown>): Promise<Save> => {
    calls.push([draft, revision, changes]);
    return { saved: true, result: { revision: "r2" }, valid: true, errors: [], warnings: [], changed: ["primitive_roots"], preview: {}, base_revision: revision };
  };
  assert.equal(await saveSourcesDraft("d1", "r1", [ROOT, "/srv/primitives/other"], drafted[0], save), "r2");
  assert.deepEqual(calls, [["d1", "r1", { primitive_roots: ["/srv/primitives/other"] }]]);
  assert.equal(await saveSourcesDraft("d1", "r1", [ROOT], [], save), "r1", "nothing switched off saves nothing");
  const refuse = async (): Promise<Save> => ({ saved: false, result: null, valid: false, errors: [{ path: "primitive_roots", message: "bad root" }], warnings: [], changed: [], preview: {}, base_revision: "r1" });
  await assert.rejects(saveSourcesDraft("d1", "r1", [ROOT], drafted[0], refuse), /bad root/);
});

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

function findButton(node: unknown, label: string): { props: { onClick: () => void } } {
  const stack: unknown[] = [node];
  while (stack.length) {
    const current = stack.pop() as Node | Node[] | null | undefined;
    if (Array.isArray(current)) { stack.push(...current); continue; }
    if (!current || typeof current !== "object" || !current.props) continue;
    if (current.props.children === label && typeof current.props.onClick === "function") {
      return current as { props: { onClick: () => void } };
    }
    stack.push(current.props.children);
  }
  throw new Error(`no ${label} button`);
}
