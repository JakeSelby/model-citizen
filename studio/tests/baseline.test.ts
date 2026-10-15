import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement as h, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { loadBaseline, recordBaseline } from "../src/baseline/api.ts";
import {
  BaselinePage, BaselineView, CurrentSetup, FirstRunLink, RecordForm, RecordedBaselines,
} from "../src/baseline/BaselinePage.tsx";
import {
  FIRST_RUN_HREF, baselineSummary, labelProblem, recordedAt, settingText, showsFirstRun, sortedEntries,
  type BaselineResponse,
} from "../src/baseline/model.ts";
import { pageCommand } from "../src/navigation.ts";

// Hand-typed in the shape `GET /api/baseline` (`citizen baseline show --json`) answers with,
// values taken from `tests/test_studio_baseline.py`'s fixture; no generator exists.
const DATA: BaselineResponse = {
  schema_version: 1,
  current: {
    config_present: true,
    config_path: "/fixture/.config/agent-harness/config.json",
    stances: { voice: "concise", cost: "balanced" },
    rules: { "cache-hygiene": "off" },
    settings: [
      { path: "identity.name", section: "identity", label: "Name", value: "Casey Example" },
      { path: "telemetry.headers_env", section: "telemetry", label: "Header environment reference", value: { source: "environment", name: "OTEL" } },
    ],
    plugins: [{ id: "third-party:superpowers@market", label: "superpowers", path: "/fixture/.claude/plugins/cache/sp", modules: 17 }],
  },
  baselines: [
    { id: "b2", ts: "2026-10-14T09:30:00Z", label: "after trim", stances: { voice: "concise" }, rules: {}, plugins: ["superpowers"] },
    { id: "b1", ts: "2026-10-01T08:00:00Z", label: "", stances: { voice: "answer-card", cost: "balanced" }, rules: {}, plugins: [] },
  ],
  commands: { show: "citizen baseline show --json", record: "citizen baseline record --label {label} --json" },
};

function wrap(node: ReactElement, path = "/baseline", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function table(html: string, label: string): string {
  return new RegExp(`aria-label="${label}"[\\s\\S]*?</table>`).exec(html)?.[0] ?? "";
}

test("the view shows stances, rule switches, plugins, settings and the recorded baselines", () => {
  const html = wrap(h(BaselineView, { data: DATA, label: "", onLabel: () => {}, onRecord: () => {} }));
  assert.ok(html.includes("Start from what you have"));
  assert.deepEqual([...table(html, "Stances in effect").matchAll(/data-stance="([^"]+)"/g)].map((m) => m[1]), ["cost", "voice"]);
  assert.match(table(html, "Rule switches"), /data-rule="cache-hygiene"/);
  const plugins = table(html, "Enabled plugins");
  assert.ok(plugins.includes("superpowers") && plugins.includes("17"));
  const settings = table(html, "Settings");
  assert.ok(settings.includes("Casey Example") && settings.includes("environment: OTEL"));
  const recorded = table(html, "Recorded baselines, newest first");
  assert.deepEqual([...recorded.matchAll(/data-baseline="([^"]+)"/g)].map((m) => m[1]), ["b2", "b1"]);
  assert.ok(recorded.includes("latest") && recorded.includes("unlabelled"));
  assert.match(recorded, /href="\/evals"/);
  assert.ok(html.includes("Record baseline"));
  assert.ok(html.includes("citizen baseline record --label {label} --json"));
});

test("the guided first run stays one link away, and leads when there is no config", () => {
  const html = wrap(h(BaselineView, { data: DATA, label: "", onLabel: () => {}, onRecord: () => {} }));
  assert.ok(html.includes(`href="${FIRST_RUN_HREF}"`));
  const fresh = { ...DATA, current: { ...DATA.current, config_present: false, stances: {}, rules: {}, settings: [], plugins: [] }, baselines: [] };
  const empty = wrap(h(BaselineView, { data: fresh, label: "", onLabel: () => {}, onRecord: () => {} }));
  assert.ok(empty.indexOf("Set up from scratch") < empty.indexOf("What you already run"), "the guide leads a home with no config");
  assert.ok(empty.includes("No stances chosen") && empty.includes("No third-party plugins enabled") && empty.includes("No baseline recorded yet"));
  assert.ok(wrap(h(FirstRunLink, { configPresent: true })).includes("Starting over"));
});

test("the record form refuses a bad label before sending and shows a failure", () => {
  const long = wrap(h(RecordForm, { label: "x".repeat(81), onLabel: () => {}, onRecord: () => {}, command: "c" }));
  assert.ok(long.includes("Keep the label to 80 characters."));
  assert.match(long, /<button[^>]*disabled[^>]*>/);
  const failed = wrap(h(RecordForm, { label: "ok", onLabel: () => {}, onRecord: () => {}, error: "invalid_request", command: "c" }));
  assert.ok(failed.includes("The baseline was not recorded") && failed.includes("invalid_request"));
});

test("the recorded table and current setup render on their own", () => {
  assert.ok(wrap(h(RecordedBaselines, { baselines: [] })).includes("No baseline recorded yet"));
  assert.ok(wrap(h(CurrentSetup, { current: DATA.current })).includes(DATA.current.config_path));
});

test("the page opens the guided first run under ?guide=first-run with a way back", () => {
  const html = wrap(h(Routes, {}, h(Route, { path: "/baseline", element: h(BaselinePage) })), FIRST_RUN_HREF);
  assert.match(html, /aria-label="Breadcrumb"[\s\S]*href="\/baseline"/);
  assert.ok(html.includes("Guided first run"));
  const loading = wrap(h(Routes, {}, h(Route, { path: "/baseline", element: h(BaselinePage) })));
  assert.ok(loading.includes("Reading what you have"));
});

test("model helpers", () => {
  assert.equal(showsFirstRun("?guide=first-run"), true);
  assert.equal(showsFirstRun(""), false);
  assert.equal(labelProblem(""), "");
  assert.match(labelProblem("a\u0001"), /control/);
  assert.equal(settingText(["a", "b"]), "a, b");
  assert.equal(settingText({ configured: true }), "set (hidden)");
  assert.equal(settingText(false), "false");
  assert.equal(settingText(null), "");
  assert.deepEqual(sortedEntries({ b: "2", a: "1" }), [["a", "1"], ["b", "2"]]);
  assert.equal(baselineSummary(DATA.baselines[0]), "1 stance · 1 plugin");
  assert.equal(baselineSummary(DATA.baselines[1]), "2 stances · 0 plugins");
  assert.equal(recordedAt("2026-10-14T09:30:00Z"), "2026-10-14 09:30 UTC");
  assert.equal(pageCommand("/baseline"), "citizen baseline show");
});

test("the client reads with GET and records with the session's CSRF token", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; method: string; csrf?: string; body?: unknown }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "c" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ input: path, method: init?.method ?? "GET", csrf: headers["X-Studio-CSRF"],
      body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/api/baseline/record") {
      const label = (JSON.parse(String(init?.body)) as { label: string }).label;
      return label === "bad"
        ? new Response(JSON.stringify({ error: "invalid_request" }), { status: 400, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ schema_version: 1, baseline: DATA.baselines[0] }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(DATA), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    assert.equal((await loadBaseline()).baselines.length, 2);
    assert.equal((await recordBaseline("after trim")).baseline.id, "b2");
    await assert.rejects(recordBaseline("bad"), /invalid_request/);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [
    { input: "/api/baseline", method: "GET", csrf: undefined, body: undefined },
    { input: "/api/baseline/record", method: "POST", csrf: "c", body: { label: "after trim" } },
    { input: "/api/baseline/record", method: "POST", csrf: "c", body: { label: "bad" } },
  ]);
});
