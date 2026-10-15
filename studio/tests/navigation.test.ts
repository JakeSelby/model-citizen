import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import {
  documentTitle,
  FOOT_NAVIGATION,
  LEGACY_REDIRECTS,
  legacyLocation,
  legacyTarget,
  NAVIGATION,
  NAVIGATION_GROUPS,
  pageCommand,
  pageTitle,
} from "../src/navigation.ts";
import { NAV_ICON_KEYS } from "../src/shell/NavIcon.tsx";
import { REBUILDING, RebuildingPage } from "../src/shell/RebuildingPage.tsx";
import { StudioNav } from "../src/shell/StudioNav.tsx";

test("the nav groups pages by the question they answer", () => {
  assert.deepEqual(
    NAVIGATION_GROUPS.map(({ label, items }) => [label, items.map((item) => [item.label, item.path])]),
    [
      [null, [["Home", "/home"], ["Sessions", "/sessions"]]],
      ["Harness", [
        ["Rules", "/harness/rules"], ["Stances", "/harness/stances"], ["Skills", "/harness/skills"],
        ["Hooks", "/harness/hooks"], ["Roles", "/harness/roles"], ["Workflows", "/harness/workflows"],
        ["Sources", "/harness/sources"],
      ]],
      ["Operate", [["Routines", "/routines"], ["Changes", "/changes"]]],
      ["Measure", [["Evals", "/evals"]]],
    ],
  );
  assert.deepEqual(FOOT_NAVIGATION.map(({ label, path }) => [label, path]), [["Baseline", "/baseline"]]);
  assert.equal(NAVIGATION.length, 13);
  assert.equal(new Set(NAVIGATION.map((item) => item.path)).size, NAVIGATION.length);
  for (const { key } of NAVIGATION) assert.ok(NAV_ICON_KEYS.includes(key), `no icon for ${key}`);
});

test("the rendered nav shows each group under its label and marks the current page", () => {
  const nav = renderToStaticMarkup(h(MantineProvider, {},
    h(MemoryRouter, { initialEntries: ["/harness/stances"] }, h(StudioNav))));
  const labels = [...nav.matchAll(/class="nav-group-label"[^>]*>([^<]+)</g)].map((match) => match[1]);
  assert.deepEqual(labels, ["Harness", "Operate", "Measure"]);
  const links = [...nav.matchAll(/class="[^"]*nav-link[^"]*"[^>]*href="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(links, NAVIGATION.map((item) => item.path));
  assert.match(nav, /aria-current="page"[^>]*href="\/harness\/stances"|href="\/harness\/stances"[^>]*aria-current="page"/);
  assert.match(nav, /role="group" aria-labelledby="nav-group-harness"|aria-labelledby="nav-group-harness"[^>]*role="group"/);
  assert.match(nav, /href="\/home"[^>]*>.*Model Citizen/);
});

test("every old route redirects to its page in the new nav", () => {
  const expected: Record<string, string> = {
    "/": "/home",
    "/setup": "/baseline",
    "/configure": "/changes",
    "/library": "/harness/sources/modules",
    "/experiments": "/evals",
    "/experiments/runs/run-7": "/evals/runs/run-7",
    "/activity": "/harness/hooks",
    "/reports": "/evals",
    "/reports/rules": "/harness/rules",
    "/reports/usage": "/sessions",
    "/reports/trends": "/evals/trends",
  };
  for (const [from, to] of Object.entries(expected)) assert.equal(legacyTarget(from), to, from);
  assert.equal(LEGACY_REDIRECTS.length, Object.keys(expected).length);
  // Every target is a page the Studio routes.
  const routed = [...NAVIGATION.map((item) => item.path), "/harness/sources/modules", "/evals/trends", "/evals/runs/run-7"];
  for (const to of Object.values(expected)) assert.ok(routed.includes(to), to);
  // New routes, unknown routes and an empty run id are not old routes.
  assert.equal(legacyTarget("/home"), null);
  assert.equal(legacyTarget("/not-a-route"), null);
  assert.equal(legacyTarget("/experiments/runs/"), null);
});

test("an old module deep link keeps its query and lands on the module browser", () => {
  assert.deepEqual(legacyLocation({ pathname: "/library", search: "?path=rules/a.md&line=3", hash: "" }),
    { pathname: "/harness/sources/modules", search: "?path=rules/a.md&line=3", hash: "" });
  assert.deepEqual(legacyLocation({ pathname: "/activity", search: "", hash: "" }),
    { pathname: "/harness/hooks", search: "?tab=log", hash: "" });
  assert.deepEqual(legacyLocation({ pathname: "/activity", search: "?apply=a1", hash: "" }),
    { pathname: "/harness/hooks", search: "?apply=a1&tab=log", hash: "" });
  assert.deepEqual(legacyLocation({ pathname: "/not-a-route", search: "?x=1", hash: "#top" }),
    { pathname: "/home", search: "?x=1", hash: "#top" });
});

test("each page carries the CLI command of the page it re-homes", () => {
  assert.equal(pageCommand("/home"), "citizen home");
  assert.equal(pageCommand("/sessions"), "citizen sessions list");
  assert.equal(pageCommand("/harness/rules"), "citizen usage --rules");
  assert.equal(pageCommand("/harness/hooks"), "citizen activity");
  assert.equal(pageCommand("/harness/sources"), "citizen catalog --library");
  assert.equal(pageCommand("/changes"), "citizen draft list");
  assert.equal(pageCommand("/evals"), "citizen runs history");
  assert.equal(pageCommand("/evals/trends"), "citizen reports trends");
  assert.equal(pageCommand("/baseline"), "citizen baseline show");
  // The command follows the page, not the old path.
  for (const [from] of LEGACY_REDIRECTS) {
    const target = legacyTarget(from.replace(":runId", "r-1"));
    assert.ok(target);
    assert.match(pageCommand(target), /^citizen [a-z]/);
  }
});

test("page titles follow the route and unknown routes read as Home", () => {
  assert.equal(pageTitle("/harness/rules"), "Rules");
  assert.equal(pageTitle("/evals/runs/r-1"), "Evals");
  assert.equal(pageTitle("/evals/trends"), "Evals");
  assert.equal(pageTitle("/not-a-route"), "Home");
  assert.equal(documentTitle("/harness/hooks"), "Hooks · Model Citizen Studio");
});

test("pages not yet rebuilt name the story that builds them", () => {
  assert.deepEqual(Object.keys(REBUILDING).sort(), ["roles", "routines", "stances", "workflows"]);
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(RebuildingPage, { page: "routines" })));
  assert.match(html, /<h1[^>]*>Routines<\/h1>/);
  assert.match(html, /story #1335/);
});

test("colours are declared once, in the theme, and no page's CSS copies them", () => {
  const source = new URL("../src/", import.meta.url);
  const sheets: string[] = [];
  const walk = (directory: URL) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(new URL(`${entry.name}/`, directory));
      else if (entry.name.endsWith(".css")) sheets.push(new URL(entry.name, directory).pathname);
    }
  };
  walk(source);
  assert.ok(sheets.length > 1);
  for (const sheet of sheets) {
    const css = readFileSync(sheet, "utf8");
    assert.doesNotMatch(css, /#[0-9A-Fa-f]{3,8}\b(?![-\w])/, `${sheet} declares a colour`);
    assert.doesNotMatch(css, /--studio-[a-z0-9-]+\s*:/, `${sheet} declares a token`);
  }
});
