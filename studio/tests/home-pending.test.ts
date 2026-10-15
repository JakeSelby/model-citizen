import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { CHECK_AGAIN_MS, homeQuery, stillChecking, type AttentionItem, type HomeReport } from "../src/home/api.ts";
import { AttentionQueue, DriftSection } from "../src/home/HomePage.tsx";
import { failedSources, pendingSources } from "../src/home/model.ts";

// Hand-typed in the shape `GET /api/home` answers while its slow reads warm; no generator exists.
const ITEM: AttentionItem = {
  id: "drafts:tidy", source: "drafts", kind: "draft", severity: "medium", title: "Draft tidy is tested",
  detail: "", at: null, action: { label: "Review draft", href: "/changes?draft=tidy" },
};

const REPORT: HomeReport = {
  schema_version: 1, generated_at: "2026-10-15T12:00:00Z",
  digest: { status: "unavailable", message: "no index" },
  drift: { status: "current", items: [], message: null },
  queue: { items: [], total: 0, sources: { drafts: "ok", drift: "ok", rules: "ok" } },
};

function wrap(node: ReturnType<typeof h>): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, node)));
}

test("a pending source is still checking, never a failure", () => {
  const sources = { rules: "pending", drift: "pending", hooks: "error", drafts: "ok" };
  assert.deepEqual(pendingSources(sources), ["Drift", "Rules"]);
  assert.deepEqual(failedSources(sources), ["Hooks"]);
});

test("an empty queue with a source still checking says so instead of nothing needs you", () => {
  const html = wrap(h(AttentionQueue, { queue: { items: [], total: 0, sources: { rules: "pending", drafts: "ok" } } }));
  assert.match(html, /aria-busy="true"[\s\S]*Still checking Rules/);
  assert.ok(!html.includes("Nothing needs you<"), "no all-clear while a source is unread");
  assert.ok(!html.includes("Could not read"));
});

test("a queue with items names the sources still checking under it", () => {
  const html = wrap(h(AttentionQueue, { queue: { items: [ITEM], total: 1, sources: { rules: "pending", drift: "pending", drafts: "ok" } } }));
  assert.match(html, /data-item-id="drafts:tidy"/);
  assert.match(html, /role="status"[^>]*>Still checking Drift, Rules; their items will join the queue/);
});

test("drift still being read is a checking badge, not unavailable", () => {
  const html = wrap(h(DriftSection, { drift: { status: "pending", items: [], message: null } }));
  assert.ok(html.includes("Still checking") && !html.includes("Unavailable"));
  assert.ok(!html.includes("Drift could not be read"));
});

test("Home asks again while anything is still checking, and stops once it all landed", () => {
  const interval = (data?: HomeReport) => homeQuery.refetchInterval({ state: { data } });
  assert.equal(interval(REPORT), false);
  assert.equal(interval(undefined), false);
  assert.equal(stillChecking({ ...REPORT, drift: { ...REPORT.drift, status: "pending" } }), true);
  assert.equal(interval({ ...REPORT, queue: { ...REPORT.queue, sources: { rules: "pending" } } }), CHECK_AGAIN_MS);
  const indexing = { ...REPORT, digest: { status: "ready", days: 1, basis: "", indexing: true, pending: 2, sessions: 0, calls: 0,
    cost: null, cost_complete: true, median_context: null, max_context: null, calls_over_warn: 0, calls_over_limit: 0,
    sessions_over_limit: 0, context_warn: 1, context_limit: 2 } } as HomeReport;
  assert.equal(interval(indexing), CHECK_AGAIN_MS);
});
