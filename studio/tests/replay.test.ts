import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";

import { loadReplayCatalog, loadReplayResult, startReplay } from "../src/experiments/replay/api.ts";
import { ReplayPanel } from "../src/experiments/replay/ReplayPanel.tsx";
import {
  formatCost, formatPercent, progressResult, replayErrorMessage, ReplayRequestGate, validateReplay,
  type ReplayLaunchInput, type ReplayRequest,
} from "../src/experiments/replay/model.ts";

const draft: ReplayLaunchInput = {
  targets: [{ kind: "release", ref: "v0.17.0" }, { kind: "draft", ref: "cost-pass" }],
  model: "claude-test", repetitions: 2, tasks: ["one"],
  max_budget_usd: "2", spend_cap_usd: "20", pre_registration: "docs/plan.md",
};
const resolvedRelease = {
  ...draft.targets[0], revision: "a".repeat(40), version: "0.17.0", draft: null,
};

test("a replay requires two different explicit targets", () => {
  assert.deepEqual(validateReplay(draft), []);
  assert.deepEqual(validateReplay({ ...draft, targets: [draft.targets[0], draft.targets[0]] }), [
    "Choose two different targets.",
  ]);
});

test("release runs require evidence registration and the spend guard is checked locally", () => {
  assert.deepEqual(validateReplay({ ...draft, pre_registration: "" }), [
    "A release replay needs a pre-registration before it can write history.",
  ]);
  assert.deepEqual(validateReplay({ ...draft, max_budget_usd: "21" }), [
    "The per-run budget must be positive and no greater than the spend cap.",
  ]);
});

test("the per-task display says when cost or pass rate is unavailable", () => {
  assert.equal(formatCost(1.23456), "$1.2346");
  assert.equal(formatCost(null), "Unavailable");
  assert.equal(formatPercent(0.75), "75.0%");
  assert.equal(formatPercent(null), "Unavailable");
});

test("a changed request invalidates an in-flight preview generation", () => {
  const gate = new ReplayRequestGate();
  const first = gate.next();
  assert.equal(gate.accepts(first), true);
  const changed = gate.next();
  assert.equal(gate.accepts(first), false);
  assert.equal(gate.accepts(changed), true);
});

test("a paid start blocks edits and a second start until its queued id is accepted", () => {
  const gate = new ReplayRequestGate();
  const paid = gate.beginPaid();
  assert.notEqual(paid, null);
  assert.equal(gate.paidBusy(), true);
  assert.equal(gate.beginPaid(), null);
  assert.equal(gate.beginEdit(), false);
  assert.equal(gate.paidBusy(), true);
  assert.equal(gate.acceptsPaid(paid as number), true);
  assert.equal(gate.finishPaid(paid as number), true);
  assert.equal(gate.paidBusy(), false);
  assert.equal(gate.beginEdit(), true);
});

test("start sends the exact request with its one-use confirmation", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
    }
    return new Response(JSON.stringify({ run_id: "run-1" }), { status: 200 });
  }) as typeof fetch;
  const resolved: ReplayRequest = {
    ...draft,
    targets: [
      resolvedRelease,
      { ...draft.targets[1], revision: "b".repeat(40), version: null, draft: "cost-pass",
        config_digest: "config-digest" },
    ],
  };
  try {
    assert.deepEqual(await startReplay(resolved, "confirm-once"), { run_id: "run-1" });
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls[1].input, "/api/runs/replay/start");
  assert.equal(calls[1].init?.method, "POST");
  assert.equal(new Headers(calls[1].init?.headers).get("X-Studio-CSRF"), "csrf");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    request: resolved, confirmation_token: "confirm-once",
  });
});

test("catalog and result polling use fixed same-origin replay routes", async () => {
  const original = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request) => {
    const path = String(input); calls.push(path);
    if (path === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
    }
    if (path.endsWith("/catalog")) {
      return new Response(JSON.stringify({ schema_version: 1, tasks: [], target_kinds: [],
        default_model: "claude-test", commands: { run: "python3 scripts/cost_bench.py replay" } }), { status: 200 });
    }
    return new Response(JSON.stringify({ schema_version: 1,
      run: { run_id: "run-1", status: "running" }, progress: [], result: null }), { status: 200 });
  }) as typeof fetch;
  try {
    assert.equal((await loadReplayCatalog()).default_model, "claude-test");
    assert.equal((await loadReplayResult("run-1")).run.status, "running");
  } finally { globalThis.fetch = original; }
  assert.deepEqual(calls, ["/api/runs/replay/catalog", "/api/session", "/api/runs/replay/result"]);
});

test("the panel renders the operational launch guard and per-arm result table", () => {
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(ReplayPanel, {
    tasks: ["one"],
    runStatus: "running",
    progress: [{ target: resolvedRelease, task: "one", arm: "bare", repetition: 1,
      status: "completed", passed: true, cost_usd: 0.25 }],
    rows: [{ target: resolvedRelease, task: "one", arm: "harness", runs: 2,
      passed: 2, pass_rate: 1, cost_per_passed: 0.5 }],
  })));
  assert.match(html, /Measure two explicit targets/);
  assert.match(html, /Preview spend/);
  assert.match(html, /Confirm and run/);
  assert.match(html, /Live progress by target, task, repetition and arm/);
  assert.match(html, /Passed/);
  assert.match(html, /Cost per passed task/);
  assert.match(html, /100\.0%/);
});

function render(props: Record<string, unknown>): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(ReplayPanel, { tasks: ["one"], ...props })));
}

test("the panel labels every control and captions its tables for assistive technology", () => {
  const html = render({
    progress: [{ target: resolvedRelease, task: "one", arm: "bare", repetition: 1,
      status: "completed", passed: true, cost_usd: 0.25 }],
    rows: [{ target: resolvedRelease, task: "one", arm: "harness", runs: 2,
      passed: 2, pass_rate: 1, cost_per_passed: 0.5 }],
  });
  const inputs = [...html.matchAll(/<input\b[^>]*>/g)].map((match) => match[0])
    .filter((tag) => !/type="hidden"/.test(tag));
  assert.ok(inputs.length >= 8);
  for (const input of inputs) {
    const id = /\bid="([^"]+)"/.exec(input)?.[1];
    assert.ok(id, `input without an id: ${input}`);
    assert.match(html, new RegExp(`<label[^>]*for="${id}"`), `input ${id} has no label`);
  }
  assert.match(html, /aria-live="polite"/);
  assert.equal([...html.matchAll(/<caption\b/g)].length, 2);
  assert.equal([...html.matchAll(/<th\b/g)].length, 12);
});

test("an untouched empty form does not announce that the replay is not ready", () => {
  const html = render({});
  assert.doesNotMatch(html, /Replay is not ready/);
  assert.doesNotMatch(html, /role="alert"/);
});

test("the panel never advertises a CLI command that does not exist", () => {
  assert.doesNotMatch(render({}), /citizen runs replay/);
  assert.match(render({}), /native benchmark commands appear once the preview resolves/);
});

test("an errored native row reads as errored, not completed and pending", () => {
  const errored = { target: resolvedRelease, task: "one", arm: "bare" as const, repetition: 1,
    status: "errored" as const, passed: null, cost_usd: null };
  assert.equal(progressResult(errored), "Errored");
  assert.equal(progressResult({ ...errored, status: "pending" }), "Pending");
  const html = render({ progress: [errored] });
  assert.match(html, /Errored/);
  assert.doesNotMatch(html, /Pending/);
});

test("refusal codes become sentences a person can act on", () => {
  assert.match(replayErrorMessage("replay_target_config_unsupported"), /configuration/);
  assert.match(replayErrorMessage("replay_worktree_dirty"), /uncommitted changes/);
  assert.equal(replayErrorMessage("unknown_code"), "unknown_code");
});
