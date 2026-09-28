import assert from "node:assert/strict";
import test from "node:test";

import {
  coalesceUpdates,
  OrderedEventGate,
  PendingUpdates,
  parseLiveUpdate,
  updateTouchesPaths,
  updateTouchesTopics,
  type LiveUpdate,
} from "../src/live/model.ts";

const event = (sequence: number, topics = ["library"], paths = ["/rules/one.md"]): LiveUpdate => ({
  kind: "change", instance_epoch: "epoch", sequence, topics, paths,
});

test("ordered events reject duplicates while a new server epoch is accepted", () => {
  const gate = new OrderedEventGate();
  assert.equal(gate.accepts(event(1)), true);
  assert.equal(gate.accepts(event(1)), false);
  assert.equal(gate.accepts(event(0)), false);
  assert.equal(gate.accepts({ ...event(0), kind: "gap", instance_epoch: "next" }), true);
});

test("paused updates coalesce topics and paths at the newest sequence", () => {
  assert.deepEqual(coalesceUpdates([
    event(1), event(2, ["selection"], ["/config.json"]), event(3, ["library"], ["/rules/two.md"]),
  ]), {
    kind: "change", instance_epoch: "epoch", sequence: 3,
    topics: ["library", "selection"],
    paths: ["/config.json", "/rules/one.md", "/rules/two.md"],
  });
});

test("an indefinite pause keeps only bounded latest state per domain", () => {
  const pending = new PendingUpdates();
  for (let sequence = 1; sequence <= 10_000; sequence += 1) {
    pending.push(event(sequence, [sequence % 2 ? "library" : "selection"],
      Array.from({ length: 300 }, (_, index) => `/rules/${sequence}-${index}.md`)));
  }
  assert.equal(pending.size, 2);
  const merged = pending.drain();
  assert.equal(merged?.sequence, 10_000);
  assert.deepEqual(merged?.topics, ["library", "selection"]);
  assert.deepEqual(merged?.paths, []);
  assert.equal(pending.size, 0);
});

test("paused updates preserve every changed path needed by visible-path filters", () => {
  const pending = new PendingUpdates();
  pending.push(event(1, ["library"], ["/rules/visible.md"]));
  pending.push(event(2, ["library"], ["/rules/hidden.md"]));
  const merged = pending.drain();
  assert.ok(merged);
  assert.equal(updateTouchesPaths(merged, new Set(["/rules/visible.md"])), true);
  assert.equal(updateTouchesPaths(merged, new Set(["/rules/other.md"])), false);
});

test("topic and visible-path filters refetch only affected views", () => {
  assert.equal(updateTouchesTopics(event(1), new Set(["selection"])), false);
  assert.equal(updateTouchesTopics(event(1), new Set(["library"])), true);
  assert.equal(updateTouchesPaths(event(1), new Set(["/rules/one.md"])), true);
  assert.equal(updateTouchesPaths(event(1), new Set(["/rules/other.md"])), false);
  assert.equal(updateTouchesPaths(
    event(1, ["library"], ["/rules"]), new Set(["/rules/one.md"])), true);
});

test("malformed stream payloads never reach subscribers", () => {
  assert.equal(parseLiveUpdate("not json"), null);
  assert.equal(parseLiveUpdate(JSON.stringify({ ...event(1), topics: [7] })), null);
  assert.deepEqual(parseLiveUpdate(JSON.stringify(event(1))), event(1));
});
