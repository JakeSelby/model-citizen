import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DEFAULT_THEME, MantineProvider, mergeMantineTheme } from "@mantine/core";

import { TrendChart } from "../src/reports/trends/TrendsPage.tsx";
import type { Trends } from "../src/reports/trends/model.ts";
import { studioCssVariables, studioTheme, toneFor } from "../src/theme.ts";

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

// The Studio tokens as `studio/src/styles.css` declares them, light and dark.
function tokens(): { light: Record<string, string>; dark: Record<string, string> } {
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  const block = (selector: string) => {
    const start = css.indexOf(selector + " {");
    const body = css.slice(start, css.indexOf("}", start));
    return Object.fromEntries([...body.matchAll(/(--studio-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})/g)].map((match) => [match[1], match[2]]));
  };
  const light = block(":root");
  return { light, dark: { ...light, ...block(':root[data-mantine-color-scheme="dark"]') } };
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(first: string, second: string): number {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

const PALETTE = ["clear", "blue", "cyan", "indigo", "teal", "green", "lime", "yellow", "orange", "red", "pink", "gray", "dark"];

test("every palette name resolves to a token pair", () => {
  for (const name of PALETTE.filter((item) => item !== "clear")) {
    const tone = toneFor(`${name}.6`);
    assert.ok(tone, name);
    assert.match(tone.color, /^var\(--studio-[a-z-]+\)$/);
    assert.match(tone.wash, /^var\(--studio-[a-z-]+\)$/);
  }
  assert.equal(toneFor("not-a-palette"), undefined);
  assert.equal(toneFor(undefined), undefined);
});

test("each button variant's text meets 4.5:1 on its own surface in both schemes", () => {
  const theme = mergeMantineTheme(DEFAULT_THEME, studioTheme);
  const resolve = (scheme: Record<string, string>, value: string) => {
    const match = /^var\((--studio-[a-z-]+)\)$/.exec(value);
    assert.ok(match, `not a Studio token: ${value}`);
    const hex = scheme[match[1]];
    assert.ok(hex, match[1]);
    return hex;
  };
  for (const [schemeName, scheme] of Object.entries(tokens())) {
    for (const color of PALETTE) {
      for (const variant of ["filled", "light", "outline", "subtle"]) {
        const colors = theme.variantColorResolver({ color, variant, theme });
        const surface = variant === "outline" || variant === "subtle" ? "var(--studio-surface-raised)" : colors.background;
        const ratio = contrast(resolve(scheme, colors.color), resolve(scheme, surface));
        assert.ok(ratio >= 4.5, `${schemeName} ${color} ${variant}: ${ratio.toFixed(2)}:1`);
      }
    }
  }
});
