import assert from "node:assert/strict";
import test from "node:test";

import { evaluationLines } from "../src/experiments/history/model.ts";

test("no contract shows no evaluation lines", () => {
  assert.deepEqual(evaluationLines(null), []);
  assert.deepEqual(evaluationLines(undefined), []);
});

test("a pack row shows the pack identity and digest the engine stamped", () => {
  const lines = evaluationLines({
    shape: "two-arm", arms: ["bare", "harness"],
    pack: { pack: "evals", pack_version: "1.0.0", pack_commit: "a".repeat(40), pack_digest: "d".repeat(64) },
    ablation: null, design: null,
    registration: { evidence: "pre-registered", pre_registration: "plan.md", pre_registration_commit: "b".repeat(40) },
  });
  assert.deepEqual(lines, [
    ["Contract", "Two-arm replay"], ["Arms", "bare, harness"],
    ["Pack", `evals 1.0.0 @ ${"a".repeat(40)}`], ["Pack digest", "d".repeat(64)],
    ["Evidence label", "Pre-registered"], ["Pre-registration", "plan.md"],
  ]);
});

test("ablation and four-cell stamps keep their digests and schema", () => {
  const ablation = evaluationLines({ shape: "variable-arm", arms: ["bare", "control", "no-rule"],
    ablation: { name: "drop-rule", sha256: "e".repeat(64), schema: 2 }, design: null, pack: null,
    registration: { evidence: "exploratory", pre_registration: null, pre_registration_commit: null } });
  assert.deepEqual(ablation.slice(0, 4), [["Contract", "Variable-arm ablation"], ["Arms", "bare, control, no-rule"],
    ["Ablation", "drop-rule (schema 2)"], ["Ablation digest", "e".repeat(64)]]);
  assert.deepEqual(ablation[4], ["Evidence label", "Exploratory"]);
  const grid = evaluationLines({ shape: "four-cell", design: { name: "unit-economy-2x2", schema: 1, manifest_sha256: "f".repeat(64) } });
  assert.deepEqual(grid, [["Contract", "Four-cell design"], ["Design", "unit-economy-2x2 (schema 1)"], ["Design digest", "f".repeat(64)]]);
});

test("a missing evidence label stays unlabelled rather than exploratory", () => {
  const lines = evaluationLines({ shape: "two-arm", registration: { evidence: null, pre_registration: null, pre_registration_commit: null } });
  assert.deepEqual(lines.at(-1), ["Evidence label", "Unlabelled"]);
});

test("proof status is the verifier's, with every failed check and unknown kept", () => {
  assert.deepEqual(evaluationLines({ shape: "proof-bundle",
    proof: { status: "failed", bundle_id: "proof-1", errors: ["item 3: no init event"], unknown: ["harness effort"] } }), [
    ["Contract", "Proof bundle"], ["Proof", "Verification failed"], ["Bundle", "proof-1"],
    ["Failed check", "item 3: no init event"], ["Unknown, not verified", "harness effort"],
  ]);
  assert.deepEqual(evaluationLines({ shape: "proof-bundle", proof: { status: "verified", bundle_id: null, errors: [], unknown: [] } }),
    [["Contract", "Proof bundle"], ["Proof", "Verified"]]);
});
