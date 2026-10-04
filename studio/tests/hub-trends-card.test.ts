import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

import { ReportCards, reportCards } from "../src/overview/OverviewPage.tsx";

test("the Hub links the trends report beside the proof set", () => {
  const card = reportCards.find((item) => item.label === "Trends");
  assert.ok(card);
  assert.equal(card.href, "/reports/trends");
  const html = renderToStaticMarkup(h(MantineProvider, {}, h(MemoryRouter, {}, h(ReportCards))));
  assert.ok(html.includes('href="/reports/trends"'));
  assert.ok(html.includes(">Trends<"));
  assert.ok(html.includes("By version and date"));
  assert.ok(html.includes("labelled exploratory or pre-registered"));
});
