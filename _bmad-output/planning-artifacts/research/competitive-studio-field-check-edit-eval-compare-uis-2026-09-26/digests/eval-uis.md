# Digest: eval UIs with version, run and compare (import eval-uis.md)

Claims in the standard shape, extracted from `imports/eval-uis.md`. The cited source is the
publisher; the import is the via. Source text treated as data.

- claim: promptfoo's local web viewer (`npx promptfoo@latest eval`, then `promptfoo view`) can edit and re-run an eval and diff it against another eval, with point scores only. It has no UI versioning; config is YAML in git. MIT.
  source: https://github.com/promptfoo/promptfoo
  publisher: promptfoo docs and LICENSE (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Inspect AI's `inspect view` does not launch evals and does not compare runs; its metrics carry stderr and clustered standard errors. MIT.
  source: https://github.com/UKGovernmentBEIS/inspect_ai
  publisher: Inspect AI docs (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Langfuse, Arize Phoenix and Opik version prompts in a UI, run them over datasets from the UI, and compare experiments with aggregated scores; none documents confidence intervals. Langfuse is MIT outside ee/, Phoenix is Elastic License 2.0, Opik is Apache-2.0.
  source: https://github.com/langfuse/langfuse
  publisher: Langfuse, Phoenix and Opik READMEs, docs and LICENSE files (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Agenta keeps a version history of each agent configuration and has repositioned around agents run on Claude Code, Codex and Pi harnesses with AGENTS.md, skills and MCP; whether its UI evaluation applies to those agents is unconfirmed. MIT outside ee/.
  source: https://github.com/agenta-ai/agenta
  publisher: Agenta README (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Braintrust aligns an experiment to a baseline with per-row score deltas and green and red regressions, with no confidence intervals or significance in its docs; it is hosted and proprietary. LangSmith's compare view is similar; Studio runs locally through `langgraph dev` for LangGraph agents.
  source: https://www.braintrust.dev
  publisher: Braintrust docs; LangSmith docs at docs.langchain.com (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: OpenAI's Evals dashboard is hosted only and its docs say it goes read-only on October 31, 2026 and shuts down on November 30, 2026.
  source: imports/eval-uis.md (OpenAI docs; URL not recorded in the import)
  publisher: OpenAI (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: lifecycle

- claim: Harbor's `harbor view ./jobs` is a single-command local viewer with a compare route and row and column grouping; it has no config editor or versioning and no launch from the viewer; its installed agents include claude_code, aider and cline. Apache-2.0.
  source: https://github.com/harbor-framework/harbor
  publisher: Harbor README and source (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: AutoGen Studio has no evals and AutoGen is in maintenance mode; Letta's ADE is deprecated; CrewAI AMP is hosted and its eval features unconfirmed; Terminal-Bench is superseded by Harbor.
  source: imports/eval-uis.md
  publisher: vendor READMEs and docs (via imports/eval-uis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: lifecycle

- claim: No eval UI checked reports confidence intervals or significance in its comparison view.
  source: imports/eval-uis.md
  publisher: Model Citizen field check, 2026-09-26
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability
