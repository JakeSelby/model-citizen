import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { NAVIGATION, pageCommand } from "../src/navigation.ts";
import type { Overview } from "../src/overview/model.ts";
import { DeterministicOverview, ReportCards, reportCards } from "../src/overview/OverviewPage.tsx";

const overview: Overview = {
  schema_version: 1,
  generated_at: "2026-10-06T10:00:00Z",
  installed: { status: "current", version: "0.15.0" },
  release: { status: "current", version: "0.15.0", changelog_url: null },
  mode: { status: "current", value: "focused", source: "user" },
  doctor: { status: "current", message: null, checks: [
    { id: "hooks.stop-gate", status: "attention", message: "checkout not trusted", fix: "citizen trust .", fixes: ["citizen trust ."] },
    { id: "projection.codex", status: "attention", message: "AGENTS.md stale", fix: null, fixes: [] },
    { id: "identity", status: "informational", message: "identity: set", fix: null, fixes: [] },
  ] },
  drift: { status: "drift", message: null, items: ["AGENTS.md", "settings.json", "hooks.json"], command: "citizen sync" },
  runs: { status: "current", message: null, items: [
    { run_id: "r-0193", suite_id: "live-replay", status: "succeeded", created_at: "2026-10-06T08:00:00Z", target_kind: "draft" },
    { run_id: "r-0191", suite_id: "hook-matrix", status: "failed", created_at: "2026-10-05T08:00:00Z", target_kind: "release" },
  ] },
  commands: { doctor: "citizen doctor", diff: "citizen diff", catalog: "citizen catalog", sync: "citizen sync" },
};

const render = (element: ReturnType<typeof h>) => renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, element)));

test("every page names the CLI command its evidence comes from", () => {
  assert.equal(pageCommand("/"), "citizen doctor");
  assert.equal(pageCommand("/setup"), "citizen draft first-run");
  assert.equal(pageCommand("/configure"), "citizen selection");
  assert.equal(pageCommand("/library"), "citizen catalog --library");
  assert.equal(pageCommand("/experiments"), "citizen runs history");
  assert.equal(pageCommand("/experiments/runs/r-1"), "citizen runs history");
  assert.equal(pageCommand("/activity"), "citizen activity");
  assert.equal(pageCommand("/reports"), "citizen reports trends");
  assert.equal(pageCommand("/reports/trends"), "citizen reports trends");
  assert.equal(pageCommand("/reports/rules"), "citizen usage --rules");
  assert.equal(pageCommand("/reports/usage"), "citizen usage");
  // A route that only shares a prefix does not borrow its neighbour's command.
  assert.equal(pageCommand("/reportsx"), "citizen doctor");
  for (const { path } of NAVIGATION) assert.match(pageCommand(path), /^citizen [a-z]/);
});

test("the Hub opens on one number strip, not a card", () => {
  const html = render(h(DeterministicOverview, { overview }));
  assert.doesNotMatch(html, /mantine-(Card|Paper)-root/);
  const strip = html.match(/<dl class="number-strip">(.*?)<\/dl>/)?.[1] ?? "";
  const items = [...strip.matchAll(/<div class="strip-item"><dt[^>]*>([^<]*)<\/dt><dd class="strip-value"[^>]*>([^<]*)<\/dd>/g)]
    .map(([, label, value]) => [label, value]);
  assert.deepEqual(items, [
    ["Installed system", "v0.15.0"],
    ["Doctor checks", "5 items to review"],
    ["Projection drift", "3 changed"],
    ["Recent runs", "2"],
  ]);
  // Status colour is reserved for status: the attention and drift values carry a tone, the counts do not.
  assert.match(strip, /<dd class="strip-value" data-tone="warning">5 items to review<\/dd>/);
  assert.match(strip, /<dd class="strip-value">2<\/dd>/);
});

test("the Hub lists doctor checks and runs as compact rows with their ids and status", () => {
  const html = render(h(DeterministicOverview, { overview }));
  assert.equal((html.match(/<li class="doctor-row">/g) ?? []).length, 3);
  assert.match(html, /checkout not trusted/);
  const runs = html.match(/<ul class="run-list">(.*?)<\/ul>/)?.[1] ?? "";
  assert.match(runs, /<code class="run-id">r-0193<\/code>.*live-replay.*data-tone="success">succeeded/);
  assert.match(runs, /<code class="run-id">r-0191<\/code>.*hook-matrix.*data-tone="danger">failed/);
});

test("report links are rows that keep every label, measure and detail", () => {
  const html = render(h(ReportCards));
  assert.doesNotMatch(html, /mantine-Card-root/);
  assert.equal((html.match(/class="report-row"/g) ?? []).length, reportCards.length);
  for (const card of reportCards) {
    assert.ok(html.includes(`href="${card.href.replace(/&/g, "&amp;")}"`), card.href);
    assert.ok(html.includes(`<span class="report-label">${card.label}</span>`), card.label);
    assert.ok(html.includes(card.measure), card.measure);
  }
});

test("the theme names Inter and JetBrains Mono first and bundles no font file", () => {
  const theme = readFileSync(new URL("../src/theme.ts", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  assert.match(theme, /const SANS = "Inter, system-ui,/);
  assert.match(theme, /const MONO = "JetBrains Mono, ui-monospace,/);
  assert.doesNotMatch(styles + theme, /@font-face|url\(|fonts\.googleapis|\.woff2?/);
});
