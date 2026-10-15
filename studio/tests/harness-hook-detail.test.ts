import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { PeriodRow } from "../src/harness/model.ts";
import {
  HookDetailPage, HookDetailView, UnknownHook, hookDetailQuery, hookHref, loadHookDetail, outcomeTotals,
  type HookDetail,
} from "../src/pages/harness/HookDetailPage.tsx";
import { HookLinks } from "../src/pages/harness/HooksPage.tsx";

// Hand-typed in the shape `GET /api/harness/hook?name=grade-bash` answers with, values taken from
// `tests/test_studio_surface_hooks.py`'s fixture; no generator exists.
const DETAIL: HookDetail = {
  hook: "grade-bash", label: "Command grader", built_in: false, core: true, window_days: 7,
  summary: { fires: 5, blocks: 2, warns: 1, errors: 1, points: "governance · grade-bash", last_fired: "2026-10-12 09:00 UTC", liveness: "erroring" },
  by_day: [
    { day: "2026-10-09", block: 1, warn: 0, allow: 0, error: 0 },
    { day: "2026-10-10", block: 0, warn: 1, allow: 0, error: 0 },
    { day: "2026-10-11", block: 0, warn: 0, allow: 2, error: 0 },
    { day: "2026-10-12", block: 0, warn: 0, allow: 0, error: 1 },
  ],
  recent: [
    { ts: "2026-10-12T09:00:00Z", point: "grade-bash", answer: "deny", outcome: "error", session: "s1", input_hash: "ab".repeat(32), reason: "" },
    { ts: "2026-10-09T12:00:00Z", point: "grade-bash", answer: "deny", outcome: "block", session: "s2", input_hash: "cd".repeat(32), reason: "Grade 3" },
  ],
  settings: { enabled: "on", modes: { "grade-bash": "advise" }, recording: true, source: "policy/hooks/grade-bash.py" },
};

function wrap(node: ReturnType<typeof h>, path = "/", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function routed(path: string, client: QueryClient): string {
  return wrap(h(Routes, {}, h(Route, { path: "/harness/hooks/:hook", element: h(HookDetailPage) })), path, client);
}

test("the page shows liveness, figures, decisions by day, recent decisions and settings", () => {
  const html = wrap(h(HookDetailView, { detail: DETAIL }));
  assert.ok(html.includes("Command grader"));
  assert.match(html, /data-liveness="erroring"/);
  assert.match(html, /href="\/harness\/hooks"/, "the breadcrumb leads back to Hooks");
  const days = /aria-label="Decisions by day and outcome"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  assert.deepEqual([...days.matchAll(/data-day="([^"]+)"/g)].map((m) => m[1]), DETAIL.by_day.map((d) => d.day));
  const recent = /aria-label="Recent decisions, newest first"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  assert.deepEqual([...recent.matchAll(/data-outcome="([^"]+)"/g)].map((m) => m[1]), ["error", "block"]);
  assert.ok(recent.includes("s2") && recent.includes("cd".repeat(6)) && recent.includes("Grade 3"));
  const settings = /aria-label="Hook settings"[\s\S]*?<\/section>/.exec(html)?.[0] ?? "";
  assert.ok(settings.includes("advise") && settings.includes("policy/hooks/grade-bash.py"));
  assert.match(settings, /href="\/harness\/hooks\?tab=settings"/);
});

test("outcome totals add each day's counts", () => {
  assert.deepEqual(outcomeTotals(DETAIL.by_day), { block: 1, warn: 1, allow: 2, error: 1 });
});

test("an empty window says so rather than showing an empty table", () => {
  const html = wrap(h(HookDetailView, { detail: { ...DETAIL, recent: [] } }));
  assert.ok(html.includes("No decisions in this window"));
});

test("a built-in point has no draft link", () => {
  const html = wrap(h(HookDetailView, { detail: { ...DETAIL, hook: "point:evasion-deny", built_in: true, settings: { ...DETAIL.settings, enabled: "always on", source: "" } } }));
  assert.ok(html.includes("always on") && html.includes("Built in"));
  assert.doesNotMatch(html, /tab=settings/);
});

test("the routed page reads the hook named in the path", () => {
  const client = new QueryClient();
  client.setQueryData(hookDetailQuery("grade-bash").queryKey, DETAIL);
  assert.ok(routed("/harness/hooks/grade-bash", client).includes("Recent decisions"));
});

test("an unknown hook is a 404 the page names", async () => {
  const original = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = (async (url: string) => {
    seen.push(url);
    return new Response(JSON.stringify({ error: "unknown_hook" }), { status: 404 });
  }) as typeof fetch;
  try {
    await assert.rejects(loadHookDetail("point:nope"), UnknownHook);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(seen, ["/api/harness/hook?name=point%3Anope"]);
  assert.equal(hookDetailQuery("x").retry(0, new UnknownHook("x")), false);
});

test("each Hooks page row links to its detail page", () => {
  const rows: PeriodRow[] = ["grade-bash", "point:evasion-deny"].map((item_id) => ({
    item_id, item_label: item_id, setting_label: "on", period: { start: "2026-09-12T12:00:00Z", end: null },
    status: "measured", window_days: 7, metrics: { liveness: "live" }, evals_href: "",
  }));
  const html = wrap(h(HookLinks, { rows }));
  assert.match(html, /href="\/harness\/hooks\/grade-bash"/);
  assert.match(html, new RegExp(`href="${hookHref("point:evasion-deny")}"`));
  assert.equal(hookHref("point:evasion-deny"), "/harness/hooks/point%3Aevasion-deny");
});
