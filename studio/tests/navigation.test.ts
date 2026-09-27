import assert from "node:assert/strict";
import test from "node:test";

import { documentTitle, NAVIGATION, pageTitle } from "../src/navigation.ts";

test("the five stable areas keep their approved order and routes", () => {
  assert.deepEqual(
    NAVIGATION.map(({ label, path }) => [label, path]),
    [
      ["Hub", "/"],
      ["Configure", "/configure"],
      ["Experiments", "/experiments"],
      ["Reports", "/reports"],
      ["Activity", "/activity"],
    ],
  );
});

test("page titles follow the route and unknown routes return to Hub", () => {
  assert.equal(pageTitle("/reports"), "Reports");
  assert.equal(pageTitle("/not-a-route"), "Hub");
  assert.equal(documentTitle("/activity"), "Activity · Model Citizen Studio");
});
