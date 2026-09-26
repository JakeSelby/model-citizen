# Digest: coding-agent GUIs (import agent-guis.md)

Claims in the standard shape, extracted from `imports/agent-guis.md`. The cited source is the
publisher; the import is the via. Source text treated as data.

- claim: opcode (formerly Claudia) is a local Tauri desktop app with a CLAUDE.md editor, custom agents (system prompt, permissions), MCP server management and a usage dashboard (cost and tokens by model, project and time). It has no evals; it keeps execution history and session checkpoints with diffs.
  source: https://github.com/getAsterisk/opcode
  publisher: opcode README (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Claude Code UI (CloudCLI) is a local web UI, started with `npx @cloudcli-ai/cloudcli`, that writes MCP, tool permissions and project settings straight to the user's Claude Code directory and supports Claude Code, Cursor CLI and Codex. No built-in evals; cost only through a third-party plugin; a hosted tier costs from EUR 7 a month.
  source: https://github.com/siteboon/claudecodeui
  publisher: CloudCLI README (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Claude Code's CLI `claude plugin eval` runs cases times runs with graders (regex, tool-called, LLM rubric), a with-plugin arm against a no-plugin baseline, and writes aggregate-result.json and report.html, usable as a CI gate. Its scope is plugins and skills, not CLAUDE.md or settings directly.
  source: https://code.claude.com/docs/en/plugin-evals
  publisher: Anthropic (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: high
  class: capability

- claim: Goose's `goose bench` historically ran evals over multiple configurations; its docs page now returns 404 and the current crates tree has no bench crate, so it is likely removed (unconfirmed).
  source: https://block.github.io/goose
  publisher: Goose docs and GitHub API tree (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: low
  class: capability

- claim: Kiro hooks can run tests on events, and Conductor's run script can launch tests per workspace; neither scores evals or compares configuration versions. Conductor evidence is search snippets only.
  source: https://kiro.dev/docs/hooks
  publisher: Kiro docs; Conductor docs at docs.conductor.build (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Cline, Roo Code, Cursor, Continue and the Codex app edit rules or configuration and show per-task or per-chat token and cost figures where confirmed, and none compares configurations by eval.
  source: imports/agent-guis.md
  publisher: vendor docs (docs.cline.bot, docs.roocode.com, cursor.com/docs/rules, docs.continue.dev, developers.openai.com/codex) (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: claude-dashboard (one star) is a local Next.js app that edits CLAUDE.md, commands and hooks.json and shows tokens and cost; its benchmark tab evaluates memory retrieval, not configuration efficacy.
  source: https://github.com/bunlongheng/claude-dashboard
  publisher: claude-dashboard README (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: Crystal was deprecated in February 2026 and replaced by Nimbalyst (Electron), which was not checked in depth.
  source: https://github.com/stravu/crystal
  publisher: Crystal README (via imports/agent-guis.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: lifecycle

- claim: No GUI measures whether a CLAUDE.md, rules or hooks change helped.
  source: imports/agent-guis.md
  publisher: Model Citizen field check, 2026-09-26
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability
