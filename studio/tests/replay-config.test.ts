import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";

import { draftTestErrorMessage } from "../src/configure/draftTestModel.ts";
import { ReplayPanel } from "../src/experiments/replay/ReplayPanel.tsx";
import { measuredConfigCaption, replayErrorMessage } from "../src/experiments/replay/model.ts";

const ENGINE_CODES = ["config_invalid", "config_root_unreadable", "config_root_unsupported", "config_host_path",
  "config_unresolved"];
const target = { kind: "draft" as const, ref: "cost-pass", revision: "b".repeat(40), version: null, draft: "cost-pass" };

function render(props: Record<string, unknown>): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(ReplayPanel, { tasks: ["one"], ...props })));
}

test("an engine refusal the Studio has no sentence for still shows the engine's code", () => {
  assert.equal(replayErrorMessage("replay_target_config_some_future_code"), "replay_target_config_some_future_code");
  assert.match(draftTestErrorMessage("replay_target_config_some_future_code"), /replay_target_config_some_future_code/);
});

test("the result shows both configuration digests the engine measured", () => {
  const [base, draft] = ["a".repeat(64), "d".repeat(64)];
  assert.equal(measuredConfigCaption([base, draft]),
    `Configuration measured by the engine: target 1 configuration ${base}; target 2 configuration ${draft}.`);
});

test("the result shows the configuration digest the engine measured, per target", () => {
  const digest = "d".repeat(64);
  assert.equal(measuredConfigCaption([null, digest]), `Configuration measured by the engine: target 2 configuration ${digest}.`);
  assert.equal(measuredConfigCaption([null, null]), "Source only: target configuration was not applied.");
  assert.equal(measuredConfigCaption(), "Source only: target configuration was not applied.");
  const html = render({ rows: [{ target, task: "one", arm: "harness", runs: 1, passed: 1, pass_rate: 1, cost_per_passed: 0.5 }],
    measuredConfigs: [null, digest] });
  assert.match(html, new RegExp(`Configuration measured by the engine: target 2 configuration ${digest}\\.`));
  assert.doesNotMatch(html, /Source only/);
});

test("each refusal the engine names reads as the engine's reason in the replay and the draft test", () => {
  for (const code of ENGINE_CODES) {
    const replay = replayErrorMessage(`replay_target_${code}`);
    const draft = draftTestErrorMessage(`replay_target_${code}`);
    assert.match(replay, /^The engine refused the draft's configuration/, code);
    assert.match(draft, /^The engine refused this draft's configuration/, code);
  }
  assert.match(replayErrorMessage("replay_target_config_unresolved"), /switches, manifests or modes/);
  assert.match(replayErrorMessage("replay_target_config_host_path"), /path on this machine/);
});

test("a draft test no longer says a changed configuration cannot be measured", () => {
  assert.doesNotMatch(draftTestErrorMessage("replay_target_config_unsupported"), /cannot measure/);
});
