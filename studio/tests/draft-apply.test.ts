import assert from "node:assert/strict";
import test from "node:test";
import { MantineProvider } from "@mantine/core";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { applyDraft, recoverApply, reviewDraftApply } from "../src/configure/api.ts";
import {
  applyBlocker, budgetDelta, canApply, canRecover, changedLive, coreFiles, outcomeAction, personalFiles, resultHeadline,
  type ApplyResult, type ApplyReview,
} from "../src/configure/applyModel.ts";
import { ApplyControls, ApplyOutcome, ApplyReviewDetails, DraftApply, RecoverControls } from "../src/configure/DraftApply.tsx";

const CLEAN: ApplyReview = {
  schema_version: 1,
  draft: { name: "tuning", revision: "rev-2", base_revision: "rev-1", branch: "draft/tuning", path: "/w" },
  destination: "/h/.config/agent-harness/personal-primitives",
  files: [{ path: "personal-primitives/rules/greeting.md", status: "added", scope: "personal" }],
  root: [{ path: "rules/greeting.md", action: "write" }],
  config: [{ key: "primitive_roots", action: "set", before: [], after: ["/h/.config/agent-harness/personal-primitives"], live: [] }],
  checks: { status: "passed", findings: [], truncated: false, command: "citizen lint" },
  budget: { before_lines: 191, after_lines: 194, delta_lines: 3, line_cap: 225 },
  commands: [
    { step: "root", command: "git -C /w archive --format=tar rev-2 -- personal-primitives/rules/greeting.md | tar -x -C /h/.config/agent-harness" },
    { step: "config", command: "citizen config set primitive_roots '[\"/h/.config/agent-harness/personal-primitives\"]'" },
    { step: "sync", command: "citizen sync" },
    { step: "check", command: "citizen doctor" },
  ],
  core: null,
  interrupted: null,
  refusals: [],
  can_apply: true,
  apply_command: "citizen draft apply tuning --revision rev-2 --json",
  nothing_applied: true,
};

const LINTED: ApplyReview = {
  ...CLEAN,
  checks: { status: "failed", findings: ["secret: personal-primitives/rules/greeting.md:3"], truncated: false, command: "citizen lint" },
  refusals: [{ code: "checks-failed", message: "the draft has 1 check finding(s); fix them in the draft first" }],
  can_apply: false,
};

const CORE: ApplyReview = {
  ...CLEAN,
  files: [...CLEAN.files, { path: "primitives/rules/secrets.md", status: "modified", scope: "core" }],
  core: {
    files: ["primitives/rules/secrets.md"],
    fork: { modules: [{ source: "core:rules:secrets", kind: "rules", name: "secrets" }],
      command: "citizen draft module plan tuning --request request.json --json", note: "Fork it." },
    branch: { name: "draft/tuning", revision: "rev-2", commands: [
      "git -C /repo push origin draft/tuning:refs/heads/contrib/tuning",
      "cd /repo && gh pr create --head contrib/tuning --base main --fill"] },
  },
  refusals: [{ code: "core-change", message: "this draft changes 1 core file(s) in place" }],
  can_apply: false,
};

function result(patch: Partial<ApplyResult>): ApplyResult {
  return {
    schema_version: 1, status: "applied", applied: true, error_code: "", message: "Applied draft tuning.",
    holder: "", apply_id: "a1", review: {}, doctor: { status: "passed", checks: [] }, restored: false, log: [],
    ...patch,
  };
}

function render(element: ReturnType<typeof h>): string {
  return renderToStaticMarkup(h(MantineProvider, {}, element));
}

test("the review shows files, keys, checks, the budget delta and every command apply runs", () => {
  const html = render(h(ApplyReviewDetails, { review: CLEAN }));
  assert.match(html, /personal-primitives\/rules\/greeting\.md/);
  assert.match(html, /primitive_roots/);
  assert.match(html, /Checks: passed/);
  assert.match(html, /\+3 always-loaded line\(s\): 191 now, 194 after apply, cap 225\./);
  for (const item of CLEAN.commands) assert.ok(html.includes(item.command.replace(/"/g, "&quot;").replace(/'/g, "&#x27;")), item.command);
  assert.doesNotMatch(html, /Apply is refused/);
  assert.deepEqual(personalFiles(CORE).map((file) => file.path), ["personal-primitives/rules/greeting.md"]);
  assert.deepEqual(coreFiles(CORE).map((file) => file.path), ["primitives/rules/secrets.md"]);
  assert.equal(budgetDelta(null), "The context budget is unavailable for this draft.");
});

test("AC1: lint findings refuse apply and are listed with the refusal", () => {
  const html = render(h(ApplyReviewDetails, { review: LINTED }));
  assert.match(html, /Apply is refused; nothing will change/);
  assert.match(html, /Checks: failed/);
  assert.match(html, /secret: personal-primitives/);
  assert.equal(canApply(LINTED, "rev-2", "tuning", "tuning"), false);
  assert.equal(applyBlocker(LINTED, "rev-2", "tuning", "tuning"), "Apply is refused until every finding below is resolved.");
});

test("AC2: a held sync lock names its holder and says nothing changed", () => {
  const busy = result({ status: "refused", applied: false, error_code: "busy", holder: "citizen sync (pid 7, since t)",
    message: "another harness configuration operation is running: citizen sync (pid 7, since t); nothing was applied" });
  assert.equal(resultHeadline(busy), "Not applied: citizen sync (pid 7, since t) holds the sync lock. Nothing changed.");
  const html = render(h(ApplyOutcome, { result: busy }));
  assert.match(html, /citizen sync \(pid 7, since t\) holds the sync lock/);
  assert.equal(resultHeadline(result({ status: "failed", applied: false, error_code: "sync-refused", restored: true })),
    "Apply failed safely. The previous configuration and files were restored.");
});

test("AC3: an in-place core edit offers a fork and a branch with pull request commands", () => {
  const html = render(h(ApplyReviewDetails, { review: CORE }));
  assert.match(html, /This draft edits 1 core file\(s\) in place/);
  assert.match(html, /core:rules:secrets/);
  assert.match(html, /citizen draft module plan tuning/);
  assert.match(html, /draft\/tuning:refs\/heads\/contrib\/tuning/);
  assert.match(html, /gh pr create --head contrib\/tuning/);
  assert.equal(canApply(CORE, "rev-2", "tuning", "tuning"), false);
});

test("AC4: apply needs the reviewed revision and the draft's name typed back, then reports doctor", () => {
  assert.equal(canApply(CLEAN, "rev-2", "tuning", "tuning"), true);
  assert.equal(canApply(CLEAN, "rev-2", "tuning", "tun"), false);
  assert.equal(canApply(CLEAN, "rev-3", "tuning", "tuning"), false);
  assert.equal(canApply(null, "rev-2", "tuning", "tuning"), false);
  assert.equal(applyBlocker(CLEAN, "rev-3", "tuning", "tuning"), "The draft changed since this review. Review it again.");
  assert.equal(applyBlocker(CLEAN, "rev-2", "tuning", ""), "Type tuning to confirm.");
  assert.equal(applyBlocker(null, "rev-2", "tuning", ""), "Review the draft first.");
  const attention = result({ doctor: { status: "attention", checks: [
    { id: "doctor-1", status: "attention", message: "identity: still at the example value", fix: "citizen init" },
    { id: "doctor-2", status: "informational", message: "drift: none", fix: null }] } });
  assert.equal(resultHeadline(attention), "Applied. The doctor checks need attention.");
  const html = render(h(ApplyOutcome, { result: attention }));
  assert.match(html, /identity: still at the example value/);
  assert.doesNotMatch(html, /drift: none/);
  assert.equal(resultHeadline(result({})), "Applied. The doctor checks ran.");
  const panel = render(h(DraftApply, { draft: "tuning", revision: "rev-2" }));
  assert.match(panel, /Review and apply this draft/);
  assert.match(panel, /citizen draft review tuning --json/);
  assert.doesNotMatch(panel, /Apply tuning/);
});

test("the apply API posts the draft, reviewed revision and typed confirmation with CSRF", async () => {
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
    await reviewDraftApply("tuning");
    await applyDraft("tuning", "rev-2", "tuning");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [
    { input: "/api/configure/apply/review", body: { draft: "tuning" } },
    { input: "/api/configure/apply", body: { draft: "tuning", revision: "rev-2", confirm: "tuning" } },
  ]);
});

test("a blocked Apply stays focusable and its reason is announced through aria-describedby", () => {
  const controls = (confirmation: string) => render(h(ApplyControls, {
    draft: "tuning", revision: "rev-2", review: CLEAN, confirmation, busy: "",
    onConfirm: () => {}, onApply: () => {},
  }));
  const blocked = controls("");
  assert.match(blocked, /aria-describedby="draft-apply-blocker"/);
  assert.match(blocked, /aria-disabled="true"/);
  assert.match(blocked, /id="draft-apply-blocker"[^>]*>Type tuning to confirm\./);
  assert.doesNotMatch(blocked, /<button[^>]*disabled=""[^>]*>[^<]*<[^>]*>[^<]*Apply tuning/);
  const ready = controls("tuning");
  assert.doesNotMatch(ready, /aria-describedby="draft-apply-blocker"/);
  assert.doesNotMatch(ready, /aria-disabled/);
});

test("an apply outcome is shown only on the panel that started it, but always refreshes the overview", () => {
  assert.deepEqual(outcomeAction(true, 3, 3), { refreshOverview: true, show: true });
  assert.deepEqual(outcomeAction(true, 3, 4), { refreshOverview: true, show: false });
  assert.deepEqual(outcomeAction(false, 3, 4), { refreshOverview: false, show: false });
});

const INTERRUPTED: ApplyReview = {
  ...CLEAN,
  interrupted: { apply_id: "a1", draft: "older", started: "t", recover_command: "citizen draft recover --json",
    abandon_command: "citizen draft recover --abandon --json" },
  refusals: [{ code: "interrupted-apply", message: "an earlier apply of draft older was interrupted" }],
  can_apply: false,
};

test("a recovery is a change: it has its own headline and refreshes the overview", () => {
  const recovered = result({ status: "recovered", applied: false, restored: true, message: "restored" });
  assert.equal(resultHeadline(recovered), "An interrupted apply was rolled back and synced. Review the draft again.");
  assert.equal(resultHeadline({ ...recovered, error_code: "interrupted-rollback" }),
    "An interrupted rollback was undone and synced. Nothing of this draft was applied.");
  assert.equal(changedLive(recovered), true);
  assert.equal(changedLive(result({ status: "abandoned", applied: false })), false);
  assert.equal(changedLive(result({ status: "refused", applied: false })), false);
  assert.doesNotMatch(render(h(ApplyOutcome, { result: recovered })), /Not applied/);
});

test("an open interrupted apply offers Restore and Abandon behind the draft's name", () => {
  assert.equal(canRecover(INTERRUPTED, ""), false);
  assert.equal(canRecover(INTERRUPTED, "older"), true);
  assert.equal(canRecover(CLEAN, "older"), false);
  const props = { review: INTERRUPTED, busy: "" as const, onConfirm: () => {}, onRecover: () => {} };
  const blocked = render(h(RecoverControls, { ...props, confirmation: "" }));
  assert.match(blocked, /An apply of older was interrupted/);
  assert.match(blocked, /Restore/);
  assert.match(blocked, /Abandon/);
  assert.match(blocked, /aria-describedby="draft-recover-blocker"/);
  assert.match(blocked, /citizen draft recover --json/);
  assert.doesNotMatch(render(h(RecoverControls, { ...props, confirmation: "older" })), /aria-disabled/);
  assert.doesNotMatch(render(h(RecoverControls, { ...props, review: CLEAN, confirmation: "" })), /interrupted|Restore/);
  const rollback = { ...INTERRUPTED, interrupted: { ...INTERRUPTED.interrupted!, kind: "rollback" as const } };
  const named = render(h(RecoverControls, { ...props, review: rollback, confirmation: "" }));
  assert.match(named, /A rollback of older(&#x27;|')s apply was interrupted/);
  assert.doesNotMatch(named, /An apply of older/);
});

test("an interrupted rollback is named as a rollback in every headline", () => {
  assert.equal(resultHeadline(result({ status: "abandoned", applied: false, error_code: "interrupted-rollback" })),
    "The interrupted rollback was abandoned. What it wrote was kept.");
  assert.equal(resultHeadline(result({ status: "refused", applied: false, error_code: "interrupted-rollback" })),
    "Not applied: an earlier rollback was interrupted. Restore or abandon it first. Nothing changed.");
});

test("the recover API posts the action and the typed draft with CSRF", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ input: string; body: unknown }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input) === "/api/session") {
      return new Response(JSON.stringify({ csrf_token: "c" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    assert.equal((init?.headers as Record<string, string>)["X-Studio-CSRF"], "c");
    calls.push({ input: String(input), body: JSON.parse(String(init?.body)) });
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await recoverApply("abandon", "older");
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(calls, [{ input: "/api/configure/apply/recover", body: { action: "abandon", confirm: "older" } }]);
});
