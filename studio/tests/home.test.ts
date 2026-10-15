import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { homeQuery, loadHome, type AttentionItem, type HomeReport, type ReadyDigest } from "../src/home/api.ts";
import { AttentionQueue, DigestStrip, DriftSection, HomePage, HomeView } from "../src/home/HomePage.tsx";
import { digestFigures, failedSources, money, shownItems, tokens } from "../src/home/model.ts";

// Hand-typed in the shape `GET /api/home` answers (`citizen home --json` printed it on a temporary
// home); no generator exists.
function item(id: string, severity: AttentionItem["severity"], href: string, label = "Open"): AttentionItem {
  return {
    id, source: id.split(":")[0], kind: "thing", severity, title: `Title of ${id}`, detail: `Detail of ${id}`,
    at: null, action: { label, href },
  };
}

const DIGEST: ReadyDigest = {
  status: "ready", days: 1, indexing: false, pending: 0,
  basis: "An observational reading of your own session transcripts on this machine, not evidence of any effect.",
  sessions: 4, calls: 120, cost: 12.5, cost_complete: true, median_context: 180000, max_context: 512000,
  calls_over_warn: 30, calls_over_limit: 2, sessions_over_limit: 1, context_warn: 160000, context_limit: 400000,
};

const REPORT: HomeReport = {
  schema_version: 1, generated_at: "2026-10-15T12:00:00Z", digest: DIGEST,
  drift: { status: "drift", items: ["~/.claude/CLAUDE.md differs"], command: "citizen sync", message: null },
  queue: {
    items: [
      item("context:over-limit", "high", "/sessions", "See sessions"),
      item("hooks:grade-bash", "medium", "/harness/hooks/grade-bash", "Check hook"),
      item("drift:projection", "medium", "/home#drift", "See drift"),
      item("drafts:tidy", "medium", "/changes?draft=tidy", "Review draft"),
      item("rules:read-narrowly", "low", "/harness/rules", "Review rule"),
    ],
    total: 7,
    sources: { context: "ok", drafts: "ok", drift: "ok", hooks: "ok", rules: "ok" },
  },
};

function wrap(node: ReturnType<typeof h>, path = "/home", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function queueRows(html: string): string[] {
  return [...html.matchAll(/data-item-id="([^"]+)"/g)].map((match) => match[1]);
}

test("Home shows the digest, the queue with one action per item, and the drift", () => {
  const html = wrap(h(HomeView, { report: REPORT }));
  assert.match(html, /aria-label="Daily digest"/);
  assert.ok(html.includes("$12.50") && html.includes("180k") && html.includes(DIGEST.basis));
  assert.deepEqual(queueRows(html), REPORT.queue.items.map((entry) => entry.id));
  const table = /aria-label="Attention queue, most pressing first"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  for (const row of table.split("<tr").slice(2)) {
    assert.equal([...row.matchAll(/<a /g)].length, 1, "each item carries exactly one action");
  }
  assert.match(table, /href="\/harness\/hooks\/grade-bash"[^>]*>[\s\S]*?Check hook/);
  assert.ok(html.includes("The first 5 of 7"));
  const drift = /id="drift"[\s\S]*?<\/section>/.exec(html)?.[0] ?? "";
  assert.ok(drift.includes("~/.claude/CLAUDE.md differs") && drift.includes("citizen sync"));
});

test("the queue never shows more than five items, whatever it is sent", () => {
  const items = Array.from({ length: 8 }, (_, n) => item(`drafts:d${n}`, "low", `/changes?draft=d${n}`));
  assert.equal(shownItems(items).length, 5);
  const html = wrap(h(AttentionQueue, { queue: { items, total: 8, sources: { drafts: "ok" } } }));
  assert.equal(queueRows(html).length, 5);
});

test("an empty queue says nothing needs you, and an unread source is named", () => {
  const html = wrap(h(AttentionQueue, { queue: { items: [], total: 0, sources: { rules: "error", drift: "ok", hooks: "error" } } }));
  assert.ok(html.includes("Nothing needs you"));
  assert.ok(html.includes("Could not read Hooks, Rules"));
  assert.deepEqual(failedSources({ rules: "error", routines: "error", context: "ok" }), ["Routines", "Rules"]);
});

test("the digest marks a figure past the cost stance's line and says when it is still indexing", () => {
  const figures = digestFigures(DIGEST);
  assert.deepEqual(figures.filter((figure) => figure.over).map((figure) => figure.label),
    ["Median context per call", "Calls past 400k"]);
  assert.ok(figures.every((figure) => figure.href === "/sessions"));
  const html = wrap(h(DigestStrip, { digest: { ...DIGEST, indexing: true, pending: 3 } }));
  assert.ok(html.includes("Still indexing 3 transcripts"));
  assert.match(html, /data-over="true"/);
  const quiet = digestFigures({ ...DIGEST, median_context: null, calls_over_limit: 0, cost: null });
  assert.equal(quiet.some((figure) => figure.over), false);
  assert.equal(quiet[1].value, "unpriced");
});

test("an unreadable digest or drift is shown as an error, never as zero", () => {
  const digest = wrap(h(DigestStrip, { digest: { status: "unavailable", message: "index unreadable" } }));
  assert.ok(digest.includes("The digest could not be read") && digest.includes("index unreadable"));
  const drift = wrap(h(DriftSection, { drift: { status: "failed", items: [], message: "no doctor" } }));
  assert.ok(drift.includes("Drift could not be read") && drift.includes("no doctor"));
  assert.ok(wrap(h(DriftSection, { drift: { status: "current", items: [] } })).includes("match the selection"));
});

test("figures read in plain words", () => {
  assert.equal(tokens(256400), "256k");
  assert.equal(tokens(900), "900");
  assert.equal(tokens(null), "none");
  assert.equal(money(3), "$3.00");
});

test("the routed page renders the cached report and a pending one says it is reading", () => {
  const client = new QueryClient();
  client.setQueryData(homeQuery.queryKey, REPORT);
  const routed = (c: QueryClient) => wrap(h(Routes, {}, h(Route, { path: "/home", element: h(HomePage) })), "/home", c);
  const html = routed(client);
  assert.match(html, /<h1[^>]*>Home<\/h1>/);
  assert.deepEqual(queueRows(html).length, 5);
  assert.ok(routed(new QueryClient()).includes("Reading your sessions"));
});

test("loadHome reads GET /api/home and refuses an error or an incomplete answer", async () => {
  const original = globalThis.fetch;
  const seen: Array<[string, string | undefined]> = [];
  const answers: Response[] = [
    new Response(JSON.stringify(REPORT), { status: 200 }),
    new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 }),
    new Response(JSON.stringify({ digest: DIGEST }), { status: 200 }),
  ];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    seen.push([url, init?.method]);
    return answers.shift()!;
  }) as typeof fetch;
  try {
    assert.equal((await loadHome()).queue.total, 7);
    await assert.rejects(loadHome(), /unauthorized/);
    await assert.rejects(loadHome(), /incomplete/);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(seen, [["/api/home", "GET"], ["/api/home", "GET"], ["/api/home", "GET"]]);
});
