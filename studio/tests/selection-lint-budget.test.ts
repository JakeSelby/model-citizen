import assert from "node:assert/strict";
import test from "node:test";

import { budgetPercent, lintBudgetText } from "../src/selection/model.ts";

const budget = {
  runtime: "claude-code", label: "Claude Code", managed: true,
  used_tokens: 4236, token_cap: 4822, used_lines: 213, line_cap: 225, selected_lines: 180,
  lint_lines: 203, lint_tokens: 4100,
};

test("the citizen lint figure is the discounted one, against the same caps", () => {
  assert.equal(lintBudgetText(budget),
    `citizen lint counts ~${(4100).toLocaleString()} / ${(4822).toLocaleString()} tokens and 203 / 225 lines`);
  assert.notEqual(lintBudgetText(budget), lintBudgetText({ ...budget, lint_lines: 213 }));
});

test("the worst-case meter is unchanged by the lint figure", () => {
  assert.equal(budgetPercent(budget), budgetPercent({ ...budget, lint_tokens: 0, lint_lines: 0 }));
  assert.equal(budgetPercent(budget), 4236 / 4822 * 100);
});
