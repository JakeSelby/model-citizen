import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { pageCommand } from "../src/navigation.ts";
import { RoutineRefused, changeRoutine, loadRoutines, routinesQuery, runRoutine } from "../src/routines/api.ts";
import {
  changeFor, confirmationBlocker, duration, outcomeLabel, refusalText, scheduleText, when,
  type RoutineRun, type RoutineStatus, type RoutinesListing,
} from "../src/routines/model.ts";
import {
  RoutineDetail, RoutinesPage, RoutinesTable, RoutinesView, RunHistory, ScheduleControls,
} from "../src/routines/RoutinesPage.tsx";

// Hand-typed in the shape `GET /api/routines` answers with (`routines.listing`); values follow
// `tests/test_studio_routines.py`. No generator exists.
const LIVENESS: RoutineStatus = {
  name: "hook-liveness", title: "Hook liveness", description: "Checks every hook fired when it should and logged no errors.",
  command: "citizen studio routine run hook-liveness --json", installed: true,
  schedule: { interval_seconds: 3600, label: "Hourly" },
  last_run: { run_id: "r2", started_at: "2026-10-15T09:00:00Z", finished_at: "2026-10-15T09:00:04Z", outcome: "failed", summary: "3 live, 1 silent, 1 erroring; erroring: grade-bash" },
  outcome: "failed", next_run: "2026-10-15T10:00:00Z", overdue: true,
};
const AUDIT: RoutineStatus = {
  name: "rule-adherence", title: "Rule adherence audit", description: "Reads the transcripts written since the last run with the rule detectors. No model calls.",
  command: "citizen studio routine run rule-adherence --json", installed: false,
  schedule: { interval_seconds: 86400, label: "Daily" }, last_run: null, outcome: "never", next_run: null, overdue: false,
};
const RUNS: RoutineRun[] = [
  { run_id: "r2", routine: "hook-liveness", trigger: "scheduled", started_at: "2026-10-15T09:00:00Z", finished_at: "2026-10-15T09:00:04Z", outcome: "failed", summary: "3 live, 1 silent, 1 erroring; erroring: grade-bash", details: {} },
  { run_id: "r1", routine: "hook-liveness", trigger: "studio", started_at: "2026-10-14T09:00:00Z", finished_at: "2026-10-14T09:02:05Z", outcome: "ok", summary: "4 live, 1 silent, 0 erroring", details: {} },
];
const LISTING: RoutinesListing = { routines: [LIVENESS, AUDIT], history: { "hook-liveness": RUNS, "rule-adherence": [] } };

function wrap(node: ReturnType<typeof h>, path = "/routines", client = new QueryClient()): string {
  return renderToStaticMarkup(h(MantineProvider, {}, h(QueryClientProvider, { client },
    h(MemoryRouter, { initialEntries: [path] }, node))));
}

function section(html: string, label: string): string {
  const start = html.indexOf(`aria-label="${label}"`);
  assert.ok(start >= 0, `no ${label}`);
  return html.slice(start, html.indexOf("</table>", start));
}

test("the table shows each routine's schedule, installed state, last run, next run and overdue flag", () => {
  const html = wrap(h(RoutinesTable, { routines: LISTING.routines, selected: "hook-liveness", onSelect: () => {}, onRun: () => {}, busy: "" }));
  assert.deepEqual([...html.matchAll(/data-routine="([^"]+)"/g)].map((m) => m[1]), ["hook-liveness", "rule-adherence"]);
  assert.match(html, /data-routine="hook-liveness" data-installed="yes"/);
  assert.match(html, /data-routine="rule-adherence" data-installed="no"/);
  assert.ok(html.includes("Hourly") && html.includes("Daily") && html.includes("not installed"));
  assert.deepEqual([...html.matchAll(/data-outcome="([^"]+)"/g)].map((m) => m[1]), ["failed", "never"]);
  assert.equal([...html.matchAll(/data-overdue="yes"/g)].length, 1);
  assert.ok(html.includes("Oct 15 10:00 UTC"), "next run");
  assert.match(html, /class="harness-panel"/, "a hairline section, not a bordered card");
  assert.doesNotMatch(html, /mantine-Paper-root/);
  assert.match(html, /mantine-Badge-root[^"]*"[^>]*data-variant="outline"/);
  assert.equal([...html.matchAll(/>Run now</g)].length, 2);
});

test("run history lists runs newest first with their trigger, duration and summary", () => {
  const html = wrap(h(RunHistory, { routine: LIVENESS, runs: RUNS }));
  const table = section(html, "Run history: Hook liveness, newest first");
  assert.deepEqual([...table.matchAll(/data-run="([^"]+)"/g)].map((m) => m[1]), ["r2", "r1"]);
  assert.ok(table.includes("Scheduled") && table.includes("From the Studio"));
  assert.ok(table.includes("4 s") && table.includes("2 min 5 s"));
  assert.ok(table.includes("erroring: grade-bash"));
});

test("a routine that never ran says so rather than showing an empty table", () => {
  const html = wrap(h(RunHistory, { routine: AUDIT, runs: [] }));
  assert.ok(html.includes("No runs yet") && html.includes("run daily"));
  assert.doesNotMatch(html, /<table/);
});

test("install and remove need the routine's name typed, as an apply needs its draft's", () => {
  const install = wrap(h(ScheduleControls, { routine: AUDIT, typed: "", busy: "", onType: () => {}, onChange: () => {} }));
  assert.ok(install.includes("Install rule-adherence"));
  assert.match(install, /aria-disabled="true"/);
  assert.ok(install.includes("Type rule-adherence to confirm."));
  assert.ok(install.includes("Touch ID"));
  assert.ok(install.includes("citizen studio routine install rule-adherence --json"));
  const ready = wrap(h(ScheduleControls, { routine: AUDIT, typed: "rule-adherence", busy: "", onType: () => {}, onChange: () => {} }));
  assert.doesNotMatch(ready, /aria-disabled="true"/);
  const remove = wrap(h(ScheduleControls, { routine: LIVENESS, typed: "", busy: "", onType: () => {}, onChange: () => {} }));
  assert.ok(remove.includes("Remove hook-liveness") && remove.includes("citizen studio routine remove hook-liveness --json"));
});

test("the change control fires only once the name is typed", () => {
  let fired = 0;
  const element = ScheduleControls({ routine: AUDIT, typed: "rule", busy: "", onType: () => {}, onChange: () => { fired += 1; } });
  const button = findButton(element);
  button.props.onClick();
  assert.equal(fired, 0);
  const ready = ScheduleControls({ routine: AUDIT, typed: "rule-adherence", busy: "", onType: () => {}, onChange: () => { fired += 1; } });
  findButton(ready).props.onClick();
  assert.equal(fired, 1);
});

// The Button element inside the controls' Group, found by walking the element tree.
type Element = { props?: { onClick?: () => void; loading?: boolean; children?: unknown } };
function findButton(node: unknown): Element & { props: { onClick: () => void } } {
  const found = search(node);
  assert.ok(found, "no button");
  return found;
}
function search(node: unknown): (Element & { props: { onClick: () => void } }) | null {
  if (!node || typeof node !== "object") return null;
  const element = node as Element;
  if (element.props?.onClick && element.props && "loading" in element.props) return element as Element & { props: { onClick: () => void } };
  const children = element.props?.children;
  for (const child of Array.isArray(children) ? children : [children]) {
    const found = search(child);
    if (found) return found;
  }
  return null;
}

test("the detail names the selected routine and the view falls back to the first", () => {
  const detail = wrap(h(RoutineDetail, { routine: LIVENESS, runs: RUNS, typed: "", busy: "", onType: () => {}, onChange: () => {} }));
  assert.match(detail, /data-selected="hook-liveness"/);
  assert.ok(detail.includes("Hourly · next Oct 15 10:00 UTC") && detail.includes("Overdue"));
  const view = wrap(h(RoutinesView, { listing: LISTING, selected: "nope" }));
  assert.match(view, /data-selected="hook-liveness"/);
  const chosen = wrap(h(RoutinesView, { listing: LISTING, selected: "rule-adherence", message: { tone: "error", text: "Nobody confirmed" } }));
  assert.match(chosen, /data-selected="rule-adherence"/);
  assert.match(chosen, /role="alert"/);
});

test("the routed page reads the listing and the selected routine from the query", () => {
  const client = new QueryClient();
  client.setQueryData(routinesQuery.queryKey, LISTING);
  const html = wrap(h(Routes, {}, h(Route, { path: "/routines", element: h(RoutinesPage) })), "/routines?routine=rule-adherence", client);
  assert.match(html, /data-selected="rule-adherence"/);
  assert.ok(html.includes("launchd"));
});

test("the model's words", () => {
  assert.equal(outcomeLabel("never"), "Never run");
  assert.equal(when(null), "–");
  assert.equal(when("2026-10-15T09:00:00Z"), "Oct 15 09:00 UTC");
  assert.equal(duration({ ...RUNS[0], finished_at: null }), "–");
  assert.equal(scheduleText(AUDIT), "Daily · not installed");
  assert.equal(confirmationBlocker(AUDIT, "rule-adherence"), "");
  assert.equal(changeFor(AUDIT), "install");
  assert.equal(changeFor(LIVENESS), "remove");
  assert.match(refusalText("person_confirmation_required"), /Touch ID/);
  assert.equal(pageCommand("/routines"), "citizen studio routine list");
});

test("the api posts with the CSRF token and surfaces refusals with their code", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const answers: Record<string, () => Response> = {
    "/api/session": () => Response.json({ authenticated: true, csrf_token: "tok" }),
    "/api/routines": () => Response.json(LISTING),
    "/api/routines/run": () => Response.json({ run: RUNS[0] }),
    "/api/routines/install": () => Response.json({ error: "person_confirmation_required" }, { status: 403 }),
    "/api/routines/remove": () => Response.json({ error: "launchd_refused", reason: "launchctl could not run" }, { status: 409 }),
  };
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return answers[url]();
  }) as typeof fetch;
  try {
    assert.deepEqual((await loadRoutines()).routines.map((r) => r.name), ["hook-liveness", "rule-adherence"]);
    assert.equal((await runRoutine("hook-liveness")).run_id, "r2");
    const posted = calls.find((call) => call.url === "/api/routines/run");
    assert.equal((posted?.init?.headers as Record<string, string>)["X-Studio-CSRF"], "tok");
    assert.deepEqual(JSON.parse(String(posted?.init?.body)), { name: "hook-liveness" });
    await assert.rejects(changeRoutine("install", "rule-adherence", "rule-adherence"),
      (error: unknown) => error instanceof RoutineRefused && error.code === "person_confirmation_required");
    const install = calls.find((call) => call.url === "/api/routines/install");
    assert.deepEqual(JSON.parse(String(install?.init?.body)), { name: "rule-adherence", confirm: "rule-adherence" });
    await assert.rejects(changeRoutine("remove", "hook-liveness", "hook-liveness"),
      (error: unknown) => error instanceof RoutineRefused && error.reason === "launchctl could not run");
  } finally {
    globalThis.fetch = original;
  }
});
