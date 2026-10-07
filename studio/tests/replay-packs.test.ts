import assert from "node:assert/strict";
import test from "node:test";

import { initialPack, packKey, packOptions, packSelection, tasksFor, type ReplayPack } from "../src/experiments/replay/model.ts";

const pack = (name: string, digest: string, tasks: string[], set = "production"): ReplayPack => ({
  name, version: "1.0.0", commit: "c".repeat(40), digest, short_digest: digest.slice(0, 12), set,
  tasks: tasks.map((id) => ({ id, label: id })),
});

const evals = pack("model-citizen-evals", "a".repeat(64), ["one", "two"]);
const other = pack("other", "b".repeat(64), ["three"]);

const rules = pack("model-citizen-evals", "a".repeat(64), ["rule-one"], "rule-targeted");
const outcome = pack("model-citizen-evals", "a".repeat(64), ["two"], "outcome-public");

test("each pack option shows its name, version, set and short digest", () => {
  assert.deepEqual(packOptions([evals]), [{ value: `${"a".repeat(64)}/production`, label: `model-citizen-evals 1.0.0, set production (${"a".repeat(12)})` }]);
});

test("each set of one pack is its own option, chosen by name, digest and set", () => {
  assert.deepEqual(packOptions([evals, rules, outcome]).map((item) => item.value),
    ["production", "rule-targeted", "outcome-public"].map((set) => `${"a".repeat(64)}/${set}`));
  assert.deepEqual(packSelection(rules), { name: "model-citizen-evals", digest: "a".repeat(64), set: "rule-targeted" });
  // The production set sends no set, so its native command and pinned identity are what they were.
  assert.deepEqual(packSelection(evals), { name: "model-citizen-evals", digest: "a".repeat(64) });
  assert.equal(packKey(packSelection(evals)), packKey(evals));
  assert.deepEqual(tasksFor([evals, rules, outcome], packKey(rules), []), ["rule-one"]);
  assert.deepEqual(tasksFor([evals, rules, outcome], packKey(outcome), []), ["two"]);
  // A selection saved before sets were offered names none and reads as the production set.
  assert.equal(packKey({ digest: "a".repeat(64) }), packKey(evals));
});

test("the replay starts on the catalog's default pack, else the first, else none", () => {
  assert.equal(initialPack([other, evals], evals.digest), evals);
  assert.equal(initialPack([other, evals], null), other);
  assert.equal(initialPack([], null), null);
});

test("tasks come from the chosen pack, or the repository without one", () => {
  assert.deepEqual(tasksFor([evals, other], packKey(other), ["repo-task"]), ["three"]);
  assert.deepEqual(tasksFor([], null, ["repo-task"]), ["repo-task"]);
});
