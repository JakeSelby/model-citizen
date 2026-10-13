import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider, Select } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { LiveUpdatesProvider } from "../src/live/LiveUpdates.tsx";

import { createDraft, draftsQuery, historyQuery } from "../src/changes/api.ts";
import { ChangeHistory, ChangesInbox, ChangesPage } from "../src/changes/ChangesPage.tsx";
import { createAndSelect, DraftPickerView } from "../src/changes/DraftPicker.tsx";
import {
  inboxGroups, newDraftError, pickerOptions, rollbackTarget,
  type ChangeEntry, type ChangesDraft,
} from "../src/changes/model.ts";

// Hand-typed in the shape `GET /api/changes/history` answers with (see
// tests/test_studio_changes_routes.py, which checks the route produces it).
const APPLY: ChangeEntry = {
  apply_id: "b".repeat(32), kind: "apply", status: "completed", draft: "warn-earlier", draft_id: "id-2",
  revision: "r2", ts: "2026-10-02T10:00:00+00:00", actor: "studio", reverses: "",
};
const ROLLBACK: ChangeEntry = {
  apply_id: "c".repeat(32), kind: "rollback", status: "completed", draft: "drop-stance", draft_id: "id-3",
  revision: "r3", ts: "2026-10-03T10:00:00+00:00", actor: "studio", reverses: "a".repeat(32),
};

// Hand-typed in the shape `GET /api/changes/drafts` answers with, one draft per status.
function draft(name: string, status: ChangesDraft["status"], created: string, extra: Partial<ChangesDraft> = {}): ChangesDraft {
  return {
    draft_id: `id-${name}`, name, path: `/worktrees/draft-${name}`, branch: `draft/${name}`, base: "installed",
    revision: `rev-${name}`, behind_by: 0, behind_installed: false, created_at: created, source: "",
    status, tests: 0, applied: null, rolled_back: null, ...extra,
  };
}
const DRAFTS: ChangesDraft[] = [
  draft("try-hooks", "open", "2026-10-11T09:00:00+00:00", { source: "stances" }),
  draft("read-narrowly", "tested", "2026-10-10T09:00:00+00:00", { tests: 2 }),
  draft("warn-earlier", "applied", "2026-10-01T09:00:00+00:00", { applied: APPLY }),
  draft("drop-stance", "rolled back", "2026-09-30T09:00:00+00:00",
    { applied: { ...APPLY, apply_id: "a".repeat(32), draft: "drop-stance" }, rolled_back: ROLLBACK }),
];

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...elements(element.props.children as ReactNode)];
}

function markup(node: ReactElement): string {
  return renderToStaticMarkup(h(MantineProvider, {}, node));
}

test("the inbox puts open and tested drafts first and shows each status", () => {
  const groups = inboxGroups(DRAFTS);
  assert.deepEqual(groups.waiting.map((item) => item.name), ["try-hooks", "read-narrowly"]);
  assert.deepEqual(groups.settled.map((item) => item.name), ["warn-earlier", "drop-stance"]);
  const html = markup(h(ChangesInbox, { drafts: DRAFTS, status: "ready", error: "", selected: "read-narrowly", onSelect: () => undefined }));
  assert.ok(html.includes("Waiting for you (2)") && html.includes("Applied or rolled back (2)"));
  for (const [name, status] of [["try-hooks", "open"], ["read-narrowly", "tested"], ["warn-earlier", "applied"], ["drop-stance", "rolled back"]]) {
    assert.ok(html.includes(`data-draft="${name}" data-status="${status}"`), name);
  }
  assert.ok(html.includes("From stances"));
  assert.ok(html.includes("2 tests"));
  assert.ok(html.includes('aria-label="read-narrowly: Tested"'));
  assert.ok(html.includes("Showing read-narrowly"));
  assert.match(html, /aria-label="drop-stance: Rolled back"[\s\S]*?aria-current="step"[^>]*>Rolled back</);
});

test("the inbox reads loading, error and empty", () => {
  const props = { drafts: [], error: "boom", selected: "", onSelect: () => undefined };
  assert.ok(markup(h(ChangesInbox, { ...props, status: "loading" })).includes("Reading drafts"));
  assert.ok(markup(h(ChangesInbox, { ...props, status: "error" })).includes("boom"));
  const empty = markup(h(ChangesInbox, { ...props, status: "ready" }));
  assert.ok(empty.includes("Nothing waiting") && !empty.includes("Applied or rolled back"));
});

test("a draft is rolled back from the inbox only while its apply is in effect", () => {
  assert.equal(rollbackTarget(DRAFTS[2]), APPLY.apply_id);
  assert.equal(rollbackTarget(DRAFTS[3]), "");
  assert.equal(rollbackTarget(DRAFTS[0]), "");
});

test("history lists applies and rollbacks with an undo for each completed one", () => {
  const undone: string[] = [];
  const open = { ...APPLY, apply_id: "d".repeat(32), status: "open" as const };
  const tree = ChangeHistory({ entries: [ROLLBACK, APPLY, open], status: "ready", error: "", undo: "", onUndo: (id) => undone.push(id) });
  const html = markup(tree);
  assert.ok(html.includes("Applied warn-earlier") && html.includes(`Rolled back ${"a".repeat(12)} of drop-stance`));
  assert.ok(html.includes("2026-10-03 10:00 UTC"));
  assert.equal((html.match(/Roll back…/g) ?? []).length, 2, "the open entry has no undo");
  assert.ok(markup(ChangeHistory({ entries: [], status: "ready", error: "", undo: "", onUndo: () => undefined })).includes("Nothing has been applied yet"));
  // The table's cells are rendered by DataTable, so call the undo cell's button directly.
  const columns = (tree.props as { columns: { key: string; cell: (row: ChangeEntry) => ReactNode }[] }).columns;
  const cell = columns.find((column) => column.key === "undo")!.cell(APPLY);
  const button = elements(cell).find((element) => element.type === Button);
  assert.ok(button);
  (button.props.onClick as () => void)();
  assert.deepEqual(undone, [APPLY.apply_id]);
});

test("the page renders the inbox and history from the two reads", () => {
  const queries = new QueryClient();
  queries.setQueryData(draftsQuery.queryKey, { schema_version: 1, drafts: DRAFTS });
  queries.setQueryData(historyQuery.queryKey, { schema_version: 1, entries: [ROLLBACK, APPLY] });
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client: queries },
    h(LiveUpdatesProvider, {}, h(MemoryRouter, { initialEntries: ["/changes"] }, h(ChangesPage))))));
  assert.ok(html.includes("<h1") && html.includes(">Changes</h1>"));
  assert.equal((html.match(/<h1/g) ?? []).length, 1, "the embedded settings editor has no second title");
  assert.ok(html.includes("citizen draft list --json") && html.includes("citizen draft history --json"));
  assert.ok(html.includes("Waiting for you (2)") && html.includes("Applied warn-earlier"));
  assert.ok(html.includes("Edit a draft&#x27;s settings"));
});

test("the picker lists drafts newest first and selects one", () => {
  assert.deepEqual(pickerOptions(DRAFTS).map((option) => option.label),
    ["try-hooks (drafted)", "read-narrowly (tested)", "warn-earlier (applied)", "drop-stance (rolled back)"]);
  const chosen: string[] = [];
  const tree = DraftPickerView({
    drafts: DRAFTS, status: "ready", error: "", value: "", onChange: (name) => chosen.push(name),
    newName: "", onNewName: () => undefined, creating: false, createError: "", onCreate: () => undefined,
  });
  const select = elements(tree).find((element) => element.type === Select);
  assert.ok(select);
  assert.equal((select.props.data as unknown[]).length, 4);
  (select.props.onChange as (value: string | null) => void)("warn-earlier");
  (select.props.onChange as (value: string | null) => void)(null);
  assert.deepEqual(chosen, ["warn-earlier"]);
  const create = elements(tree).find((element) => element.type === Button);
  assert.equal(create?.props.disabled, true, "no name, no create");
});

test("the picker creates a draft and selects it, or shows why not", async () => {
  assert.match(newDraftError("", DRAFTS), /Name the new draft/);
  assert.match(newDraftError("has space", DRAFTS), /letters, numbers/);
  assert.match(newDraftError("try-hooks", DRAFTS), /already exists/);
  assert.equal(newDraftError("fresh", DRAFTS), "");

  const created: string[] = [];
  const chosen: string[] = [];
  const create = async (name: string) => { created.push(name); return draft(name, "open", "2026-10-12T00:00:00+00:00"); };
  assert.equal(await createAndSelect(" fresh ", DRAFTS, create, (name) => chosen.push(name)), "");
  assert.deepEqual([created, chosen], [["fresh"], ["fresh"]]);

  assert.match(await createAndSelect("try-hooks", DRAFTS, create, (name) => chosen.push(name)), /already exists/);
  assert.deepEqual(created, ["fresh"], "a name already listed never reaches the server");
  const refused = async () => { throw new Error("draft-exists"); };
  assert.equal(await createAndSelect("other", DRAFTS, refused, (name) => chosen.push(name)), "draft-exists");
  assert.deepEqual(chosen, ["fresh"]);
});

test("createDraft posts the name and source with the session's CSRF token", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    const body = String(input) === "/api/session" ? { csrf_token: "token-1" } : draft("fresh", "open", "now", { source: "stances" });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    const answer = await createDraft(" fresh ", "stances");
    assert.equal(answer.source, "stances");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls.map((call) => call.url), ["/api/session", "/api/changes/drafts/create"]);
  assert.equal(calls[1].init?.method, "POST");
  assert.equal((calls[1].init?.headers as Record<string, string>)["X-Studio-CSRF"], "token-1");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { draft: "fresh", source: "stances" });
});
