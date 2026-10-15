import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { createDraft } from "../src/changes/api.ts";
import { createAndSelect, DraftPicker } from "../src/changes/DraftPicker.tsx";
import type { ChangesDraft } from "../src/changes/model.ts";
import type { DraftSelectionRead, DraftSelectionSave } from "../src/configure/api.ts";
import { periodsQuery } from "../src/harness/api.ts";
import { HarnessFrame, HarnessPage, saveToDraft, type DraftSelection } from "../src/harness/HarnessPage.tsx";
import { tabSearch, type DraftChange, type HarnessTab, type SettingRow } from "../src/harness/model.ts";
import { changesHref, saveSelectionToDraft, selectedDraft, withDraft } from "../src/harness/selectedDraft.ts";
import { ruleSelectionChanges } from "../src/pages/harness/rulesModel.ts";

// Hand-typed rows in the shape the Settings tab receives; no generator exists.
const SETTINGS: SettingRow[] = [{
  item_id: "voice", item_label: "Voice", values: { variant: "concise" },
  options: { variant: ["concise", "detailed"] }, last_changed: null,
}];
const FIELDS = [{ key: "variant", heading: "Variant" }];
const CHANGE: DraftChange = { item_id: "voice", field: "variant", from: "concise", to: "detailed" };

function draftSelection(overrides: Partial<DraftSelection> = {}): DraftSelection {
  return { value: "", source: "stances", onChange: () => undefined, ...overrides };
}

function frameProps(overrides: Record<string, unknown> = {}) {
  return {
    title: "Stances", question: "Which variant runs?", itemNoun: "Stance", fields: FIELDS,
    tab: "settings" as HarnessTab, onTabChange: () => undefined,
    periods: { state: "ready" as const, rows: [] }, settings: { state: "ready" as const, rows: SETTINGS }, edits: {},
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: () => undefined,
    ...overrides,
  };
}

function render(node: ReactNode, path = "/harness/stances?tab=settings", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  // The settings table carries the draft picker in its footer prop.
  return [element, ...elements(element.props.children as ReactNode), ...elements(element.props.draftControls as ReactNode)];
}

function pickerIn(tree: ReactNode) {
  return elements(tree).find((element) => element.type === DraftPicker);
}

function listed(name: string): ChangesDraft {
  return {
    draft_id: `id-${name}`, name, path: `/drafts/${name}`, branch: `draft/${name}`, base: "abc", revision: `rev-${name}`,
    behind_by: 0, behind_installed: false, created_at: "2026-10-12T09:00:00+00:00", source: "", status: "open",
    tests: 0, applied: null, rolled_back: null,
  };
}

test("the selected draft lives in the URL beside the tab", () => {
  assert.equal(selectedDraft("?tab=settings&draft=try-voice"), "try-voice");
  assert.equal(selectedDraft("?tab=settings"), "");
  assert.equal(withDraft("?tab=settings", "try-voice").toString(), "tab=settings&draft=try-voice");
  assert.equal(withDraft("?tab=settings&draft=old", "").toString(), "tab=settings");
  assert.equal(tabSearch("?tab=settings&draft=try-voice", "efficacy").toString(), "draft=try-voice", "switching tabs keeps the draft");
  assert.equal(changesHref("try voice"), "/changes?draft=try%20voice");
});

test("the Settings tab carries the draft picker, and picking a draft reports it to the page", () => {
  const picked: string[] = [];
  const tree = HarnessFrame(frameProps({ draft: draftSelection({ value: "try-voice", onChange: (name: string) => picked.push(name) }) }));
  const picker = pickerIn(tree);
  assert.ok(picker, "the frame renders the shared picker");
  assert.equal(picker.props.value, "try-voice");
  (picker.props.onChange as (name: string) => void)("other-draft");
  assert.deepEqual(picked, ["other-draft"]);
  assert.equal(pickerIn(HarnessFrame(frameProps())), undefined, "a frame given no selection shows no picker");

  const html = render(h(HarnessFrame, frameProps({ draft: draftSelection({ value: "try-voice" }) })));
  assert.ok(html.includes("Save into draft"));
  assert.ok(html.includes("New draft name"));
  assert.match(html, /aria-label="Stances: draft to save into"/);
  assert.match(html, /Edits save into draft <code[^>]*>try-voice<\/code>/);
  const failed = render(h(HarnessFrame, frameProps({ draft: draftSelection({ value: "gone", error: "No draft named gone." }) })));
  assert.match(failed, /role="alert"[^>]*>No draft named gone\./);
});

test("every Harness page shows the picker on Settings with the URL's draft selected", () => {
  const client = new QueryClient();
  client.setQueryData(periodsQuery("stances").queryKey, { surface: "stances", rows: [] });
  const page = h(HarnessPage, {
    surface: "stances", title: "Stances", question: "Which variant runs?", itemNoun: "Stance", fields: FIELDS,
    settings: { state: "ready", rows: SETTINGS }, onDraft: () => undefined,
  });
  const html = render(page, "/harness/stances?tab=settings&draft=try-voice", client);
  assert.ok(html.includes("Save into draft"));
  assert.match(html, /Edits save into draft <code[^>]*>try-voice<\/code>/);
  const none = render(page, "/harness/stances?tab=settings", client);
  assert.ok(none.includes("Pick or create a draft"));
});

test("creating a draft from the picker records the page as its source and selects it", async () => {
  const tree = HarnessFrame(frameProps({ draft: draftSelection({ source: "hooks" }) }));
  const source = pickerIn(tree)?.props.source;
  assert.equal(source, "hooks", "the frame passes the surface name as the source");

  const original = globalThis.fetch;
  const bodies: unknown[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input) === "/api/session") return new Response(JSON.stringify({ csrf_token: "token-1" }), { status: 200 });
    bodies.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ ...listed("try-hooks"), source: "hooks" }), { status: 200 });
  }) as typeof fetch;
  const chosen: string[] = [];
  try {
    const refusal = await createAndSelect("try-hooks", [listed("existing")], (name) => createDraft(name, String(source)),
      (name) => chosen.push(name));
    assert.equal(refusal, "");
  } finally { globalThis.fetch = original; }
  assert.deepEqual(bodies, [{ draft: "try-hooks", source: "hooks" }]);
  assert.deepEqual(chosen, ["try-hooks"]);
});

test("saving hands the page the selected draft and refuses without one", async () => {
  const calls: Array<[DraftChange[], string]> = [];
  const onDraft = (changes: DraftChange[], draft: string) => { calls.push([changes, draft]); };
  assert.equal(await saveToDraft([CHANGE], "try-voice", onDraft), "try-voice");
  assert.deepEqual(calls, [[[CHANGE], "try-voice"]]);
  await assert.rejects(saveToDraft([CHANGE], "", onDraft), /Pick or create a draft/);
  assert.equal(calls.length, 1, "nothing is saved without a draft");
  assert.equal(await saveToDraft([CHANGE], "", onDraft, true), "", "a page that makes its own draft may save with none");
  assert.deepEqual(calls[1], [[CHANGE], ""]);
  await assert.rejects(saveToDraft([CHANGE], "try-voice", async () => { throw new Error("stale revision"); }), /stale revision/);
});

test("after a save the Settings tab links to that draft in Changes", () => {
  const html = render(h(HarnessFrame, frameProps({ draft: draftSelection({ value: "try-voice" }), savedDraft: "try-voice" })));
  assert.match(html, /<a[^>]*href="\/changes\?draft=try-voice"[^>]*>Review try-voice in Changes<\/a>/);
  assert.ok(html.includes("Nothing is live until you apply it"));
  const before = render(h(HarnessFrame, frameProps({ draft: draftSelection({ value: "try-voice" }) })));
  assert.ok(!before.includes("Review try-voice in Changes"), "no link before a save");
});

function selectionRead(overrides: Partial<DraftSelectionRead> = {}): DraftSelectionRead {
  return {
    status: "ready", message: "", draft: { name: "try-roles", revision: "rev-1" },
    controls: { modes: [], stances: [], switches: [], core_acknowledged: false }, current: {} as DraftSelectionRead["current"],
    ...overrides,
  };
}

function selectionSave(overrides: Partial<DraftSelectionSave>): DraftSelectionSave {
  return {
    valid: true, error: "", error_code: "", changed: [], unchanged: false, base_revision: "rev-1", before: {},
    saved: true, result: { revision: "rev-2" }, ...overrides,
  } as DraftSelectionSave;
}

test("a switch save goes through selection editing against the draft's current revision", async () => {
  const saves: unknown[][] = [];
  const api = {
    read: async () => selectionRead(),
    save: async (...args: unknown[]) => { saves.push(args); return selectionSave({}); },
  };
  await saveSelectionToDraft("try-roles", { "roles.builder": "off" }, api as never, () => "key-1");
  assert.deepEqual(saves, [["try-roles", "rev-1", "key-1", { "roles.builder": "off" }]]);
  await saveSelectionToDraft("try-roles", {}, api as never);
  assert.equal(saves.length, 1, "no change, no save");
  await saveSelectionToDraft("try-roles", { "roles.builder": "off" }, { ...api, save: async () => selectionSave({ unchanged: true, saved: false }) } as never);
  await assert.rejects(
    saveSelectionToDraft("try-roles", { "roles.builder": "off" }, { ...api, save: async () => selectionSave({ saved: false, error: "unknown unit" }) } as never),
    /unknown unit/,
  );
  await assert.rejects(
    saveSelectionToDraft("gone", { "roles.builder": "off" }, { ...api, read: async () => selectionRead({ status: "unavailable", message: "No draft named gone." }) } as never),
    /No draft named gone/,
  );
});

test("a rule switch saved into a picked draft is a rules.<name> selection change", () => {
  assert.deepEqual(ruleSelectionChanges([
    { item_id: "conciseness", field: "on", from: "on", to: "off" },
    { item_id: "conciseness", field: "tokens", from: "1", to: "2" },
  ]), { "rules.conciseness": "off" });
});
