import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  loadDraftModule, previewDraftModule, saveDraftModule,
} from "../src/configure/api.ts";
import { ModuleEditor, shellQuote } from "../src/configure/ModuleEditor.tsx";

test("module editor keeps draft truth and explicit checkpoint language visible", () => {
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(ModuleEditor, {
    draft: "tuning", revision: "abcdef1234567890", onRevision: () => {},
  })));
  assert.match(html, /Edit module text/);
  assert.match(html, /Nothing applied/);
  assert.match(html, /Save or Ctrl\/Cmd-S creates a checkpoint/);
  assert.match(html, /Draft-owned module/);
});

test("copied module CLI arguments are shell quoted", () => {
  assert.equal(shellQuote("plain value"), "'plain value'");
  assert.equal(shellQuote("rule'; touch nope"), "'rule'\"'\"'; touch nope'");
});

test("module API uses server identities, CSRF, source guards, and one durable save key", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf-test" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    calls.push({ input: path, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    return new Response(JSON.stringify(path.endsWith("/read") ? {
      status: "ready", message: "ready", draft: { name: "tuning", revision: "rev" },
      modules: [], module: null, content: "", source_digest: "", nothing_applied: true,
      error_code: "",
    } : path.endsWith("/save") ? {
      valid: true, error: "", error_code: "", base_revision: "rev", source_digest: "digest",
      content_digest: "next", unchanged: false, module: null, diagnostics: [], budgets: [],
      projections: [], nothing_applied: true, saved: true, result: { revision: "next" }, saved_lint: [],
    } : {
      valid: true, error: "", error_code: "", base_revision: "rev", source_digest: "digest",
      content_digest: "next", unchanged: false, module: null, diagnostics: [], budgets: [],
      projections: [], nothing_applied: true,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await loadDraftModule("tuning", "root-1:rules:sample");
    await previewDraftModule("tuning", "root-1:rules:sample", "candidate");
    await saveDraftModule("tuning", "root-1:rules:sample", "rev", "digest", "one-key", "candidate");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls.map((call) => call.input), [
    "/api/configure/module/read", "/api/configure/module/preview", "/api/configure/module/save",
  ]);
  assert.deepEqual(calls[2].body, {
    draft: "tuning", module: "root-1:rules:sample", base_revision: "rev",
    source_digest: "digest", idempotency_key: "one-key", content: "candidate",
  });
  assert.equal("path" in calls[2].body, false);
});
