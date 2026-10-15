import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import {
  documentTitle,
  fallbackLocation,
  FOOT_NAVIGATION,
  NAVIGATION,
  NAVIGATION_GROUPS,
  OLD_PATHS,
  pageCommand,
  pageTitle,
} from "../src/navigation.ts";
import { NAV_ICON_KEYS } from "../src/shell/NavIcon.tsx";
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

test("only the old top-level paths still found in bookmarks map to a new page", () => {
  const expected: Record<string, string> = {
    "/": "/home",
    "/setup": "/baseline",
    "/configure": "/changes",
    "/library": "/harness/sources/modules",
    "/experiments": "/evals",
    "/activity": "/harness/hooks",
    "/reports": "/evals",
    "/reports/rules": "/harness/rules",
    "/reports/usage": "/sessions",
    "/reports/trends": "/evals/trends",
  };
  assert.deepEqual(OLD_PATHS, expected);
  const empty = { search: "", hash: "" };
  for (const [from, to] of Object.entries(expected)) {
    assert.equal(fallbackLocation({ pathname: from, ...empty }).pathname, to, from);
  }
  // Every target is a page the Studio routes.
  const routed = [...NAVIGATION.map((item) => item.path), "/harness/sources/modules", "/evals/trends"];
  for (const to of Object.values(expected)) assert.ok(routed.includes(to), to);
  // Retired deep paths, prototype keys and unknown routes all land on Home.
  for (const pathname of ["/experiments/runs/run-7", "/reports/spend", "/toString", "/not-a-route"]) {
    assert.equal(fallbackLocation({ pathname, ...empty }).pathname, "/home", pathname);
  }
});

test("an old bookmark keeps its query and fragment, and Activity opens the safety log", () => {
  assert.deepEqual(fallbackLocation({ pathname: "/library", search: "?path=rules/a.md&line=3", hash: "" }),
    { pathname: "/harness/sources/modules", search: "?path=rules/a.md&line=3", hash: "" });
  assert.deepEqual(fallbackLocation({ pathname: "/activity", search: "", hash: "" }),
    { pathname: "/harness/hooks", search: "?tab=log", hash: "" });
  assert.deepEqual(fallbackLocation({ pathname: "/activity", search: "?apply=a1", hash: "" }),
    { pathname: "/harness/hooks", search: "?apply=a1&tab=log", hash: "" });
  assert.deepEqual(fallbackLocation({ pathname: "/not-a-route", search: "?x=1", hash: "#top" }),
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
  for (const target of Object.values(OLD_PATHS)) assert.match(pageCommand(target), /^citizen [a-z]/);
});

test("page titles follow the route and unknown routes read as Home", () => {
  assert.equal(pageTitle("/harness/rules"), "Rules");
  assert.equal(pageTitle("/evals/runs/r-1"), "Evals");
  assert.equal(pageTitle("/evals/trends"), "Evals");
  assert.equal(pageTitle("/not-a-route"), "Home");
  assert.equal(documentTitle("/harness/hooks"), "Hooks · Model Citizen Studio");
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
