import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import type { ActivityEntry } from "../src/activity/model.ts";
import type { SelectionControls } from "../src/configure/api.ts";
import type { ConfigureSchema } from "../src/configure/model.ts";
import { periodsQuery } from "../src/harness/api.ts";
import { HarnessFrame } from "../src/harness/HarnessPage.tsx";
import type { DraftChange, PeriodRow } from "../src/harness/model.ts";
import { HooksPage, loadSafetyLog, safetyQuery, saveHookChanges, type HookDraftApi } from "../src/pages/harness/HooksPage.tsx";
import {
  HOOK_FIELDS, HOOK_METRICS, NO_MODE, livenessCounts, modeChoices, settingRows, splitChanges, worstFirst,
  type DraftState,
} from "../src/pages/harness/hooksModel.ts";

// Hand-typed in the shape `GET /api/harness/periods?surface=hooks` answers with, values taken
// from `tests/test_studio_surface_hooks.py`'s fixture; no generator exists.
const NOTE = "No setting change was recorded for these items in this window, so each shows one period.";
function row(item_id: string, item_label: string, metrics: PeriodRow["metrics"], setting_label = "on"): PeriodRow {
  return {
    item_id, item_label, setting_label, period: { start: "2026-09-12T12:00:00Z", end: null },
    status: "measured", window_days: 7, metrics, evals_href: `/evals?surface=hooks&item=${item_id}`,
  };
}
const ROWS: PeriodRow[] = [
  row("grade-bash", "Command grader", { fires: 5, blocks: 2, warns: 1, errors: 1, last_fired: "2026-10-12 09:00 UTC", liveness: "erroring", points: "governance · grade-bash" }),
  row("stop-gate", "Quality gate before a turn ends", { fires: 2, blocks: 1, warns: 0, errors: 0, last_fired: "2026-10-12 11:00 UTC", liveness: "live", points: "stop-gate" }),
  row("steer-polling", "Background long waits", { fires: 0, blocks: 0, warns: 0, errors: 0, last_fired: "2026-10-02 12:00 UTC", liveness: "silent", points: "none" }),
  row("usage-log", "Usage ledger", { fires: 0, blocks: 0, warns: 0, errors: 0, last_fired: "not in window", liveness: "not observable", points: "none" }),
  row("point:evasion-deny", "evasion-deny (built in)", { fires: 1, blocks: 1, warns: 0, errors: 0, last_fired: "2026-10-10 12:00 UTC", liveness: "built in", points: "evasion-deny" }, "always on"),
];

function entry(id: string, outcome: string, timestamp: string, extra: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id, timestamp, source: "decision-log", kind: "decision", title: "", outcome, reason: `${outcome} reason`,
    actor: "claude-code", session: "s1", repository: "", hook: "hooks/grade-bash", grade: "", command: `cmd-${id}`,
    draft: "", files: [], evidence_href: "", evidence_label: "", apply_id: "", rollback_target: "", ...extra,
  };
}

const SCHEMA: ConfigureSchema = {
  schema_version: 1,
  commands: { schema: "", read: "", preview: "", save: "" },
  sections: [{ id: "governance", label: "Governance", description: "", fields: [{
    path: "governance.jev.modes", section: "governance", label: "JEV point modes", help: "", kind: "json-object",
    provenance: "", required: false, options: [], reference_sources: [], default: {},
    constraints: { keys: ["grade-bash", "stop-gate", "evasion-deny"], values: ["off", "shadow", "advise", "act"] },
  }] }],
};

const CONTROLS: SelectionControls = {
  modes: [], stances: [], core_acknowledged: false,
  switches: [{ kind: "hooks", rows: [
    { unit: "grade-bash", value: "on", core: true },
    { unit: "stop-gate", value: "on", core: true },
    { unit: "steer-polling", value: "off", core: false },
    { unit: "usage-log", value: "on", core: false },
  ] }],
};

const DRAFT: DraftState = {
  draft: "hooks-tune", revision: "r1", controls: CONTROLS,
  values: { governance: { jev: { mode: "shadow", modes: { "grade-bash": "advise" } } } },
};

function render(path: string, client: QueryClient): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, h(HooksPage)))));
}

function seeded(entries: ActivityEntry[] = []): QueryClient {
  const client = new QueryClient();
  client.setQueryData(periodsQuery("hooks").queryKey, { surface: "hooks", rows: ROWS, note: NOTE });
  client.setQueryData(safetyQuery.queryKey, entries);
  client.setQueryData(["configure-schema"], SCHEMA);
  return client;
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

test("Efficacy renders one row per hook with liveness, figures and the note", () => {
  const html = render("/harness/hooks", seeded());
  assert.ok(html.includes("Hooks: efficacy by setting period"));
  for (const label of ["Command grader", "Quality gate before a turn ends", "Usage ledger", "evasion-deny (built in)"]) {
    assert.ok(html.includes(label), label);
  }
  for (const heading of ["Fires", "Blocks", "Warns", "Errors", "Last fired", "Liveness"]) assert.ok(html.includes(heading), heading);
  const grader = /<tbody[^>]*data-item="grade-bash"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(grader.includes("erroring") && grader.includes("2026-10-12 09:00 UTC") && grader.includes("governance · grade-bash"));
  assert.ok(html.includes(NOTE), "the API's note is rendered");
  assert.ok(html.includes("not observable"));
});

test("liveness counts put the worst first and never call an unobservable hook silent", () => {
  assert.deepEqual(livenessCounts(ROWS), [
    { liveness: "erroring", count: 1 }, { liveness: "silent", count: 1 }, { liveness: "not observable", count: 1 },
    { liveness: "built in", count: 1 }, { liveness: "live", count: 1 },
  ]);
  const html = render("/harness/hooks", seeded());
  assert.match(html, /aria-label="Hook liveness"/);
  assert.ok(html.includes("1 silent") && html.includes("1 not observable"));
});

test("the Safety log shows blocked, asked and warned actions worst first", async () => {
  const entries = [
    entry("a", "warn", "2026-10-12T10:00:00Z"),
    entry("b", "confirmation-required", "2026-10-12T11:00:00Z"),
    entry("c", "refused", "2026-10-10T09:00:00Z"),
    entry("d", "refused", "2026-10-11T09:00:00Z"),
    entry("d", "refused", "2026-10-11T09:00:00Z"),
    entry("e", "completed", "2026-10-12T12:00:00Z", { source: "studio-action" }),
  ];
  assert.deepEqual(worstFirst(entries).map((item) => item.id), ["d", "c", "b", "a"]);
  const html = render("/harness/hooks", seeded(worstFirst(entries)));
  const log = /aria-label="Safety log, worst first"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  assert.deepEqual([...log.matchAll(/data-outcome="([^"]+)"/g)].map((match) => match[1]),
    ["refused", "refused", "confirmation-required", "warn"]);
  assert.ok(log.includes("Blocked") && log.includes("Asked") && log.includes("Warned"));
  assert.ok(render("/harness/hooks", seeded([])).includes("Nothing blocked or warned"));

  const asked: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/session") return new Response(JSON.stringify({ csrf_token: "t" }), { status: 200 });
    const body = JSON.parse(String(init?.body)) as { outcome: string };
    asked.push(body.outcome);
    const page = { schema_version: 1, entries: [entry(body.outcome, body.outcome, "2026-10-12T00:00:00Z")], next_cursor: "", next_command: "", sources: [], filters: {}, command: "" };
    return new Response(JSON.stringify(page), { status: 200 });
  }) as typeof fetch;
  try {
    const loaded = await loadSafetyLog();
    assert.deepEqual(asked, ["refused", "confirmation-required", "warn"]);
    assert.deepEqual(loaded.map((item) => item.outcome), ["refused", "confirmation-required", "warn"]);
  } finally { globalThis.fetch = original; }
});

test("Settings shows each hook's switch and mode, read-only until a draft is loaded", () => {
  const choices = modeChoices(SCHEMA);
  assert.deepEqual(choices.points, ["grade-bash", "stop-gate", "evasion-deny"]);
  const bare = settingRows(ROWS, null, choices);
  assert.deepEqual(bare.map((item) => item.item_id), ["grade-bash", "stop-gate", "steer-polling", "usage-log"], "built-in points have no settings");
  assert.deepEqual(bare[0].values, { enabled: "on", mode: "load a draft" });
  assert.equal(bare[2].values.mode, NO_MODE, "a hook with no decision mode is labelled, not editable");
  assert.ok(bare.every((item) => item.options === undefined));

  const edited = settingRows(ROWS, DRAFT, choices);
  assert.deepEqual(edited[0].values, { enabled: "on", mode: "advise" });
  assert.deepEqual(edited[1].values, { enabled: "on", mode: "shadow" }, "an unset point falls back to the default mode");
  assert.deepEqual(edited[2], { item_id: "steer-polling", item_label: "Background long waits", item_detail: "steer-polling",
    values: { enabled: "off", mode: NO_MODE }, options: { enabled: ["on", "off"] }, last_changed: null });
  assert.equal(edited[0].item_detail, "grade-bash · core");

  const html = render("/harness/hooks?tab=settings", seeded());
  assert.ok(html.includes("Hooks: settings"));
  assert.ok(html.includes("Decision mode") && html.includes("load a draft") && html.includes(NO_MODE));
  assert.match(html, /aria-label="Edit hooks in a draft"/);
  assert.ok(!html.includes("Safety log"), "the Safety log belongs to Efficacy");
});

test("saving from Settings calls onDraft, which routes switches and modes to their save paths", async () => {
  const rows = settingRows(ROWS, DRAFT, modeChoices(SCHEMA));
  const edits = { "stop-gate": { enabled: "off", mode: "act" }, "steer-polling": { enabled: "on" } };
  const drafts: DraftChange[][] = [];
  const tree = HarnessFrame({
    title: "Hooks", question: "", itemNoun: "Hook", metrics: HOOK_METRICS, fields: HOOK_FIELDS, tab: "settings",
    onTabChange: () => undefined, periods: { state: "ready", rows: ROWS }, settings: { state: "ready", rows }, edits,
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes: DraftChange[]) => drafts.push(changes),
  });
  const table = elements(tree).find((element) => element.props.caption === "Hooks: settings");
  assert.ok(table);
  const send = elements((table.type as (props: unknown) => ReactNode)(table.props))
    .find((element) => element.type === Button && text(element) === "Send to Changes");
  assert.ok(send);
  (send.props.onClick as () => void)();
  assert.equal(drafts.length, 1);
  assert.deepEqual(splitChanges(drafts[0]), {
    selection: { "hooks.stop-gate": "off", "hooks.steer-polling": "on" },
    modes: { "stop-gate": "act" },
  });

  const calls: unknown[][] = [];
  const api: HookDraftApi = {
    saveSelection: async (...args) => {
      calls.push(["selection", args[0], args[1], args[3]]);
      return { saved: true, result: { revision: "r2" } } as Awaited<ReturnType<HookDraftApi["saveSelection"]>>;
    },
    loadConfig: async (draft) => {
      calls.push(["read", draft]);
      return { status: "ready", message: "", draft: { revision: "r2" }, values: DRAFT.values, warnings: [] };
    },
    saveConfig: async (...args) => {
      calls.push(["config", ...args]);
      return { saved: true, result: { revision: "r3" } } as Awaited<ReturnType<HookDraftApi["saveConfig"]>>;
    },
  };
  assert.equal(await saveHookChanges(DRAFT, drafts[0], api), "r3");
  assert.deepEqual(calls, [
    ["selection", "hooks-tune", "r1", { "hooks.stop-gate": "off", "hooks.steer-polling": "on" }],
    ["read", "hooks-tune"],
    ["config", "hooks-tune", "r2", { "governance.jev.modes": { "grade-bash": "advise", "stop-gate": "act" } }],
  ]);

  const refusing: HookDraftApi = { ...api, saveSelection: async () =>
    ({ saved: false, error: "core hooks need core_switches_acknowledged" }) as Awaited<ReturnType<HookDraftApi["saveSelection"]>> };
  await assert.rejects(saveHookChanges(DRAFT, drafts[0], refusing), /core_switches_acknowledged/);
});
