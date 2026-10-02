import assert from "node:assert/strict";
import test from "node:test";

import { analysisLines, comparisonLine, engineValue, samplingLines, type DraftComparison } from "../src/experiments/replay/model.ts";

test("engine values are shown as the engine wrote them, null and intervals included", () => {
  assert.equal(engineValue(null), "null");
  assert.equal(engineValue([0.41, 1.07]), "[0.41,1.07]");
  assert.equal(engineValue(undefined), "absent");
});

test("a zero-pass analysis keeps the undefined ratio and its reason", () => {
  const lines = new Map(analysisLines({ target: 1, result: {
    arms: { bare: { attempts: 4, passes: 0, errors: 0, pass_rate: 0, cost_of_pass: null, cost_usd: 4 } },
    ratio: null, ratio_undefined: "bare passed no task", ratio_interval: null,
    difference: 0, difference_interval: [-0.2, 0.2], verdict: "inconclusive", reason: "r", claim: null,
  } }));
  assert.equal(lines.get("arms.bare.cost_of_pass"), "null");
  assert.equal(lines.get("ratio"), "null");
  assert.equal(lines.get("ratio_undefined"), "\"bare passed no task\"");
  assert.equal(lines.get("difference_interval"), "[-0.2,0.2]");
  assert.equal(lines.get("verdict"), "\"inconclusive\"");
});

test("an engine refusal is shown in its own words", () => {
  assert.deepEqual(analysisLines({ target: 2, error: "cost-bench: cannot derive SM-2" }),
    [["Engine refused", "cost-bench: cannot derive SM-2"]]);
});

test("sampling names the label, the request and the registered sample", () => {
  assert.deepEqual(samplingLines(undefined), []);
  const lines = samplingLines({ evidence: "pre-registered", note: "Pre-registered.",
    registered: { tasks: 7, long: 2, trials: 5, min_trials: 5, power_calculation: "replay_power.py --have 7 2 5" },
    requested: { tasks: 7, trials: 5 } });
  assert.deepEqual(lines, ["Pre-registered.", "This replay: 7 task(s), 5 trial(s) per task and arm.",
    "Registered: 7 task(s), 5 trial(s) per task and arm (floor 5).", "Power calculation: replay_power.py --have 7 2 5"]);
});

test("a stale comparison says why", () => {
  const item: DraftComparison = { draft: "cost-pass", revision: "b".repeat(40), config_digest: null,
    base: { kind: "release", ref: "v1.0.0", revision: "a".repeat(40), version: "1.0.0", draft: null },
    tasks: ["one"], model: "m", trials: 5, pack_digest: null, evidence: "exploratory", key: "k",
    stale: true, stale_reason: "the draft has a newer checkpoint" };
  assert.equal(comparisonLine(item),
    `cost-pass at ${"b".repeat(12)} against release v1.0.0; 1 task(s), m, 5 trial(s). Stale: the draft has a newer checkpoint.`);
});
