# Digest: spot-checks of the load-bearing claims (2026-09-26)

Independent primary-source checks at `normal` validation, seven fetches and two web searches.
Source text treated as data.

- claim: `claude plugin eval` runs each case in a with-arm (plugin loaded) and a without-arm (no plugin), three runs each by default, and reports `WITH`, `W/OUT` and their difference `Δ`; `--ablation none` drops the baseline. It writes `aggregate-result.json` and a self-contained `report.html`. In the eval sandbox the user's settings, hooks, `CLAUDE.md` files, memory and other plugins are absent. The delta never changes the exit code; the threshold applies to the with-arm score. The page mentions no confidence interval or standard error.
  source: https://code.claude.com/docs/en/plugin-evals
  publisher: Anthropic
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: high
  class: capability
  status: verified

- claim: Harbor's viewer compare route takes one or more jobs and groups results by agent and model in rows and columns; the installed-agent directory holds claude_code, codex, aider, cline and goose among others. The compare route has no stderr, interval or significance terms.
  source: https://github.com/harbor-framework/harbor/blob/main/apps/viewer/app/routes/compare.tsx
  publisher: Harbor (harbor-framework)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: high
  class: capability
  status: verified

- claim: Agenta's README says it "keeps a version history of each agent configuration", that agents are defined with `AGENTS.md`, skills and MCP servers, and that Claude Code, Pi and Codex are supported harnesses. The README has no mention of evaluation.
  source: https://github.com/agenta-ai/agenta
  publisher: Agenta
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: high
  class: capability
  status: verified (versioning); eval linkage unconfirmed

- claim: promptfoo's web viewer documents "Compare - Diff against another eval" and head-to-head prompt comparison, with no interval or significance terms on the page.
  source: https://github.com/promptfoo/promptfoo/blob/main/site/docs/usage/web-ui.md
  publisher: promptfoo
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: high
  class: capability
  status: supports the no-intervals claim for promptfoo only

- claim: One web search for eval-platform comparison UIs with confidence intervals, and one for a local UI that edits Claude Code configuration and compares evals against a baseline, surfaced no counterexample. Absence of a search hit is not proof of absence.
  source: web search, 2026-09-26
  publisher: n/a
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: low
  class: capability
  status: no counterexample

- note: the Braintrust interpret-results page returned 404 and a Langfuse docs URL returned an HTML error shell; neither is used as evidence.
