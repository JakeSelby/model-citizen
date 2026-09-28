import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { ActivityTimeline } from "../src/activity/ActivityPage.tsx";
import { loadActivity } from "../src/activity/api.ts";
import {
  activityTone,
  ActivityRequestGate,
  continuationLabel,
  mergeEntries,
  mergeSources,
  type ActivityPage,
} from "../src/activity/model.ts";

const payload: ActivityPage = {
  schema_version: 1,
  entries: [
    {
      id: "decision:one",
      timestamp: "2026-09-28T12:00:00Z",
      source: "decision-log",
      kind: "decision",
      title: "Command refused",
      outcome: "refused",
      reason: "grade-bash refused this command under the active guardrails.",
      actor: "codex",
      session: "session-one",
      repository: "repo:agent-harness/feature",
      hook: "hooks/grade-bash",
      grade: "3",
      command: "<script>globalThis.activityInjected = true</script>",
      draft: "",
      files: [],
      evidence_href: "/library?path=policy/hooks/grade-bash.py",
      evidence_label: "Open hooks/grade-bash in Library",
    },
    {
      id: "studio:apply-one",
      timestamp: "2026-09-28T11:00:00Z",
      source: "studio-action",
      kind: "apply",
      title: "Draft applied",
      outcome: "completed",
      reason: "Studio recorded the governed apply action.",
      actor: "Studio",
      session: "",
      repository: "repo:agent-harness/feature",
      hook: "",
      grade: "unknown",
      command: "citizen draft apply guard-change",
      draft: "guard-change",
      files: ["primitives/rules/guard.md", "policy/hooks/guard.py"],
      evidence_href: "",
      evidence_label: "",
    },
  ],
  next_cursor: "v1:20",
  next_command: "citizen activity --limit 25 --cursor v1:20",
  sources: [
    { id: "decision-log", status: "current", message: "Decision ledger available." },
    { id: "ownership-journal", status: "current", message: "Current ownership snapshot records 2 owned files." },
  ],
  filters: { session: "", repository: "", hook: "", outcome: "" },
  command: "citizen activity --json --limit 25",
};

test("activity renders command text as data with immutable provenance links", () => {
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(ActivityTimeline, { payload }))));
  assert.match(html, /&lt;script&gt;globalThis\.activityInjected = true&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>globalThis\.activityInjected/);
  assert.match(html, /href="\/library\?path=policy\/hooks\/grade-bash\.py"/);
  assert.match(html, /session-one/);
  assert.match(html, /guard-change/);
  assert.match(html, /primitives\/rules\/guard\.md/);
  assert.match(html, /Current ownership snapshot records 2 owned files/);
  assert.doesNotMatch(html, /Ownership journal reconciled/);
});

test("a bounded empty page offers to continue through remaining rows", () => {
  const partial = { ...payload, entries: [], sources: [
    { id: "decision-log" as const, status: "partial" as const, message: "2 malformed rows skipped." },
    payload.sources[1],
  ] };
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(ActivityTimeline, { payload: partial }))));
  assert.match(html, />decision-log<\/code>/);
  assert.match(html, />partial<\/span>/);
  assert.match(html, /2 malformed rows skipped/);
  assert.match(html, /No matches in this page/);
  assert.match(html, /More rows remain/);
  assert.doesNotMatch(html, /No activity matches/);
  assert.equal(continuationLabel(partial.entries), "Search older activity");
});

test("older pages merge without duplicating immutable entries", () => {
  assert.deepEqual(mergeEntries(payload.entries.slice(0, 1), payload.entries), payload.entries);
  const repeated = { ...payload.entries[0], id: "decision:one@20" };
  const repeatedAgain = { ...payload.entries[0], id: "decision:one@10" };
  assert.deepEqual(mergeEntries([repeated], [repeatedAgain]), [repeated, repeatedAgain]);
  const priorPartial = [
    { id: "decision-log" as const, status: "partial" as const, message: "2 malformed rows skipped." },
    payload.sources[1],
  ];
  assert.deepEqual(mergeSources(priorPartial, payload.sources), priorPartial);
  const priorFailed = [
    { id: "decision-log" as const, status: "failed" as const, message: "Ledger unreadable." },
    payload.sources[1],
  ];
  assert.deepEqual(mergeSources(priorFailed, priorPartial), priorFailed);
  assert.equal(continuationLabel(payload.entries), "Load older entries");
  assert.equal(activityTone("refused"), "danger");
  assert.equal(activityTone("confirmation-required"), "warning");
  assert.equal(activityTone("completed"), "success");
});

test("an exhausted empty query remains a true no-match state", () => {
  const exhausted = { ...payload, entries: [], next_cursor: "", next_command: "" };
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(ActivityTimeline, { payload: exhausted }))));
  assert.match(html, /No activity matches/);
  assert.doesNotMatch(html, /More rows remain/);
});

test("new activity requests abort and invalidate every older request", () => {
  const gate = new ActivityRequestGate();
  const first = gate.next();
  const second = gate.next();
  assert.equal(first.signal.aborted, true);
  assert.equal(gate.accepts(first.generation), false);
  assert.equal(gate.accepts(second.generation), true);
  gate.cancel(second.generation);
  assert.equal(second.signal.aborted, true);
  assert.equal(gate.accepts(second.generation), false);
  const third = gate.next();
  gate.invalidate();
  assert.equal(third.signal.aborted, true);
  assert.equal(gate.accepts(third.generation), false);
});

test("activity fetches through the authenticated CSRF-bound route", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ authenticated: true, csrf_token: "csrf-one" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(payload), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const controller = new AbortController();
    assert.deepEqual(await loadActivity(payload.filters, "v1:50", 10, controller.signal), payload);
    assert.equal(calls[0].init?.signal, controller.signal);
    assert.equal(calls[1].init?.signal, controller.signal);
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls[0].input, "/api/session");
  assert.equal(calls[1].input, "/api/activity");
  assert.equal(calls[1].init?.method, "POST");
  assert.deepEqual(calls[1].init?.headers, {
    "Content-Type": "application/json", "X-Studio-CSRF": "csrf-one",
  });
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    ...payload.filters, cursor: "v1:50", limit: 10,
  });
});
