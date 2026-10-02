import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { ActivityTimeline } from "../src/activity/ActivityPage.tsx";
import { previewRollback, rollBack } from "../src/activity/api.ts";
import type { ActivityEntry, ActivityPage } from "../src/activity/model.ts";
import { RollbackControls, RollbackDetails, RollbackOutcome, RollbackPanel } from "../src/activity/Rollback.tsx";
import {
  focusedEntry, isFocused, missingFocus, rollbackBlocker, rollbackHeadline, rollbackTarget, shownValue,
  type RollbackPreview, type RollbackResult,
} from "../src/activity/rollbackModel.ts";

const APPLY_ID = "0123456789abcdef0123456789abcdef";

const APPLY: ActivityEntry = {
  id: `studio:${APPLY_ID}@120`, timestamp: "2026-10-02T12:00:00Z", source: "studio-action", kind: "apply",
  title: "Draft applied", outcome: "completed", reason: "Applied draft tuning through the governed path.",
  actor: "Studio", session: "", repository: "", hook: "", grade: "unknown",
  command: "citizen draft apply tuning --revision r --json", draft: "tuning",
  files: ["personal-primitives/rules/greeting.md"], evidence_href: "", evidence_label: "",
  apply_id: APPLY_ID, rollback_target: APPLY_ID,
};

const ROLLBACK: ActivityEntry = {
  ...APPLY, id: `studio:${"f".repeat(32)}@240`, kind: "rollback", title: "Apply rolled back",
  reason: `Rolled back the apply ${APPLY_ID.slice(0, 12)} of draft tuning.`,
  evidence_href: `/activity?apply=${APPLY_ID}`, evidence_label: "Open the change this rolled back",
  apply_id: "f".repeat(32), rollback_target: "f".repeat(32),
};

const PREVIEW: RollbackPreview = {
  schema_version: 1,
  apply: { apply_id: APPLY_ID, kind: "apply", status: "completed", draft: "tuning", revision: "r", ts: "t", actor: "Studio", reverses: "" },
  destination: "/h/.config/agent-harness/personal-primitives",
  config: [
    { key: "primitive_roots", action: "set", current: ["/h/p"], current_present: true, restored: [], restored_present: true },
    { key: "rules.cache-hygiene", action: "unset", current: "off", current_present: true, restored: null, restored_present: false },
    { key: "mode", action: "none", current: "a", current_present: true, restored: "a", restored_present: true },
  ],
  files: [{ path: "rules/greeting.md", action: "delete" }],
  commands: [
    { step: "rollback", command: `citizen draft rollback ${APPLY_ID} --draft tuning --json` },
    { step: "sync", command: "citizen sync" },
    { step: "check", command: "citizen doctor" },
  ],
  refusals: [],
  can_rollback: true,
  rollback_command: `citizen draft rollback ${APPLY_ID} --draft tuning --json`,
  nothing_changed: true,
};

function result(overrides: Partial<RollbackResult>): RollbackResult {
  return {
    schema_version: 1, status: "rolled-back", applied: false, error_code: "", message: "Rolled back.",
    holder: "", apply_id: "e".repeat(32), review: PREVIEW, doctor: { status: "passed", checks: [] },
    restored: true, log: [], ...overrides,
  };
}

function render(node: ReturnType<typeof h>, path = "/activity"): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, { initialEntries: [path] }, node)));
}

test("the engine's rollback target decides which entries offer a rollback", () => {
  assert.equal(rollbackTarget(APPLY), APPLY_ID);
  assert.equal(rollbackTarget(ROLLBACK), "f".repeat(32));
  assert.equal(rollbackTarget({ ...APPLY, outcome: "failed", rollback_target: "" }), "");
  assert.equal(rollbackTarget({ ...APPLY, id: "studio:anything", rollback_target: APPLY_ID }), APPLY_ID);
});

test("a link focuses only a well-formed Studio entry", () => {
  assert.equal(focusedEntry(`?apply=${APPLY_ID}`), APPLY_ID);
  assert.equal(focusedEntry("?apply=decision%3Aone"), "");
  assert.equal(focusedEntry(""), "");
  assert.equal(isFocused(APPLY, APPLY_ID), true);
  assert.equal(isFocused(ROLLBACK, APPLY_ID), false);
  assert.equal(isFocused({ ...APPLY, apply_id: "" }, ""), false);
  assert.equal(missingFocus([APPLY], APPLY_ID, true), "");
  assert.match(missingFocus([ROLLBACK], APPLY_ID, true), /not in the activity loaded so far\. Load older activity/);
  assert.match(missingFocus([ROLLBACK], APPLY_ID, false), /not in the activity log/);
  assert.equal(missingFocus([ROLLBACK], "", true), "");
});

test("rolling back needs a clean preview and the applied draft typed back", () => {
  assert.equal(rollbackBlocker(null, "tuning"), "Preview the rollback first.");
  assert.match(rollbackBlocker({ ...PREVIEW, can_rollback: false }, "tuning"), /refused/);
  assert.equal(rollbackBlocker(PREVIEW, "tun"), "Type tuning to confirm.");
  assert.equal(rollbackBlocker(PREVIEW, "tuning"), "");
  assert.equal(shownValue(false, null), "unset");
  assert.equal(shownValue(true, []), "[]");
});

test("headlines say what changed and what did not", () => {
  assert.equal(rollbackHeadline(result({})), "Rolled back. The doctor checks ran.");
  assert.match(rollbackHeadline(result({ status: "refused", error_code: "busy", holder: "citizen sync (pid 1)" })),
    /citizen sync \(pid 1\) holds the sync lock\. Nothing changed\./);
  assert.match(rollbackHeadline(result({ status: "failed", restored: true })), /failed safely/);
  assert.match(rollbackHeadline(result({ status: "failed", restored: false })), /citizen draft recover/);
  assert.equal(rollbackHeadline(result({ status: "refused", error_code: "later-apply" })), "Not rolled back. Nothing changed.");
});

test("the preview shows the reverse diff, refusals and the exact commands", () => {
  const html = render(h(RollbackDetails, { preview: PREVIEW }));
  assert.match(html, /primitive_roots/);
  assert.match(html, /off → unset/);
  assert.doesNotMatch(html, /<code[^>]*>mode<\/code>/);
  assert.match(html, /rules\/greeting\.md<\/code> removed/);
  assert.match(html, /citizen draft rollback 0123456789abcdef0123456789abcdef --draft tuning --json/);
  const refused = render(h(RollbackDetails, { preview: { ...PREVIEW, can_rollback: false, refusals: [
    { code: "later-apply", message: "the apply 9f8e7d6c5b4a of draft other changed rules.cache-hygiene" }] } }));
  assert.match(refused, /Rollback is refused; nothing will change/);
  assert.match(refused, /later-apply/);
  assert.match(refused, /draft other/);
});

test("the button stays blocked and says why until the draft is typed back", () => {
  const blocked = render(h(RollbackControls, { preview: PREVIEW, confirmation: "", busy: false, describedBy: "why",
    onConfirm: () => undefined, onRollBack: () => undefined }));
  assert.match(blocked, /aria-disabled="true"/);
  assert.match(blocked, /Type tuning to confirm\./);
  const ready = render(h(RollbackControls, { preview: PREVIEW, confirmation: "tuning", busy: false, describedBy: "why",
    onConfirm: () => undefined, onRollBack: () => undefined }));
  assert.doesNotMatch(ready, /aria-disabled="true"/);
  assert.match(ready, /Roll back tuning/);
});

test("an outcome names the result", () => {
  assert.match(render(h(RollbackOutcome, { result: result({ status: "failed", restored: true, message: "sync refused" }) })),
    /failed safely[\s\S]*sync refused/);
});

test("the timeline offers rollback on an apply and links a rollback to the apply it reversed", () => {
  const page: ActivityPage = {
    schema_version: 1, entries: [ROLLBACK, APPLY], next_cursor: "", next_command: "", sources: [],
    filters: { session: "", repository: "", hook: "", outcome: "" }, command: "citizen activity --json",
  };
  const html = render(h(ActivityTimeline, { payload: page, focus: APPLY_ID }));
  assert.match(html, /Apply rolled back/);
  assert.match(html, /href="\/activity\?apply=0123456789abcdef0123456789abcdef"/);
  assert.match(html, /tabindex="-1"/);
  assert.doesNotMatch(html, /not in the activity/);
  const elsewhere = render(h(ActivityTimeline, { payload: { ...page, entries: [ROLLBACK], next_cursor: "v1:9" }, focus: APPLY_ID }));
  assert.match(elsewhere, /role="status"[^>]*>The linked change 0123456789ab is not in the activity loaded so far/);
  assert.match(html, /Open the change this rolled back/);
  assert.equal((html.match(/Preview rollback/g) ?? []).length, 2);
  assert.equal((html.match(/aria-current="true"/g) ?? []).length, 1);
  assert.match(html, /activity-row-focused/);
  const panel = render(h(RollbackPanel, { applyId: APPLY_ID }));
  assert.match(panel, /citizen draft rollback 0123456789abcdef0123456789abcdef --preview --json/);
  assert.doesNotMatch(panel, /Roll back tuning/);
});

test("the rollback API posts the apply id and the typed confirmation with CSRF", async () => {
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
    return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await previewRollback(APPLY_ID);
    await rollBack(APPLY_ID, "tuning");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [
    { input: "/api/configure/apply/rollback/preview", body: { apply_id: APPLY_ID } },
    { input: "/api/configure/apply/rollback", body: { apply_id: APPLY_ID, confirm: "tuning" } },
  ]);
});
