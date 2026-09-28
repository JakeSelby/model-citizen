import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";

import {
  loadNativeProgress, previewNativeRun, retryFailedCase, startNativeRun,
} from "../src/experiments/native-acceptance/api.ts";
import { NativeAcceptancePanel } from "../src/experiments/native-acceptance/NativeAcceptancePanel.tsx";
import {
  completion, mayBeginRequest, nextPreviewGeneration, phaseAfterEdit,
  previewGenerationIsCurrent, retryable, selectionAfterStart, selectionForResume,
  selectionReady, stateAfterStart,
  spendReady, type NativeCatalog, type NativeSelection, type NativeSnapshot, type SpendPreview,
  type SpendRequest,
} from "../src/experiments/native-acceptance/model.ts";

const catalog: NativeCatalog = {
  schema_version: 1,
  default_model: "claude-haiku-4-5",
  commands: { run: "run", resume: "resume", retry_failed: "retry" },
  clients: [
    { id: "claude", runtime: "claude-code", platform: "macos", observed: true, spend_cap_supported: true, unavailable_reason: null },
    { id: "codex", runtime: "codex", platform: "macos", observed: false, spend_cap_supported: false, unavailable_reason: "Codex has no in-flight dollar cap; paid Studio launch is refused." },
  ],
  cases: [
    { id: "installation", description: "Install and inspect the native client." },
    { id: "stance-switch", description: "Switch stances in the native client." },
    { id: "cost-posture", description: "Observe routed spend." },
  ],
  target: { kind: "installed", ref: "current", source_commit: null },
  initial: {
    client: "claude", cases: ["installation", "stance-switch", "cost-posture"],
    model: "claude-haiku-4-5", source_commit: "", progress_id: "run-123",
    target_kind: "installed", target_ref: "current",
  },
};

const selection: NativeSelection = {
  client: "claude",
  cases: ["installation", "stance-switch", "cost-posture"],
  model: "claude-haiku-4-5",
  source_commit: "a".repeat(40),
  progress_id: "run-123",
  target_kind: "installed",
  target_ref: "current",
};

const snapshot: NativeSnapshot = {
  schema_version: 1,
  selection,
  settled_count: 2,
  eligible_count: 3,
  interrupted: true,
  warnings: [],
  resume_cases: ["cost-posture"],
  command: "python3 scripts/native_acceptance.py --cases installation,stance-switch,cost-posture",
  cases: [
    { id: "installation", status: "passed", observation: "installed", seconds: 1, sessions: 1, spend_usd: 0.02, price_as_of: null },
    { id: "stance-switch", status: "failed", observation: "wrong stance", seconds: 2, sessions: 1, spend_usd: 0.03, price_as_of: null },
    { id: "cost-posture", status: "unverified", observation: "no transcript", seconds: 3, sessions: 1, spend_usd: null, price_as_of: null },
  ],
};

const spend: SpendRequest = {
  max_budget_usd: "0.20", spend_cap_usd: "1.00", pricing_source: "api_credit",
};

const preview: SpendPreview = {
  estimate: { amount_usd: null, basis: "no_history", sample_count: 0, suite_id: "native-acceptance", case_count: 3 },
  caps: { max_budget_usd: "0.20", spend_cap_usd: "1.00" },
  pricing: { source: "api_credit", basis: "money charged to API credit" },
  confirmation_required: true,
  confirmation_token: "f".repeat(64),
  case_identities: selection.cases,
};

const render = (element: ReturnType<typeof h>) => renderToStaticMarkup(h(MantineProvider, {}, element));

test("three picked cases are valid only for a client with bounded spend", () => {
  assert.equal(selectionReady(catalog, selection), true);
  assert.equal(selectionReady(catalog, { ...selection, source_commit: "" }), true);
  assert.equal(selectionReady(catalog, { ...selection, client: "codex" }), false);
  assert.equal(selectionReady(catalog, { ...selection, cases: ["installation", "installation"] }), false);
  assert.equal(selectionReady(catalog, { ...selection, cases: ["installation"] }), false);
  assert.equal(selectionReady(catalog, {
    ...selection, cases: ["installation", "stance-switch", "cost-posture", "extra"],
  }), false);
  assert.equal(selectionReady(catalog, {
    ...selection, cases: ["installation"], progress_id: "retry-fresh",
    retry_source: "run-123", retry_case: "installation",
  }), true);
  assert.equal(selectionReady(catalog, {
    ...selection, cases: ["installation"], retry_source: "", retry_case: "",
  }), false);
  assert.equal(selectionReady(catalog, { ...selection, source_commit: "short" }), false);
  assert.equal(selectionReady(catalog, { ...selection, target_ref: "" }), false);
  assert.equal(spendReady(spend), true);
  assert.equal(spendReady({ ...spend, max_budget_usd: "1.01", spend_cap_usd: "1.00" }), false);
  assert.equal(spendReady({ ...spend, spend_cap_usd: "unknown" }), false);
});

test("resume keeps settled verdicts while failed retry remains explicit", () => {
  assert.equal(completion(snapshot), 67);
  assert.deepEqual(snapshot.resume_cases, ["cost-posture"]);
  assert.deepEqual(retryable(snapshot), ["stance-switch"]);
});

test("editing inputs while preview is in flight invalidates the stale response generation", async () => {
  let generation = 0;
  const previewTicket = nextPreviewGeneration(generation);
  generation = previewTicket;
  let release: (value: SpendPreview) => void = () => {};
  const inFlight = new Promise<SpendPreview>((resolve) => { release = resolve; });

  generation = nextPreviewGeneration(generation); // the operator edits a cap or selected case
  release(preview);
  await inFlight;

  assert.equal(previewGenerationIsCurrent(previewTicket, generation), false);
});

test("confirmed start stays exclusive and adopts the server-resolved target", () => {
  assert.equal(phaseAfterEdit("starting"), "starting");
  assert.equal(mayBeginRequest("starting"), false);
  const resolved = {
    ...selection, source_commit: "b".repeat(40), target_kind: "branch" as const,
    target_ref: "b".repeat(40),
  };
  assert.deepEqual(selectionAfterStart({
    run_id: "native-run", status: "queued", selection: resolved,
    target: { kind: "branch", ref: resolved.target_ref,
              source_commit: resolved.source_commit, version: "0.18.0" },
  }), resolved);
  const started = stateAfterStart({
    run_id: "native-run", status: "queued", selection: resolved,
    target: { kind: "branch", ref: resolved.target_ref,
              source_commit: resolved.source_commit, version: "0.18.0" },
  });
  assert.equal(started.snapshot, null);
  assert.deepEqual(started.selection, resolved);
  assert.deepEqual(selectionForResume({ ...snapshot, selection: resolved }), resolved);
});

test("the panel renders literal verdicts, evidence and interruption without color-only meaning", () => {
  const html = render(h(NativeAcceptancePanel, {
    catalog, initial: selection, initialSpend: spend, initialPreview: preview, snapshot,
    onResume() {}, onRetryFailed() {},
  }));
  for (const text of ["passed", "failed", "unverified", "installed", "wrong stance", "interrupted · evidence kept", "Retry with fresh log"]) {
    assert.ok(html.includes(text), text);
  }
  assert.match(html, /2 of 3 cases have reusable verdicts/);
  assert.match(html, /aria-label="Settled native acceptance cases"/);
  for (const text of ["Target kind", "Target reference", "Maximum per turn (USD)", "Whole-set cap (USD)", "Pricing basis", "Spend preview", "No comparable completed run", "3 selected cases", "Confirm and launch"]) {
    assert.ok(html.includes(text), text);
  }
});

test("Codex limitation is visible beside the selected client", () => {
  const html = render(h(NativeAcceptancePanel, {
    catalog, initial: { ...selection, client: "codex" }, snapshot: null,
    onResume() {}, onRetryFailed() {},
  }));
  assert.ok(html.includes("Launch refused"));
  assert.ok(html.includes("no in-flight dollar cap"));
});

test("three selected cases flow through preview and confirmed start with same-origin CSRF", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf-test" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    let response: unknown = snapshot;
    if (String(input).endsWith("/preview")) response = preview;
    if (String(input).endsWith("/start")) response = {
      run_id: "native-run", status: "queued", selection,
      target: { kind: "installed", ref: "current", source_commit: selection.source_commit,
                version: "0.18.0" },
    };
    if (String(input).endsWith("/retry")) {
      response = { ...selection, cases: ["stance-switch"], progress_id: "fresh" };
    }
    return new Response(JSON.stringify(response), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    await loadNativeProgress(selection);
    assert.deepEqual(await previewNativeRun(selection, spend), preview);
    assert.deepEqual(await startNativeRun(selection, spend, preview.confirmation_token), {
      run_id: "native-run", status: "queued", selection,
      target: { kind: "installed", ref: "current", source_commit: selection.source_commit,
                version: "0.18.0" },
    });
    await retryFailedCase(selection, "stance-switch");
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls.length, 8);
  for (const call of [calls[1], calls[3], calls[5], calls[7]]) {
    assert.equal(call.init?.credentials, "same-origin");
    assert.deepEqual(call.init?.headers, {
      "Content-Type": "application/json", "X-Studio-CSRF": "csrf-test",
    });
  }
  assert.deepEqual(JSON.parse(String(calls[3].init?.body)), { selection, spend });
  assert.deepEqual(JSON.parse(String(calls[5].init?.body)), {
    selection, spend, confirmation_token: preview.confirmation_token,
  });
  assert.deepEqual(JSON.parse(String(calls[7].init?.body)), {
    selection, case: "stance-switch",
  });
});
