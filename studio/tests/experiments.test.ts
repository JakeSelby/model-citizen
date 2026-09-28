import assert from "node:assert/strict";
import test from "node:test";

import { cancelRun, startRun, streamRun } from "../src/experiments/api.ts";
import {
  commandFor, mergeUpdate, parseEventStream, scopeOptions, shellQuote, streamComplete, terminal, type FreeSuite, type RunCatalog,
} from "../src/experiments/model.ts";

const suite: FreeSuite = {
  id: "unit-tests", label: "Unit tests", description: "", cost_class: "free",
  expected_duration_seconds: 10, case_count: 1, parameters: { root: "/repo", case: "all" },
  command: "", command_argv: ["citizen", "runs", "start", "unit-tests", "--param", "case=all", "--json"],
};

test("selected test commands remain copyable and quote one argv element", () => {
  assert.equal(shellQuote("plain/path"), "plain/path");
  assert.equal(shellQuote("test with space"), "'test with space'");
  assert.equal(commandFor(suite, "test_module.Class.test_one"),
    "citizen runs start unit-tests --param case=test_module.Class.test_one --json");
});

test("discovery scopes list all, module, class and individual tests", () => {
  const catalog = { unit_tests: {
    cases: [{ id: "test_a.C.test_one", module: "test_a", class_name: "C", test_name: "test_one", label: "test one" }],
    scopes: { all: ["test_a.C.test_one"], test_a: ["test_a.C.test_one"], "test_a.C": ["test_a.C.test_one"], "test_a.C.test_one": ["test_a.C.test_one"] },
  } } as RunCatalog;
  assert.deepEqual(scopeOptions(catalog).map((item) => item.value),
    ["all", "test_a", "test_a.C", "test_a.C.test_one"]);
});

test("SSE data preserves semantic progress, traceback and lint links", () => {
  const update = { run: { status: "failed" }, progress: { completed: 1, eligible: 1,
    cases: [{ id: "test_a.C.test_one", status: "failed", detail: "Traceback" }],
    lint_findings: [{ path: "rules/a.md", line: 3, library_href: "/library?path=rules/a.md&line=3" }] } };
  assert.deepEqual(parseEventStream(`event: run\ndata: ${JSON.stringify(update)}\n\n`)[0], update);
  assert.equal(terminal("failed"), true);
  assert.equal(terminal("running"), false);
});

test("terminal streams keep polling until both bounded logs reach EOF", () => {
  const terminalWithMore = { run: { status: "succeeded" }, stdout: { eof: false },
    stderr: { eof: true } } as never;
  assert.equal(streamComplete(terminalWithMore), false);
  assert.equal(streamComplete({ ...terminalWithMore, stdout: { eof: true } } as never), true);
});

test("a fast terminal run still drains more than one 64 KiB output chunk", () => {
  const output = "x".repeat(70 * 1024);
  const updates = [
    { run: { status: "succeeded" }, stdout: { chunk: output.slice(0, 64 * 1024), eof: false }, stderr: { eof: true } },
    { run: { status: "succeeded" }, stdout: { chunk: output.slice(64 * 1024), eof: true }, stderr: { eof: true } },
  ] as never[];
  let received = "";
  for (const update of updates) {
    received += update.stdout.chunk;
    if (streamComplete(update)) break;
  }
  assert.equal(received, output);
});

test("stream chunks merge prior case results instead of losing progress", () => {
  const base = { run: { status: "running" }, stdout: {}, stderr: {}, progress: {
    completed: 1, eligible: 2, cases: [{ id: "one", status: "passed", detail: "" }], lint_findings: [],
  } } as never;
  const next = { run: { status: "succeeded" }, stdout: {}, stderr: {}, progress: {
    completed: 1, eligible: 2, cases: [{ id: "two", status: "passed", detail: "" }], lint_findings: [],
  } } as never;
  assert.deepEqual(mergeUpdate(base, next).progress.cases.map((item) => item.id), ["one", "two"]);
  const check = { ...next, progress: { ...next.progress, completed: 1, eligible: 1, cases: [] } } as never;
  assert.equal(mergeUpdate(base, check).progress.completed, 1);
});

test("run mutations acquire CSRF and send fixed same-origin routes", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ input: String(input), init });
    if (String(input) === "/api/session") return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
    if (String(input) === "/api/runs/stream") return new Response("event: run\ndata: {}\n\n", { status: 200 });
    return new Response(JSON.stringify({ run_id: "run", case_identities: [] }), { status: 200 });
  }) as typeof fetch;
  try {
    await startRun("unit-tests", "/repo", "test_a.C.test_one");
    await cancelRun("run");
    await streamRun("run", 4, 8);
  } finally { globalThis.fetch = original; }
  assert.deepEqual(calls.filter((call) => call.input.startsWith("/api/runs/")).map((call) => call.input),
    ["/api/runs/start", "/api/runs/cancel", "/api/runs/stream"]);
  for (const call of calls.filter((item) => item.input.startsWith("/api/runs/"))) {
    assert.equal(call.init?.credentials, "same-origin");
    assert.deepEqual(call.init?.headers, { "Content-Type": "application/json", "X-Studio-CSRF": "csrf" });
  }
});
