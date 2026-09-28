import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { SelectionCategory } from "../src/selection/SelectionPanel.tsx";

import { budgetPercent, SelectionRequestGate, selectedCount, sourceLabel, type SelectionReport } from "../src/selection/model.ts";

test("budget meters are bounded and use the lint token cap", () => {
  const budget = {
    runtime: "codex", label: "Codex", managed: true,
    used_tokens: 3500, token_cap: 5000, used_lines: 170, line_cap: 225, selected_lines: 160,
  };
  assert.equal(budgetPercent(budget), 70);
  assert.equal(budgetPercent({ ...budget, used_tokens: 9000 }), 100);
  assert.equal(budgetPercent({ ...budget, token_cap: 0 }), 0);
});

test("session environment provenance is explicit and never described as saved", () => {
  assert.equal(sourceLabel({
    source: "session", value: "off", source_file: "HARNESS_STANCE_TESTING", saved: false,
  }), "session environment");
  assert.equal(sourceLabel({
    source: "mode:minimal", value: "off", source_file: "/modes/minimal.json", saved: true,
  }), "mode / minimal");
});

test("resolved count covers every grouped primitive row", () => {
  const report = {
    groups: [{ kind: "rules", rows: [{}, {}] }, { kind: "stances", rows: [{}] }],
  } as unknown as SelectionReport;
  assert.equal(selectedCount(report), 3);
});

test("a late selection response cannot replace a newer request", () => {
  const gate = new SelectionRequestGate();
  const launcher = gate.next();
  const repository = gate.next();
  assert.equal(gate.accepts(repository), true);
  assert.equal(gate.accepts(launcher), false);
});

test("default categories start collapsed while overrides reveal their source summary", () => {
  const row = { unit: "testing", value: "required", source: "default", source_file: "", saved: true, overridden: [] };
  const render = (source: string) => renderToStaticMarkup(h(MantineProvider, {}, h(SelectionCategory, {
    group: { kind: "stances", rows: [{ ...row, source }] },
  })));
  assert.match(render("default"), /All defaults/);
  assert.doesNotMatch(render("default"), /<details[^>]*open/);
  assert.match(render("project"), /1 override · project/);
  assert.match(render("project"), /<details class="selection-category" open=""/);
});
