import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";

import { TrendChart } from "../src/reports/trends/TrendsPage.tsx";
import type { Trends } from "../src/reports/trends/model.ts";
import { studioCssVariables, studioTheme } from "../src/theme.ts";

// The trends route's payload; see trends.test.ts for its generator.
const fixture = JSON.parse(readFileSync(new URL("./fixtures/trends.json", import.meta.url), "utf8")) as Trends;

test("every chart label paints with the page ink, not SVG's default black", () => {
  const line = fixture.lines.find((item) => item.points.length > 0);
  assert.ok(line);
  const measure = fixture.measures.find((item) => line.points.some((point) => point.measures[item.id]?.value != null));
  assert.ok(measure);
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(TrendChart, { line, measure })));
  const labels = html.match(/<text\b[^>]*>/g) ?? [];
  assert.ok(labels.length >= 3, html);
  for (const label of labels) assert.match(label, /fill="currentColor"/);
});

test("placeholders and filled palette surfaces resolve to the Studio tokens in both schemes", () => {
  const resolved = studioCssVariables(studioTheme as never);
  for (const scheme of [resolved.light, resolved.dark]) {
    assert.equal(scheme["--mantine-color-placeholder"], "var(--studio-ink-secondary)");
    assert.equal(scheme["--mantine-color-red-filled"], "var(--studio-danger)");
    assert.equal(scheme["--mantine-color-red-filled-hover"], "var(--studio-danger)");
  }
});
