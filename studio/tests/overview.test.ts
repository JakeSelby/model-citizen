import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { loadOverview } from "../src/overview/api.ts";
import { attentionCount, releaseSummary, systemSummary, type Overview } from "../src/overview/model.ts";
import { DeterministicOverview } from "../src/overview/OverviewPage.tsx";

const fixture: Overview = {
  schema_version: 1,
  generated_at: "2026-09-27T10:00:00Z",
  installed: { status: "current", version: "1.2.3" },
  release: { status: "update_available", version: "1.3.0", changelog_url: "https://example.invalid/changelog" },
  mode: { status: "current", value: "focused", source: "user" },
  doctor: { status: "current", message: null, checks: [
    { id: "doctor-1", status: "attention", message: "plugin missing", fix: "bin/harness install", fixes: ["bin/harness install", "/plugin install model-citizen@market"] },
    { id: "doctor-2", status: "informational", message: "identity: set", fix: null, fixes: [] },
  ] },
  drift: { status: "drift", message: null, items: ["missing link"], command: "citizen sync" },
  runs: { status: "current", message: null, items: [] },
  commands: { doctor: "citizen doctor", diff: "citizen diff", catalog: "citizen catalog", sync: "citizen sync" },
};

test("overview helpers keep doctor attention and drift separate", () => {
  assert.equal(attentionCount(fixture), 2);
  assert.equal(releaseSummary(fixture), "Version 1.3.0 is available");
  assert.equal(releaseSummary({ ...fixture, release: { status: "current", version: "1.2.3", changelog_url: null } }), "Installed release is current");
});

test("failed drift stays unknown instead of rendering a green healthy summary", () => {
  const failed: Overview = {
    ...fixture,
    doctor: { status: "current", message: null, checks: [] },
    drift: { status: "failed", message: "Projection drift is unavailable.", items: [], command: "citizen sync" },
  };
  assert.deepEqual(systemSummary(failed), { label: "Drift unknown", tone: "danger" });
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(DeterministicOverview, { overview: failed }))));
  assert.match(html, /Drift unknown/);
  assert.match(html, /Drift unavailable/);
  assert.doesNotMatch(html, /No detected drift/);
});

test("operational cards show exact fixes, governed sync, updates, and empty runs", () => {
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(DeterministicOverview, { overview: fixture }))));
  assert.match(html, /Version 1\.3\.0 is available/);
  assert.match(html, /Read changelog/);
  assert.match(html, /plugin missing/);
  assert.match(html, /bin\/harness install/);
  assert.match(html, /\/plugin install model-citizen@market/);
  assert.match(html, /Review sync before applying/);
  assert.match(html, /No runs yet/);
  assert.match(html, /<details class="doctor-repair"><summary>Show 2 repair commands/);
  assert.ok(html.indexOf("diagnostic-secondary") < html.indexOf("Projection drift"));
  assert.ok(html.indexOf("Projection drift") < html.indexOf("Recent runs"));
  assert.doesNotMatch(html, /Update now|Run sync/);
});

test("overview loads from the authenticated same-origin route", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    return new Response(JSON.stringify(fixture), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    assert.deepEqual(await loadOverview(), fixture);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [{ input: "/api/overview", init: { credentials: "same-origin" } }]);
});

test("attention checks precede collapsed informational diagnostics", () => {
  const overview = { ...fixture, doctor: { ...fixture.doctor, checks: [...fixture.doctor.checks].reverse() } };
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {},
    h(DeterministicOverview, { overview }))));
  assert.ok(html.indexOf("plugin missing") < html.indexOf("identity: set"));
  assert.match(html, /<details class="doctor-information"><summary>1 informational check<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*open/);
});

test("overview route errors are explicit", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({ error: "unavailable" }), {
    status: 503, headers: { "Content-Type": "application/json" },
  })) as typeof fetch;
  try {
    await assert.rejects(loadOverview(), /unavailable/);
  } finally {
    globalThis.fetch = original;
  }
});
