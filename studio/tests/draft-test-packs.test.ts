import assert from "node:assert/strict";
import test from "node:test";

import { draftTestTasks, initialForm, withPack } from "../src/configure/draftTestModel.ts";
import { packKey, packOptions, type ReplayCatalog, type ReplayPack } from "../src/experiments/replay/model.ts";

const pack = (digest: string, tasks: string[], set: string): ReplayPack => ({
  name: "model-citizen-evals", version: "1.0.0", commit: "c".repeat(40), digest, short_digest: digest.slice(0, 12), set,
  tasks: tasks.map((id) => ({ id, label: id })),
});

// Two sets of one pack share a digest, so only the set tells their task lists apart.
const production = pack("a".repeat(64), ["one", "two"], "production");
const rules = pack("a".repeat(64), ["rule-one"], "rule-targeted");
const catalog = {
  schema_version: 1, tasks: [{ id: "repo-task", label: "repo-task" }], packs: [production, rules],
  default_pack: production.digest, target_kinds: [], default_model: "sonnet",
} as unknown as ReplayCatalog;

test("the draft test's task list comes from the chosen pack set", () => {
  const form = initialForm("sonnet", { name: production.name, digest: production.digest });
  assert.deepEqual(draftTestTasks(catalog, form.pack), ["one", "two"]);
  const chosen = withPack({ ...form, tasks: ["one"] }, catalog, packKey(rules));
  assert.deepEqual(chosen.pack, { name: rules.name, digest: rules.digest, set: "rule-targeted" });
  assert.deepEqual(chosen.tasks, []);
  assert.deepEqual(draftTestTasks(catalog, chosen.pack), ["rule-one"]);
  // The picker's value for the chosen pack is one of its options, so the Select shows it.
  assert.ok(packOptions(catalog.packs).some((option) => option.value === packKey(chosen.pack!)));
});

test("without a pack the repository's tasks are offered, and an unknown key changes nothing", () => {
  const form = initialForm("sonnet", null);
  assert.deepEqual(draftTestTasks(catalog, null), ["repo-task"]);
  assert.deepEqual(draftTestTasks(null, null), []);
  assert.equal(withPack(form, catalog, "missing/production"), form);
});
