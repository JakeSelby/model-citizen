import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { BaselineView } from "../src/baseline/BaselinePage.tsx";
import type { BaselineResponse } from "../src/baseline/model.ts";
import type { Overview } from "../src/overview/model.ts";
import { SystemSummary } from "../src/overview/SystemSummary.tsx";

// Hand-typed in the shapes `GET /api/baseline` and `GET /api/overview` answer with; no generator exists.
const DATA: BaselineResponse = {
  schema_version: 1,
  current: { config_present: true, config_path: "/fixture/config.json", stances: {}, rules: {}, settings: [], plugins: [] },
  baselines: [],
  commands: { show: "citizen baseline show --json", record: "citizen baseline record --label {label} --json" },
};

const OVERVIEW: Overview = {
  schema_version: 1,
  generated_at: "2026-10-15T10:00:00Z",
  installed: { status: "current", version: "0.18.0" },
  release: { status: "current", version: "0.18.0", changelog_url: null },
  mode: { status: "current", value: "focused", source: "user" },
  doctor: { status: "current", message: null, checks: [
    { id: "hooks.stop-gate", status: "attention", message: "checkout not trusted", fix: null, fixes: [] },
  ] },
  drift: { status: "current", message: null, items: [], command: "citizen sync" },
  runs: { status: "current", message: null, items: [] },
  commands: { doctor: "citizen doctor", diff: "citizen diff", catalog: "citizen catalog", sync: "citizen sync" },
};

const render = (element: ReactElement) => renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, element)));
const view = (installed?: ReactElement) => h(BaselineView, {
  data: DATA, label: "", onLabel: () => {}, onRecord: () => {}, installed,
});

test("Baseline shows the installed-system summary under What you have now", () => {
  const html = render(view(h(SystemSummary, { overview: OVERVIEW })));
  assert.match(html, /What you have now/);
  assert.match(html, /class="system-summary"/);
  assert.match(html, /Installed system/);
  assert.match(html, /v0\.18\.0/);
  assert.ok(html.indexOf("What you have now") < html.indexOf("What you already run"));
});

test("Baseline without an installed summary renders no empty section", () => {
  assert.doesNotMatch(render(view()), /What you have now/);
});
