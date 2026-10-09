import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { ActivityPage } from "../src/activity/ActivityPage.tsx";
import { RollbackResultRegion } from "../src/activity/Rollback.tsx";
import {
  noticeHeadline, noticeTone, restoredChanges, rollbackNotice,
  type RollbackPreview, type RollbackResult,
} from "../src/activity/rollbackModel.ts";

const APPLY_ID = "0123456789abcdef0123456789abcdef";

const REVIEW: Partial<RollbackPreview> = {
  destination: "/h/.config/agent-harness/personal-primitives",
  config: [
    { key: "primitive_roots", action: "set", current: ["/h/p"], current_present: true, restored: [], restored_present: true },
    { key: "rules.cache-hygiene", action: "unset", current: "off", current_present: true, restored: null, restored_present: false },
    { key: "mode", action: "none", current: "a", current_present: true, restored: "a", restored_present: true },
  ],
  files: [{ path: "rules/greeting.md", action: "delete" }, { path: "rules/kept.md", action: "none" }],
};

function result(overrides: Partial<RollbackResult>): RollbackResult {
  return {
    schema_version: 1, status: "rolled-back", applied: false, error_code: "", message: "Rolled back.",
    holder: "", apply_id: "e".repeat(32), review: REVIEW, doctor: { status: "passed", checks: [] },
    restored: true, log: [], ...overrides,
  };
}

function render(node: ReturnType<typeof h>): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, { initialEntries: ["/activity"] }, node)))
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, "");
}

/** The markup from the region's always-mounted live element up to the dismiss control. */
function statusOf(html: string): string {
  const start = html.indexOf('<div aria-atomic="true" aria-live="polite" role="status">');
  const end = html.indexOf("<button", start);
  return start < 0 ? "" : html.slice(start, end < 0 ? undefined : end);
}

test("a notice keeps the engine's result, or the error when none arrived", () => {
  const done = rollbackNotice(APPLY_ID, "tuning", result({}));
  assert.equal(noticeHeadline(done), "Rolled back. The doctor checks ran.");
  assert.equal(noticeTone(done), "success");
  assert.equal(noticeTone(rollbackNotice(APPLY_ID, "tuning", result({ doctor: { status: "attention", checks: [] } }))), "warning");
  const failed = rollbackNotice(APPLY_ID, "tuning", result({ status: "failed", restored: true }));
  assert.match(noticeHeadline(failed), /failed safely/);
  assert.equal(noticeTone(failed), "danger");
  const lost = rollbackNotice(APPLY_ID, "tuning", null, "Network down");
  assert.equal(lost.error, "Network down");
  assert.equal(noticeHeadline(lost), "Rollback did not complete. Network down");
  assert.equal(noticeTone(lost), "danger");
  assert.match(rollbackNotice(APPLY_ID, "tuning", null).error, /did not report a result/);
});

test("a completed rollback lists what it restored; a refusal restored nothing", () => {
  const restored = restoredChanges(rollbackNotice(APPLY_ID, "tuning", result({})));
  assert.deepEqual(restored.keys.map((row) => row.key), ["primitive_roots", "rules.cache-hygiene"]);
  assert.deepEqual(restored.files.map((row) => row.path), ["rules/greeting.md"]);
  assert.deepEqual(restoredChanges(rollbackNotice(APPLY_ID, "tuning", result({ status: "refused" }))), { keys: [], files: [] });
  assert.deepEqual(restoredChanges(rollbackNotice(APPLY_ID, "tuning", result({ review: {} }))), { keys: [], files: [] });
});

test("the status region is mounted empty before any rollback, with nothing to dismiss", () => {
  const html = render(h(RollbackResultRegion, { notice: null, onDismiss: () => undefined }));
  assert.equal(html, '<div><div aria-atomic="true" aria-live="polite" role="status"></div></div>');
});

test("a completed result is announced once, lists the restore, and is dismissed from outside the live region", () => {
  const html = render(h(RollbackResultRegion, {
    notice: rollbackNotice(APPLY_ID, "tuning", result({})), onDismiss: () => undefined,
  }));
  assert.match(html, /class="activity-rollback-result" data-tone="success"/);
  assert.equal((html.match(/role="status"/g) ?? []).length, 1);
  assert.equal((html.match(/Rolled back\. The doctor checks ran\./g) ?? []).length, 1);
  const status = statusOf(html);
  assert.match(status, /<h2[^>]*>Rollback of tuning<\/h2>/);
  assert.match(status, /Rolled back\. The doctor checks ran\./);
  assert.match(status, /primitive_roots<\/code> \[\]/);
  assert.match(status, /rules\.cache-hygiene<\/code> unset/);
  assert.match(status, /rules\/greeting\.md<\/code> removed/);
  assert.doesNotMatch(status, /kept\.md|<code[^>]*>mode<\/code>/);
  assert.doesNotMatch(status, /Dismiss/);
  assert.match(html, /<button[^>]*type="button"[^>]*>[\s\S]*Dismiss rollback result/);
  assert.doesNotMatch(html, /role="alert"/);
});

test("a failed rollback, and one that never answered, keep their error on screen the same way", () => {
  const failed = render(h(RollbackResultRegion, {
    notice: rollbackNotice(APPLY_ID, "tuning", result({ status: "failed", restored: false, message: "sync refused" })),
    onDismiss: () => undefined,
  }));
  assert.match(failed, /data-tone="danger"/);
  assert.match(statusOf(failed), /citizen draft recover[\s\S]*sync refused/);
  assert.match(failed, /Dismiss rollback result/);
  const busy = render(h(RollbackResultRegion, {
    notice: rollbackNotice(APPLY_ID, "tuning", result({
      status: "refused", error_code: "busy", holder: "citizen sync (pid 1)", message: "nothing was changed" })),
    onDismiss: () => undefined,
  }));
  assert.match(statusOf(busy), /citizen sync \(pid 1\) holds the sync lock[\s\S]*nothing was changed/);
  const lost = render(h(RollbackResultRegion, {
    notice: rollbackNotice(APPLY_ID, "", null, "Network down"), onDismiss: () => undefined,
  }));
  assert.match(statusOf(lost), /Rollback of 0123456789ab[\s\S]*no result[\s\S]*Rollback did not complete\. Network down/);
  assert.match(lost, /Dismiss rollback result/);
});

test("Activity mounts the result region above the list and can take focus back on its heading", () => {
  const html = render(h(ActivityPage));
  assert.match(html, /<h1[^>]*tabindex="-1"[^>]*>What the harness decided and changed\.<\/h1>/);
  const region = html.indexOf('aria-atomic="true" aria-live="polite" role="status"');
  assert.ok(region > html.indexOf("Apply filters"), "the region follows the filters");
  assert.ok(region < html.indexOf("Loading local activity"), "the region precedes the list");
});
