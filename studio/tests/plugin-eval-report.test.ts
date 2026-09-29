import assert from "node:assert/strict";
import test from "node:test";

import { reportHref } from "../src/experiments/history/model.ts";

const RUN = "316a30c9-0340-5fbd-bf47-ff320c2e377c";

test("an available plugin eval report links to its fixed same-origin route", () => {
  assert.equal(reportHref({ id: "imported-1", label: "HTML report", available: true,
    kind: "html-report", href: `/api/runs/plugin-eval-report/${RUN}` }),
  `/api/runs/plugin-eval-report/${RUN}`);
});

test("other artifacts, unavailable reports and foreign links are never rendered as links", () => {
  assert.equal(reportHref({ id: "imported-0", label: "Result JSON", available: true }), null);
  assert.equal(reportHref({ id: "imported-1", label: "HTML report", available: false,
    kind: "html-report", href: `/api/runs/plugin-eval-report/${RUN}` }), null);
  for (const href of ["https://example.com/report.html", "javascript:alert(1)",
    `/api/runs/plugin-eval-report/${RUN}/../../session`, "/api/runs/plugin-eval-report/x"]) {
    assert.equal(reportHref({ id: "imported-1", label: "HTML report", available: true,
      kind: "html-report", href }), null, href);
  }
});
