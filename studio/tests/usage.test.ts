import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { loadSpend } from "../src/spend/api.ts";
import { formatUsd, moneyLabel, PAGE_SIZE, pageOf, partialNotes, pricingDate, type SpendReport, type UsageLedger } from "../src/spend/model.ts";
import { SpendReportView } from "../src/spend/SpendPage.tsx";

// Written by `python3 tests/test_studio_spend.py --write` from the CLI's own fixture ledger.
const fixture = JSON.parse(readFileSync(new URL("./fixtures/spend.json", import.meta.url), "utf8")) as Record<"session" | "role", SpendReport>;
const session = fixture.session;
const label = moneyLabel(session.basis);

function render(report: SpendReport): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, h(SpendReportView, { report }))));
}

function escaped(text: string): RegExp {
  return new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
}

test("money keeps unpriced distinct from zero and names its price snapshot", () => {
  assert.equal(formatUsd(null), "unpriced");
  assert.equal(formatUsd(0), "$0.00");
  assert.equal(formatUsd(0.1234567), "$0.1235");
  assert.equal(formatUsd(12.5), "$12.50");
  assert.match(label, /^list-price equivalent, prices as of \d{4}-\d{2}-\d{2}$/);
  assert.equal(moneyLabel({ ...session.basis, price_as_of: "unknown" }), "list-price equivalent, pricing date unknown");
  assert.equal(pricingDate({ ...session.basis, price_as_of: null }), "no figure priced from the price table");
  assert.equal(moneyLabel({ ...session.basis, label: "unknown basis" }), `unknown basis, prices as of ${session.basis.price_as_of}`);
});

test("a year of sessions renders one page of groups plus the ledger's total row", () => {
  const ledger = session.ledger as UsageLedger;
  const groups = Array.from({ length: 7300 }, (_, index) => ({ ...ledger.groups[0], name: `s-${index}` }));
  const big: SpendReport = { ...session, ledger: { ...ledger, groups } };
  const html = render(big);
  assert.equal(html.match(/<tr>/g)?.length, PAGE_SIZE + 2);
  assert.match(html, /s-49</);
  assert.doesNotMatch(html, /s-50</);
  assert.match(html, /Page 1 of 146 · 7,300 groups/);
  assert.match(html, /<strong>Total<\/strong>/);
  assert.deepEqual(pageOf(groups, 999).rows.map((row) => row.name), ["s-7250", ...groups.slice(7251).map((row) => row.name)]);
  assert.equal(pageOf([], 3).pages, 1);
});

test("the total row is the ledger's own totals, not a sum the Studio computed", () => {
  const ledger = session.ledger as UsageLedger;
  const altered: SpendReport = { ...session, ledger: { ...ledger, totals: { ...ledger.totals, runs: 4242, usd: 9.87 } } };
  const html = render(altered);
  assert.match(html, /<strong>Total<\/strong>/);
  assert.match(html, />4,242</);
  assert.match(html, /\$9\.87/);
});

test("every dollar figure carries the list-price label and the pricing date", () => {
  const ledger = session.ledger as UsageLedger;
  const html = render(session);
  const cells = ledger.groups.length + 1;
  // One visible-to-assistive-technology label and one title per money cell, plus the headings.
  assert.equal(html.match(escaped(`(${label})`))?.length, cells);
  assert.equal(html.match(escaped(`title="${label}"`))?.length, cells);
  assert.match(html, escaped(`USD · list-price equivalent · prices as of ${session.basis.price_as_of}`));
  assert.match(html, /Every dollar figure is a list-price equivalent, prices as of/);
  const roles = render(fixture.role);
  const priced = (fixture.role.ledger.groups as Array<unknown>).length * 2;
  assert.equal(roles.match(escaped(`title="${moneyLabel(fixture.role.basis)}"`))?.length, priced);
});

test("partial data shows in the header and the footer, and unpriced stays unpriced", () => {
  const notes = partialNotes(session.ledger);
  assert.ok(notes.length > 0);
  const html = render(session);
  assert.equal(html.match(escaped(notes[0]))?.length, 2);
  assert.match(html, />unpriced</);
  assert.match(html, /citizen usage --json --by session --days 3660/);
});

test("roles below thirty runs are marked and sessions are named by the ledger", () => {
  const roles = render(fixture.role);
  assert.match(roles, /n&lt;30/);
  assert.match(roles, /gatherer/);
  const sessions = render(session);
  for (const name of ["sess-alpha", "codex-parent", "worker-1", "run-0001"]) assert.match(sessions, new RegExp(name));
});

test("rebuild attribution lists each cause with its excess and keeps unknown dollars unpriced", () => {
  const rebuild: SpendReport = {
    ...session,
    by: "rebuild",
    command: "citizen usage --json --by rebuild --days 7",
    ledger: {
      schema_version: 1, report: "rebuild", by: "rebuild", days: 7, price_as_of: session.basis.price_as_of, unpriced: 1, unpriced_calls: 4,
      groups: [{ scope: "all", sessions: 3, calls: 40, priced_spend_usd: 1.5, unpriced_calls: 4, unpriced_breaks: 1, unknown_breaks: 0,
        causes: [
          { cause: "idle over 1h (TTL expiry)", breaks: 2, rewritten_tokens: 90000, unpriced_breaks: 0, excess_usd: 0.3, cost_per_break: 0.15 },
          { cause: "compaction", breaks: 1, rewritten_tokens: 4000, unpriced_breaks: 1, excess_usd: null, cost_per_break: null },
        ] }],
    },
  };
  const html = render(rebuild);
  assert.match(html, /idle over 1h \(TTL expiry\)/);
  assert.match(html, /\$0\.3000/);
  // The counts are the CLI's own fields, shown unchanged in the header and the footer.
  assert.equal(html.match(/1 rebuild\(s\) unpriced/g)?.length, 2);
  assert.equal(html.match(/4 call\(s\) unpriced: priced spend excludes them/g)?.length, 2);
  assert.match(html, /4 unpriced call\(s\) excluded from priced spend · 1 unpriced break\(s\) · 0 unexplained break\(s\)/);
  assert.equal(html.match(/>unpriced</g)?.length, 2);
  // Priced spend, two excess cells and two per-break cells: each carries the label and date.
  assert.equal(html.match(escaped(`title="${label}"`))?.length, 5);
  assert.equal(html.match(escaped(`(${label})`))?.length, 5);
});

test("spend loads from the authenticated same-origin route with the CSRF token", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    const body = String(input) === "/api/session" ? { csrf_token: "token-1" } : session;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    assert.deepEqual(await loadSpend("session", 30), session);
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls[1].input, "/api/reports/spend");
  assert.equal(calls[1].init?.method, "POST");
  assert.deepEqual(calls[1].init?.headers, { "Content-Type": "application/json", "X-Studio-CSRF": "token-1" });
  assert.equal(calls[1].init?.body, JSON.stringify({ by: "session", days: 30 }));
});

test("a refused request surfaces the route's error rather than an empty report", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request) => String(input) === "/api/session"
    ? new Response(JSON.stringify({ csrf_token: "t" }), { status: 200 })
    : new Response(JSON.stringify({ error: "spend_unavailable" }), { status: 503 })) as typeof fetch;
  try {
    await assert.rejects(loadSpend("day", 30), /spend_unavailable/);
  } finally {
    globalThis.fetch = original;
  }
});
