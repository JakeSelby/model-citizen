import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ToastProvider } from "../src/components/StudioKit.tsx";
import { evalsQueries } from "../src/evals/api.ts";
import {
  AutoTrimView, BeforeAfterView, EvalsPage, EvalsView, PreregisteredTable, QualificationTable, type EvalsViewProps,
} from "../src/evals/EvalsPage.tsx";
import {
  OBSERVATIONAL_BADGE, changeText, filterBoundaries, parseEvalsTab, registeredResults, resultText,
  type AutoTrim, type BeforeAfter, type Preregistrations, type Qualification,
} from "../src/evals/model.ts";
import type { Line, Point } from "../src/reports/trends/model.ts";

// Hand-typed in the shapes `citizen evals <view> --json` printed on a fixture home and this
// checkout (`lib/harness_core/studio/evals.py`); no generator exists.
const NOTE = "Observational: read from this machine's own sessions, not from a controlled experiment. A check that cannot meet the experiment protocol is labelled exploratory and supports no claim.";
const LABEL = { label: "observational", standard_label: "exploratory", note: NOTE } as const;

const PREREGISTRATIONS: Preregistrations = {
  schema_version: 1, directory: "benchmarks/preregistrations",
  items: [
    { path: "benchmarks/preregistrations/2026-10-01-power-pilot.md", file: "2026-10-01-power-pilot.md", title: "Power pilot", date: "2026-10-01", status: null, question: "How many task runs a cost claim needs", deviations: 1, label: "pre-registered" },
    { path: "benchmarks/preregistrations/2026-10-01-micro-mechanisms.md", file: "2026-10-01-micro-mechanisms.md", title: "Micro-mechanism campaign", date: "2026-10-01", status: "running", question: null, deviations: 0, label: "pre-registered" },
  ],
};

function point(date: string, label: "pre-registered" | "exploratory", registration: string | null): Point {
  return {
    run_id: date, date, harness_version: "0.15.0", tag: null, harness_sha: null, model: null, cli_version: null,
    series: "s", bucket: "", change_note: "", status: null, cache_basis: null, reps: 5,
    evidence: { label, reason: "", pre_registration: registration }, measures: {},
    sm2: { verdict: null, reason: null, claim: null, limitation: null, ratio_undefined: null, eligible: null, confidence: null, unavailable: null, text: null },
    delegation: null, source: { path: null, line: null },
  };
}
const LINES: Line[] = [{
  id: "s", series: "s", bucket: "", evidence: "mixed", evidence_note: "",
  points: [
    point("2026-10-03", "pre-registered", "benchmarks/preregistrations/2026-10-01-power-pilot.md"),
    point("2026-10-05", "pre-registered", "2026-10-01-power-pilot.md"),
    point("2026-10-06", "exploratory", "benchmarks/preregistrations/2026-10-01-power-pilot.md"),
  ],
}];

const QUALIFICATION: Qualification = {
  schema_version: 1, state: "ready", reason: null, harness_version: "0.15.0", qualification_source: "drifted",
  required_cases: ["install"], limitations: ["Codex is not qualified in this release."],
  clients: [
    { id: "claude-code-cli-macos", runtime: "claude-code", client: "cli", platform: "macos", status: "qualified", required_for_release: true, runtime_version: "2.1.283", client_version: "2.1.283", records: [{ path: "compatibility/evidence/claude-code-cli-macos-0.15.0.json", sha256: "ab" }] },
    { id: "codex-cli-macos", runtime: "codex", client: "cli", platform: "macos", status: "unqualified", required_for_release: false, runtime_version: null, client_version: null, records: [] },
  ],
};

const SIDE = { setting: "answer-card", period: { start: "2026-09-22T12:00:00Z", end: "2026-10-02T12:00:00Z" }, status: "measured" as const, metrics: { sessions: 3, cache_read_share: 0.9 } };
const BEFORE_AFTER: BeforeAfter = {
  ...LABEL, schema_version: 1, days: 30, window_days: 7, surfaces: { stances: "ok", rules: "error" },
  boundaries: [
    { id: "stances:voice:2026-10-02T12:00:00Z", surface: "stances", item_id: "voice", item_label: "Voice", at: "2026-10-02T12:00:00Z", source: "baseline", from: "answer-card", to: "concise",
      before: SIDE, after: { ...SIDE, setting: "concise", period: { start: "2026-10-02T12:00:00Z", end: null }, metrics: { sessions: 4, cache_read_share: 0.95 } },
      changes: [{ key: "cache_read_share", before: 0.9, after: 0.95, delta: 0.05 }, { key: "sessions", before: 3, after: 4, delta: 1 }],
      href: "/evals?tab=before-after&surface=stances&item=voice" },
    { id: "hooks:grade-bash:2026-10-01T00:00:00Z", surface: "hooks", item_id: "grade-bash", item_label: "Command grader", at: "2026-10-01T00:00:00Z", source: "applies", from: "advise", to: "enforce",
      before: SIDE, after: { ...SIDE, status: "measuring", measuring_day: 3 }, changes: [], href: "/evals?tab=before-after&surface=hooks&item=grade-bash" },
  ],
};

const SUMMARY = { calls: 3, median_context: 200_000, mean_context: 200_000, over_warn_share: 0.667 };
const AUTO_TRIM: AutoTrim = {
  ...LABEL, schema_version: 1, days: 30, context_warn: 160_000, state: "ready", reason: null, indexing: false, pending: 0,
  sessions_with_reset: 1, sessions_without_reset: 2, before: SUMMARY,
  after: { calls: 2, median_context: 50_000, mean_context: 50_000, over_warn_share: 0 }, median_change: -150_000,
  sessions: [{ session_id: "s1", reset_at: "2026-10-10T00:00:03Z", before: SUMMARY, after: SUMMARY }],
};

function wrap(node: ReturnType<typeof h>, path = "/", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(ToastProvider, {}, h(MemoryRouter, { initialEntries: [path] }, node)))));
}

/** Visible text: tags, attributes and styles removed. */
function text(html: string): string {
  return html.replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, "").replace(/<[^>]+>/g, " ")
    .replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/\s+/g, " ");
}

function section(html: string, label: string): string {
  return new RegExp(`<section aria-label="${label}"[\\s\\S]*?</section>`).exec(html)?.[0] ?? "";
}

const VIEW: EvalsViewProps = {
  tab: "experiments", onTab: () => undefined,
  preregistrations: { state: "ready", data: PREREGISTRATIONS }, lines: LINES,
  qualification: { state: "ready", data: QUALIFICATION },
  beforeAfter: { state: "ready", data: BEFORE_AFTER }, autoTrim: { state: "ready", data: AUTO_TRIM },
  experiments: h("p", { className: "experiments-stand-in" }, "experiments panel"),
};

test("pre-registrations list each plan and only the results the engine labelled pre-registered", () => {
  const html = wrap(h(PreregisteredTable, { data: PREREGISTRATIONS, lines: LINES }));
  assert.deepEqual([...html.matchAll(/data-preregistration="([^"]+)"/g)].map((m) => m[1]),
    ["2026-10-01-power-pilot.md", "2026-10-01-micro-mechanisms.md"]);
  assert.match(html, /data-results="2"/);
  assert.ok(text(html).includes("2 runs, latest 2026-10-05"));
  assert.ok(text(html).includes("running") && text(html).includes("Registered"));
  assert.ok(text(html).includes("1 logged deviation"));
  assert.match(html, /mantine-Badge-root[^"]*"[^>]*data-variant="outline"|data-variant="outline"/);
  assert.doesNotMatch(html, /mantine-Paper-root/, "tables sit on hairlines, not a bordered Paper");
  assert.doesNotMatch(html, /<caption/, "the Experiments panel's results table keeps the tab's first caption");
  assert.match(html, /<table[^>]*aria-label="Pre-registered experiments"/);
});

test("registered results match by path or file name, newest first, never an exploratory point", () => {
  const found = registeredResults(PREREGISTRATIONS.items[0], LINES);
  assert.deepEqual(found.map((p) => p.date), ["2026-10-05", "2026-10-03"]);
  assert.equal(registeredResults(PREREGISTRATIONS.items[1], LINES).length, 0);
  assert.equal(resultText([]), "No result yet");
});

test("runtime qualification lists each client, its status and its records", () => {
  const html = wrap(h(QualificationTable, { data: QUALIFICATION }));
  assert.match(html, /data-client="claude-code-cli-macos" data-status="qualified"/);
  assert.ok(html.includes("compatibility/evidence/claude-code-cli-macos-0.15.0.json"));
  assert.ok(text(html).includes("qualify it again"), "a drifted source says so");
  assert.ok(text(html).includes("Codex is not qualified in this release."));
  const refused = wrap(h(QualificationTable, { data: { ...QUALIFICATION, state: "unavailable", reason: "evidence digest mismatch", clients: [] } }));
  assert.ok(text(refused).includes("evidence digest mismatch"));
});

test("before and after carries the observational label and is never called evidence", () => {
  const html = wrap(h(BeforeAfterView, { data: BEFORE_AFTER }));
  assert.match(html, /data-label="observational"/);
  assert.ok(text(html).includes(OBSERVATIONAL_BADGE));
  assert.ok(text(html).includes(NOTE));
  assert.doesNotMatch(text(html), /evidence/i);
  assert.deepEqual([...html.matchAll(/data-source="([^"]+)"/g)].map((m) => m[1]), ["baseline", "applies"],
    "any boundary source is shown as named");
  assert.ok(text(html).includes("answer-card → concise"));
  assert.ok(text(html).includes("+5.0 pts") && text(html).includes("+1"));
  assert.ok(text(html).includes("measuring day 3 of 7"));
  assert.ok(text(html).includes("Could not read: rules."));
});

test("a deep link narrows before and after to one item", () => {
  const html = wrap(h(BeforeAfterView, { data: BEFORE_AFTER, surface: "stances", item: "voice" }));
  assert.deepEqual([...html.matchAll(/data-boundary="([^"]+)"/g)].map((m) => m[1]), ["stances:voice:2026-10-02T12:00:00Z"]);
  assert.match(html, /href="\/evals\?tab=before-after"/);
  assert.equal(filterBoundaries(BEFORE_AFTER.boundaries, null, "grade-bash").length, 1);
  const none = wrap(h(BeforeAfterView, { data: { ...BEFORE_AFTER, boundaries: [] } }));
  assert.ok(text(none).includes("no before and after yet"));
});

test("the auto-trim reading carries the observational label and is never called evidence", () => {
  const html = wrap(h(AutoTrimView, { data: AUTO_TRIM }));
  assert.match(html, /data-label="observational"/);
  assert.ok(text(html).includes(OBSERVATIONAL_BADGE) && text(html).includes(NOTE));
  assert.doesNotMatch(text(html), /evidence/i);
  assert.ok(text(html).includes("200k") && text(html).includes("50k") && text(html).includes("67%"));
  assert.ok(text(html).includes("Calls over 160k"));
  const unavailable = wrap(h(AutoTrimView, { data: { ...AUTO_TRIM, state: "unavailable", reason: "The transcript index is not installed in this Studio yet." } }));
  assert.match(unavailable, /data-state="unavailable"/);
  assert.ok(text(unavailable).includes("transcript index is not installed"));
  assert.ok(text(unavailable).includes(OBSERVATIONAL_BADGE));
  assert.doesNotMatch(text(unavailable), /evidence/i);
});

test("a still-indexing auto-trim reading says so in a status and keeps the observational label", () => {
  const reason = "Still indexing 4 transcripts; this reading covers the ones read so far.";
  const cold = wrap(h(AutoTrimView, { data: { ...AUTO_TRIM, state: "indexing", indexing: true, pending: 4, reason,
    sessions_with_reset: 0, sessions: [], median_change: null } }));
  assert.match(cold, /data-state="indexing"/);
  assert.match(cold, /role="status"[^>]*data-indexing="true"[^>]*>Still indexing 4 transcripts/);
  assert.ok(text(cold).includes(OBSERVATIONAL_BADGE) && text(cold).includes(NOTE));
  assert.ok(!text(cold).includes("Before the reset"), "no table before a reset session is read");
  assert.doesNotMatch(text(cold), /evidence/i);
  const partial = wrap(h(AutoTrimView, { data: { ...AUTO_TRIM, indexing: true, pending: 1,
    reason: "Still indexing 1 transcript; this reading covers the ones read so far." } }));
  assert.match(partial, /data-state="ready"/);
  assert.match(partial, /role="status"[^>]*data-indexing="true"[^>]*>Still indexing 1 transcript;/);
  assert.ok(text(partial).includes("Before the reset") && text(partial).includes(OBSERVATIONAL_BADGE));
  assert.doesNotMatch(text(partial), /evidence/i);
  assert.doesNotMatch(wrap(h(AutoTrimView, { data: AUTO_TRIM })), /data-indexing/);
});

test("the page has one level-one heading and keeps pre-registered results apart from observational ones", () => {
  const html = wrap(h(EvalsView, VIEW));
  assert.equal([...html.matchAll(/<h1\b/g)].length, 1);
  assert.match(html, /aria-label="Evals views"/);
  const registered = section(html, "Pre-registered experiments");
  assert.match(registered, /data-label="pre-registered"/);
  assert.doesNotMatch(registered, /observational/i);
  assert.ok(html.includes("experiments panel"), "the Experiments panel sits under the Experiments tab");
  assert.doesNotMatch(html, /data-label="observational"/, "observational readings live on their own tab");
  const after = wrap(h(EvalsView, { ...VIEW, tab: "before-after" }));
  assert.deepEqual([...after.matchAll(/<section aria-label="([^"]+)" data-label="observational"/g)].map((m) => m[1]),
    ["Before and after each setting change", "Auto-trim reading"]);
  assert.doesNotMatch(after, /data-label="pre-registered"/);
  assert.doesNotMatch(text(after), /evidence/i);
  assert.match(html, /href="\/evals\/trends"/);
});

test("a loading or failed reading says so in its own place", () => {
  const html = wrap(h(EvalsView, { ...VIEW, tab: "qualification", qualification: { state: "error", message: "boom" } }));
  assert.ok(text(html).includes("could not be read: boom"));
  const loading = wrap(h(EvalsView, { ...VIEW, preregistrations: { state: "loading" } }));
  assert.ok(text(loading).includes("Reading Pre-registrations"));
});

test("the tab follows the URL, and a Harness page's item link opens Before and after", () => {
  assert.equal(parseEvalsTab(""), "experiments");
  assert.equal(parseEvalsTab("?tab=qualification"), "qualification");
  assert.equal(parseEvalsTab("?surface=stances&item=voice"), "before-after");
  assert.equal(parseEvalsTab("?tab=nonsense"), "experiments");
  assert.equal(changeText({ key: "sessions", before: 1, after: 1, delta: null }), "–");
  assert.equal(changeText({ key: "output_tokens", before: 1, after: 0, delta: -1200 }), "-1,200");
});

test("the routed page opens on the tab and item its URL names", () => {
  const client = new QueryClient();
  client.setQueryData(evalsQueries.preregistrations().queryKey, PREREGISTRATIONS);
  client.setQueryData(evalsQueries.beforeAfter().queryKey, BEFORE_AFTER);
  client.setQueryData(evalsQueries.autoTrim().queryKey, AUTO_TRIM);
  const html = wrap(h(Routes, {}, h(Route, { path: "/evals", element: h(EvalsPage) })), "/evals?surface=stances&item=voice", client);
  assert.match(html, /aria-selected="true"[^>]*>[\s\S]*?Before and after|Before and after[\s\S]*?aria-selected="true"/);
  assert.deepEqual([...html.matchAll(/data-boundary="([^"]+)"/g)].map((m) => m[1]), ["stances:voice:2026-10-02T12:00:00Z"]);
});
