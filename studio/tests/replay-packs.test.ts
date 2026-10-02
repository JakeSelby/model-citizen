import assert from "node:assert/strict";
import test from "node:test";

import { initialPack, packOptions, tasksFor, type ReplayPack } from "../src/experiments/replay/model.ts";

const pack = (name: string, digest: string, tasks: string[]): ReplayPack => ({
  name, version: "1.0.0", commit: "c".repeat(40), digest, short_digest: digest.slice(0, 12),
  tasks: tasks.map((id) => ({ id, label: id })),
});

const evals = pack("model-citizen-evals", "a".repeat(64), ["one", "two"]);
const other = pack("other", "b".repeat(64), ["three"]);

test("each pack option shows its name, version and short digest", () => {
  assert.deepEqual(packOptions([evals]), [{ value: "a".repeat(64), label: `model-citizen-evals 1.0.0 (${"a".repeat(12)})` }]);
});

test("the replay starts on the catalog's default pack, else the first, else none", () => {
  assert.equal(initialPack([other, evals], evals.digest), evals);
  assert.equal(initialPack([other, evals], null), other);
  assert.equal(initialPack([], null), null);
});

test("tasks come from the chosen pack, or the repository without one", () => {
  assert.deepEqual(tasksFor([evals, other], other.digest, ["repo-task"]), ["three"]);
  assert.deepEqual(tasksFor([], null, ["repo-task"]), ["repo-task"]);
});
