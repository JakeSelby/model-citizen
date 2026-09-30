import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { loadAuthoring, loadDraftLibrary, previewAuthoring, saveAuthoring } from "../src/configure/api.ts";
import {
  authoringCommand, canPreview, draftOwnedModules, EMPTY_FORM, nameProblem, needsRoot, toRequest,
  type AuthoringPreview, type AuthoringRead,
} from "../src/configure/authoringModel.ts";
import { AuthoringFiles, AuthoringStatus, ModuleAuthoring, RootOffer } from "../src/configure/ModuleAuthoring.tsx";
import { ForkProvenance, LibraryGroups } from "../src/library/LibraryPage.tsx";
import type { LibraryModule } from "../src/library/model.ts";

const READY: AuthoringRead = {
  status: "ready", message: "", draft: { name: "tuning", revision: "rev" }, root: null,
  offer: { label: "personal-primitives", registers: "<checkout>/personal-primitives" },
  templates: [{ kind: "rules", label: "Rule", name_hint: "lowercase-name", detail: "" }],
  forkable: [{ key: "core:rules:secrets", kind: "rules", name: "secrets" }],
  nothing_applied: true, error_code: "",
};

function module(key: string, core: boolean, fork: LibraryModule["fork"] = null): LibraryModule {
  const [root, kind, name] = key.split(":");
  return {
    key, name, kind, root: { id: root, label: core ? "Core" : "personal-primitives", path: "/r", core },
    state: { value: "on", layer: "default", switchable: true }, collision: false, manifest: null,
    source: { path: `/r/${name}.md`, text: "source" }, rendered: { text: "rendered" }, projections: [],
    context_cost: { tokens: 1, estimate: "soft estimate", method: "chars/4" }, fork,
  };
}

test("AC1: a draft without a personal root must accept the offer before a check runs", () => {
  const form = { ...EMPTY_FORM, name: "greeting", description: "Greets first." };
  assert.equal(needsRoot(READY), true);
  assert.equal(canPreview(form, READY), false);
  assert.equal(canPreview({ ...form, createRoot: true }, READY), true);
  assert.equal(needsRoot({ root: { id: "root-1", label: "personal-primitives" } }), false);
  assert.deepEqual(toRequest({ ...form, createRoot: true }), {
    action: "add", kind: "rules", name: "greeting", description: "Greets first.", create_root: true,
  });
  const offered = renderToStaticMarkup(h(MantineProvider, {}, h(RootOffer, {
    read: READY, checked: false, onChange: () => {},
  })));
  assert.match(offered, /This draft has no personal root/);
  assert.match(offered, /type="checkbox"/);
  assert.doesNotMatch(offered, /checked=""/);
  assert.match(offered, /personal-primitives/);
  assert.match(offered, /primitive_roots/);
  const withRoot = renderToStaticMarkup(h(MantineProvider, {}, h(RootOffer, {
    read: { ...READY, root: { id: "root-1", label: "personal-primitives" } }, checked: false, onChange: () => {},
  })));
  assert.doesNotMatch(withRoot, /personal root|checkbox/);
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(ModuleAuthoring, {
    draft: "tuning", revision: "rev", onRevision: () => {},
  })));
  assert.match(html, /Add or fork a module/);
});

test("names follow the resolver's identifier rule; a fork may leave its name to the server", () => {
  assert.equal(nameProblem({ ...EMPTY_FORM, name: "good-name" }), "");
  assert.notEqual(nameProblem({ ...EMPTY_FORM, name: "Bad_Name" }), "");
  assert.notEqual(nameProblem({ ...EMPTY_FORM, kind: "stances", name: "novariant" }), "");
  assert.equal(nameProblem({ ...EMPTY_FORM, kind: "stances", name: "tone/plain" }), "");
  assert.equal(nameProblem({ ...EMPTY_FORM, action: "fork", name: "" }), "");
  assert.deepEqual(toRequest({ ...EMPTY_FORM, action: "fork", source: "core:rules:secrets" }), {
    action: "fork", source: "core:rules:secrets", name: "", description: "", create_root: false,
  });
});

test("AC2: findings are announced in an always-mounted live region that never holds file text", () => {
  const base: AuthoringPreview = {
    valid: false, error: "Fix the manifest findings before saving.", error_code: "manifest-refused",
    base_revision: "rev", action: "add", module: { key: "root-1:rules:greeting", kind: "rules", name: "greeting" },
    root: { id: "root-1", label: "personal-primitives", created: true },
    files: [{ path: "rules/greeting.md", text: "# Greeting body text\n" }], manifest: null, fork: null,
    config_changes: [{ path: "primitive_roots", value: "+ <checkout>/personal-primitives" }],
    findings: ["module manifest: rules/greeting conflicts with rules/secrets; switch one of them off"],
    nothing_applied: true,
  };
  const idle = renderToStaticMarkup(h(MantineProvider, {}, h(AuthoringStatus, { message: "", preview: null })));
  assert.match(idle, /<div aria-live="polite" class="authoring-status" role="status"><\/div>/);
  const refused = renderToStaticMarkup(h(MantineProvider, {}, h(AuthoringStatus, { message: "Nothing was saved.", preview: base })));
  assert.match(refused, /aria-live="polite"/);
  assert.match(refused, /manifest checks refused this module/);
  assert.match(refused, /conflicts with rules\/secrets/);
  assert.doesNotMatch(refused, /Greeting body text/);
  const clean = { ...base, valid: true, error: "", error_code: "", findings: [] };
  const verdict = renderToStaticMarkup(h(MantineProvider, {}, h(AuthoringStatus, { message: "", preview: clean })));
  assert.match(verdict, /Manifest checks pass/);
  assert.match(verdict, /registers the new personal root/);
  assert.doesNotMatch(verdict, /Greeting body text/);
  const files = renderToStaticMarkup(h(MantineProvider, {}, h(AuthoringFiles, { preview: clean })));
  assert.doesNotMatch(files, /aria-live/);
  assert.match(files, /personal-primitives\/rules\/greeting\.md/);
  assert.match(files, /Greeting body text/);
  assert.match(files, /primitive_roots/);
});

test("AC3 and AC4: the library shows a fork's source, version and upstream diff", () => {
  const fork = { source: "rules/secrets", version: "0.18.0", revision: "abcdef1234567890",
    upstream: { changed: true, missing: false, original_available: true, diff: "--- forked\n+++ core\n+An upstream line.\n" } };
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(LibraryGroups, {
    modules: [module("root-1:rules:secrets-fork", false, fork)],
  })));
  assert.match(html, /Fork of rules\/secrets/);
  assert.match(html, /Upstream changed/);
  assert.match(html, /at 0\.18\.0 \(abcdef123456\)/);
  assert.match(html, /\+An upstream line\./);
  const unchanged = renderToStaticMarkup(h(MantineProvider, {}, h(ForkProvenance, {
    fork: { ...fork, upstream: { changed: false, missing: false, original_available: true, diff: "" } },
  })));
  assert.match(unchanged, /unchanged since this fork/);
  const unavailable = renderToStaticMarkup(h(MantineProvider, {}, h(ForkProvenance, {
    fork: { ...fork, upstream: { changed: true, missing: false, original_available: false, diff: "" } },
  })));
  assert.match(unavailable, /not in this checkout/);
  const gone = renderToStaticMarkup(h(MantineProvider, {}, h(ForkProvenance, {
    fork: { ...fork, upstream: { changed: true, missing: true, original_available: false, diff: "" } },
  })));
  assert.match(gone, /no longer installed/);
  assert.doesNotMatch(gone, /the diff shows what it was/);
  assert.match(gone, /no diff is shown/);
  const owned = draftOwnedModules([module("core:rules:secrets", true), module("root-1:rules:mine", false),
    module("root-1:rules:secrets-fork", false, fork)]);
  assert.deepEqual(owned.map((item) => item.key), ["root-1:rules:secrets-fork", "root-1:rules:mine"]);
});

test("authoring API posts with CSRF to the draft routes, and the CLI command is quoted", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "csrf-test" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    assert.equal((init?.headers as Record<string, string>)["X-Studio-CSRF"], "csrf-test");
    calls.push({ input: path, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    return new Response(JSON.stringify({ modules: [] }), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  const request = toRequest({ ...EMPTY_FORM, name: "greeting", description: "d", createRoot: true });
  try {
    await loadAuthoring("tuning");
    await previewAuthoring("tuning", request);
    await saveAuthoring("tuning", "rev", "one-key", request);
    await loadDraftLibrary("tuning");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls.map((call) => call.input), [
    "/api/configure/authoring/read", "/api/configure/authoring/preview",
    "/api/configure/authoring/save", "/api/configure/authoring/library",
  ]);
  assert.deepEqual(calls[2].body, { draft: "tuning", base_revision: "rev", idempotency_key: "one-key", request });
  assert.equal(authoringCommand("a'b", "rev", true),
    "citizen draft module add 'a'\"'\"'b' --request request.json --base-revision 'rev' --idempotency-key KEY --json");
});
