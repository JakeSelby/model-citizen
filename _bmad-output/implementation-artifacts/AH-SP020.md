---
bmad_id: "AH-SP020"
type: "spike"
title: "Spike: native auto-compaction at a lowered window, headless, with PreCompact and SessionStart(compact)"
lifecycle: "active"
provenance: "authored"
github_issue: 1291
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/1291"
parent_bmad_id: "AH-E028"
parent_github_issue: 1290
updated: "2026-10-12"
---

# AH-SP020 — Spike: native auto-compaction at a lowered window, headless, with PreCompact and SessionStart(compact)

<!-- bmad-sync:begin -->
- **GitHub issue:** [#1291](https://github.com/JakeSelby/model-citizen/issues/1291)
- **Primary parent:** [AH-E028](https://github.com/JakeSelby/model-citizen/issues/1290)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Does Claude Code's native auto-compaction, set to a lowered window, fire headless with PreCompact and SessionStart(`compact`) hooks and let the same `claude -p` turn continue? The whole epic's design waits on it.

## Experiment

The spike contract: Claude Code CLI as in the qualification targets, one throwaway container with an empty profile and the harness synced.

- Settings `env`: `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`, and `HARNESS_HANDOFF_BLOCK=on`, because headless runs skip the block today (`handoff_enabled()` in `policy/hooks/usage-feed.py`).
- Install a logging-only PreCompact hook and a SessionStart hook that echoes its `source` and emits `additionalContext` naming a marker string.
- Task: a ten-item checklist whose items each read a large fixture file, so context passes 40k by item four.
- Record whether: (a) compaction fires and at what context size; (b) PreCompact receives `trigger: auto`; (c) SessionStart fires with `source: compact` and the marker reaches the model, which it shows by quoting it; (d) the agent finishes the checklist in the same `-p` invocation.
- Also record whether the env var is read from settings `env` or only from the process environment, and the observed default trigger percentage.

## Exit criterion

All four of (a) to (d) hold. If (d) fails, compaction does not continue headless and the plan returns for review. If (c) fails, the reseed moves to the Stop hook's re-entry.

## Result

Not yet run.

## Decision

Pending the result. A pass unblocks AH-S363 to AH-S365 and fixes which env pair AH-S363 projects on a 1M model.

## Dev notes

- PreCompact can block with exit 2 but cannot supply the summary, so the harness reseeds through SessionStart instead of shaping the summary.
- Runs follow the controlled-experiments rule: fresh container, each arm exactly its named components.
- [Source: https://code.claude.com/docs/en/hooks] (PreCompact and SessionStart sections)
- [Source: https://code.claude.com/docs/en/env-vars]
- [Source: policy/hooks/usage-feed.py#handoff_enabled]
- [Source: .agent-harness/plans/2026-10-12-context-reset-and-continue.md#step-1--spike-the-native-mechanics]

## Change log

- 2026-10-12: written at filing (#1289).
