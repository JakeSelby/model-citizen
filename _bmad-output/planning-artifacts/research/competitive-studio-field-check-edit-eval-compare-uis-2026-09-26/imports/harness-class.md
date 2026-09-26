# Studio-like UIs in the harness class (read 2026-09-26, via GitHub API READMEs/docs; 0 web searches)

## Ships a UI
- Burnd (github.com/garvitsurana271/burnd): local web dashboard, 10 waste detectors, spend chart, per-project breakdown (usage/cost view over Claude Code sessions). Start: `npx getburnd serve`, http://localhost:4711. Source: README.
- claude-code-templates (github.com/davila7/claude-code-templates): local UIs. `npx claude-code-templates@latest --analytics` (live session analytics), `--chats` (conversation monitor; `--tunnel` for remote via Cloudflare), `--plugins` (plugin dashboard: marketplaces, installed plugins, permissions). Hosted catalog at aitmpl.com. No config editing of rules, no eval triggering. Source: README. Whether --analytics/--plugins open a browser vs TUI: unconfirmed beyond "interface".
- claude-flow / RuFlo (github.com/ruvnet/claude-flow): hosted Web UI Beta at flo.ruv.io (multi-model chat, MCP tool gallery); hosted goal.ruv.io/agents live agent dashboard (inspect trajectories, kill/reassign agents). MetaHarness grades setup and `audit-trend` diffs two audits (run comparison) but is CLI/MCP, not UI. Source: README, docs/metaharness-user-guide.md.
- OpenSpec (github.com/Fission-AI/OpenSpec): `openspec view` "Interactive dashboard" of changes/specs (README screenshot). Terminal vs web: unconfirmed (screenshot not inspected). Browsing only. Source: README, docs/cli.md.
- claude-md-doctor (github.com/agent-clinic/claude-md-doctor): static HTML report `.claude-md-doctor/report.html`, local. Report, not an interactive UI. Source: README.

## No UI found in README
rulesync, ruler, ctxlint (JSON output "useful for building dashboards"), planning-with-files, gsd-core, wshobson/agents, gstack, agents-md-cookbook (Taiizor), RuleReceipt, superpowers, SuperClaude, BMad Method, Spec Kit, Agent OS (README thin; product site not read, unconfirmed).

## best-of-Agent-Harnesses "Coding harness configs and SDKs"
The section's entries are personal-agent runtimes (OpenClaw, AnythingLLM, nanobot, Khoj, Agent Zero, QM) with chat web UIs; none is a config-generator studio.

## Verdict
No tool in the class ships a Studio that edits rules, triggers evals on drafts and compares runs. Nearest: usage/cost dashboards (Burnd, claude-code-templates) and CLI audit diffs (MetaHarness).
