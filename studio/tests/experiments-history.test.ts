import assert from "node:assert/strict";
import test from "node:test";

import { loadCaseHistory, loadEvidence, loadHistory, loadRunDetail, rerun } from "../src/experiments/history/api.ts";
import { displayUnknown, emptyFilters } from "../src/experiments/history/model.ts";
import { pageTitle } from "../src/navigation.ts";

test("unknown values stay unknown instead of becoming zero", () => {
  assert.equal(displayUnknown(null), "Unknown");
  assert.equal(displayUnknown(0, " ms"), "0 ms");
});

test("run detail stays inside the Experiments navigation context", () => {
  assert.equal(pageTitle("/experiments/runs/run-id"), "Experiments");
});

test("history, detail, case history, evidence and rerun use fixed CSRF routes", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
    if (String(input) === "/api/runs/history") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
    if (String(input) === "/api/runs/case-history") return new Response(JSON.stringify({ case_id: "case", items: [], next_cursor: null }), { status: 200 });
    if (String(input) === "/api/runs/evidence") return new Response(JSON.stringify({ content: "log" }), { status: 200 });
    if (String(input) === "/api/runs/rerun") return new Response(JSON.stringify({ run_id: "rerun" }), { status: 200 });
    return new Response(JSON.stringify({ run_id: "run", cases: [], reruns: [] }), { status: 200 });
  }) as typeof fetch;
  try {
    await loadHistory(emptyFilters);
    await loadRunDetail("run");
    await loadCaseHistory("case");
    await loadEvidence("run", "stdout");
    await rerun("run");
  } finally { globalThis.fetch = original; }
  const routes = calls.filter((call) => call.input.startsWith("/api/runs/"));
  assert.deepEqual(routes.map((call) => call.input), [
    "/api/runs/history", "/api/runs/detail", "/api/runs/case-history",
    "/api/runs/evidence", "/api/runs/rerun",
  ]);
  for (const call of routes) {
    assert.equal(call.init?.credentials, "same-origin");
    assert.deepEqual(call.init?.headers, { "Content-Type": "application/json", "X-Studio-CSRF": "csrf" });
  }
  assert.deepEqual(JSON.parse(routes[1].init?.body as string), {
    run_id: "run", lineage_limit: 50, lineage_cursor: null,
  });
});
