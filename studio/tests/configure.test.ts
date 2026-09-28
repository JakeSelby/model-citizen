import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { loadDraft, previewDraft } from "../src/configure/api.ts";
import { GovernedSyncReview } from "../src/configure/ConfigurePage.tsx";
import {
  AUTOSAVE_DELAY_MS, commandFor, errorMap, fieldEnabled, fieldsOf, hydrateValues,
  parseJsonObject, remainingChanges, type ConfigureSchema,
} from "../src/configure/model.ts";

const schema: ConfigureSchema = {
  schema_version: 1,
  commands: {
    schema: "citizen draft settings schema --json",
    read: "citizen draft settings read {draft} --json",
    preview: "citizen draft settings preview {draft} --changes changes.json --json",
    save: "citizen draft settings save {draft} --base-revision {revision} --idempotency-key KEY --changes changes.json --json",
  },
  sections: [{
    id: "identity",
    label: "Identity",
    description: "",
    fields: [{
      path: "identity.name",
      section: "identity",
      label: "Name",
      help: "",
      kind: "string",
      provenance: "test",
      required: true,
      options: [],
      reference_sources: [],
      constraints: {},
      default: "Default name",
    }],
  }],
};

test("governed sync starts with dry-run review and withholds the apply command", () => {
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(GovernedSyncReview)));
  assert.match(html, /id="governed-sync"/);
  assert.match(html, /citizen sync --dry-run/);
  assert.match(html, /I reviewed the dry-run output/);
  assert.doesNotMatch(html, /Apply reviewed sync/);
});

test("schema fields drive the form model without route-specific code", () => {
  assert.deepEqual(fieldsOf(schema).map((field) => field.path), ["identity.name"]);
  assert.deepEqual(hydrateValues(schema, {}), { "identity.name": "Default name" });
  assert.deepEqual(hydrateValues(schema, { "identity.name": "Operator" }), { "identity.name": "Operator" });
});

test("validation errors are associated with their described field", () => {
  assert.deepEqual(errorMap([{ path: "identity.name", message: "is required" }]), {
    "identity.name": "is required",
  });
});

test("JSON object fields refuse arrays and scalar values", () => {
  assert.deepEqual(parseJsonObject('{"enabled":true}'), { enabled: true });
  assert.throws(() => parseJsonObject("[]"), /JSON object/);
  assert.throws(() => parseJsonObject('"value"'), /JSON object/);
});

test("descriptor dependencies enable fields without component-specific rules", () => {
  const field = { ...schema.sections[0].fields[0], constraints: {
    depends_on: { path: "mode", equals: "custom" },
  } };
  assert.equal(fieldEnabled(field, { mode: "builtin" }), false);
  assert.equal(fieldEnabled(field, { mode: "custom" }), true);
});

test("mutating API calls acquire a session CSRF token and send same-origin JSON", async () => {
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
      valid: true, errors: [], warnings: [], changed: [], preview: {}, base_revision: "revision",
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await previewDraft("draft", { "identity.name": "Operator" });
  } finally {
    globalThis.fetch = original;
  }
  assert.equal(calls.length, 2);
  assert.equal(calls[1].input, "/api/configure/preview");
  assert.equal(calls[1].init?.credentials, "same-origin");
  assert.deepEqual(calls[1].init?.headers, {
    "Content-Type": "application/json",
    "X-Studio-CSRF": "csrf-test",
  });
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    draft: "draft", changes: { "identity.name": "Operator" },
  });
});

test("draft loading normalizes surrounding whitespace before requesting identity", async () => {
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
      status: "ready", message: "ready", draft: { name: "experiment", revision: "revision" },
      values: {}, warnings: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await loadDraft("  experiment  ");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { draft: "experiment" });
});

test("form autosave uses the architecture delay and preserves edits made during a save", () => {
  assert.equal(AUTOSAVE_DELAY_MS, 750);
  assert.deepEqual(
    remainingChanges(
      { "identity.name": "Newer", permissions: "auto", "telemetry.native": ["codex"] },
      { "identity.name": "Older", permissions: "auto", "telemetry.native": ["codex"] },
    ),
    { "identity.name": "Newer" },
  );
});

test("displayed CLI equivalents interpolate the active draft revision", () => {
  assert.equal(
    commandFor(schema.commands.save, "experiment", "revision-1"),
    "citizen draft settings save experiment --base-revision revision-1 --idempotency-key KEY --changes changes.json --json",
  );
});
