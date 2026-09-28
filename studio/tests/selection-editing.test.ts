import assert from "node:assert/strict";
import test from "node:test";

import { previewDraftSelection, saveDraftSelection, type SelectionControls, type SelectionSnapshot } from "../src/configure/api.ts";
import {
  changedStances, changedSwitchUnits, cliCommands, controlValues, needsCoreAcknowledgement,
  selectionRequestSignature, selectionSaveFailureAction, selectionSwitchKinds,
  switchSelectionLines, SELECTION_AUTOSAVE_DELAY_MS,
} from "../src/configure/selectionModel.ts";

const controls: SelectionControls = {
  modes: ["full", "minimal"],
  stances: [{ name: "voice", value: "answer-card", options: ["answer-card", "concise"] }],
  switches: [{ kind: "hooks", rows: [{ unit: "stop-gate", value: "on", core: true }] }],
  core_acknowledged: false,
};

const before: SelectionSnapshot = {
  selection: { mode: "full", stances: { voice: "answer-card" } },
  budget: { selected_lines: 600, worst_case_lines: 900, line_cap: 1000, worst_case_tokens: 5000, token_cap: 6000 },
  stance_text: { voice: "Answer first." },
};

test("selection controls hydrate draft values and use the structured-form cadence", () => {
  assert.deepEqual(controlValues(controls, before), {
    mode: "full",
    "stances.voice": "answer-card",
    "hooks.stop-gate": "on",
    core_switches_acknowledged: false,
  });
  assert.equal(SELECTION_AUTOSAVE_DELAY_MS, 750);
});

test("preview modeling names changed stance operative text and core acknowledgement", () => {
  const after = {
    ...before,
    selection: { mode: "minimal", stances: { voice: "concise" } },
    stance_text: { voice: "Keep it short." },
  };
  assert.deepEqual(changedStances(before, after), ["voice"]);
  assert.equal(needsCoreAcknowledgement("set core_switches_acknowledged true"), true);
  assert.equal(needsCoreAcknowledgement("module dependency is off"), false);
});

test("preview modeling includes every switch kind and changed projected unit", () => {
  const switchBefore: SelectionSnapshot = {
    ...before,
    selection: {
      ...before.selection,
      rules: { concise: "on" }, hooks: { approvals: "on" },
      skills: { review: "on" }, workflows: { build: "on" }, roles: { builder: "on" },
    },
  };
  const switchAfter: SelectionSnapshot = {
    ...switchBefore,
    selection: {
      ...switchBefore.selection,
      rules: { concise: "off" }, hooks: { approvals: "off" },
      skills: { review: "off" }, workflows: { build: "off" }, roles: { builder: "off" },
    },
  };
  assert.deepEqual(selectionSwitchKinds(switchBefore, switchAfter), [
    "hooks", "roles", "rules", "skills", "workflows",
  ]);
  assert.deepEqual(changedSwitchUnits(switchBefore, switchAfter), [
    "hooks.approvals", "roles.builder", "rules.concise", "skills.review", "workflows.build",
  ]);
  assert.equal(switchSelectionLines(switchAfter, "rules"), "concise: off");
});

test("save failures distinguish reload, new identity, and durable transport retry", () => {
  assert.equal(selectionSaveFailureAction("stale-revision"), "reload");
  assert.equal(selectionSaveFailureAction("idempotency-conflict"), "new-identity");
  assert.equal(selectionSaveFailureAction("busy"), "retry");
  assert.equal(selectionSaveFailureAction(""), "retry");
});

test("matching CLI commands preserve the visible change paths and values", () => {
  assert.deepEqual(cliCommands({ "hooks.stop-gate": "off", core_switches_acknowledged: true }), [
    "citizen config set core_switches_acknowledged true",
    "citizen config set hooks.stop-gate off",
  ]);
  assert.deepEqual(cliCommands({ "workflows.build": "on", "roles.builder": "on" }, [
    "roles.builder", "workflows.build",
  ]), [
    "citizen config set roles.builder on",
    "citizen config set workflows.build on",
  ]);
});

test("save request identity is stable across object ordering and changes with its request", () => {
  const first = selectionRequestSignature("experiment", "revision", {
    "rules.conciseness": "off", "rules.cache-hygiene": "off",
  });
  const reordered = selectionRequestSignature("experiment", "revision", {
    "rules.cache-hygiene": "off", "rules.conciseness": "off",
  });
  assert.equal(first, reordered);
  assert.notEqual(first, selectionRequestSignature("other", "revision", {
    "rules.cache-hygiene": "off", "rules.conciseness": "off",
  }));
});

test("selection preview and save use CSRF-protected same-origin routes", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf-test" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({
      valid: true, error: "", error_code: "", changed: ["mode"], unchanged: false,
      base_revision: "revision", before, after: before, controls, applied: ["mode"],
      saved: true, result: { revision: "next" },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await previewDraftSelection("experiment", { mode: "minimal" });
    await saveDraftSelection("experiment", "revision", "stable-key", { mode: "minimal" });
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls[1].input, "/api/configure/selection/preview");
  assert.equal(calls[3].input, "/api/configure/selection/save");
  assert.equal((calls[1].init?.headers as Record<string, string>)["X-Studio-CSRF"], "csrf-test");
  assert.deepEqual(JSON.parse(String(calls[3].init?.body)).changes, { mode: "minimal" });
  assert.equal(JSON.parse(String(calls[3].init?.body)).idempotency_key, "stable-key");
});
