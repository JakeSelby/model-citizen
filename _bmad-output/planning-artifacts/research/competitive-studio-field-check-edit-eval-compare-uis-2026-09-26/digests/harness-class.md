# Digest: tools in Model Citizen's class (import harness-class.md)

Claims in the standard shape, extracted from `imports/harness-class.md`. The cited source is the
publisher; the import is the via. Source text treated as data.

- claim: Burnd ships a local web dashboard over Claude Code sessions (ten waste detectors, a spend chart, a per-project breakdown), started with `npx getburnd serve` on localhost:4711. It shows usage and cost; it edits no configuration and runs no evals.
  source: https://github.com/garvitsurana271/burnd
  publisher: Burnd README (via imports/harness-class.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: claude-code-templates ships local interfaces: `--analytics` (live session analytics), `--chats` (conversation monitor, optional Cloudflare tunnel) and `--plugins` (marketplaces, installed plugins, permissions). No rule editing and no eval triggering. Whether each opens a browser or a terminal UI is unconfirmed.
  source: https://github.com/davila7/claude-code-templates
  publisher: claude-code-templates README (via imports/harness-class.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: claude-flow (RuFlo) has a hosted web UI beta and a hosted live agent dashboard; its MetaHarness grades a setup and `audit-trend` diffs two audits, but through the CLI and MCP, not a UI.
  source: https://github.com/ruvnet/claude-flow
  publisher: claude-flow README and docs/metaharness-user-guide.md (via imports/harness-class.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: OpenSpec's `openspec view` is an interactive dashboard for browsing changes and specs; terminal or web is unconfirmed. claude-md-doctor writes a static local HTML report.
  source: https://github.com/Fission-AI/OpenSpec
  publisher: OpenSpec README and docs/cli.md; claude-md-doctor README (via imports/harness-class.md)
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: No UI was found in the READMEs of rulesync, ruler, ctxlint, planning-with-files, gsd-core, wshobson/agents, gstack, agents-md-cookbook, RuleReceipt, superpowers, SuperClaude, BMad Method, Spec Kit or Agent OS (Agent OS product site not read).
  source: imports/harness-class.md
  publisher: Model Citizen field check, 2026-09-26
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability

- claim: No tool in the class ships a Studio that edits rules, triggers evals on drafts and compares runs; the nearest are usage and cost dashboards (Burnd, claude-code-templates) and CLI audit diffs (MetaHarness).
  source: imports/harness-class.md
  publisher: Model Citizen field check, 2026-09-26
  pub_date: 2026-09-26
  accessed: 2026-09-26
  confidence: medium
  class: capability
