import assert from "node:assert/strict";
import test from "node:test";

import { initialForm, registerBlocked, startingPack, withPack } from "../src/configure/draftTestModel.ts";
import { packKey, type ReplayCatalog, type ReplayPack } from "../src/experiments/replay/model.ts";

const pack = (set: string, tasks: string[]): ReplayPack => ({
  name: "model-citizen-evals", version: "1.0.0", commit: "c".repeat(40), digest: "a".repeat(64),
  short_digest: "a".repeat(12), set, tasks: tasks.map((id) => ({ id, label: id })),
});
const production = pack("production", ["one"]);
const rules = pack("rule-targeted", ["rule-one"]);
const catalog = (packs: ReplayPack[]) => ({
  schema_version: 1, tasks: [], packs, default_pack: "a".repeat(64), target_kinds: [], default_model: "sonnet",
}) as unknown as ReplayCatalog;

test("the starting pack is the catalog's default and a non-production default keeps its set", () => {
  assert.deepEqual(startingPack(catalog([production, rules]), null), { name: production.name, digest: production.digest });
  assert.deepEqual(startingPack(catalog([rules, production]), null), { name: rules.name, digest: rules.digest, set: "rule-targeted" });
  const chosen = { name: rules.name, digest: rules.digest, set: "rule-targeted" };
  assert.equal(startingPack(catalog([production]), chosen), chosen);
  assert.equal(startingPack(catalog([]), null), null);
});

test("registering is refused for a non-production pack set and allowed for production or none", () => {
  const form = initialForm("sonnet", null);
  assert.equal(registerBlocked(form), null);
  assert.equal(registerBlocked(withPack(form, catalog([production, rules]), packKey(production))), null);
  assert.equal(registerBlocked({ ...form, pack: { name: "p", digest: "d", set: "production" } }), null);
  assert.equal(registerBlocked(withPack(form, catalog([production, rules]), packKey(rules))),
    "A registered test pins the production set only. Pick the production set to register.");
});
