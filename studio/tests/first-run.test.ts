import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { loadFirstRun, startFirstRun } from "../src/firstrun/api.ts";
import { FirstRunBanner, FirstRunEntryView } from "../src/firstrun/FirstRunEntry.tsx";
import { afterApply, beginSetup, loadStatus, saveIdentity } from "../src/firstrun/flow.ts";
import { DoneSummary, GuideProgress, ReproduceCommands } from "../src/firstrun/FirstRunPage.tsx";
import {
  claimGuideOpen, doctorPassed, entryCopy, errorMessage, liveChangeNotice, resetGuideClaim, nextStep, opensGuide, resumeStep, stepReachable,
  type FirstRunStatus,
} from "../src/firstrun/model.ts";
import { documentTitle, pageTitle } from "../src/navigation.ts";

const STEPS: FirstRunStatus["steps"] = [
  { id: "health", label: "Check the install", command: "citizen doctor" },
  { id: "draft", label: "Start a draft", command: "citizen draft create first-run --json" },
  { id: "identity", label: "Say who you are", command: "citizen draft settings save first-run" },
  { id: "preferences", label: "Pick a stance for each dimension", command: "citizen draft selection save first-run" },
  { id: "check", label: "Run a free check", command: "citizen lint" },
  { id: "apply", label: "Review and apply", command: "citizen draft review first-run --json" },
  { id: "done", label: "Confirm the doctor checks", command: "citizen doctor" },
];

const FRESH: FirstRunStatus = {
  schema_version: 1,
  state: "not-started",
  fresh: true,
  nothing_live_changed: true,
  draft_name: "first-run",
  draft: {},
  applied: {},
  interrupted: {},
  blocked_by: {},
  steps: STEPS,
  choices: [],
  commands: {
    headless: ["citizen sync", "citizen doctor"],
    agent: ["citizen draft create first-run --json"],
    status: "citizen draft first-run --json",
  },
};

const KEPT: FirstRunStatus = {
  ...FRESH,
  state: "in-progress",
  draft: { name: "first-run", revision: "rev-2", base_revision: "rev-1" },
  choices: [{ key: "stances.voice", action: "set", value: "concise", command: "citizen config set stances.voice concise" }],
  commands: { ...FRESH.commands, headless: ["citizen config set stances.voice concise", "citizen sync", "citizen doctor"] },
};

const DONE: FirstRunStatus = {
  ...KEPT,
  state: "complete",
  fresh: false,
  nothing_live_changed: false,
  applied: { apply_id: "a1", ts: "2026-10-02T00:00:00Z", revision: "0123456789abcdef", doctor: "passed" },
};

function render(element: ReactElement): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, element)));
}

test("a fresh install opens the guide once, and no other state does", () => {
  assert.equal(opensGuide(FRESH, false), true);
  assert.equal(opensGuide(FRESH, true), false);
  assert.equal(opensGuide({ ...FRESH, fresh: false }, false), false);
  assert.equal(opensGuide(KEPT, false), false);
  assert.equal(opensGuide(DONE, false), false);
  assert.equal(opensGuide(null, false), false);
});

test("an abandoned run resumes after its draft, an interrupted one at review, a finished one at done", () => {
  assert.equal(resumeStep(FRESH), "health");
  assert.equal(resumeStep(KEPT), "identity");
  assert.equal(resumeStep({ ...KEPT, state: "interrupted" }), "apply");
  assert.equal(resumeStep(DONE), "done");
  assert.equal(nextStep("health"), "draft");
  assert.equal(nextStep("done"), "done");
});

test("steps past the draft need the draft, and done needs an applied run", () => {
  assert.equal(stepReachable(FRESH, "health"), true);
  assert.equal(stepReachable(FRESH, "draft"), true);
  assert.equal(stepReachable(FRESH, "identity"), false);
  assert.equal(stepReachable(KEPT, "apply"), true);
  assert.equal(stepReachable(KEPT, "done"), false);
  assert.equal(stepReachable(DONE, "done"), true);
});

test("the Hub says nothing live changed and offers to resume a kept draft", () => {
  assert.equal(entryCopy(DONE), null);
  assert.equal(entryCopy(KEPT)?.action, "Resume setup");
  assert.equal(entryCopy(FRESH)?.action, "Start setup");
  assert.match(liveChangeNotice(KEPT), /Setup has changed nothing live\. Draft first-run is kept/);
  const html = render(h(FirstRunBanner, { status: KEPT }));
  assert.match(html, /Setup is waiting for you/);
  assert.match(html, /href="\/setup"/);
  assert.doesNotMatch(render(h(FirstRunBanner, { status: DONE })), /setup/i);
});

test("the progress marks the current step and disables steps the run cannot reach yet", () => {
  const html = render(h(GuideProgress, { status: FRESH, active: "health", onSelect: () => {} }));
  assert.match(html, /aria-current="step"/);
  assert.match(html, /1\. Check the install/);
  assert.equal((html.match(/disabled=""/g) ?? []).length, 5);
});

test("the CLI sequence reproduces the choices headless and names the agent loop", () => {
  const html = render(h(ReproduceCommands, { status: KEPT }));
  assert.match(html, /citizen config set stances\.voice concise/);
  assert.match(html, /citizen sync/);
  assert.match(html, /citizen draft create first-run --json/);
  assert.match(html, /citizen draft first-run --json/);
});

test("a finished run reports green doctor checks and links to the Hub", () => {
  assert.equal(doctorPassed(DONE), true);
  assert.equal(doctorPassed({ ...DONE, applied: { ...DONE.applied, doctor: "attention" } }), false);
  assert.equal(doctorPassed(KEPT), false);
  const html = render(h(DoneSummary, { status: DONE, overview: null }));
  assert.match(html, /Setup applied and the doctor checks passed/);
  assert.match(html, /0123456789ab/);
  assert.match(html, /href="\/"/);
});

test("the setup route has its own title", () => {
  assert.equal(pageTitle("/setup"), "First run");
  assert.equal(documentTitle("/setup"), "First run · Model Citizen Studio");
});

test("status and start post the draft name with the session's CSRF token", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; body: unknown }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "c" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    assert.equal((init?.headers as Record<string, string>)["X-Studio-CSRF"], "c");
    calls.push({ input: String(input), body: JSON.parse(String(init?.body)) });
    if (String(input) === "/api/first-run/start") {
      return new Response(JSON.stringify({ error: "busy" }), { status: 409, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(FRESH), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    assert.equal((await loadFirstRun("first-run")).state, "not-started");
    await assert.rejects(startFirstRun("first-run"), /busy/);
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [
    { input: "/api/first-run", body: { draft: "first-run" } },
    { input: "/api/first-run/start", body: { draft: "first-run" } },
  ]);
});

type Call = { input: string; body: Record<string, unknown> };

async function withFetch(
  answer: (path: string, body: Record<string, unknown>, calls: Call[]) => { status?: number; body: unknown },
  run: (calls: Call[]) => Promise<void>,
): Promise<void> {
  const original = globalThis.fetch;
  const calls: Call[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "c" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    calls.push({ input: path, body });
    const reply = answer(path, body, calls);
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    globalThis.fetch = original;
  }
}

const VALID = { valid: true, errors: [], warnings: [], changed: ["identity.name"], preview: {}, base_revision: "rev-2" };
const STALE = { ...VALID, valid: false, saved: false, result: null,
  errors: [{ path: "draft", message: "draft revision changed; reload before saving" }] };

test("a configured home that never started setup gets no banner and no redirect", () => {
  const configured = { ...FRESH, fresh: false };
  assert.equal(entryCopy(configured), null);
  resetGuideClaim();
  assert.equal(claimGuideOpen(configured), false);
  assert.doesNotMatch(render(h(FirstRunEntryView, { status: configured, opened: false })), /setup/i);
});

test("the guide opens once per page load, then the Hub shows its banner", () => {
  resetGuideClaim();
  assert.equal(claimGuideOpen(FRESH), true);
  assert.equal(claimGuideOpen(FRESH), false);
  assert.match(render(h(FirstRunEntryView, { status: FRESH, opened: false })), /Start setup/);
  assert.doesNotMatch(render(h(FirstRunEntryView, { status: FRESH, opened: true })), /Start setup/);
  resetGuideClaim();
});

test("identity saves once on a current revision", async () => {
  await withFetch((path) => path === "/api/configure/preview"
    ? { body: VALID } : { body: { ...VALID, saved: true, result: { revision: "rev-3" } } }, async (calls) => {
    const outcome = await saveIdentity("first-run", "rev-2", { "identity.name": "Casey" });
    assert.deepEqual([outcome.saved, outcome.revision], [true, "rev-3"]);
    assert.deepEqual(calls.map((call) => call.input), ["/api/configure/preview", "/api/configure/save"]);
  });
});

test("a stale revision reloads the latest one and retries the save once", async () => {
  await withFetch((path, body) => {
    if (path === "/api/configure/preview") return { body: VALID };
    if (path === "/api/configure/read") return { body: { status: "ready", message: "", draft: { name: "first-run", revision: "rev-9" }, values: {}, warnings: [] } };
    return body.base_revision === "rev-9"
      ? { body: { ...VALID, saved: true, result: { revision: "rev-10" } } } : { body: STALE };
  }, async (calls) => {
    const outcome = await saveIdentity("first-run", "rev-2", { "identity.name": "Casey" });
    assert.deepEqual([outcome.saved, outcome.revision], [true, "rev-10"]);
    assert.deepEqual(calls.filter((call) => call.input === "/api/configure/save").map((call) => call.body.base_revision), ["rev-2", "rev-9"]);
  });
});

test("a second stale refusal stops and says so plainly, keeping the latest revision", async () => {
  await withFetch((path) => {
    if (path === "/api/configure/preview") return { body: VALID };
    if (path === "/api/configure/read") return { body: { status: "ready", message: "", draft: { revision: "rev-9" }, values: {}, warnings: [] } };
    return { body: STALE };
  }, async (calls) => {
    const outcome = await saveIdentity("first-run", "rev-2", { "identity.name": "Casey" });
    assert.equal(outcome.saved, false);
    assert.equal(outcome.revision, "rev-9");
    assert.match(outcome.message, /changed elsewhere/);
    assert.equal(calls.filter((call) => call.input === "/api/configure/save").length, 2);
  });
});

test("invalid identity fields come back per field and nothing is saved", async () => {
  await withFetch(() => ({ body: { ...VALID, valid: false, errors: [{ path: "identity.name", message: "required" }] } }), async (calls) => {
    const outcome = await saveIdentity("first-run", "rev-2", { "identity.name": "" });
    assert.equal(outcome.saved, false);
    assert.deepEqual(outcome.saved ? {} : outcome.errors, { "identity.name": "required" });
    assert.equal(calls.length, 1);
  });
});

test("start turns a route error code into a sentence the user can act on", async () => {
  await withFetch(() => ({ status: 409, body: { error: "create-timeout" } }), async () => {
    await assert.rejects(beginSetup("first-run"), /took too long.*start again/);
  });
  assert.match(errorMessage(new Error("something-new")), /Setup stopped: something-new/);
});

test("a busy status is retried, and a run left busy is reported", async () => {
  let tries = 0;
  await withFetch(() => (++tries < 3 ? { status: 429, body: { error: "first_run_busy" } } : { body: KEPT }), async () => {
    assert.equal((await loadStatus("first-run", 0)).state, "in-progress");
  });
  await withFetch(() => ({ status: 429, body: { error: "first_run_busy" } }), async (calls) => {
    await assert.rejects(loadStatus("first-run", 0), /busy saving a checkpoint/);
    assert.equal(calls.length, 3);
  });
});

test("after an apply the guide moves to done, or stays at review when it did not finish", async () => {
  await withFetch(() => ({ body: DONE }), async () => {
    assert.equal((await afterApply("first-run", 0)).step, "done");
  });
  await withFetch(() => ({ body: { ...KEPT, state: "interrupted" } }), async () => {
    assert.equal((await afterApply("first-run", 0)).step, "apply");
  });
});
