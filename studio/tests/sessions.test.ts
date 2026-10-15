import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ContextHistogram, ContextPerCall } from "../src/pages/sessions/ContextCharts.tsx";
import { SessionDetailPage, SessionDetailView, costliestTurns } from "../src/pages/sessions/SessionDetailPage.tsx";
import { SessionsPage, SessionsView, TopSessions } from "../src/pages/sessions/SessionsPage.tsx";
import {
  UnknownSession, band, headline, loadSession, loadSessions, parseDays, sessionHref, sessionQuery, sessionsQuery,
  tokens, topSessions, type SessionDetail, type SessionRow, type SessionsList,
} from "../src/pages/sessions/sessionsModel.ts";

// Hand-typed in the shape `GET /api/sessions?days=30` and `GET /api/sessions/session?id=busy`
// answer with; the figures follow `tests/test_studio_sessions.py`'s synthetic `busy` session.
const LINES = { warn: 160_000, limit: 400_000 };
const BASIS = "Observed in your transcripts: context is input plus cache read plus cache write per main-thread call. A reading of your own logs, not a measured effect.";

function row(session_id: string, extra: Partial<SessionRow> = {}): SessionRow {
  return {
    session_id, title: null, project: "-work-proj", repository: "proj", started: "2026-10-11T12:00:00Z",
    ended: "2026-10-11T12:07:00Z", models: ["claude-opus-5-5"], calls: 3, subagent_calls: 0, turns: 3,
    cost: 1.5, cost_complete: true, cost_per_turn: 0.5, max_context: 450_000, median_context: 200_000,
    over_warn: 2, over_limit: 1, compacts: 1, reseeds: 1, ...extra,
  };
}

const LIST: SessionsList = {
  window_days: 30, indexing: false, pending: 0, lines: LINES, basis: BASIS,
  summary: {
    sessions: 3, calls: 9, median_context: 256_000, over_warn: 7, over_limit: 2, cost: 2.75, unpriced_calls: 1,
    turns: 5, median_turn_cost: 0.4, mean_turn_cost: 0.55, compacts: 1, auto_compacts: 1, reseeds: 1,
  },
  histogram: Array.from({ length: 13 }, (_, i) => ({ from: i * 50_000, to: i < 12 ? (i + 1) * 50_000 : null, calls: [1, 0, 1, 0, 2, 0, 0, 0, 3, 0, 0, 0, 2][i] })),
  sessions: [
    row("aaaaaaaa-busy", { cost: 1.5, max_context: 450_000, calls: 3 }),
    row("bbbbbbbb-long", { cost: 1.25, max_context: 700_000, calls: 5, title: "Long one" }),
    row("cccccccc-free", { cost: null, cost_complete: false, cost_per_turn: null, max_context: 50_000, calls: 1, over_warn: 0, over_limit: 0 }),
  ],
  events: [
    { session_id: "aaaaaaaa-busy", ts: "2026-10-11T12:06:00Z", kind: "reseed", trigger: null, pre_tokens: null, post_tokens: null },
    { session_id: "aaaaaaaa-busy", ts: "2026-10-11T12:05:00Z", kind: "compact", trigger: "auto", pre_tokens: 210_000, post_tokens: 9_000 },
  ],
};

const DETAIL: SessionDetail = {
  session: row("aaaaaaaa-busy"), lines: LINES, basis: BASIS,
  calls: [
    { index: 0, ts: "2026-10-11T12:01:00Z", model: "claude-opus-5-5", turn: 0, context_tokens: 100_000, output: 50, cost: 0.4, event: null },
    { index: 1, ts: "2026-10-11T12:03:00Z", model: "claude-opus-5-5", turn: 1, context_tokens: 200_000, output: 50, cost: 0.5, event: null },
    { index: 2, ts: "2026-10-11T12:07:00Z", model: "claude-opus-5-5", turn: 2, context_tokens: 450_000, output: 50, cost: 0.6, event: "reseed" },
  ],
  turns: [
    { turn: 0, ts: "2026-10-11T12:00:00Z", calls: 1, cost: 0.4, cost_complete: true, max_context: 100_000 },
    { turn: 1, ts: "2026-10-11T12:02:00Z", calls: 1, cost: 0.5, cost_complete: true, max_context: 200_000 },
    { turn: 2, ts: "2026-10-11T12:04:00Z", calls: 1, cost: 0.6, cost_complete: true, max_context: 450_000 },
  ],
  events: LIST.events,
};

function wrap(node: ReturnType<typeof h>, path = "/", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

const view = (data: SessionsList, sort: "cost" | "peak" | "calls" = "cost") =>
  wrap(h(SessionsView, { data, days: 30, onDays: () => {}, sort, onSort: () => {} }));

const section = (html: string, label: string) => new RegExp(`aria-label="${label}"[\\s\\S]*?</section>`).exec(html)?.[0] ?? "";

test("the page shows cost per turn, the two lines and the top sessions by cost", () => {
  const html = view(LIST);
  assert.ok(html.includes("Your median call carried 256k tokens of context, above your 160k line."));
  const figures = section(html, "Session figures");
  assert.ok(figures.includes("Cost per turn") && figures.includes("$0.40") && figures.includes("mean $0.55 over 5 turns"));
  assert.ok(figures.includes("78%") && figures.includes("7 of 9"), "calls past 160k as a share and a count");
  assert.ok(figures.includes("1 unpriced"));
  const top = /aria-label="Top 3 of 3 sessions by cost"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  assert.deepEqual([...top.matchAll(/data-session="([^"]+)"/g)].map((m) => m[1]), ["aaaaaaaa-busy", "bbbbbbbb-long", "cccccccc-free"]);
  assert.match(top, /href="\/sessions\/aaaaaaaa-busy"/);
});

test("every figure is labelled an observation of the user's transcripts, never evidence", () => {
  const html = view(LIST);
  assert.ok(html.includes("Observed in your transcripts"));
  for (const label of ["Headline", "Context per call", "Resets", "Top sessions"]) {
    assert.ok(section(html, label).includes("Observed in your transcripts"), `${label} carries the label`);
  }
  const text = html.replace(/<[^>]+>/g, " ").replace(/not a measured effect/g, "");
  assert.doesNotMatch(text, /\bevidence\b|measured effect/i);
  const detail = wrap(h(SessionDetailView, { detail: DETAIL }));
  assert.ok(section(detail, "Context per call").includes("Observed in your transcripts"));
  assert.ok(section(detail, "Cost per turn").includes("Observed in your transcripts"));
});

test("figures are right-aligned, the table is a hairline panel and badges are outlined", () => {
  const html = view(LIST);
  const top = section(html, "Top sessions");
  assert.match(top, /class="harness-panel"/);
  assert.doesNotMatch(html, /mantine-Paper-root[^"]*"[^>]*data-with-border/);
  const heads = [...top.matchAll(/<th[^>]*class="([^"]*)"[^>]*>(Calls|Cost|Cost per turn|Peak context|Calls past 400k|Resets)<\/th>/g)];
  assert.equal(heads.length, 6);
  for (const [, cls] of heads) assert.match(cls, /harness-num/);
  assert.match(top, /data-variant="outline"[^>]*data-band="limit"|data-band="limit"[^>]*data-variant="outline"/);
});

test("the sessions can be ranked by peak context or calls", () => {
  assert.deepEqual(topSessions(LIST.sessions, "peak").map((r) => r.session_id), ["bbbbbbbb-long", "aaaaaaaa-busy", "cccccccc-free"]);
  assert.deepEqual(topSessions(LIST.sessions, "calls").map((r) => r.session_id), ["bbbbbbbb-long", "aaaaaaaa-busy", "cccccccc-free"]);
  assert.equal(topSessions(Array.from({ length: 9 }, (_, i) => row(`s${i}`)), "cost").length, 6);
  const html = wrap(h(TopSessions, { rows: LIST.sessions, lines: LINES, sort: "peak", onSort: () => {} }));
  assert.ok(html.includes("by peak context"));
});

test("the histogram colours each bucket by its side of the lines and draws both lines", () => {
  const html = wrap(h(ContextHistogram, { buckets: LIST.histogram, lines: LINES }));
  assert.match(html, /data-bucket="0" data-band="under"/);
  assert.match(html, /data-bucket="200000" data-band="warn"/);
  assert.match(html, /data-bucket="400000" data-band="limit"/);
  assert.match(html, /data-line="warn"/);
  assert.match(html, /data-line="limit"/);
  assert.match(html, /aria-label="Context per call: 9 calls in 50k buckets/);
});

test("a cold index shows a still-indexing state with what is ready", () => {
  const html = view({ ...LIST, indexing: true, pending: 12 });
  assert.match(html, /data-indexing[^>]*>Still indexing 12 transcripts/);
  assert.doesNotMatch(view(LIST), /Still indexing/);
});

test("an empty window says so rather than drawing an empty table", () => {
  const html = view({ ...LIST, sessions: [], events: [], summary: { ...LIST.summary, calls: 0, sessions: 0 } });
  assert.ok(html.includes("No sessions in this window") && html.includes("No resets in this window"));
  assert.ok(html.includes("No calls in this window yet."));
});

test("one session draws context per call against the lines with its reset marked", () => {
  const html = wrap(h(SessionDetailView, { detail: DETAIL }));
  const chart = wrap(h(ContextPerCall, { calls: DETAIL.calls, lines: LINES }));
  assert.deepEqual([...chart.matchAll(/data-call="(\d+)" data-band="(\w+)"/g)].map((m) => m[2]), ["under", "warn", "limit"]);
  assert.match(chart, /data-event="reseed" data-at="2"/);
  assert.match(chart, /data-line="warn"/);
  assert.match(chart, /data-line="limit"/);
  assert.match(html, /href="\/sessions"/, "the breadcrumb leads back to Sessions");
  const turns = /aria-label="Costliest 3 of 3 turns"[\s\S]*?<\/table>/.exec(html)?.[0] ?? "";
  assert.deepEqual([...turns.matchAll(/data-turn="(\d+)"/g)].map((m) => m[1]), ["2", "1", "0"]);
});

test("the costliest turns skip turns with no call and put unpriced ones last", () => {
  const turns = [...DETAIL.turns, { turn: 3, ts: null, calls: 0, cost: null, cost_complete: true, max_context: 0 },
    { turn: 4, ts: null, calls: 2, cost: null, cost_complete: false, max_context: 10 }];
  assert.deepEqual(costliestTurns(turns).map((t) => t.turn), [2, 1, 0, 4]);
});

test("helpers: bands, token text, the window and the headline", () => {
  assert.deepEqual([band(160_000, LINES), band(160_001, LINES), band(400_001, LINES)], ["under", "warn", "limit"]);
  assert.deepEqual([tokens(950), tokens(256_400), tokens(1_510_000_000)], ["950", "256k", "1510M"]);
  assert.equal(parseDays(new URLSearchParams("days=7")), 7);
  assert.equal(parseDays(new URLSearchParams("days=9")), 30);
  assert.equal(headline({ ...LIST.summary, median_context: 90_000 }, LINES),
    "Your median call carried 90k tokens of context, under your 160k line.");
  assert.equal(sessionHref("a/b"), "/sessions/a%2Fb");
});

test("the loaders call the two routes and name an unknown session", async () => {
  const original = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = (async (url: string) => {
    seen.push(url);
    return url.startsWith("/api/sessions?")
      ? new Response(JSON.stringify(LIST), { status: 200 })
      : new Response(JSON.stringify({ error: "unknown_session" }), { status: 404 });
  }) as typeof fetch;
  try {
    assert.equal((await loadSessions(7)).summary.calls, 9);
    await assert.rejects(loadSession("no such"), UnknownSession);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(seen, ["/api/sessions?days=7", "/api/sessions/session?id=no%20such"]);
  assert.equal(sessionQuery("x").retry(0, new UnknownSession("x")), false);
});

test("the list asks again while the index warms, and stops once it is done", () => {
  const { refetchInterval } = sessionsQuery(30);
  assert.equal(refetchInterval({ state: { data: { ...LIST, indexing: true } } }), 2_000);
  assert.equal(refetchInterval({ state: { data: LIST } }), false);
});

test("the routed pages read the window and the session named in the path", () => {
  const client = new QueryClient();
  client.setQueryData(sessionsQuery(7).queryKey, LIST);
  client.setQueryData(sessionQuery("aaaaaaaa-busy").queryKey, DETAIL);
  const list = wrap(h(Routes, {}, h(Route, { path: "/sessions", element: h(SessionsPage) })), "/sessions?days=7", client);
  assert.ok(list.includes("Top sessions"));
  const one = wrap(h(Routes, {}, h(Route, { path: "/sessions/:session", element: h(SessionDetailPage) })), "/sessions/aaaaaaaa-busy", client);
  assert.ok(one.includes("Cost per turn"));
});
