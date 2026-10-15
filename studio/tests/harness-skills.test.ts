import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Button, MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { periodsQuery } from "../src/harness/api.ts";
import { HarnessFrame } from "../src/harness/HarnessPage.tsx";
import type { DraftChange, HarnessTab, PeriodRow, PeriodsResponse } from "../src/harness/model.ts";
import { LIBRARY_QUERY_KEY } from "../src/pages/harness/librarySettings.ts";
import { OBSERVATIONAL, SKILLS_CONTENT, SkillsLead, SkillsPage, useColor } from "../src/pages/harness/SkillsPage.tsx";
import {
  INDEXING_POLL_MS, NEVER_INVOKED, SKILL_READ_ONLY_NOTE, indexingText, invokedText, leadFigures, loadLabel,
  loadSkillSummary, presentRows, skillSelectionChanges, skillSettings, skillSummaryQuery, summaryPoll,
  type SkillSummary,
} from "../src/pages/harness/skillsModel.ts";
import { module } from "./fixtures/harness-library.ts";
import type { LibraryPayload } from "../src/library/model.ts";

function row(item: string, use: string, extra: Partial<PeriodRow> = {}, metrics: PeriodRow["metrics"] = {}): PeriodRow {
  return {
    item_id: item, item_label: item, setting_label: "on",
    period: { start: "2026-09-20T10:00:00Z", end: null }, status: "measured", window_days: 7,
    metrics: { sessions: 34, loaded_in: 34, invoked_in: 0, invocations: 0, invoked_rate: 0, use, ...metrics },
    ...extra,
  };
}

// Hand-typed in the shape `GET /api/harness/periods?surface=skills` answers with.
const PERIODS: PeriodsResponse = {
  surface: "skills",
  rows: [
    row("sandbox", NEVER_INVOKED),
    row("plan-authoring", "invoked", {}, { invoked_in: 6, invocations: 9, invoked_rate: 0.176 }),
    row("design-loop", "invoked", { period: { start: "2026-09-26T10:00:00Z", end: "2026-10-10T10:00:00Z" } },
      { invoked_in: 1, invocations: 1, invoked_rate: 0.04 }),
    row("design-loop", NEVER_INVOKED, { setting_label: "off", status: "measuring", measuring_day: 3,
      period: { start: "2026-10-10T10:00:00Z", end: null } }, { sessions: 10, loaded_in: 0, invoked_rate: "not loaded", use: "not loaded" }),
  ],
};

// Hand-typed in the shape `GET /api/harness/skills` answers with.
const SUMMARY: SkillSummary = {
  window_days: 30, sessions: 34, indexing: false, pending: 0,
  basis: "Observational: read from your own Claude Code transcripts on this machine.",
  skills: [
    { skill: "plan-authoring", loaded_sessions: 34, invoked_sessions: 6, invocations: 9, use: "invoked" },
    { skill: "sandbox", loaded_sessions: 34, invoked_sessions: 0, invocations: 0, use: NEVER_INVOKED },
    { skill: "licensing-review", loaded_sessions: 34, invoked_sessions: 0, invocations: 0, use: NEVER_INVOKED },
    { skill: "personal", loaded_sessions: 0, invoked_sessions: 2, invocations: 2, use: "invoked" },
  ],
};

const LIBRARY: LibraryPayload = {
  schema_version: 1, repository: "/repo",
  summary: { modules: 4, collisions: 0, roots: 1, generated_ms: 1 },
  modules: [
    { ...module("skills", "sandbox", "---\nname: sandbox\ndescription: Fence an unattended loop. More.\n---\n", ""),
      context_cost: { tokens: 66, estimate: "soft estimate", method: "chars/4" } },
    module("skills", "plan-authoring", "---\nname: plan-authoring\ndescription: \"Review Card contract\"\n---\n", "", "off", "user"),
    { ...module("skills", "locked", "---\nname: locked\n---\n", ""), state: { value: "on", layer: "not switchable", switchable: false } },
    module("roles", "builder", "---\nname: builder\n---\n", ""),
  ],
};

function render(node: ReactNode, path: string, client: QueryClient): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function client(summary: SkillSummary = SUMMARY): QueryClient {
  const value = new QueryClient();
  value.setQueryData(periodsQuery("skills").queryKey, PERIODS);
  value.setQueryData(LIBRARY_QUERY_KEY, LIBRARY);
  value.setQueryData(skillSummaryQuery.queryKey, summary);
  return value;
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

test("the Skills page shows each skill's loaded and invoked sessions per period", () => {
  const html = render(h(SkillsPage), "/harness/skills", client());
  assert.match(html, /<h1[^>]*>Skills<\/h1>/);
  assert.ok(html.includes("Skills: efficacy by setting period"));
  for (const heading of ["Sessions", "Loaded in", "Invoked in", "Invocations", "Invoked when loaded", "Use"]) {
    assert.ok(html.includes(`>${heading}<`), heading);
  }
  const plan = /<tbody[^>]*data-item="plan-authoring"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  for (const value of ["Always", "6 of 34", "17.6%", ">invoked<", "since Sep 20"]) assert.ok(plan.includes(value), value);
  const sandbox = /<tbody[^>]*data-item="sandbox"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(sandbox.includes(NEVER_INVOKED), "a skill loaded and never invoked is marked so");
  const design = /<tbody[^>]*data-item="design-loop"[^>]*>([\s\S]*?)<\/tbody>/.exec(html)?.[1] ?? "";
  assert.ok(design.includes("Off") && design.includes("Before") && design.includes("measuring, day 3 of 7"));
  assert.ok(html.indexOf('data-item="plan-authoring"') < html.indexOf('data-item="sandbox"'),
    "invoked skills come before those never invoked");
  assert.ok(html.includes(OBSERVATIONAL), "the figures are labelled observational");
});

test("rows are ordered by use and the switch reads as Always or Off", () => {
  const ordered = presentRows(PERIODS.rows);
  assert.deepEqual([...new Set(ordered.map((r) => r.item_id))], ["plan-authoring", "sandbox", "design-loop"],
    "design-loop ranks by its newest period, not loaded since it was switched off");
  assert.deepEqual(ordered.map((r) => r.setting_label), ["Always", "Always", "Always", "Off"]);
  assert.equal(PERIODS.rows[0].setting_label, "on", "the API's rows are not changed");
  assert.equal(loadLabel("default"), "Always");
  assert.equal(loadLabel("something"), "something");
  assert.equal(invokedText(row("x", "invoked", {}, { invoked_in: 2, loaded_in: 0 })), "2");
  assert.equal(invokedText(row("x", "invoked", {}, { invoked_in: "?" })), "not measured");
  assert.deepEqual([useColor("invoked"), useColor(NEVER_INVOKED), useColor("not loaded")], ["green", "yellow", "gray"]);
});

test("the lead names the skills loaded and never invoked, with the basis", () => {
  const html = render(h(SkillsLead, { summary: SUMMARY }), "/harness/skills", client());
  assert.ok(html.includes("Sessions read, last 30 days"));
  assert.ok(html.includes("1 of 3 loaded"), "invoked counts only skills that were loaded");
  assert.ok(html.includes("invoked in no session: sandbox, licensing-review."));
  assert.ok(html.includes(SUMMARY.basis));
  assert.ok(!html.includes("Still indexing"));
  assert.deepEqual(leadFigures(SUMMARY).never.map((r) => r.skill), ["sandbox", "licensing-review"]);
  assert.ok(!render(h(SkillsLead, { summary: undefined }), "/", client()).includes("Sessions read"),
    "no lead before the summary arrives");
});

test("while the index warms the lead says so and the summary polls", () => {
  const warming = { ...SUMMARY, indexing: true, pending: 12 };
  const html = render(h(SkillsLead, { summary: warming }), "/harness/skills", client());
  assert.ok(html.includes("Still indexing"));
  assert.ok(html.includes("12 transcripts not read yet"));
  assert.match(indexingText({ ...warming, pending: 1 }), /1 transcript not read yet/);
  assert.match(indexingText({ ...warming, pending: 0 }), /may be short/);
  assert.equal(indexingText(SUMMARY), "");
  assert.equal(summaryPoll(warming), INDEXING_POLL_MS);
  assert.equal(summaryPoll(SUMMARY), false);
  assert.equal(summaryPoll(undefined), false);
  const page = render(h(SkillsPage), "/harness/skills", client(warming));
  assert.ok(page.includes("Still indexing"), "the page shows the index state above the table");
});

test("loading the summary reports a refusal by its error and a malformed body", async () => {
  const original = globalThis.fetch;
  const answers: Array<[number, unknown]> = [[401, { error: "unauthorized" }], [200, { window_days: 30 }], [200, SUMMARY]];
  const urls: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request) => {
    urls.push(String(input));
    const [status, body] = answers.shift()!;
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await assert.rejects(loadSkillSummary(), /unauthorized/);
    await assert.rejects(loadSkillSummary(), /no skills/);
    assert.deepEqual(await loadSkillSummary(), SUMMARY);
    assert.deepEqual(urls, ["/api/harness/skills", "/api/harness/skills", "/api/harness/skills"]);
  } finally {
    globalThis.fetch = original;
  }
});

test("Settings lists each skill's load switch, source and description tokens", () => {
  const rows = skillSettings(LIBRARY);
  assert.deepEqual(rows.map((r) => r.item_id), ["locked", "plan-authoring", "sandbox"]);
  const [locked, plan, sandbox] = rows;
  assert.deepEqual(sandbox.values, { load: "Always", source: "Core", tokens: "~66" });
  assert.deepEqual(sandbox.options, { load: ["Always", "Off"] });
  assert.equal(sandbox.item_detail, "Fence an unattended loop");
  assert.equal(plan.values.load, "Off");
  assert.equal(plan.item_detail, "Review Card contract");
  assert.match(plan.last_change_note ?? "", /^set by user/);
  assert.equal(locked.options, undefined, "a skill the selection cannot switch has no control");

  const html = render(h(SkillsPage), "/harness/skills?tab=settings", client());
  assert.ok(html.includes("Skills: settings"));
  assert.ok(html.includes(">Load<") && html.includes(">Description tokens<"));
  assert.equal(html.split(SKILL_READ_ONLY_NOTE).length - 1, 1, "the read-only rule is stated once");
  assert.ok(html.includes('aria-label="Load: sandbox"'), "the switch is a control");
  assert.ok(!html.includes('aria-label="Source: sandbox"'), "the source has no editing route");
});

test("saving a load change hands onDraft a skills selection change", () => {
  const rows = skillSettings(LIBRARY);
  const drafts: DraftChange[][] = [];
  const tree = HarnessFrame({
    ...SKILLS_CONTENT, tab: "settings" as HarnessTab, onTabChange: () => undefined,
    periods: { state: "ready", rows: PERIODS.rows },
    settings: { state: "ready", rows },
    edits: { sandbox: { load: "Off" } },
    onEdit: () => undefined, onDiscard: () => undefined, onDraft: (changes) => drafts.push(changes),
  });
  const panel = elements(tree).find((element) => typeof element.props.onDraft === "function" && element.props.rows === rows);
  assert.ok(panel, "the settings table receives the page's rows");
  const table = (panel.type as (props: Record<string, unknown>) => ReactNode)(panel.props);
  const send = elements(table).find((element) => element.type === Button && text(element) === "Send to Changes");
  assert.ok(send);
  (send.props.onClick as () => void)();
  const expected: DraftChange[] = [{ item_id: "sandbox", field: "load", from: "Always", to: "Off" }];
  assert.deepEqual(drafts, [expected]);
  assert.deepEqual(skillSelectionChanges(expected), { "skills.sandbox": "off" });
  assert.deepEqual(skillSelectionChanges([{ item_id: "plan-authoring", field: "load", from: "Off", to: "Always" }]),
    { "skills.plan-authoring": "on" });
  assert.deepEqual(skillSelectionChanges([
    { item_id: "sandbox", field: "tokens", from: "~66", to: "~1" },
    { item_id: "sandbox", field: "load", from: "Always", to: "On demand" },
  ]), {}, "a read-only field or an unknown load never becomes a selection change");
});
